"""
ShopScope - ETL pipeline orchestrator (Stages 1-4).

    load  ->  profile (BEFORE report)  ->  clean  ->  transform  ->  validate
           ->  write outputs  ->  profile (AFTER report)  ->  verify sources unchanged

Run from the project root on Windows PowerShell::

    python -m src.etl_pipeline

Exit codes
----------
``0``  all checks passed (warnings may still be present - they are listed as
       unresolved issues in the reports)
``1``  a blocking error occurred: missing file, schema breach, failed
       validation check, or a raw source file changed during the run

Idempotency
-----------
Every file this module writes is *overwritten*, and the known output files are
deleted first. Running the pipeline twice therefore produces byte-identical
outputs instead of appending a second copy of the data.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone

import pandas as pd

from . import config, data_cleaning, data_loader, quality_audit, reporting, transform, validation
from .data_cleaning import CleaningResult
from .data_loader import DataLoadError, LoadResult
from .validation import ERROR, ValidationReport

logger = logging.getLogger("shopscope.etl")


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------
def setup_logging(verbose: bool = False) -> None:
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        # overwritten each run so the log always describes exactly one run
        logging.FileHandler(config.LOG_FILE, mode="w", encoding="utf-8"),
    ]
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format=fmt,
        handlers=handlers,
        force=True,
    )


# --------------------------------------------------------------------------
# Output writing (idempotent)
# --------------------------------------------------------------------------
def prepare_output_dirs() -> None:
    """Recreate output folders and remove only the files this run owns."""
    for directory in (config.PROCESSED_DIR, config.QUARANTINE_DIR):
        directory.mkdir(parents=True, exist_ok=True)
        for name in config.OUTPUT_FILES + config.QUARANTINE_FILES:
            target = directory / name
            if target.exists():
                target.unlink()
                logger.debug("Removed stale output %s", target)


def write_frame(directory: Path, filename: str, frame: pd.DataFrame) -> Path:
    path = directory / filename
    frame.to_csv(path, index=False, lineterminator="\n")
    logger.info("  wrote %-42s %8s rows  %10s bytes",
                path.name, f"{len(frame):,}", f"{path.stat().st_size:,}")
    return path


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------
def run(strict: bool = False, verbose: bool = False) -> int:
    started = time.perf_counter()
    setup_logging(verbose)

    logger.info("=" * 78)
    logger.info("ShopScope ETL - run started")
    logger.info("Project root: %s", config.PROJECT_ROOT)
    logger.info("=" * 78)

    # ---------------- Stage 1: ingest -----------------------------------
    logger.info("[1/6] Ingesting raw CSVs (read-only)")
    try:
        raw_hashes = data_loader.hash_raw_files()
        loaded: dict[str, LoadResult] = data_loader.load_all()
    except DataLoadError as exc:
        logger.error("Ingestion failed: %s", exc)
        return 1
    except Exception:  # noqa: BLE001
        logger.exception("Unexpected failure while reading raw files")
        return 1

    raw_frames = {name: res.frame for name, res in loaded.items()}

    # ---------------- Profile BEFORE -------------------------------------
    logger.info("[2/6] Profiling raw data -> before report")
    before_profiles = reporting.profile_many(raw_frames)
    loaded_meta = [
        {
            "file": r.name,
            "file_bytes": r.file_bytes,
            "source_rows": r.source_rows,
            "source_columns": r.source_columns,
            "sha256": r.sha256,
            "parse_failures": r.parse_failures,
        }
        for r in loaded.values()
    ]

    # ---------------- Clean ----------------------------------------------
    logger.info("[3/6] Cleaning")
    ledger: list[dict] = []
    quarantine_frames: dict[str, pd.DataFrame] = {}

    def record(result: CleaningResult, note: str = "") -> CleaningResult:
        entry = {
            "table": result.name,
            "input_rows": result.input_rows,
            "output_rows": result.output_rows,
            "excluded_rows": result.excluded_rows,
            "exclusion_reason": result.exclusion_reason or note,
            "note": note or result.exclusion_reason or "no exclusions (row count preserved)",
            "issues": result.issues,
            "actions": result.actions,
        }
        ledger.append(entry)
        logger.info(
            "  %-24s %8s -> %8s  (excluded %s)",
            result.name, f"{result.input_rows:,}", f"{result.output_rows:,}",
            f"{result.excluded_rows:,}",
        )
        if result.quarantine is not None and len(result.quarantine):
            quarantine_frames[f"quarantine_{result.name}.csv"] = result.quarantine
        return result

    try:
        orders_raw = raw_frames["olist_orders_dataset.csv"]
        items_raw = raw_frames["olist_order_items_dataset.csv"]
        payments_raw = raw_frames["olist_order_payments_dataset.csv"]
        reviews_raw = raw_frames["olist_order_reviews_dataset.csv"]

        dim_customer, dim_identity = data_cleaning.clean_customers(
            raw_frames["olist_customers_dataset.csv"]
        )
        record(dim_customer)
        record(dim_identity, note="1 row per real person (repeat buyers collapsed)")

        dim_order = record(
            data_cleaning.clean_orders(
                orders_raw,
                set(items_raw["order_id"].dropna()),
                set(payments_raw["order_id"].dropna()),
                set(reviews_raw["order_id"].dropna()),
            ),
            note="orders are never excluded; bad dates are flagged and quarantined for audit",
        )

        dim_category = record(
            data_cleaning.clean_categories(
                raw_frames["product_category_name_translation.csv"],
                set(raw_frames["olist_products_dataset.csv"]["product_category_name"].str.strip().dropna()),
            ),
            note="lookup = translation UNION categories actually used UNION 'unknown'",
        )
        dim_product = record(
            data_cleaning.clean_products(raw_frames["olist_products_dataset.csv"], dim_category.frame)
        )
        dim_seller = record(data_cleaning.clean_sellers(raw_frames["olist_sellers_dataset.csv"]))
        fact_item = record(data_cleaning.clean_order_items(items_raw))
        bridge_pay = record(data_cleaning.clean_payments(payments_raw))
        bridge_rev = record(data_cleaning.clean_reviews(reviews_raw))
        # ZIP master must cover every address, even where the source has a gap
        referenced_zips = set(dim_customer.frame["customer_zip_code_prefix"].dropna()) | set(
            dim_seller.frame["seller_zip_code_prefix"].dropna()
        )
        dim_geo = record(
            data_cleaning.clean_geolocation(
                raw_frames["olist_geolocation_dataset.csv"], referenced_zips
            )
        )
    except Exception:  # noqa: BLE001
        logger.exception("Cleaning failed")
        return 1

    cleaned: dict[str, pd.DataFrame] = {
        "dim_customer": dim_customer.frame,
        "dim_customer_identity": dim_identity.frame,
        "dim_order": dim_order.frame,
        "dim_product_category": dim_category.frame,
        "dim_product": dim_product.frame,
        "dim_seller": dim_seller.frame,
        "dim_geolocation": dim_geo.frame,
        "fact_order_item": fact_item.frame,
        "bridge_payment": bridge_pay.frame,
        "bridge_review": bridge_rev.frame,
    }

    # ---------------- Transform ------------------------------------------
    logger.info("[4/6] Transforming -> fact_order_summary")
    try:
        summary = transform.build_order_summary(
            dim_order.frame, fact_item.frame, bridge_pay.frame, bridge_rev.frame
        )
    except Exception:  # noqa: BLE001
        logger.exception("Transformation failed")
        return 1
    cleaned["fact_order_summary"] = summary
    ledger.append({
        "table": "fact_order_summary",
        "input_rows": len(dim_order.frame),
        "output_rows": len(summary),
        "excluded_rows": 0,
        "exclusion_reason": None,
        "note": "rollup: one row per order (pre-aggregates items/payouts/reviews)",
        "issues": {},
        "actions": {"prevents_join_fanout": int(len(summary))},
    })

    # ---------------- Validate -------------------------------------------
    logger.info("[5/6] Validating keys, grain and reconciliation vs source")
    report: ValidationReport = validation.run_validation(cleaned, summary, raw_frames)

    # ---------------- Write outputs --------------------------------------
    prepare_output_dirs()
    logger.info("[6/6] Writing outputs")
    for name, frame in cleaned.items():
        write_frame(config.PROCESSED_DIR, f"{name}.csv", frame)

    quarantine_name_map = {
        "quarantine_dim_geolocation.csv": "quarantine_geolocation_outliers.csv",
        "quarantine_dim_order.csv": "quarantine_invalid_dates.csv",
    }
    for name, frame in quarantine_frames.items():
        write_frame(config.QUARANTINE_DIR, quarantine_name_map.get(name, name), frame)

    # ---------------- Reports --------------------------------------------
    after_profiles = reporting.profile_many(cleaned)
    validation_rows = [
        [c.check_id, c.description, c.severity,
         "PASS" if c.passed else ("FAIL" if c.severity == ERROR else "WARN"),
         c.observed, c.expected or "-"]
        for c in report.checks
    ]

    unresolved = build_unresolved_issues(cleaned, report, ledger)

    reporting.write_before_report(before_profiles, loaded_meta, collect_invalid_values(ledger))
    reporting.write_after_report(after_profiles, ledger, validation_rows, unresolved)

    # ---------------- Source integrity + summary -------------------------
    changed = data_loader.verify_raw_unchanged(raw_hashes)
    if changed:
        logger.error("RAW SOURCE FILES CHANGED DURING THE RUN: %s", changed)
    duration = time.perf_counter() - started

    payload = build_summary(loaded, ledger, report, unresolved, changed, duration)
    reporting.write_json_summary(payload)

    # ---------------- Data-quality audit (machine-readable, reusable) ------
    # Extra artefacts on top of the human-readable reports: the per-file /
    # per-column audit (missing, duplicates, invalid values, business keys,
    # referential-integrity orphans) for raw and cleaned data. Read-only.
    try:
        quality_audit.write_audit(
            loaded, cleaned, ledger=ledger, validation=report,
            source="etl_pipeline",
        )
    except Exception:  # noqa: BLE001 - an audit failure must not mask a green run
        logger.exception("Data-quality audit report failed to write (run continues)")

    logger.info("-" * 78)
    logger.info("Run finished in %.2fs", duration)
    logger.info("Checks: %d total | %d passed | %d warnings | %d errors",
                len(report.checks),
                sum(1 for c in report.checks if c.passed),
                len(report.warnings),
                len(report.errors))
    if unresolved:
        logger.info("Unresolved issues:")
        for item in unresolved:
            logger.info("  - %s", item)

    blocking: list[validation.CheckResult] = list(report.errors)
    if strict:
        blocking += report.warnings

    if changed:
        logger.error("FAILED: raw source files were modified during the run")
        return 1
    if blocking:
        logger.error("FAILED: %d blocking check(s)%s",
                     len(blocking), "" if not strict else " (strict mode)")
        for c in blocking:
            logger.error("  %s: %s (observed=%s expected=%s)",
                         c.check_id, c.description, c.observed, c.expected or "-")
        return 1

    logger.info("SUCCESS - outputs in %s", config.PROCESSED_DIR.relative_to(config.PROJECT_ROOT))
    return 0


# --------------------------------------------------------------------------
# Report helpers
# --------------------------------------------------------------------------
def collect_invalid_values(ledger: list[dict]) -> dict[str, dict[str, int]]:
    """Flatten the measured issue counters into the BEFORE report's section 5."""
    groups: dict[str, dict[str, int]] = {}
    for entry in ledger:
        if entry["issues"]:
            groups[entry["table"]] = entry["issues"]
    return groups


def build_unresolved_issues(
    cleaned: dict[str, pd.DataFrame],
    report: ValidationReport,
    ledger: list[dict],
) -> list[str]:
    """Derive the outstanding-issues list from measured values, never constants."""
    issues: list[str] = []
    for check in report.warnings:
        issues.append(f"{check.description} -> {check.observed} (expected {check.expected})")

    by_table = {e["table"]: e for e in ledger}
    reviews = by_table.get("bridge_review", {}).get("issues", {})
    if reviews.get("duplicate_order_id_rows"):
        issues.append(
            f"{reviews['duplicate_order_id_rows']:,} source rows were a second review on the "
            f"same order and were collapsed to one (latest kept); "
            f"{reviews.get('orders_with_conflicting_duplicate_scores', 0):,} orders had "
            f"conflicting scores."
        )
    geo = by_table.get("dim_geolocation", {}).get("actions", {})
    if geo.get("quarantined_outlier_coordinates"):
        issues.append(
            f"{geo['quarantined_outlier_coordinates']:,} geolocation coordinates were outside "
            f"Brazil and were moved to data/quarantine/ (ZIP kept, centroid from valid rows)."
        )
    orders = by_table.get("dim_order", {}).get("issues", {})
    if orders.get("no_line_items"):
        issues.append(
            f"{orders['no_line_items']:,} orders have no line items: item_revenue is NULL for "
            f"them, so GMV built on fact_order_item omits them by construction."
        )
    if orders.get("status_delivered_but_no_delivery_date"):
        issues.append(
            f"{orders['status_delivered_but_no_delivery_date']} orders are marked 'delivered' "
            f"with no delivery timestamp - excluded from delivery-delay metrics (NULL kept)."
        )
    if orders.get("impossible_date_sequence"):
        issues.append(
            f"{orders['impossible_date_sequence']:,} orders have a non-monotonic date sequence "
            f"(approval / carrier / delivery out of order). They are kept and flagged with "
            f"has_impossible_date_sequence; an audit copy is in "
            f"data/quarantine/quarantine_invalid_dates.csv."
        )
    pay = by_table.get("bridge_payment", {}).get("issues", {})
    if pay.get("zero_or_negative_installments_and_value"):
        issues.append(
            f"{pay['zero_or_negative_installments_and_value']} payment rows have a "
            f"non-positive value or instalment count; kept and flagged, excluded from "
            f"payment-mix rates."
        )
    prod = by_table.get("dim_product", {}).get("issues", {})
    if prod.get("product_rows_with_untranslated_category"):
        issues.append(
            f"{prod['product_rows_with_untranslated_category']} product rows belong to a "
            f"category with no English translation (English label left NULL, not guessed)."
        )
    if prod.get("missing_category_name"):
        issues.append(
            f"{prod['missing_category_name']} products had no category and were tagged "
            f"'{config.UNKNOWN_CATEGORY}' so they survive group-by and joins."
        )
    return issues


def build_summary(
    loaded: dict[str, LoadResult],
    ledger: list[dict],
    report: ValidationReport,
    unresolved: list[str],
    changed_sources: list[str],
    duration: float,
) -> dict:
    return {
        "run": {
            "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "duration_seconds": round(duration, 2),
            "project_root": str(config.PROJECT_ROOT),
            "raw_sources_unchanged": not changed_sources,
            "changed_sources": changed_sources,
        },
        "inputs": {
            name: {"rows": r.source_rows, "columns": r.source_columns,
                   "bytes": r.file_bytes, "sha256": r.sha256}
            for name, r in loaded.items()
        },
        "row_counts": [
            {"table": e["table"], "input_rows": e["input_rows"],
             "output_rows": e["output_rows"], "excluded_rows": e["excluded_rows"],
             "reason": e["exclusion_reason"] or e["note"]}
            for e in ledger
        ],
        "validation": {
            "total_checks": len(report.checks),
            "passed": sum(1 for c in report.checks if c.passed),
            "errors": len(report.errors),
            "warnings": len(report.warnings),
            "failed_checks": [c.as_dict() for c in report.checks if not c.passed],
        },
        "unresolved_issues": unresolved,
        "outputs": sorted(config.OUTPUT_FILES),
        "quarantine": sorted(config.QUARANTINE_FILES),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.etl_pipeline",
        description="ShopScope ETL: ingest -> clean -> transform -> validate -> report.",
    )
    parser.add_argument("--strict", action="store_true",
                        help="also fail (exit 1) when there are warnings")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = parser.parse_args(argv)
    return run(strict=args.strict, verbose=args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
