"""Cached, read-only data access for the ShopScope dashboard.

Everything the pages need from SQLite lives here. The rules this module
enforces:

* the database is opened **read-only** (``mode=ro``), so the app can never
  mutate ``data/shopscope.db`` or any raw/cleaned CSV;
* every query is wrapped in ``st.cache_data`` keyed on the exact SQL and
  parameters, so moving between pages and re-running filters does not re-hit
  the database;
* no page ever touches the 1,000,163-row raw geolocation CSV - only the
  size-bounded ``dim_geolocation`` table (and here, not even that);
* aggregation stays at the documented grain (orders for rates, (category,
  order) for category reviews) so joins never fan out.

The functions are deliberately small and composable rather than one giant
filter object, which keeps caching effective and makes each result easy to
test.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

from src import config, insights, metrics, queries

# --------------------------------------------------------------------------
# Connection
# --------------------------------------------------------------------------
# Canonical FROM/JOIN chain for any query that needs order + customer +
# (optionally) item + product + category. The item/product joins are LEFT so
# order-level facts survive even when the category filter is not used.
_JOINS = """
FROM dim_order o
JOIN dim_customer c ON c.customer_id = o.customer_id
LEFT JOIN fact_order_item i ON i.order_id = o.order_id
LEFT JOIN dim_product p ON p.product_id = i.product_id
LEFT JOIN dim_product_category cpg ON cpg.product_category_name = p.product_category_name
"""


def db_path() -> Path:
    return Path(config.DB_PATH)


def database_exists() -> bool:
    return db_path().exists()


def _connect() -> sqlite3.Connection:
    path = db_path()
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found at {path}. Build it first with 'python -m src.database'."
        )
    uri = path.resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def db_info() -> dict:
    """Small fingerprint of the database used on the data-quality page."""
    path = db_path()
    if not path.exists():
        return {"exists": False, "path": str(path)}
    stat = path.stat()
    return {
        "exists": True,
        "path": str(path),
        "size_bytes": stat.st_size,
        "modified": pd.Timestamp(stat.st_mtime, unit="s").strftime("%Y-%m-%d %H:%M:%S"),
    }


# --------------------------------------------------------------------------
# Generic runners (cached)
# --------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def run_sql(sql: str, params: tuple = ()) -> pd.DataFrame:
    conn = _connect()
    try:
        return pd.read_sql_query(sql, conn, params=tuple(params))
    finally:
        conn.close()


@st.cache_data(show_spinner=False)
def run_scalar(sql: str, params: tuple = ()):
    conn = _connect()
    try:
        row = conn.execute(sql, tuple(params)).fetchone()
        return None if row is None else row[0]
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Query library passthrough (reuse the SQL layer, don't duplicate it)
# --------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def query_library() -> dict[str, dict]:
    return {
        name: {"description": spec.description, "sql": spec.sql}
        for name, spec in queries.catalogue().items()
    }


def named_query(name: str) -> pd.DataFrame:
    spec = query_library()[name]
    return run_sql(spec["sql"])


# --------------------------------------------------------------------------
# Metrics / insights (heavy - cached once for the whole session)
# --------------------------------------------------------------------------
@st.cache_data(show_spinner="Computing 14 KPIs...")
def kpis() -> list[metrics.KPI]:
    conn = _connect()
    try:
        return metrics.compute_kpis(conn)
    finally:
        conn.close()


def kpi_map() -> dict[str, metrics.KPI]:
    return {k.key: k for k in kpis()}


def kpis_frame() -> pd.DataFrame:
    return metrics.kpis_to_frame(kpis())


@st.cache_data(show_spinner="Running the EDA layer...")
def analysis() -> insights.AnalysisReport:
    conn = _connect()
    try:
        return insights.analyse(conn)
    finally:
        conn.close()


@st.cache_data(show_spinner=False)
def period_context() -> dict:
    conn = _connect()
    try:
        _, ctx = insights.load_periods(conn)
        return ctx
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Filter option sources
# --------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def date_bounds() -> dict:
    row = run_sql(
        "SELECT MIN(purchase_date) AS min_date, MAX(purchase_date) AS max_date FROM dim_order"
    ).iloc[0]
    return {
        "min_date": pd.to_datetime(row["min_date"]).date(),
        "max_date": pd.to_datetime(row["max_date"]).date(),
        "window_start": config.ANALYSIS_WINDOW_START,
        "window_end": config.ANALYSIS_WINDOW_END,
    }


@st.cache_data(show_spinner=False)
def state_options() -> list[str]:
    df = run_sql(
        "SELECT customer_state FROM vw_state_customer_order_distribution "
        "ORDER BY orders DESC"
    )
    return df["customer_state"].tolist()


@st.cache_data(show_spinner=False)
def category_options() -> list[str]:
    df = run_sql(
        "SELECT COALESCE(product_category_name_english, product_category_name) AS label "
        "FROM vw_category_performance ORDER BY merchandise_sales DESC"
    )
    return df["label"].tolist()


@st.cache_data(show_spinner=False)
def status_options() -> list[str]:
    df = run_sql("SELECT DISTINCT order_status FROM dim_order ORDER BY 1")
    return df["order_status"].tolist()


# --------------------------------------------------------------------------
# Filter builder
# --------------------------------------------------------------------------
def _in(column: str, values) -> tuple[str, list]:
    values = list(values)
    if not values:
        return "", []
    placeholders = ",".join("?" * len(values))
    return f" AND {column} IN ({placeholders})", values


def _where(start, end, states=(), categories=(), statuses=()) -> tuple[str, tuple]:
    clauses = ["o.purchase_date >= ?", "o.purchase_date <= ?"]
    params: list = [str(start), str(end)]
    for column, values in (
        ("c.customer_state", states),
        (
            "COALESCE(cpg.product_category_name_english, p.product_category_name)",
            categories,
        ),
        ("o.order_status", statuses),
    ):
        clause, p = _in(column, values)
        if clause:
            clauses.append(clause[5:])  # strip the leading " AND "
            params += p
    return " AND ".join(clauses), tuple(params)


# --------------------------------------------------------------------------
# Time series (order + item grain) -- used by Executive and Sales pages
# --------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def monthly_series(start, end, states=(), categories=(), statuses=()) -> pd.DataFrame:
    where, params = _where(start, end, states, categories, statuses)
    df = run_sql(
        f"""
        SELECT substr(o.order_purchase_timestamp, 1, 7) AS sales_month,
               COUNT(DISTINCT o.order_id)               AS orders,
               COUNT(i.order_id)                        AS item_lines,
               ROUND(COALESCE(SUM(i.price), 0), 2)      AS merchandise_sales,
               ROUND(COALESCE(SUM(i.freight_value), 0), 2) AS freight_charges,
               ROUND(AVG(i.price), 2)                   AS avg_unit_price
        {_JOINS}
        WHERE {where}
        GROUP BY 1 ORDER BY 1
        """,
        params,
    )
    if df.empty:
        return df
    df["is_complete"] = df["sales_month"].between(
        config.ANALYSIS_WINDOW_START, config.ANALYSIS_WINDOW_END
    )
    df["avg_order_value"] = (df["merchandise_sales"] / df["orders"]).round(2)
    return df


@st.cache_data(show_spinner=False)
def quarterly_series(start, end, states=(), categories=(), statuses=()) -> pd.DataFrame:
    monthly = monthly_series(start, end, states, categories, statuses)
    if monthly.empty:
        return monthly
    q = monthly.copy()
    q["quarter"] = q["sales_month"].map(insights._quarter_of)
    grouped = (
        q.groupby("quarter", as_index=False)
        .agg(
            orders=("orders", "sum"),
            merchandise_sales=("merchandise_sales", "sum"),
            freight_charges=("freight_charges", "sum"),
            months=("sales_month", "count"),
        )
        .sort_values("quarter")
        .reset_index(drop=True)
    )
    # A quarter is complete only if all its months are inside the window.
    grouped["is_complete"] = grouped["quarter"].map(
        lambda qq: all(
            config.ANALYSIS_WINDOW_START <= m <= config.ANALYSIS_WINDOW_END
            for m in monthly["sales_month"]
            if insights._quarter_of(m) == qq
        )
    )
    grouped["avg_order_value"] = (
        grouped["merchandise_sales"] / grouped["orders"]
    ).round(2)
    return grouped


@st.cache_data(show_spinner=False)
def status_breakdown(start, end, states=(), statuses=()) -> pd.DataFrame:
    where, params = _where(start, end, states, (), statuses)
    return run_sql(
        f"""
        SELECT o.order_status,
               COUNT(DISTINCT o.order_id)                     AS orders,
               ROUND(COALESCE(SUM(i.price), 0), 2)            AS merchandise_sales,
               ROUND(COALESCE(SUM(i.freight_value), 0), 2)    AS freight_charges
        {_JOINS}
        WHERE {where}
        GROUP BY o.order_status
        ORDER BY orders DESC
        """,
        params,
    )


# --------------------------------------------------------------------------
# Category grain -- used by Sales, Satisfaction, Payments pages
# --------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def category_series(start, end, states=(), statuses=()) -> pd.DataFrame:
    where, params = _where(start, end, states, (), statuses)
    return run_sql(
        f"""
        SELECT COALESCE(cpg.product_category_name_english, p.product_category_name) AS category,
               COUNT(DISTINCT o.order_id)               AS orders,
               COUNT(i.order_id)                        AS units,
               ROUND(COALESCE(SUM(i.price), 0), 2)      AS merchandise_sales,
               ROUND(COALESCE(SUM(i.freight_value), 0), 2) AS freight_charges,
               ROUND(AVG(i.price), 2)                   AS avg_unit_price
        {_JOINS}
        WHERE {where} AND i.order_id IS NOT NULL
        GROUP BY 1
        ORDER BY merchandise_sales DESC
        """,
        params,
    )


@st.cache_data(show_spinner=False)
def category_satisfaction(start, end, states=(), statuses=()) -> pd.DataFrame:
    """Category review + late rate at (category, order) grain (no unit fan-out)."""
    where, params = _where(start, end, states, (), statuses)
    return run_sql(
        f"""
        WITH cat_order AS (
            SELECT COALESCE(cpg.product_category_name_english, p.product_category_name) AS category,
                   i.order_id
            {_JOINS}
            WHERE {where} AND i.order_id IS NOT NULL
            GROUP BY category, i.order_id
        )
        SELECT co.category,
               COUNT(*)                                          AS orders,
               ROUND(AVG(r.review_score), 3)                     AS avg_review_score,
               COUNT(r.review_score)                             AS reviewed_orders,
               ROUND(100.0 * SUM(CASE WHEN o.is_late_vs_estimate = 1 THEN 1 ELSE 0 END)
                     / NULLIF(SUM(CASE WHEN o.is_late_vs_estimate IS NOT NULL THEN 1 ELSE 0 END), 0), 2)
                                                                 AS late_pct,
               SUM(CASE WHEN o.is_late_vs_estimate IS NOT NULL THEN 1 ELSE 0 END) AS late_evaluable
        FROM cat_order co
        JOIN dim_order o ON o.order_id = co.order_id
        LEFT JOIN bridge_review r ON r.order_id = co.order_id
        GROUP BY co.category
        ORDER BY orders DESC
        """,
        params,
    )


# --------------------------------------------------------------------------
# Price / freight distributions
# --------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def price_freight_rows(start, end, states=(), categories=(), statuses=()) -> pd.DataFrame:
    where, params = _where(start, end, states, categories, statuses)
    return run_sql(
        f"""
        SELECT i.price, i.freight_value
        {_JOINS}
        WHERE {where} AND i.order_id IS NOT NULL
        """,
        params,
    )


# --------------------------------------------------------------------------
# Geography
# --------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def state_series(start, end, categories=(), statuses=()) -> pd.DataFrame:
    where, params = _where(start, end, (), categories, statuses)
    return run_sql(
        f"""
        SELECT c.customer_state,
               COUNT(DISTINCT c.customer_unique_id)       AS customers,
               COUNT(DISTINCT o.order_id)                 AS orders,
               ROUND(COALESCE(SUM(i.price), 0), 2)        AS merchandise_sales,
               ROUND(COALESCE(SUM(i.freight_value), 0), 2) AS freight_charges
        {_JOINS}
        WHERE {where}
        GROUP BY c.customer_state
        ORDER BY orders DESC
        """,
        params,
    )


# --------------------------------------------------------------------------
# Delivery / operations
# --------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def delivery_orders(start, end, states=(), statuses=()) -> pd.DataFrame:
    where, params = _where(start, end, states, (), statuses)
    return run_sql(
        f"""
        SELECT o.order_id,
               o.order_status,
               c.customer_state,
               substr(o.order_purchase_timestamp, 1, 7) AS sales_month,
               o.delivery_delay_days,
               CASE WHEN o.order_delivered_customer_date IS NOT NULL
                         AND o.order_estimated_delivery_date IS NOT NULL
                    THEN julianday(o.order_delivered_customer_date)
                         - julianday(o.order_estimated_delivery_date) END AS days_vs_estimate,
               CASE WHEN o.order_delivered_customer_date IS NOT NULL
                         AND o.order_estimated_delivery_date IS NOT NULL
                    THEN julianday(o.order_estimated_delivery_date)
                         - julianday(o.order_purchase_timestamp) END AS promised_days,
               o.is_late_vs_estimate
        {_JOINS}
        WHERE {where}
        GROUP BY o.order_id
        """,
        params,
    )


_ORDER_JOINS = """
FROM dim_order o
JOIN dim_customer c ON c.customer_id = o.customer_id
"""


@st.cache_data(show_spinner=False)
def delivery_monthly(start, end, states=(), statuses=()) -> pd.DataFrame:
    # Order grain only - no item join, so rates are per order.
    where, params = _where(start, end, states, (), statuses)
    return run_sql(
        f"""
        SELECT substr(o.order_purchase_timestamp, 1, 7) AS sales_month,
               COUNT(*)                                 AS orders,
               ROUND(AVG(o.delivery_delay_days), 2)     AS avg_days_to_deliver,
               ROUND(AVG(julianday(o.order_delivered_customer_date)
                         - julianday(o.order_estimated_delivery_date)), 2) AS avg_days_vs_estimate,
               SUM(CASE WHEN o.is_late_vs_estimate = 0 THEN 1 ELSE 0 END) AS on_time_orders,
               SUM(CASE WHEN o.is_late_vs_estimate = 1 THEN 1 ELSE 0 END) AS late_orders
        {_ORDER_JOINS}
        WHERE {where} AND o.order_delivered_customer_date IS NOT NULL
        GROUP BY 1 ORDER BY 1
        """,
        params,
    )


@st.cache_data(show_spinner=False)
def delivery_by_state(start, end, statuses=()) -> pd.DataFrame:
    where, params = _where(start, end, (), (), statuses)
    return run_sql(
        f"""
        SELECT c.customer_state,
               COUNT(*)                                                  AS delivered_orders,
               ROUND(AVG(o.delivery_delay_days), 2)                      AS avg_days_to_deliver,
               ROUND(100.0 * SUM(CASE WHEN o.is_late_vs_estimate = 1 THEN 1 ELSE 0 END)
                     / NULLIF(SUM(CASE WHEN o.is_late_vs_estimate IS NOT NULL THEN 1 ELSE 0 END), 0), 2)
                                                                        AS late_pct
        {_ORDER_JOINS}
        WHERE {where} AND o.order_delivered_customer_date IS NOT NULL
        GROUP BY c.customer_state
        ORDER BY delivered_orders DESC
        """,
        params,
    )


@st.cache_data(show_spinner=False)
def review_comments(start, end, states=()) -> pd.DataFrame:
    """Review text for theme extraction (message + score only)."""
    where, params = _where(start, end, states, (), ())
    return run_sql(
        f"""
        SELECT r.review_score, r.review_comment_message
        {_ORDER_JOINS}
        JOIN bridge_review r ON r.order_id = o.order_id
        WHERE {where} AND r.review_comment_message IS NOT NULL
              AND TRIM(r.review_comment_message) <> ''
        """,
        params,
    )


# --------------------------------------------------------------------------
# Reviews / satisfaction
# --------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def reviews_context(start, end, states=(), statuses=()) -> pd.DataFrame:
    where, params = _where(start, end, states, (), statuses)
    return run_sql(
        f"""
        SELECT r.order_id,
               r.review_score,
               o.order_status,
               c.customer_state,
               o.is_late_vs_estimate,
               o.delivery_delay_days,
               substr(o.order_purchase_timestamp, 1, 7) AS sales_month,
               o.purchase_date
        {_JOINS}
        JOIN bridge_review r ON r.order_id = o.order_id
        WHERE {where}
        GROUP BY r.order_id
        """,
        params,
    )


# --------------------------------------------------------------------------
# Payments
# --------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def payment_methods(start, end, states=(), statuses=()) -> pd.DataFrame:
    where, params = _where(start, end, states, (), statuses)
    return run_sql(
        f"""
        SELECT bp.payment_type,
               COUNT(*)                       AS payments,
               COUNT(DISTINCT bp.order_id)    AS orders,
               ROUND(SUM(bp.payment_value), 2) AS payment_value,
               ROUND(AVG(bp.payment_value), 2) AS avg_payment_value
        FROM bridge_payment bp
        JOIN dim_order o ON o.order_id = bp.order_id
        JOIN dim_customer c ON c.customer_id = o.customer_id
        WHERE {where}
        GROUP BY bp.payment_type
        ORDER BY payment_value DESC
        """,
        params,
    )


@st.cache_data(show_spinner=False)
def installment_buckets(start, end, states=(), statuses=()) -> pd.DataFrame:
    where, params = _where(start, end, states, (), statuses)
    return run_sql(
        f"""
        SELECT CASE
                 WHEN bp.payment_installments IS NULL  THEN 'unknown'
                 WHEN bp.payment_installments <= 1     THEN '1 (full payment)'
                 WHEN bp.payment_installments <= 3     THEN '2-3'
                 WHEN bp.payment_installments <= 6     THEN '4-6'
                 WHEN bp.payment_installments <= 12    THEN '7-12'
                 ELSE '13+'
               END AS installment_bucket,
               COUNT(*)                        AS payments,
               ROUND(AVG(bp.payment_value), 2) AS avg_payment_value,
               ROUND(SUM(bp.payment_value), 2) AS total_payment_value
        FROM bridge_payment bp
        JOIN dim_order o ON o.order_id = bp.order_id
        JOIN dim_customer c ON c.customer_id = o.customer_id
        WHERE {where}
        GROUP BY 1
        ORDER BY total_payment_value DESC
        """,
        params,
    )


@st.cache_data(show_spinner=False)
def payment_values(start, end, states=(), statuses=()) -> pd.DataFrame:
    where, params = _where(start, end, states, (), statuses)
    return run_sql(
        f"""
        SELECT bp.payment_value, bp.payment_type, bp.payment_installments
        FROM bridge_payment bp
        JOIN dim_order o ON o.order_id = bp.order_id
        JOIN dim_customer c ON c.customer_id = o.customer_id
        WHERE {where}
        """,
        params,
    )


# --------------------------------------------------------------------------
# Customers
# --------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def customer_summary(start, end, states=()) -> dict:
    where, params = _where(start, end, states, (), ())
    row = run_sql(
        f"""
        SELECT COUNT(DISTINCT c.customer_unique_id) AS customers,
               COUNT(DISTINCT o.order_id)           AS orders
        {_ORDER_JOINS}
        WHERE {where}
        """,
        params,
    ).iloc[0]
    customers = int(row["customers"])
    orders = int(row["orders"])
    return {
        "customers": customers,
        "orders": orders,
        "orders_per_customer": round(orders / customers, 3) if customers else 0.0,
    }


@st.cache_data(show_spinner=False)
def new_vs_repeat(start, end, states=()) -> pd.DataFrame:
    where, params = _where(start, end, states, (), ())
    return run_sql(
        f"""
        WITH firsts AS (
            SELECT c.customer_unique_id, MIN(o.purchase_date) AS first_date
            FROM dim_order o
            JOIN dim_customer c ON c.customer_id = o.customer_id
            GROUP BY c.customer_unique_id
        )
        SELECT substr(o.order_purchase_timestamp, 1, 7) AS sales_month,
               COUNT(DISTINCT c.customer_unique_id)     AS active_customers,
               COUNT(DISTINCT CASE
                   WHEN substr(f.first_date, 1, 7) = substr(o.order_purchase_timestamp, 1, 7)
                   THEN c.customer_unique_id END)       AS new_customers
        FROM dim_order o
        JOIN dim_customer c ON c.customer_id = o.customer_id
        JOIN firsts f ON f.customer_unique_id = c.customer_unique_id
        WHERE {where}
        GROUP BY 1 ORDER BY 1
        """,
        params,
    )


@st.cache_data(show_spinner=False)
def purchase_frequency() -> pd.DataFrame:
    return run_sql(
        """
        SELECT CASE
                 WHEN placement_count = 1 THEN '1 order'
                 WHEN placement_count = 2 THEN '2 orders'
                 WHEN placement_count = 3 THEN '3 orders'
                 ELSE '4+ orders'
               END AS segment,
               COUNT(*) AS customers,
               SUM(placement_count) AS orders
        FROM dim_customer_identity
        GROUP BY 1
        ORDER BY MIN(placement_count)
        """
    )


@st.cache_data(show_spinner=False)
def cohort_retention() -> pd.DataFrame:
    """Raw (cohort, active_month, customers) counts; pivoted in the page."""
    return run_sql(
        """
        WITH firsts AS (
            SELECT c.customer_unique_id,
                   MIN(substr(o.order_purchase_timestamp, 1, 7)) AS cohort
            FROM dim_order o
            JOIN dim_customer c ON c.customer_id = o.customer_id
            GROUP BY c.customer_unique_id
        ),
        acts AS (
            SELECT c.customer_unique_id,
                   substr(o.order_purchase_timestamp, 1, 7) AS active_month
            FROM dim_order o
            JOIN dim_customer c ON c.customer_id = o.customer_id
            GROUP BY c.customer_unique_id, active_month
        )
        SELECT f.cohort, a.active_month,
               COUNT(DISTINCT a.customer_unique_id) AS customers
        FROM acts a
        JOIN firsts f ON f.customer_unique_id = a.customer_unique_id
        GROUP BY f.cohort, a.active_month
        ORDER BY f.cohort, a.active_month
        """
    )


# --------------------------------------------------------------------------
# Sellers / products
# --------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def seller_performance() -> pd.DataFrame:
    # Full population (3,095 sellers); the query-library variant is capped at 50.
    return run_sql("SELECT * FROM vw_seller_performance")


@st.cache_data(show_spinner=False)
def weight_freight(start, end, categories=(), states=()) -> pd.DataFrame:
    where, params = _where(start, end, states, categories, ())
    return run_sql(
        f"""
        WITH sample AS (
            SELECT CASE
                     WHEN p.product_weight_g IS NULL OR p.product_weight_g <= 0 THEN 'unknown/invalid'
                     WHEN p.product_weight_g <= 200  THEN '1. 0-200 g'
                     WHEN p.product_weight_g <= 500  THEN '2. 201-500 g'
                     WHEN p.product_weight_g <= 1000 THEN '3. 501-1000 g'
                     WHEN p.product_weight_g <= 2000 THEN '4. 1001-2000 g'
                     WHEN p.product_weight_g <= 5000 THEN '5. 2001-5000 g'
                     ELSE '6. 5001+ g'
                   END AS weight_bucket,
                   i.product_id, i.price, i.freight_value, p.product_weight_g
            {_JOINS}
            WHERE {where} AND i.order_id IS NOT NULL AND i.freight_value >= 0
        )
        SELECT weight_bucket,
               COUNT(*)                        AS units,
               ROUND(AVG(price), 2)            AS avg_unit_price,
               ROUND(AVG(freight_value), 2)    AS avg_freight,
               ROUND(AVG(CASE WHEN product_weight_g > 0
                              THEN freight_value / (product_weight_g / 1000.0) END), 4)
                                               AS avg_freight_per_kg
        FROM sample
        GROUP BY weight_bucket
        ORDER BY weight_bucket
        """,
        params,
    )


# --------------------------------------------------------------------------
# Data quality
# --------------------------------------------------------------------------
_NULL_CHECKS: list[tuple[str, str]] = [
    ("dim_order", "order_approved_at"),
    ("dim_order", "order_delivered_carrier_date"),
    ("dim_order", "order_delivered_customer_date"),
    ("dim_order", "order_estimated_delivery_date"),
    ("dim_product", "product_category_name"),
    ("dim_product", "product_weight_g"),
    ("dim_product", "product_photos_qty"),
    ("bridge_review", "review_comment_message"),
    ("bridge_review", "review_comment_title"),
    ("bridge_payment", "payment_type"),
    ("bridge_payment", "payment_installments"),
    ("dim_customer", "customer_state"),
]


@st.cache_data(show_spinner=False)
def null_summary() -> pd.DataFrame:
    rows = []
    for table, column in _NULL_CHECKS:
        total = run_scalar(f"SELECT COUNT(*) FROM {table}") or 0
        nulls = run_scalar(
            f"SELECT SUM(CASE WHEN {column} IS NULL THEN 1 ELSE 0 END) FROM {table}"
        ) or 0
        rows.append(
            {
                "table": table,
                "column": column,
                "rows": int(total),
                "nulls": int(nulls),
                "null_pct": round(100.0 * nulls / total, 2) if total else 0.0,
            }
        )
    return pd.DataFrame(rows)


@st.cache_data(show_spinner=False)
def duplicate_summary() -> pd.DataFrame:
    checks = [
        (
            "bridge_review: orders with >1 source review (collapsed to latest)",
            "SELECT COUNT(*) FROM (SELECT order_id FROM bridge_review "
            "GROUP BY order_id HAVING COUNT(*) > 1)",
        ),
        (
            "dim_customer: customer_unique_id linked to >1 placement id",
            "SELECT COUNT(*) FROM (SELECT customer_unique_id FROM dim_customer "
            "GROUP BY customer_unique_id HAVING COUNT(DISTINCT customer_id) > 1)",
        ),
        (
            "dim_customer: customer_unique_id across >1 state",
            "SELECT COUNT(*) FROM (SELECT customer_unique_id FROM dim_customer "
            "GROUP BY customer_unique_id HAVING COUNT(DISTINCT customer_state) > 1)",
        ),
        (
            "dim_product: duplicate product ids",
            "SELECT COUNT(*) FROM (SELECT product_id FROM dim_product "
            "GROUP BY product_id HAVING COUNT(*) > 1)",
        ),
    ]
    rows = [{"check": label, "count": int(run_scalar(sql) or 0)} for label, sql in checks]
    return pd.DataFrame(rows)


@st.cache_data(show_spinner=False)
def integrity_checks() -> dict:
    conn = _connect()
    try:
        integrity = [r[0] for r in conn.execute("PRAGMA integrity_check").fetchall()]
        fk = [list(r) for r in conn.execute("PRAGMA foreign_key_check").fetchall()]
    finally:
        conn.close()
    return {"integrity": integrity, "fk_violations": fk}


@st.cache_data(show_spinner=False)
def table_counts() -> pd.DataFrame:
    tables = [
        "dim_customer",
        "dim_customer_identity",
        "dim_order",
        "dim_product_category",
        "dim_product",
        "dim_seller",
        "dim_geolocation",
        "fact_order_item",
        "bridge_payment",
        "bridge_review",
        "fact_order_summary",
    ]
    rows = [{"table": t, "rows": int(run_scalar(f"SELECT COUNT(*) FROM {t}") or 0)} for t in tables]
    return pd.DataFrame(rows)


@st.cache_data(show_spinner=False)
def etl_summary() -> dict | None:
    path = Path(config.REPORTS_DIR) / "etl_run_summary.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


@st.cache_data(show_spinner=False)
def analysis_summary_json() -> dict | None:
    path = Path(config.ANALYSIS_SUMMARY_JSON)
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
