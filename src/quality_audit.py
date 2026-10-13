"""
ShopScope - reusable, machine-readable data-quality audit (Phase 2 deliverable).

For **every** CSV (raw and cleaned) the audit reports, in one place:

* original row and column counts;
* missing values by column, including percentages;
* fully duplicated rows;
* potential duplicate *business* keys (with a written note explaining when a
  repeated value is legitimate - e.g. several line items sharing one order_id);
* invalid and inconsistent values (unparseable dates/numbers, non-positive
  prices, payments, installments or product dimensions, review scores outside
  1-5, impossible date sequences, coordinates outside the Brazil bounding box);
* primary-key uniqueness and foreign-key orphans (referential integrity
  between customers, orders, items, payments, reviews, products, sellers,
  categories and geolocation).

Outputs (all overwritten on every run, never appended to)::

    reports/data_quality_audit.csv    - long format, one row per (file, check);
                                        opens directly in Excel / pandas
    reports/data_quality_audit.json   - the same facts, structured
    reports/data_quality_summary.md   - human-readable summary

The audit is **read-only** - it never writes to the raw CSVs or the cleaned
CSVs. It runs as the final stage of the ETL pipeline (":code:`src.etl_pipeline`",
which hands over the frames it already loaded), or standalone::

    python -m src.quality_audit

Because the checks are pure functions of DataFrames, they are unit-testable
(see tests/test_quality_audit.py) and safe to re-run any number of times.

Design rule: a repeated value is only ever called a "duplicate" when the
business key says so. Multiple order_item rows for one order, multiple payment
records for one order, or several rows for one customer_unique_id are reported
as *expected* multiplicity, not as defects.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from . import config, data_loader, validation

logger = logging.getLogger(__name__)

AUDIT_COLUMNS = ["file", "check", "column", "value", "note", "status"]
STATUS_INFO = "info"
STATUS_OK = "ok"
STATUS_FLAGGED = "flagged"

# name: business-key definition (key_columns, note, flag_as_defect?)
# `key_columns` may be a comma-separated compound key (e.g. "order_id,order_item_id").
# flag_as_defect=False marks legitimate multiplicity (repeat buyers, multi-item
# orders, multiple payments per order, multiple geo rows per zip before centroiding).
RAW_BUSINESS_KEYS: dict[str, list[tuple[str, str, bool]]] = {
    "olist_orders_dataset.csv": [("order_id", "primary key", True)],
    "olist_customers_dataset.csv": [
        ("customer_id", "primary key", True),
        ("customer_unique_id",
         "repeat-buyer group: 2+ rows = one person ordering more than once "
         "(cleaner collapses them into dim_customer_identity; NOT a defect)", False),
    ],
    "olist_order_items_dataset.csv": [
        ("order_id",
         "one order legitimately holds many line items - the line-item grain is "
         "(order_id, order_item_id); NOT a defect", False),
        ("order_id,order_item_id", "compound primary key of the line-item fact", True),
    ],
    "olist_order_payments_dataset.csv": [
        ("order_id",
         "one order legitimately has several payment records (types/sequential); "
         "the bridge grain is (order_id, payment_sequential); NOT a defect", False),
        ("order_id,payment_sequential", "compound primary key of the payment bridge", True),
    ],
    "olist_order_reviews_dataset.csv": [
        ("review_id", "primary key", True),
        ("order_id",
         "1:N is legitimate, but a second review on the same order is collapsed "
         "(latest kept) so it never double-weights CSAT", True),
    ],
    "olist_products_dataset.csv": [("product_id", "primary key", True)],
    "olist_sellers_dataset.csv": [("seller_id", "primary key", True)],
    "olist_geolocation_dataset.csv": [
        ("geolocation_zip_code_prefix",
         "zip master: many source rows per zip are centroided to one row; "
         "261,831 exact duplicate rows are the known case", True),
    ],
    "product_category_name_translation.csv": [
        ("product_category_name", "primary key", True),
    ],
}

# Cleaned tables: every primary key must be unique and non-null.
CLEANED_BUSINESS_KEYS: dict[str, list[tuple[str, str, bool]]] = {
    "dim_customer": [("customer_id", "primary key (one row per order placement)", True)],
    "dim_customer_identity": [("customer_unique_id", "one row per real person", True)],
    "dim_order": [("order_id", "primary key", True)],
    "dim_product": [("product_id", "primary key", True)],
    "dim_product_category": [("product_category_name", "primary key", True)],
    "dim_seller": [("seller_id", "primary key", True)],
    "dim_geolocation": [("geolocation_zip_code_prefix", "primary key (one centroid per zip)", True)],
    "fact_order_item": [("order_id,order_item_id", "compound primary key", True)],
    "bridge_payment": [("order_id,payment_sequential", "compound primary key", True)],
    "bridge_review": [("order_id", "one review per order by design", True)],
    "fact_order_summary": [("order_id", "primary key (one pre-aggregated row per order)", True)],
}

# Referential-integrity pairs: (relationship, child_file, child_key, parent_file, parent_key).
# Checked on the RAW files to show the source-level gaps.
RAW_RELATIONSHIPS: list[tuple[str, str, str, str, str]] = [
    ("item.order", "olist_order_items_dataset.csv", "order_id",
     "olist_orders_dataset.csv", "order_id"),
    ("item.product", "olist_order_items_dataset.csv", "product_id",
     "olist_products_dataset.csv", "product_id"),
    ("item.seller", "olist_order_items_dataset.csv", "seller_id",
     "olist_sellers_dataset.csv", "seller_id"),
    ("payment.order", "olist_order_payments_dataset.csv", "order_id",
     "olist_orders_dataset.csv", "order_id"),
    ("review.order", "olist_order_reviews_dataset.csv", "order_id",
     "olist_orders_dataset.csv", "order_id"),
    ("order.customer", "olist_orders_dataset.csv", "customer_id",
     "olist_customers_dataset.csv", "customer_id"),
    ("product.category", "olist_products_dataset.csv", "product_category_name",
     "product_category_name_translation.csv", "product_category_name"),
    ("customer.zip", "olist_customers_dataset.csv", "customer_zip_code_prefix",
     "olist_geolocation_dataset.csv", "geolocation_zip_code_prefix"),
    ("seller.zip", "olist_sellers_dataset.csv", "seller_zip_code_prefix",
     "olist_geolocation_dataset.csv", "geolocation_zip_code_prefix"),
]

# Same pairs on the cleaned tables.
CLEANED_RELATIONSHIPS: list[tuple[str, str, str, str, str]] = [
    ("item.order", "fact_order_item", "order_id", "dim_order", "order_id"),
    ("item.product", "fact_order_item", "product_id", "dim_product", "product_id"),
    ("item.seller", "fact_order_item", "seller_id", "dim_seller", "seller_id"),
    ("payment.order", "bridge_payment", "order_id", "dim_order", "order_id"),
    ("review.order", "bridge_review", "order_id", "dim_order", "order_id"),
    ("order.customer", "dim_order", "customer_id", "dim_customer", "customer_id"),
    ("summary.order", "fact_order_summary", "order_id", "dim_order", "order_id"),
    ("product.category", "dim_product", "product_category_name",
     "dim_product_category", "product_category_name"),
    ("customer.zip", "dim_customer", "customer_zip_code_prefix",
     "dim_geolocation", "geolocation_zip_code_prefix"),
    ("seller.zip", "dim_seller", "seller_zip_code_prefix",
     "dim_geolocation", "geolocation_zip_code_prefix"),
]


# --------------------------------------------------------------------------
# Primitives
# --------------------------------------------------------------------------
def _row(file: str, check: str, column: str, value: str, note: str,
         status: str) -> dict[str, str]:
    return {"file": file, "check": check, "column": column,
            "value": value, "note": note, "status": status}


def _missing_pct(frame: pd.DataFrame, col: str) -> float:
    n = len(frame)
    return round(float(frame[col].isna().sum()) / n * 100, 2) if n else 0.0


def dup_key_stats(frame: pd.DataFrame, key_columns: list[str]) -> tuple[int, int]:
    """Return (extra_rows, duplicate_groups) for a key after dropping NaN keys.

    ``extra_rows`` = rows that would disappear if the key were unique
    (sum of (group_size - 1) over groups with size > 1).
    """
    keys = [c for c in key_columns if c in frame.columns]
    if len(keys) != len(key_columns) or not keys:
        return 0, 0
    sized = frame[keys].dropna(how="any").groupby(keys, dropna=False).size()
    dup = sized[sized > 1]
    return int((dup - 1).sum()), int(len(dup))


def orphan_count(child: pd.DataFrame, child_key: str,
                parent: pd.DataFrame, parent_key: str) -> tuple[int, int] | None:
    """Return (orphan_rows, child_rows_with_key) or None if columns are missing."""
    if child_key not in child.columns or parent_key not in parent.columns:
        return None
    child_vals = child[child_key].dropna()
    parent_vals = set(parent[parent_key].dropna())
    return int((~child_vals.isin(parent_vals)).sum()), int(len(child_vals))


# --------------------------------------------------------------------------
# Row builders (raw + cleaned share these)
# --------------------------------------------------------------------------
def file_level_rows(file_name: str, frame: pd.DataFrame,
                    keys: list[tuple[str, str, bool]]) -> list[dict[str, str]]:
    rows = [
        _row(file_name, "row_count", "-", f"{len(frame):,}",
             "rows in the file", STATUS_INFO),
        _row(file_name, "column_count", "-", str(frame.shape[1]),
             "columns in the file", STATUS_INFO),
    ]
    dup = int(frame.duplicated().sum())
    rows.append(_row(file_name, "exact_duplicate_rows", "-", f"{dup:,}",
                     "fully duplicated rows", STATUS_FLAGGED if dup else STATUS_OK))
    for key, note, flag in keys:
        cols = [c.strip() for c in key.split(",")]
        if not all(c in frame.columns for c in cols):
            continue
        extra, groups = dup_key_stats(frame, cols)
        status = STATUS_FLAGGED if (flag and extra) else (STATUS_INFO if not flag else STATUS_OK)
        rows.append(_row(
            file_name, f"business_key.{key}", "-",
            f"{extra:,} extra rows in {groups:,} groups",
            note, status,
        ))
    return rows


def missing_value_rows(file_name: str, frame: pd.DataFrame,
                       columns: list[str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for col in columns:
        if col not in frame.columns:
            continue
        n = int(frame[col].isna().sum())
        rows.append(_row(
            file_name, f"missing.{col}", col,
            f"{n:,} ({_missing_pct(frame, col)}%)",
            "missing values by column",
            STATUS_FLAGGED if n else STATUS_OK,
        ))
    return rows


def invalid_value_rows(file_name: str, frame: pd.DataFrame) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []

    def add(check: str, count: int, note: str) -> None:
        rows.append(_row(file_name, check, "-", f"{count:,}", note,
                         STATUS_FLAGGED if count else STATUS_OK))

    if file_name == "olist_order_reviews_dataset.csv":
        s = frame["review_score"].dropna()
        add("invalid.review_score_outside_1_5", int(((s < 1) | (s > 5)).sum()),
            "review scores outside the 1-5 scale")
    elif file_name == "olist_order_items_dataset.csv":
        p = frame["price"].dropna()
        f2 = frame["freight_value"].dropna()
        add("invalid.price_non_positive", int((p <= 0).sum()),
            "item unit price <= 0")
        add("invalid.freight_negative", int((f2 < 0).sum()),
            "freight value < 0")
    elif file_name == "olist_order_payments_dataset.csv":
        v = frame["payment_value"].dropna()
        i2 = frame["payment_installments"].dropna()
        add("invalid.payment_value_non_positive", int((v <= 0).sum()),
            "payment value <= 0")
        add("invalid.installments_non_positive", int((i2 <= 0).sum()),
            "installment count <= 0")
    elif file_name == "olist_products_dataset.csv":
        for col in ("product_weight_g", "product_length_cm",
                    "product_height_cm", "product_width_cm"):
            s = frame[col].dropna()
            add(f"invalid.{col}_non_positive", int((s <= 0).sum()),
                "physical dimension or weight <= 0")
    elif file_name == "olist_orders_dataset.csv":
        a = frame[["order_purchase_timestamp", "order_approved_at"]].dropna()
        add("invalid.approved_before_purchase",
            int((a["order_approved_at"] < a["order_purchase_timestamp"]).sum()),
            "approval timestamp precedes purchase timestamp")
        b = frame[["order_purchase_timestamp", "order_delivered_customer_date"]].dropna()
        add("invalid.delivered_before_purchase",
            int((b["order_delivered_customer_date"] < b["order_purchase_timestamp"]).sum()),
            "delivery timestamp precedes purchase timestamp")
    elif file_name == "olist_geolocation_dataset.csv":
        lat = frame["geolocation_lat"].dropna()
        lng = frame["geolocation_lng"].dropna()
        add("invalid.lat_outside_brazil_box",
            int(((lat < config.BRAZIL_LAT_MIN) | (lat > config.BRAZIL_LAT_MAX)).sum()),
            "latitude outside the Brazil bounding box")
        add("invalid.lng_outside_brazil_box",
            int(((lng < config.BRAZIL_LNG_MIN) | (lng > config.BRAZIL_LNG_MAX)).sum()),
            "longitude outside the Brazil bounding box")
        add("invalid.lat_magnitude_over_90", int((lat.abs() > 90).sum()),
            "latitude magnitude > 90 (physically impossible)")
        add("invalid.lng_magnitude_over_180", int((lng.abs() > 180).sum()),
            "longitude magnitude > 180 (physically impossible)")
    return rows


def relationship_counts(relationships: list[tuple[str, str, str, str, str]],
                        frames: dict[str, pd.DataFrame],
                        only_file: str | None = None) -> dict[str, tuple[int, int]]:
    """Foreign-key orphan counts for the pairs whose child is ``only_file``.

    Returns ``{relationship_name: (orphan_rows, child_rows_with_key)}``.
    """
    out: dict[str, tuple[int, int]] = {}
    for rel_name, child_file, child_key, parent_file, parent_key in relationships:
        if only_file is not None and child_file != only_file:
            continue
        child = frames.get(child_file)
        parent = frames.get(parent_file)
        if child is None or parent is None:
            continue
        result = orphan_count(child, child_key, parent, parent_key)
        if result is not None:
            out[rel_name] = result
    return out


# --------------------------------------------------------------------------
# Full audit assembly
# --------------------------------------------------------------------------
def audit_one_frames(file_name: str, frame: pd.DataFrame,
                     keys: list[tuple[str, str, bool]],
                     relationships: list[tuple[str, str, str, str, str]],
                     frames: dict[str, pd.DataFrame],
                     required: list[str] | None = None,
                     parse_failures: dict[str, int] | None = None) -> dict[str, Any]:
    """Profile one file/table and bundle everything under its name."""
    return {
        "rows": len(frame),
        "column_count": frame.shape[1],
        "exact_duplicate_rows": int(frame.duplicated().sum()),
        "business_keys": {
            key: {"extra_rows": extra, "duplicate_groups": groups}
            for key, _note, _flag in keys
            if all((c.strip() in frame.columns) for c in key.split(","))
            for extra, groups in [dup_key_stats(frame, [c.strip() for c in key.split(",")])]
        },
        "columns": {
            col: {
                "dtype": str(frame[col].dtype),
                "non_null": int(frame[col].notna().sum()),
                "unique": int(frame[col].nunique(dropna=True)),
            }
            for col in frame.columns
        },
        "missing": {
            col: {"count": int(frame[col].isna().sum()), "pct": _missing_pct(frame, col)}
            for col in (required if required is not None else list(frame.columns))
            if col in frame.columns
        },
        "invalid": {r["check"].split(".", 1)[1]: int(r["value"].replace(",", ""))
                    for r in invalid_value_rows(file_name, frame) if r["status"] == STATUS_FLAGGED},
        "parse_failures": dict(parse_failures or {}),
        "relationships": {
            rel_name: {"orphan_rows": orphans, "child_rows": child_rows}
            for rel_name, (orphans, child_rows) in
            relationship_counts(relationships, frames, only_file=file_name).items()
        },
    }


def build_payload(loaded: dict[str, data_loader.LoadResult] | dict[str, pd.DataFrame],
                  cleaned: dict[str, pd.DataFrame] | None = None,
                  validation_report: validation.ValidationReport | None = None,
                  source: str = "standalone") -> dict[str, Any]:
    """Assemble the full machine-readable audit payload.

    ``loaded`` may be either the loader's :class:`LoadResult` map (preferred -
    carries parse failures) or a plain ``{filename: frame}`` map.
    """
    raw_frames: dict[str, pd.DataFrame] = {}
    raw_failures: dict[str, dict[str, int]] = {}
    for name, item in loaded.items():
        if isinstance(item, data_loader.LoadResult):
            raw_frames[name] = item.frame
            raw_failures[name] = item.parse_failures
        else:
            raw_frames[name] = item

    raw = {
        name: audit_one_frames(name, frame, RAW_BUSINESS_KEYS.get(name, []),
                               RAW_RELATIONSHIPS, raw_frames,
                               required=config.REQUIRED_COLUMNS.get(name),
                               parse_failures=raw_failures.get(name))
        for name, frame in raw_frames.items()
    }

    clean: dict[str, Any] = {}
    if cleaned:
        clean = {
            name: audit_one_frames(name, frame, CLEANED_BUSINESS_KEYS.get(name, []),
                                   CLEANED_RELATIONSHIPS, cleaned)
            for name, frame in cleaned.items()
        }

    validation_block: dict[str, Any] = {}
    if validation_report is not None:
        validation_block = {
            "total_checks": len(validation_report.checks),
            "passed": sum(1 for c in validation_report.checks if c.passed),
            "errors": len(validation_report.errors),
            "warnings": len(validation_report.warnings),
            "failed_checks": [c.as_dict() for c in validation_report.checks if not c.passed],
        }

    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source,
        "raw": raw,
        "cleaned": clean,
        "validation": validation_block,
    }


def build_csv_rows(payload: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for section in ("raw", "cleaned"):
        for file_name, entry in payload[section].items():
            rows.append(_row(file_name, "row_count", "-", f"{entry['rows']:,}",
                             "rows", STATUS_INFO))
            rows.append(_row(file_name, "column_count", "-", str(entry["column_count"]),
                             "columns in the file", STATUS_INFO))
            dup = entry["exact_duplicate_rows"]
            rows.append(_row(file_name, "exact_duplicate_rows", "-", f"{dup:,}",
                             "fully duplicated rows",
                             STATUS_FLAGGED if dup else STATUS_OK))
            for key, stats in entry["business_keys"].items():
                extra = stats["extra_rows"]
                groups = stats["duplicate_groups"]
                rows.append(_row(file_name, f"business_key.{key}", "-",
                                 f"{extra:,} extra rows in {groups:,} groups",
                                 "duplicate business key",
                                 STATUS_FLAGGED if extra else STATUS_OK))
            for col, stat in entry["missing"].items():
                rows.append(_row(file_name, f"missing.{col}", col,
                                 f"{stat['count']:,} ({stat['pct']}%)",
                                 "missing values by column",
                                 STATUS_FLAGGED if stat["count"] else STATUS_OK))
            for col, prof in entry["columns"].items():
                rows.append(_row(file_name, "column_profile", col,
                                 f"{prof['non_null']:,} non-null",
                                 f"dtype={prof['dtype']} unique={prof['unique']:,}",
                                 STATUS_INFO))
            for check, n in entry.get("invalid", {}).items():
                rows.append(_row(file_name, f"invalid.{check}", "-", f"{n:,}",
                                 "invalid or out-of-range value",
                                 STATUS_FLAGGED if n else STATUS_OK))
            for label, n in entry.get("parse_failures", {}).items():
                rows.append(_row(file_name, "invalid.parse_failure", "-", f"{n:,}",
                                 f"type-conversion failures ({label})", STATUS_FLAGGED))
            for rel, stats in entry.get("relationships", {}).items():
                orphans = stats["orphan_rows"]
                rows.append(_row(file_name, f"orphan.{rel}", "-",
                                 f"{orphans:,} of {stats['child_rows']:,}",
                                 "foreign-key orphans (child rows without a parent)",
                                 STATUS_FLAGGED if orphans else STATUS_OK))
    return rows


def build_summary_md(payload: dict[str, Any]) -> str:
    md: list[str] = ["# ShopScope - Data Quality Audit (machine-readable summary)",
                     "",
                     f"Generated (UTC): {payload['generated_utc']}  |  "
                     f"source: `{payload['source']}`",
                     "",
                     "This summary is the human-readable companion to "
                     "`reports/data_quality_audit.csv` and "
                     "`reports/data_quality_audit.json`. Regenerate it with:",
                     "",
                     "```powershell",
                     "python -m src.quality_audit     # standalone",
                     "python -m src.etl_pipeline      # as the final ETL stage",
                     "```",
                     "",
                     "> A repeated value is only a duplicate when the business key says so. "
                     "Multi-item orders, multi-record payments and repeat buyers are reported "
                     "as expected multiplicity, not as defects.",
                     ""]

    for section in ("raw", "cleaned"):
        label = "Raw source files" if section == "raw" else "Cleaned tables"
        key_defs = RAW_BUSINESS_KEYS if section == "raw" else CLEANED_BUSINESS_KEYS
        md += [f"## {label}", ""]
        if not payload[section]:
            md += ["_None available in this run._", ""]
            continue
        md += ["| File | Rows | Cols | Exact dup rows | Keys duplicated | Missing cols | Orphans |",
               "|------|-----:|-----:|---------------:|----------------:|-------------:|--------:|"]
        for file_name, entry in payload[section].items():
            flag_map = {key: flag for key, _note, flag in key_defs.get(file_name, [])}
            dup_keys = [k for k, s in entry["business_keys"].items()
                        if s["extra_rows"] and flag_map.get(k, True)]
            missing = {c for c, s in entry["missing"].items() if s["count"]}
            orphans = {r for r, s in entry["relationships"].items() if s["orphan_rows"]}
            md.append(
                f"| `{file_name}` | {entry['rows']:,} | {entry['column_count']} | "
                f"{entry['exact_duplicate_rows']:,} | "
                f"{', '.join(dup_keys) if dup_keys else '-'} | "
                f"{len(missing)} cols | "
                f"{', '.join(sorted(orphans)) if orphans else '-'} |"
            )
        md += [""]

    md += ["## Invalid / out-of-range values (flagged counts)", "", "| File | Check | Count |",
           "|------|-------|------:|"]
    seen = False
    for section in ("raw", "cleaned"):
        for file_name, entry in payload[section].items():
            for check, n in {**entry.get("invalid", {}),
                             **{f"parse_failure.{k}": v for k, v in entry.get("parse_failures", {}).items()}}.items():
                if n:
                    seen = True
                    md.append(f"| `{file_name}` | `{check}` | {n:,} |")
    if not seen:
        md += ["| - | no flagged invalid values | 0 |"]
    md += [""]

    if payload["validation"]:
        v = payload["validation"]
        md += ["## Pipeline validation", "",
               f"- Checks: **{v['total_checks']}** total | **{v['passed']}** passed | "
               f"**{v['errors']}** errors | **{v['warnings']}** warnings",
               ""]
        if v["failed_checks"]:
            md += ["Failed / warning checks:", ""]
            for c in v["failed_checks"]:
                md.append(f"- `{c['check_id']}` ({c['severity']}): {c['description']} "
                          f"-> observed {c['observed']} (expected {c['expected'] or '-'})")
            md += [""]

    ref = Path("reports/data_quality_before_cleaning.md").absolute()
    md += ["## Full context",
           "",
           "- `reports/data_quality_before_cleaning.md` / `reports/data_quality_after_cleaning.md` - "
           "human-readable before/after quality reports.",
           "- `reports/etl_run_summary.json` - ETL run ledger (row counts, validation, quarantines).",
           "- `reports/metrics_before_after.md` - headline KPIs computed naively on the raw files "
           "vs the cleaned database.",
           ""]
    return "\n".join(md)


# --------------------------------------------------------------------------
# Writers
# --------------------------------------------------------------------------
def write_audit(loaded: dict[str, data_loader.LoadResult] | dict[str, pd.DataFrame],
                cleaned: dict[str, pd.DataFrame] | None = None,
                ledger: list[dict] | None = None,
                validation: validation.ValidationReport | None = None,
                source: str = "standalone",
                out_dir: Path | str | None = None) -> dict[str, Path]:
    """Write the three audit artifacts. Returns ``{kind: path}``."""
    out = Path(out_dir) if out_dir else config.REPORTS_DIR
    out.mkdir(parents=True, exist_ok=True)

    payload = build_payload(loaded, cleaned, validation_report=validation, source=source)
    if ledger:
        payload["row_ledger"] = ledger

    csv_rows = build_csv_rows(payload)

    csv_path = out / "data_quality_audit.csv"
    pd.DataFrame(csv_rows, columns=AUDIT_COLUMNS).to_csv(csv_path, index=False)

    json_path = out / "data_quality_audit.json"
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    md_path = out / "data_quality_summary.md"
    md_path.write_text(build_summary_md(payload), encoding="utf-8")

    logger.info("Data-quality audit written: %s, %s, %s",
                csv_path.name, json_path.name, md_path.name)
    return {"csv": csv_path, "json": json_path, "summary": md_path}


# --------------------------------------------------------------------------
# Standalone CLI
# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.quality_audit",
        description="Regenerate the machine-readable data-quality audit from the "
                    "raw CSVs and the cleaned CSVs in data/processed/. Read-only.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    loaded = data_loader.load_all()
    raw = {name: result.frame for name, result in loaded.items()}
    cleaned = {}
    for name in config.OUTPUT_FILES:
        path = config.PROCESSED_DIR / name
        if path.is_file():
            cleaned[name] = pd.read_csv(path)

    paths = write_audit(loaded, cleaned, source="standalone")
    print(f"Wrote {len(loaded)} raw + {len(cleaned)} cleaned audits to reports/:")
    for kind, path in paths.items():
        print(f"  {kind:7s} {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())