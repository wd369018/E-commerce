"""
ShopScope - configuration.

Every path is derived from the project root (the parent of ``src/``), so the
pipeline works on any machine regardless of where the repository is cloned.
No absolute, user-specific path is stored here.

Raw CSV handling
----------------
The nine source files are searched in ``RAW_SEARCH_DIRS`` in order. They are
opened read-only; the loader records a SHA-256 digest of each file at the start
of a run and re-checks it at the end to *prove* they were not modified.
"""

from __future__ import annotations

from pathlib import Path

# --------------------------------------------------------------------------
# Paths (project-root relative)
# --------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Where the raw CSVs may live. The current workspace keeps them at the project
# root; the pipeline also accepts the conventional data/raw location.
RAW_SEARCH_DIRS: tuple[Path, ...] = (
    PROJECT_ROOT / "data" / "raw",
    PROJECT_ROOT,
)

DATA_DIR = PROJECT_ROOT / "data"
PROCESSED_DIR = DATA_DIR / "processed"
QUARANTINE_DIR = DATA_DIR / "quarantine"
REPORTS_DIR = PROJECT_ROOT / "reports"

LOG_FILE = REPORTS_DIR / "etl.log"
BEFORE_REPORT_MD = REPORTS_DIR / "data_quality_before_cleaning.md"
AFTER_REPORT_MD = REPORTS_DIR / "data_quality_after_cleaning.md"
RUN_SUMMARY_JSON = REPORTS_DIR / "etl_run_summary.json"

# Reusable machine-readable data-quality audit (written by src.quality_audit,
# both as a stage of the ETL run and standalone via `python -m src.quality_audit`).
AUDIT_CSV = REPORTS_DIR / "data_quality_audit.csv"
AUDIT_JSON = REPORTS_DIR / "data_quality_audit.json"
AUDIT_SUMMARY_MD = REPORTS_DIR / "data_quality_summary.md"

# Naive "before cleaning" baseline vs cleaned-KPI comparison (Phase 5 report).
METRICS_BEFORE_AFTER_MD = REPORTS_DIR / "metrics_before_after.md"

# --------------------------------------------------------------------------
# SQL layer
# --------------------------------------------------------------------------
SQL_DIR = PROJECT_ROOT / "sql"
SCHEMA_SQL = SQL_DIR / "schema.sql"
ANALYTICAL_SQL = SQL_DIR / "analytical_queries.sql"
DB_PATH = DATA_DIR / "shopscope.db"

# --------------------------------------------------------------------------
# Analysis layer (metrics / insights / visualizations)
# --------------------------------------------------------------------------
FIGURES_DIR = REPORTS_DIR / "figures"
METRICS_REPORT_MD = REPORTS_DIR / "business_metrics.md"
FINDINGS_REPORT_MD = REPORTS_DIR / "eda_findings.md"
ANALYSIS_SUMMARY_JSON = REPORTS_DIR / "analysis_summary.json"

# Full-volume trading window. The marketplace ramps up in autumn 2016 (four
# months, 329 orders) and the export is cut on 2018-10-17, leaving 2018-09 and
# 2018-10 with 20 orders in total. Trend metrics use only complete, mature
# months; the excluded months are always reported, never silently dropped.
ANALYSIS_WINDOW_START = "2017-01"
ANALYSIS_WINDOW_END = "2018-08"

# --------------------------------------------------------------------------
# Raw file contracts: filename -> required columns (verified on every run)
# --------------------------------------------------------------------------
REQUIRED_COLUMNS: dict[str, list[str]] = {
    "olist_orders_dataset.csv": [
        "order_id",
        "customer_id",
        "order_status",
        "order_purchase_timestamp",
        "order_approved_at",
        "order_delivered_carrier_date",
        "order_delivered_customer_date",
        "order_estimated_delivery_date",
    ],
    "olist_customers_dataset.csv": [
        "customer_id",
        "customer_unique_id",
        "customer_zip_code_prefix",
        "customer_city",
        "customer_state",
    ],
    "olist_order_items_dataset.csv": [
        "order_id",
        "order_item_id",
        "product_id",
        "seller_id",
        "shipping_limit_date",
        "price",
        "freight_value",
    ],
    "olist_order_payments_dataset.csv": [
        "order_id",
        "payment_sequential",
        "payment_type",
        "payment_installments",
        "payment_value",
    ],
    "olist_order_reviews_dataset.csv": [
        "review_id",
        "order_id",
        "review_score",
        "review_comment_title",
        "review_comment_message",
        "review_creation_date",
        "review_answer_timestamp",
    ],
    "olist_products_dataset.csv": [
        "product_id",
        "product_category_name",
        "product_name_lenght",
        "product_description_lenght",
        "product_photos_qty",
        "product_weight_g",
        "product_length_cm",
        "product_height_cm",
        "product_width_cm",
    ],
    "olist_sellers_dataset.csv": [
        "seller_id",
        "seller_zip_code_prefix",
        "seller_city",
        "seller_state",
    ],
    "olist_geolocation_dataset.csv": [
        "geolocation_zip_code_prefix",
        "geolocation_lat",
        "geolocation_lng",
        "geolocation_city",
        "geolocation_state",
    ],
    "product_category_name_translation.csv": [
        "product_category_name",
        "product_category_name_english",
    ],
}

# --------------------------------------------------------------------------
# Type conversion contracts
# --------------------------------------------------------------------------
# Columns parsed with pd.to_datetime(..., errors="coerce"). Any value that
# cannot be parsed becomes NaT and is counted as an *invalid date* in the
# quality report - it is never silently dropped.
DATE_COLUMNS: dict[str, list[str]] = {
    "olist_orders_dataset.csv": [
        "order_purchase_timestamp",
        "order_approved_at",
        "order_delivered_carrier_date",
        "order_delivered_customer_date",
        "order_estimated_delivery_date",
    ],
    "olist_order_items_dataset.csv": ["shipping_limit_date"],
    "olist_order_reviews_dataset.csv": [
        "review_creation_date",
        "review_answer_timestamp",
    ],
}

# Columns parsed with pd.to_numeric(..., errors="coerce").
# "Int64" = nullable integer, "Float64" = nullable float (pandas nullable
# extension dtypes - NA stays NA instead of becoming 0.0 or a float NaN).
NUMERIC_COLUMNS: dict[str, dict[str, str]] = {
    "olist_customers_dataset.csv": {"customer_zip_code_prefix": "Int64"},
    "olist_order_items_dataset.csv": {
        "order_item_id": "Int64",
        "price": "Float64",
        "freight_value": "Float64",
    },
    "olist_order_payments_dataset.csv": {
        "payment_sequential": "Int64",
        "payment_installments": "Int64",
        "payment_value": "Float64",
    },
    "olist_order_reviews_dataset.csv": {"review_score": "Int64"},
    "olist_products_dataset.csv": {
        "product_name_lenght": "Int64",
        "product_description_lenght": "Int64",
        "product_photos_qty": "Int64",
        "product_weight_g": "Float64",
        "product_length_cm": "Float64",
        "product_height_cm": "Float64",
        "product_width_cm": "Float64",
    },
    "olist_sellers_dataset.csv": {"seller_zip_code_prefix": "Int64"},
    "olist_geolocation_dataset.csv": {
        "geolocation_zip_code_prefix": "Int64",
        "geolocation_lat": "Float64",
        "geolocation_lng": "Float64",
    },
}

# --------------------------------------------------------------------------
# Cleaning constants
# --------------------------------------------------------------------------
# Latitude / longitude bounding box used to quarantine impossible coordinates.
# Derived from the coastline extremes of Brazil (documented in docs/project_plan.md
# as rule C4); rows outside it are moved to quarantine, never deleted silently.
BRAZIL_LAT_MIN, BRAZIL_LAT_MAX = -33.75, 5.27
BRAZIL_LNG_MIN, BRAZIL_LNG_MAX = -73.99, -34.79

# Explicit sentinel for products with no category (rule C8). Using a literal
# keeps "unclassified" a first-class, queryable business state instead of
# letting NULL disappear in INNER JOINs or group-by.
UNKNOWN_CATEGORY = "unknown"

# Order statuses counted as revenue-bearing (rule C5).
REVENUE_STATUSES = ("delivered", "shipped")

# Files written to PROCESSED_DIR - wiped before every run so a re-run can
# never append to, or duplicate, a previous run's output.
OUTPUT_FILES: tuple[str, ...] = (
    "dim_customer.csv",
    "dim_customer_identity.csv",
    "dim_product.csv",
    "dim_product_category.csv",
    "dim_seller.csv",
    "dim_order.csv",
    "dim_geolocation.csv",
    "fact_order_item.csv",
    "bridge_payment.csv",
    "bridge_review.csv",
    "fact_order_summary.csv",
)

QUARANTINE_FILES: tuple[str, ...] = (
    "quarantine_geolocation_outliers.csv",
    "quarantine_invalid_dates.csv",
)
