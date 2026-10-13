"""Tests for the naive "before cleaning" baseline metrics (src/baseline_metrics.py).

Small synthetic raw frames keep the tests fast and deterministic. The point of
these tests is that the *naive* computations reproduce exactly the fan-out and
multiplicity a raw-join dashboard would produce - and that division by zero
never fabricates a rate.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src import baseline_metrics as base


def _orders():
    return pd.DataFrame({
        "order_id": ["o1", "o2"],
        "customer_id": ["c1", "c2"],
        "order_status": ["delivered", "delivered"],
        "order_purchase_timestamp": pd.to_datetime(["2020-01-01", "2020-02-01"]),
        "order_approved_at": pd.to_datetime(["2020-01-02", None]),
        "order_delivered_carrier_date": pd.to_datetime([None, None]),
        "order_delivered_customer_date": pd.to_datetime(["2020-01-10", None]),
        "order_estimated_delivery_date": pd.to_datetime(["2020-01-15", "2020-02-15"]),
    })


def _items():
    # o1 has two line items, o2 has one -> 3 item rows, 2 item-bearing orders.
    return pd.DataFrame({
        "order_id": ["o1", "o1", "o2"],
        "order_item_id": [1, 2, 1],
        "product_id": ["p1", "p2", "p1"],
        "seller_id": ["s1", "s1", "s2"],
        "shipping_limit_date": pd.to_datetime(["2020-01-05"] * 3),
        "price": [10.0, 20.0, 5.0],
        "freight_value": [3.0, 4.0, 1.0],
    })


def _payments():
    # o1 has two payment records, o2 one -> join fan-out will multiply.
    return pd.DataFrame({
        "order_id": ["o1", "o1", "o2"],
        "payment_sequential": [1, 2, 1],
        "payment_type": ["credit_card", "boleto", "credit_card"],
        "payment_installments": [2, 1, 1],
        "payment_value": [50.0, 40.0, 25.0],
    })


def _reviews():
    return pd.DataFrame({
        "review_id": ["r1", "r2", "r3", "r4"],
        "order_id": ["o1", "o1", "o2", "o2"],
        "review_score": [5, 4, 3, 2],
        "review_comment_title": [None] * 4,
        "review_comment_message": [None] * 4,
        "review_creation_date": pd.to_datetime(["2020-01-11"] * 4),
        "review_answer_timestamp": pd.to_datetime(["2020-01-12"] * 4),
    })


def _customers():
    # c1 = repeat buyer (2 placements), c2 single -> 2 unique, 1 repeat.
    return pd.DataFrame({
        "customer_id": ["c1a", "c1b", "c2"],
        "customer_unique_id": ["c1", "c1", "c2"],
        "customer_zip_code_prefix": [1001, 1001, 2002],
        "customer_city": ["A", "A", "B"],
        "customer_state": ["SP", "SP", "RJ"],
    })


def _raw():
    return {
        "olist_orders_dataset.csv": _orders(),
        "olist_order_items_dataset.csv": _items(),
        "olist_order_payments_dataset.csv": _payments(),
        "olist_order_reviews_dataset.csv": _reviews(),
        "olist_customers_dataset.csv": _customers(),
    }


def test_fanout_join_is_measured():
    """orders(2) x items(o1:2,o2:1) x payments(o1:2,o2:1) x reviews(o1:2,o2:2).

    o1: 2 items * 2 payments * 2 reviews = 8 rows
    o2: 1 item  * 1 payment  * 2 reviews = 2 rows
    total = 10 - the fan-out artefact a naive dashboard would count.
    """
    naive = base.naive_metrics(_raw())
    assert naive["fanout_join_rows"] == 10
    assert naive["fanout_3way_rows"] == 10  # orders are 1:1, so 3-way == 4-way here
    assert naive["orders"] == 2
    assert naive["fanout_ratio_vs_orders"] == 5.0


def test_volumes_and_money():
    naive = base.naive_metrics(_raw())
    assert naive["item_rows"] == 3
    assert naive["item_orders"] == 2
    assert naive["payment_rows"] == 3
    assert naive["review_rows"] == 4
    assert naive["price_sum"] == 35.0
    assert naive["freight_sum"] == 8.0
    assert naive["payment_total"] == 115.0
    assert naive["aov_naive"] == 17.5
    assert naive["items_per_order_naive"] == 1.5


def test_customers_and_repeat():
    naive = base.naive_metrics(_raw())
    assert naive["unique_customers"] == 2
    assert naive["repeat_customers"] == 1
    assert naive["repeat_extra_rows"] == 1
    assert naive["repeat_rate_naive"] == 0.5


def test_review_mean_includes_every_raw_row():
    naive = base.naive_metrics(_raw())
    assert naive["review_orders"] == 2
    assert naive["avg_review_score_naive"] == pytest.approx(3.5)  # (5+4+3+2)/4


def test_delivery_naive_treats_missing_as_on_time():
    naive = base.naive_metrics(_raw())
    # delivered_total: both orders are 'delivered' (2). o2 has no delivery date.
    assert naive["delivered_total"] == 2
    assert naive["missing_delivery_date"] == 1
    assert naive["delivered_with_date"] == 1
    # Strict: only o1 has both dates, and it is on time (Jan 10 <= Jan 15).
    assert naive["on_time_strict"] == 1
    assert naive["on_time_rate_strict"] == 0.5
    # Naive: the missing o2 timestamp counts as on time.
    assert naive["on_time_rate_naive"] == 1.0


def test_division_by_zero_returns_none():
    raw = _raw()
    raw["olist_order_items_dataset.csv"] = _items().iloc[0:0]
    naive = base.naive_metrics(raw)
    assert naive["price_sum"] == 0.0
    assert naive["aov_naive"] is None
    assert naive["items_per_order_naive"] is None
    assert naive["fanout_join_rows"] == 0
    assert naive["fanout_ratio_vs_orders"] == 0.0