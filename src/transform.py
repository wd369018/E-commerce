"""
ShopScope - transformation layer (Stage 3).

Pure, deterministic functions that turn the cleaned entities into the
conformed analytics table(s). Nothing here reads from disk or writes to disk;
the pipeline passes frames in and gets frames back, which makes every
transformation unit-testable and safe to re-run.

Why ``fact_order_summary`` exists
---------------------------------
``order_items``, ``order_payments`` and ``order_reviews`` are all one-to-many
with ``orders``. Joining any two of them multiplies rows (measured in the
profiling phase: a three-way join inflates 99,441 orders to 119,143 rows).
This module pre-aggregates each child to **exactly one row per order** so that
downstream queries never have to join them to each other.

Zero vs NULL
------------
* Counts and sums over an *empty set of rows* are 0 - that is arithmetic, not
  an assumption (``n_items = 0`` for an order with no line items).
* A *missing measurement* stays NULL (``avg_review_score`` for an order nobody
  reviewed, ``item_revenue`` for the orders with no revenue detail at all).
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)


def build_order_summary(
    orders: pd.DataFrame,
    items: pd.DataFrame,
    payments: pd.DataFrame,
    reviews: pd.DataFrame,
) -> pd.DataFrame:
    """Return one row per ``order_id`` with child counts and pre-aggregated money.

    Parameters
    ----------
    orders
        Cleaned ``dim_order`` (grain: order_id).
    items
        Cleaned ``fact_order_item`` (grain: order_id + order_item_id).
    payments
        Cleaned ``bridge_payment`` (grain: order_id + payment_sequential).
    reviews
        Cleaned ``bridge_review`` (already one row per order_id).
    """
    keys = orders[["order_id"]].copy()

    item_money = (items["price"] + items["freight_value"]).rename("item_revenue")
    item_agg = (
        items.assign(item_revenue=item_money)
        .groupby("order_id")
        .agg(
            n_items=("order_item_id", "size"),
            n_distinct_products=("product_id", "nunique"),
            item_revenue=("item_revenue", "sum"),
        )
    )

    pay_agg = payments.groupby("order_id").agg(
        n_payments=("payment_sequential", "size"),
        payment_collected=("payment_value", "sum"),
    )

    rev_agg = reviews.groupby("order_id").agg(
        n_reviews=("review_score", "size"),
        avg_review_score=("review_score", "mean"),
    )

    summary = keys
    for agg in (item_agg, pay_agg, rev_agg):
        summary = summary.merge(agg, on="order_id", how="left")

    # Genuine empty-set arithmetic (see module docstring).
    for col in ("n_items", "n_distinct_products", "n_payments", "n_reviews"):
        summary[col] = summary[col].fillna(0).astype("Int64")
    for col in ("item_revenue", "payment_collected", "avg_review_score"):
        summary[col] = summary[col].astype("Float64")

    # An order with no line items has no revenue *detail* - keep it NULL so the
    # 775 orders without items are visible as a data gap instead of reading as
    # clean zeroes in a GMV-per-order distribution.
    summary.loc[summary["n_items"] == 0, "item_revenue"] = pd.NA

    # How far recorded cash is from recorded item revenue. NULL when either side
    # is unknown; never coerced to 0 (a 0 here would mean "perfectly reconciled").
    summary["payment_minus_item_revenue"] = (
        summary["payment_collected"] - summary["item_revenue"]
    ).astype("Float64")

    summary = summary.merge(
        orders[["order_id", "order_status", "is_revenue_status", "purchase_date",
                "delivery_delay_days", "is_late_vs_estimate"]],
        on="order_id",
        how="left",
    )

    logger.info(
        "fact_order_summary: %s rows (%s with no items, %s with no payment, %s with no review)",
        f"{len(summary):,}",
        f"{int((summary['n_items'] == 0).sum()):,}",
        f"{int((summary['n_payments'] == 0).sum()):,}",
        f"{int((summary['n_reviews'] == 0).sum()):,}",
    )
    return summary
