"""
ShopScope - validation layer (Stage 4).

Four kinds of gate, all run *after* cleaning and *before* loading:

1. **Schema** - required columns survived the transform.
2. **Key** - primary keys are unique and non-null; foreign keys have no orphans
   (every child row resolves to a parent row).
3. **Grain** - every table has exactly the declared number of rows.
4. **Reconciliation** - measures computed on the *cleaned* data equal the same
   measures computed on the *raw* data. Expected values are derived from the
   source frames at run time; nothing about the data set is hardcoded here, so
   the same checks work on a newer Olist drop.

Severity
--------
``error``  -> the run fails (exit code 1), nothing is loaded.
``warning``-> the run completes but the issue is reported as unresolved.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

logger = logging.getLogger(__name__)

ERROR = "error"
WARNING = "warning"


@dataclass
class CheckResult:
    check_id: str
    description: str
    severity: str
    passed: bool
    observed: str
    expected: str = ""

    def as_dict(self) -> dict:
        return {
            "check_id": self.check_id,
            "description": self.description,
            "severity": self.severity,
            "passed": self.passed,
            "observed": self.observed,
            "expected": self.expected,
        }


@dataclass
class ValidationReport:
    checks: list[CheckResult] = field(default_factory=list)

    def add(self, check: CheckResult) -> CheckResult:
        self.checks.append(check)
        level = logging.INFO if check.passed else (
            logging.ERROR if check.severity == ERROR else logging.WARNING
        )
        logger.log(
            level,
            "[%s] %s :: %s (observed=%s expected=%s)",
            "PASS" if check.passed else "FAIL",
            check.check_id,
            check.description,
            check.observed,
            check.expected or "-",
        )
        return check

    @property
    def errors(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed and c.severity == ERROR]

    @property
    def warnings(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed and c.severity == WARNING]

    @property
    def passed(self) -> bool:
        return not self.errors


# --------------------------------------------------------------------------
# Individual checks
# --------------------------------------------------------------------------
def check_primary_key(
    report: ValidationReport, check_id: str, frame: pd.DataFrame, keys: list[str]
) -> None:
    missing_cols = [k for k in keys if k not in frame.columns]
    if missing_cols:
        report.add(CheckResult(check_id, f"primary key columns present ({keys})", ERROR,
                               False, f"missing columns {missing_cols}"))
        return

    null_rows = int(frame[keys].isna().any(axis=1).sum())
    distinct = len(frame.drop_duplicates(subset=keys))
    dupes = len(frame) - distinct

    report.add(CheckResult(
        f"{check_id}.pk_not_null", f"primary key {keys} has no NULL", ERROR,
        null_rows == 0, f"{null_rows} rows with NULL key",
    ))
    report.add(CheckResult(
        f"{check_id}.pk_unique", f"primary key {keys} is unique", ERROR,
        dupes == 0, f"{dupes} duplicate key rows out of {len(frame):,}",
        "0",
    ))


def check_foreign_key(
    report: ValidationReport,
    check_id: str,
    child: pd.DataFrame,
    child_key: str,
    parent: pd.DataFrame,
    parent_key: str,
) -> None:
    """Every non-null child value must exist in the parent (no orphans)."""
    if child_key not in child.columns or parent_key not in parent.columns:
        report.add(CheckResult(check_id, "foreign key columns present", ERROR, False,
                               f"{child_key} in child={child_key in child.columns}, "
                               f"{parent_key} in parent={parent_key in parent.columns}"))
        return

    child_vals = child[child_key].dropna()
    parent_vals = set(parent[parent_key].dropna())
    orphans = int((~child_vals.isin(parent_vals)).sum())
    report.add(CheckResult(
        f"{check_id}.no_orphans",
        f"FK {child_key} -> {parent_key}",
        ERROR,
        orphans == 0,
        f"{orphans:,} orphan child rows of {len(child_vals):,} ({child_key})",
        "0",
    ))


def check_row_count(
    report: ValidationReport,
    check_id: str,
    actual: int,
    expected: int,
    severity: str = ERROR,
    note: str = "",
) -> None:
    report.add(CheckResult(
        f"{check_id}.row_count",
        f"row count {check_id}{(' - ' + note) if note else ''}",
        severity,
        actual == expected,
        f"{actual:,}",
        f"{expected:,}",
    ))


def check_equals(
    report: ValidationReport,
    check_id: str,
    description: str,
    observed,
    expected,
    severity: str = ERROR,
    tolerance: float = 0.0,
) -> None:
    if isinstance(observed, float) or isinstance(expected, float):
        ok = abs(float(observed) - float(expected)) <= tolerance
    else:
        ok = observed == expected
    report.add(CheckResult(
        check_id, description, severity, ok,
        f"{observed:,}" if isinstance(observed, (int, float)) else str(observed),
        f"{expected:,}" if isinstance(expected, (int, float)) else str(expected),
    ))


def check_no_null(
    report: ValidationReport, check_id: str, frame: pd.DataFrame, columns: list[str]
) -> None:
    for col in columns:
        if col not in frame.columns:
            report.add(CheckResult(f"{check_id}.{col}", f"column {col} present", ERROR,
                                   False, "column missing"))
            continue
        n = int(frame[col].isna().sum())
        report.add(CheckResult(
            f"{check_id}.{col}.not_null", f"{col} is never NULL", ERROR, n == 0,
            f"{n:,} NULL of {len(frame):,}",
        ))


def check_value_range(
    report: ValidationReport,
    check_id: str,
    frame: pd.DataFrame,
    column: str,
    low,
    high,
    severity: str = ERROR,
) -> None:
    if column not in frame.columns:
        report.add(CheckResult(f"{check_id}.{column}", f"column {column} present", ERROR,
                               False, "column missing"))
        return
    series = frame[column].dropna()
    bad = int(((series < low) | (series > high)).sum())
    report.add(CheckResult(
        f"{check_id}.{column}.range", f"{column} within [{low}, {high}]", severity,
        bad == 0, f"{bad} out-of-range values of {len(series):,}",
        "0",
    ))


# --------------------------------------------------------------------------
# Full gate
# --------------------------------------------------------------------------
def run_validation(
    cleaned: dict[str, pd.DataFrame],
    summary: pd.DataFrame,
    raw: dict[str, pd.DataFrame],
) -> ValidationReport:
    """All gates for one run. ``raw`` supplies the expected values (no constants)."""
    report = ValidationReport()

    dim_customer = cleaned["dim_customer"]
    dim_identity = cleaned["dim_customer_identity"]
    dim_product = cleaned["dim_product"]
    dim_category = cleaned["dim_product_category"]
    dim_seller = cleaned["dim_seller"]
    dim_order = cleaned["dim_order"]
    dim_geo = cleaned["dim_geolocation"]
    fact_item = cleaned["fact_order_item"]
    bridge_pay = cleaned["bridge_payment"]
    bridge_rev = cleaned["bridge_review"]

    # ---- schema: transformed tables must not come out empty ---------------
    for name, frame in cleaned.items():
        if frame.empty:
            report.add(CheckResult(f"{name}.not_empty", f"{name} is not empty", ERROR,
                                   False, "0 rows"))

    # ---- primary keys -----------------------------------------------------
    check_primary_key(report, "dim_customer", dim_customer, ["customer_id"])
    check_primary_key(report, "dim_customer_identity", dim_identity, ["customer_unique_id"])
    check_primary_key(report, "dim_product", dim_product, ["product_id"])
    check_primary_key(report, "dim_product_category", dim_category, ["product_category_name"])
    check_primary_key(report, "dim_seller", dim_seller, ["seller_id"])
    check_primary_key(report, "dim_order", dim_order, ["order_id"])
    check_primary_key(report, "dim_geolocation", dim_geo, ["geolocation_zip_code_prefix"])
    check_primary_key(report, "fact_order_item", fact_item, ["order_id", "order_item_id"])
    check_primary_key(report, "bridge_payment", bridge_pay, ["order_id", "payment_sequential"])
    check_primary_key(report, "bridge_review", bridge_rev, ["order_id"])
    check_primary_key(report, "fact_order_summary", summary, ["order_id"])

    # ---- foreign keys -----------------------------------------------------
    check_foreign_key(report, "order.customer", dim_order, "customer_id", dim_customer, "customer_id")
    check_foreign_key(report, "item.order", fact_item, "order_id", dim_order, "order_id")
    check_foreign_key(report, "item.product", fact_item, "product_id", dim_product, "product_id")
    check_foreign_key(report, "item.seller", fact_item, "seller_id", dim_seller, "seller_id")
    check_foreign_key(report, "payment.order", bridge_pay, "order_id", dim_order, "order_id")
    check_foreign_key(report, "review.order", bridge_rev, "order_id", dim_order, "order_id")
    check_foreign_key(report, "summary.order", summary, "order_id", dim_order, "order_id")
    check_foreign_key(
        report, "product.category", dim_product, "product_category_name",
        dim_category, "product_category_name",
    )
    check_foreign_key(
        report, "customer.geolocation", dim_customer, "customer_zip_code_prefix",
        dim_geo, "geolocation_zip_code_prefix",
    )
    check_foreign_key(
        report, "seller.geolocation", dim_seller, "seller_zip_code_prefix",
        dim_geo, "geolocation_zip_code_prefix",
    )

    # ---- not-null / value range on the measures that drive KPIs -----------
    check_no_null(report, "dim_order", dim_order,
                  ["order_id", "customer_id", "order_status", "order_purchase_timestamp"])
    check_no_null(report, "fact_order_item", fact_item,
                  ["order_id", "order_item_id", "product_id", "seller_id", "price"])
    check_value_range(report, "fact_order_item", fact_item, "price", 0.0001, 1e12)
    check_value_range(report, "fact_order_item", fact_item, "freight_value", 0.0, 1e12)
    check_value_range(report, "bridge_review", bridge_rev, "review_score", 1, 5)
    # Known benign source rows (fully discounted payments) are kept and flagged,
    # so this is reported as a warning rather than failing the run.
    check_value_range(report, "bridge_payment", bridge_pay, "payment_value",
                      0.0001, 1e12, severity=WARNING)

    # ---- reconciliation: cleaned vs raw (values derived at run time) ------
    raw_orders = raw["olist_orders_dataset.csv"]
    raw_items = raw["olist_order_items_dataset.csv"]
    raw_payments = raw["olist_order_payments_dataset.csv"]
    raw_reviews = raw["olist_order_reviews_dataset.csv"]
    raw_products = raw["olist_products_dataset.csv"]
    raw_customers = raw["olist_customers_dataset.csv"]
    raw_sellers = raw["olist_sellers_dataset.csv"]
    raw_geo = raw["olist_geolocation_dataset.csv"]

    # Entity tables must not lose or gain rows.
    check_row_count(report, "dim_order", len(dim_order), len(raw_orders),
                    ERROR, note="orders are never dropped")
    check_row_count(report, "dim_customer", len(dim_customer), len(raw_customers))
    check_row_count(report, "dim_product", len(dim_product), len(raw_products))
    check_row_count(report, "dim_seller", len(dim_seller), len(raw_sellers))
    check_row_count(report, "fact_order_item", len(fact_item), len(raw_items))
    check_row_count(report, "bridge_payment", len(bridge_pay), len(raw_payments))
    check_row_count(
        report, "bridge_review", len(bridge_rev), int(raw_reviews["order_id"].nunique()),
        WARNING, note="deliberate collapse to one review per order",
    )
    check_row_count(
        report, "dim_geolocation", len(dim_geo),
        len(
            set(raw_geo["geolocation_zip_code_prefix"].dropna())
            | set(dim_customer["customer_zip_code_prefix"].dropna())
            | set(dim_seller["seller_zip_code_prefix"].dropna())
        ),
        ERROR, note="grain = source ZIPs union ZIPs referenced by an address",
    )

    # Money must survive the transform untouched (dynamic expected value).
    raw_item_revenue = float((raw_items["price"] + raw_items["freight_value"]).sum())
    clean_item_revenue = float((fact_item["price"] + fact_item["freight_value"]).sum())
    check_equals(report, "recon.item_revenue",
                 "sum(price + freight) equals source total", clean_item_revenue,
                 raw_item_revenue, ERROR, tolerance=0.01)

    raw_pay_total = float(raw_payments["payment_value"].sum())
    clean_pay_total = float(bridge_pay["payment_value"].sum())
    check_equals(report, "recon.payment_total",
                 "sum(payment_value) equals source total", clean_pay_total,
                 raw_pay_total, ERROR, tolerance=0.01)

    # The rollup must be an exact re-statement of the child tables.
    summary_item_revenue = summary["item_revenue"].sum()
    check_equals(report, "recon.summary_item_revenue",
                 "fact_order_summary item_revenue equals fact_order_item total",
                 float(summary_item_revenue), raw_item_revenue, ERROR, tolerance=0.01)
    summary_pay = summary["payment_collected"].sum()
    check_equals(report, "recon.summary_payment",
                 "fact_order_summary payment_collected equals bridge_payment total",
                 float(summary_pay), raw_pay_total, ERROR, tolerance=0.01)

    check_equals(report, "recon.summary_count",
                 "fact_order_summary has one row per order",
                 len(summary), len(raw_orders))

    # Derived counts must match independent recomputation from the raw files.
    raw_no_items = int((~raw_orders["order_id"].isin(raw_items["order_id"])).sum())
    clean_no_items = int((dim_order["has_line_items"] == False).sum())  # noqa: E712
    check_equals(report, "recon.orders_without_items",
                 "orders flagged without line items equals source count",
                 clean_no_items, raw_no_items)

    raw_no_pay = int((~raw_orders["order_id"].isin(raw_payments["order_id"])).sum())
    clean_no_pay = int((dim_order["has_payments"] == False).sum())  # noqa: E712
    check_equals(report, "recon.orders_without_payments",
                 "orders flagged without payments equals source count",
                 clean_no_pay, raw_no_pay)

    # Category coverage: every product must resolve to a category row.
    check_equals(report, "recon.unknown_categories",
                 "products with no category were tagged 'unknown', not dropped",
                 int((dim_product["product_category_name"] == "unknown").sum()),
                 int(raw_products["product_category_name"].isna().sum()))

    # Category coverage: every *real* category has an English label. 'unknown' is
    # an intentional sentinel for the uncategorised products, so it is excluded
    # here - those rows are covered by check recon.unknown_categories instead.
    untranslated = int(
        (
            dim_product["product_category_name_english"].isna()
            & dim_product["product_category_name"].ne("unknown")
        ).sum()
    )
    report.add(CheckResult(
        "quality.product_untranslated_categories",
        "every real product category has an English translation", WARNING,
        untranslated == 0,
        f"{untranslated} product row(s) whose category has no English label",
        "0 (categories missing from the translation file)",
    ))

    # Geolocation usable for every ZIP that has source coordinates.
    unusable = int((dim_geo["is_valid_coordinate"] == False).sum())  # noqa: E712
    report.add(CheckResult(
        "quality.geolocation_usable",
        "every ZIP has a usable centroid", WARNING, unusable == 0,
        f"{unusable} ZIP(s) with NULL centroid (all coordinates quarantined or absent)",
        "0",
    ))

    # ZIP master rows that exist only because an address references them.
    address_only = int((dim_geo["source_row_count"] == 0).sum())
    report.add(CheckResult(
        "quality.geolocation_address_coverage",
        "every address ZIP has geolocation source data", WARNING, address_only == 0,
        f"{address_only} ZIP prefix(es) referenced by customers/sellers but absent from "
        f"the geolocation source (placeholder row, NULL centroid)",
        "0 (gap present in the source data, not created by this pipeline)",
    ))

    return report
