"""Join-discipline tests.

These prove - against the real database - the two failure modes the SQL layer
must never have:

1. **Row multiplication (fan-out)** when several one-to-many tables
   (`bridge_payment`, `bridge_review`, `fact_order_item`) are combined.
2. **Double counted money**: merchandise sales must come from item prices,
   freight and payment values must stay separate and un-merged.

Each test states the *naive* result next to the *sanctioned* one so a future
change that reintroduces a fan-out fails loudly.
"""

from __future__ import annotations

import pytest

from .conftest import scalar


# --------------------------------------------------------------------------
# One-row-per-order surfaces
# --------------------------------------------------------------------------
def test_order_summary_has_exactly_one_row_per_order(conn):
    orders = scalar(conn, "SELECT COUNT(*) FROM dim_order")
    summary = scalar(conn, "SELECT COUNT(*) FROM fact_order_summary")
    reviews = scalar(conn, "SELECT COUNT(*) FROM bridge_review")
    assert summary == orders
    # The ETL collapsed duplicate-order reviews, so this is 1:1 too.
    assert reviews <= orders


def test_header_triple_join_does_not_multiply_orders(conn):
    """dim_order x fact_order_summary x bridge_review stays one row per order."""
    n = scalar(
        conn,
        """
        SELECT COUNT(*)
          FROM dim_order o
          JOIN fact_order_summary fs ON fs.order_id = o.order_id
          LEFT JOIN bridge_review  r ON r.order_id  = o.order_id
        """,
    )
    orders = scalar(conn, "SELECT COUNT(*) FROM dim_order")
    # Every order contributes exactly once: the 1:1 summary adds no rows and
    # the LEFT JOINed review is at most one row per order (otherwise this
    # total would exceed the order count).
    assert n == orders


def test_order_status_distribution_counts_orders_once(conn):
    total = scalar(conn, "SELECT SUM(orders) FROM vw_order_status_distribution")
    assert total == scalar(conn, "SELECT COUNT(*) FROM dim_order")


# --------------------------------------------------------------------------
# Item-level joins: many:1 parents never inflate
# --------------------------------------------------------------------------
def test_item_parent_joins_preserve_unit_count(conn):
    n = scalar(
        conn,
        """
        SELECT COUNT(*)
          FROM fact_order_item i
          JOIN dim_order   o ON o.order_id   = i.order_id
          JOIN dim_product p ON p.product_id = i.product_id
          JOIN dim_seller  s ON s.seller_id  = i.seller_id
        """,
    )
    assert n == scalar(conn, "SELECT COUNT(*) FROM fact_order_item") == 112_650


def test_category_join_preserves_unit_count(conn):
    n = scalar(
        conn,
        """
        SELECT COUNT(*)
          FROM fact_order_item i
          JOIN dim_product p ON p.product_id = i.product_id
        """,
    )
    assert n == 112_650


def test_review_join_does_not_duplicate_units(conn):
    """Attaching a review to its order must not duplicate item rows."""
    with_review = scalar(
        conn,
        "SELECT COUNT(*) FROM fact_order_item i "
        "JOIN bridge_review r ON r.order_id = i.order_id",
    )
    units_of_reviewed_orders = scalar(
        conn,
        """
        SELECT COUNT(*)
          FROM fact_order_item i
          JOIN dim_order o ON o.order_id = i.order_id
         WHERE o.has_reviews = 1
        """,
    )
    assert with_review == units_of_reviewed_orders


# --------------------------------------------------------------------------
# The hazard: payments are many-to-one with items
# --------------------------------------------------------------------------
def test_naive_item_payment_join_inflates_rows(conn):
    """Documents WHY payment rows are aggregated to order level first."""
    units = scalar(conn, "SELECT COUNT(*) FROM fact_order_item")
    naive = scalar(
        conn,
        "SELECT COUNT(*) FROM fact_order_item i "
        "JOIN bridge_payment p ON p.order_id = i.order_id",
    )
    assert naive > units, "expected the naive join to fan out"


def test_naive_item_payment_join_double_counts_sales(conn):
    correct = scalar(conn, "SELECT SUM(price) FROM fact_order_item")
    naive = scalar(
        conn,
        "SELECT SUM(i.price) FROM fact_order_item i "
        "JOIN bridge_payment p ON p.order_id = i.order_id",
    )
    # every multi-payment order repeats all of its item value
    assert naive - correct > 100_000


def test_naive_payment_review_join_inflates_orders(conn):
    orders_with_both = scalar(
        conn,
        "SELECT COUNT(DISTINCT p.order_id) FROM bridge_payment p "
        "JOIN bridge_review r ON r.order_id = p.order_id",
    )
    naive = scalar(
        conn,
        "SELECT COUNT(*) FROM bridge_payment p "
        "JOIN bridge_review r ON r.order_id = p.order_id",
    )
    assert naive > orders_with_both


# --------------------------------------------------------------------------
# The sanctioned path: aggregate payments per order first
# --------------------------------------------------------------------------
def test_payment_aggregate_by_order_matches_summary(conn):
    by_order = scalar(
        conn,
        """
        SELECT ROUND(SUM(v), 2)
          FROM (SELECT SUM(payment_value) AS v FROM bridge_payment GROUP BY order_id)
        """,
    )
    summary = scalar(conn, "SELECT ROUND(SUM(payment_collected), 2) FROM fact_order_summary")
    raw = scalar(conn, "SELECT ROUND(SUM(payment_value), 2) FROM bridge_payment")
    assert by_order == summary == raw
    # ...and joining that aggregate to the header still yields one row per order.
    joined = scalar(
        conn,
        """
        SELECT ROUND(SUM(v), 2)
          FROM (SELECT order_id, SUM(payment_value) AS v FROM bridge_payment GROUP BY order_id) a
          JOIN dim_order o ON o.order_id = a.order_id
        """,
    )
    assert joined == raw


def test_order_summary_joins_to_header_without_growth(conn):
    total = scalar(
        conn,
        "SELECT COUNT(*) FROM dim_order o JOIN fact_order_summary fs ON fs.order_id = o.order_id",
    )
    assert total == scalar(conn, "SELECT COUNT(*) FROM dim_order")


# --------------------------------------------------------------------------
# Money separation
# --------------------------------------------------------------------------
def test_merchandise_sales_equal_item_prices_only(conn):
    from_sql = scalar(conn, "SELECT ROUND(SUM(merchandise_sales), 2) FROM vw_monthly_sales")
    prices = scalar(conn, "SELECT ROUND(SUM(price), 2) FROM fact_order_item")
    freight = scalar(conn, "SELECT ROUND(SUM(freight_value), 2) FROM fact_order_item")
    assert from_sql == prices
    # deliberately NOT price + freight
    assert from_sql != round(prices + freight, 2)


def test_freight_reported_separately_and_never_merged(conn):
    sql_freight = scalar(conn, "SELECT ROUND(SUM(freight_charges), 2) FROM vw_monthly_sales")
    raw_freight = scalar(conn, "SELECT ROUND(SUM(freight_value), 2) FROM fact_order_item")
    assert sql_freight == raw_freight


def test_status_distribution_separates_sales_from_cash(conn):
    rows = conn.execute(
        "SELECT order_status, merchandise_sales, payment_collected "
        "FROM vw_order_status_distribution"
    ).fetchall()
    assert rows, "status distribution is empty"
    # Cash collected and merchandise sales are distinct columns, not one total.
    total_sales = sum(r["merchandise_sales"] or 0 for r in rows)
    total_cash = sum(r["payment_collected"] or 0 for r in rows)
    assert total_sales > 0 and total_cash > 0
    assert total_sales != total_cash


# --------------------------------------------------------------------------
# Seller / repeat-customer grain
# --------------------------------------------------------------------------
def test_seller_performance_has_one_row_per_seller(conn):
    sellers = scalar(conn, "SELECT COUNT(*) FROM dim_seller")
    assert scalar(conn, "SELECT COUNT(*) FROM vw_seller_performance") == sellers


def test_seller_units_sum_back_to_all_units(conn):
    """Aggregating to (seller, order) before the review join loses no units."""
    units = scalar(conn, "SELECT SUM(units) FROM vw_seller_performance")
    assert units == scalar(conn, "SELECT COUNT(*) FROM fact_order_item")


def test_seller_review_is_order_weighted_not_unit_weighted(conn):
    """A 6-unit order contributes ONE review to its seller, not six."""
    # Pick the seller with the most units: definitely has multi-unit orders.
    seller_id = scalar(
        conn,
        "SELECT seller_id FROM fact_order_item GROUP BY seller_id "
        "ORDER BY COUNT(*) DESC LIMIT 1",
    )
    order_weighted = scalar(
        conn,
        """
        SELECT AVG(review_score)
          FROM (SELECT order_id FROM fact_order_item WHERE seller_id = ? GROUP BY order_id) so
          JOIN bridge_review r ON r.order_id = so.order_id
        """,
        (seller_id,),
    )
    unit_weighted = scalar(
        conn,
        """
        SELECT AVG(r.review_score)
          FROM fact_order_item i JOIN bridge_review r ON r.order_id = i.order_id
         WHERE i.seller_id = ?
        """,
        (seller_id,),
    )
    from_view = scalar(
        conn,
        "SELECT avg_review_score FROM vw_seller_performance WHERE seller_id = ?",
        (seller_id,),
    )

    assert from_view == pytest.approx(order_weighted, abs=0.001)
    # the two weighting schemes are genuinely different for this seller
    assert unit_weighted != pytest.approx(order_weighted, abs=0.0001)


def test_repeat_customers_counted_on_unique_id(conn):
    unique_ids = scalar(conn, "SELECT COUNT(*) FROM dim_customer_identity")
    placements = scalar(conn, "SELECT COUNT(*) FROM dim_customer")
    assert unique_ids < placements

    customers_in_view = scalar(
        conn,
        "SELECT SUM(customers) FROM vw_repeat_customer_summary",
    )
    assert customers_in_view == unique_ids

    orders_in_view = scalar(conn, "SELECT SUM(total_orders) FROM vw_repeat_customer_summary")
    assert orders_in_view == scalar(conn, "SELECT COUNT(*) FROM dim_order")
