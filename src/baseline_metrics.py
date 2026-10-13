"""
ShopScope - naive "before cleaning" baseline metrics on the raw Olist files.

These reproduce the numbers a dashboard fed directly with the raw CSVs would
have produced BEFORE the ETL pipeline existed - including the join fan-out,
payment double counting and missing-value handling that the pipeline exists to
remove. They are compared against the cleaned KPIs from ``data/shopscope.db`` by
``scripts/compare_metrics.py``, which writes ``reports/metrics_before_after.md``.

Rules
-----
* **Pure function of the raw frames** loaded by :mod:`src.data_loader`
  (read-only; dtypes already applied: dates are datetime64, numerics nullable).
* No cleaning, no imputation, no deletion.
* Division by zero returns ``None`` - a baseline can never fabricate a rate.
* Every number is a measurement, not a constant.

Use::

    import src.baseline_metrics as base
    from src import data_loader
    loaded = data_loader.load_all()
    naive = base.naive_metrics({n: r.frame for n, r in loaded.items()})
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)


def _safe_ratio(num: float, den: float) -> float | None:
    return round(num / den, 6) if den else None


def naive_metrics(raw: dict[str, pd.DataFrame]) -> dict[str, float | int | None]:
    """Compute headline metrics naively from the raw files.

    "Naively" means exactly what the raw schema invites: orders, items,
    payments and reviews are simply counted/summed as loaded, missing delivery
    dates are *not* excluded, and the classic three-way join is measured so the
    fan-out inflation can be quoted.
    """
    orders = raw["olist_orders_dataset.csv"]
    customers = raw["olist_customers_dataset.csv"]
    items = raw["olist_order_items_dataset.csv"]
    payments = raw["olist_order_payments_dataset.csv"]
    reviews = raw["olist_order_reviews_dataset.csv"]

    # --- Volumes -----------------------------------------------------------
    n_orders = len(orders)
    n_item_rows = len(items)
    item_orders = int(items["order_id"].nunique())
    pay_orders = int(payments["order_id"].nunique())
    rev_orders = int(reviews["order_id"].nunique())
    n_payment_rows = len(payments)
    n_review_rows = len(reviews)

    # --- Money -------------------------------------------------------------
    price_sum = float(items["price"].sum())
    freight_sum = float(items["freight_value"].sum())
    payment_total = float(payments["payment_value"].sum())

    # --- Fan-out measurement: orders x items x payments x reviews -----------
    joined = orders[["order_id"]].merge(items[["order_id"]], on="order_id", how="inner")
    joined = joined.merge(payments[["order_id"]], on="order_id", how="inner")
    joined = joined.merge(reviews[["order_id"]], on="order_id", how="inner")
    fanout_rows = len(joined)
    fanout_ratio = _safe_ratio(float(fanout_rows), float(n_orders))

    # items x payments x reviews (without the 1:1 orders table): the classic
    # three-way join quoted in docs/project_plan.md.
    three_way = items[["order_id"]].merge(payments[["order_id"]], on="order_id", how="inner")
    three_way = three_way.merge(reviews[["order_id"]], on="order_id", how="inner")
    fanout_3way_rows = len(three_way)

    # --- Customers / repeats ------------------------------------------------
    unique_customers = int(customers["customer_unique_id"].nunique())
    groups = customers.groupby("customer_unique_id").size()
    repeat_customers = int((groups > 1).sum())
    repeat_extra_rows = int((groups[groups > 1] - 1).sum())
    repeat_rate = _safe_ratio(float(repeat_customers), float(unique_customers))

    # --- Reviews (all review rows, duplicates included) ---------------------
    avg_review_score = float(reviews["review_score"].mean())

    # --- Delivery (naive: missing delivery date treated as "fine") ----------
    delivered_total = int((orders["order_status"] == "delivered").sum())
    canceled_total = int((orders["order_status"] == "canceled").sum())
    missing_delivery_date = int(orders["order_delivered_customer_date"].isna().sum())
    delivered_with_date = int(orders["order_delivered_customer_date"].notna().sum())

    datable = orders[["order_delivered_customer_date", "order_estimated_delivery_date"]].dropna()
    on_time_strict = int(
        (datable["order_delivered_customer_date"] <= datable["order_estimated_delivery_date"]).sum()
    )
    # The naive treatment: "no timestamp at all" counts as on time.
    on_time_naive = on_time_strict + missing_delivery_date
    on_time_rate_strict = _safe_ratio(float(on_time_strict), float(delivered_total))
    on_time_rate_naive = _safe_ratio(float(on_time_naive), float(delivered_total))

    return {
        "orders": n_orders,
        "item_rows": n_item_rows,
        "item_orders": item_orders,
        "payment_rows": n_payment_rows,
        "payment_orders": pay_orders,
        "review_rows": n_review_rows,
        "review_orders": rev_orders,
        "fanout_join_rows": fanout_rows,
        "fanout_ratio_vs_orders": fanout_ratio,
        "fanout_3way_rows": fanout_3way_rows,
        "price_sum": price_sum,
        "freight_sum": freight_sum,
        "payment_total": payment_total,
        "aov_naive": _safe_ratio(price_sum, float(item_orders)),
        "items_per_order_naive": _safe_ratio(float(n_item_rows), float(item_orders)),
        "unique_customers": unique_customers,
        "repeat_customers": repeat_customers,
        "repeat_extra_rows": repeat_extra_rows,
        "repeat_rate_naive": repeat_rate,
        "avg_review_score_naive": avg_review_score,
        "delivered_total": delivered_total,
        "canceled_total": canceled_total,
        "missing_delivery_date": missing_delivery_date,
        "delivered_with_date": delivered_with_date,
        "on_time_strict": on_time_strict,
        "on_time_rate_strict": on_time_rate_strict,
        "on_time_rate_naive": on_time_rate_naive,
    }