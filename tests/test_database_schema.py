"""Schema-level guarantees: row counts, keys, constraints, indexes, views."""

from __future__ import annotations

import sqlite3

import pytest

from src import database, queries

from .conftest import rows, scalar

EXPECTED_TABLES = set(database.TABLE_ORDER)

EXPECTED_VIEWS = {
    "vw_order_item_rollup",
    "vw_monthly_sales",
    "vw_monthly_order_volume",
    "vw_order_status_distribution",
    "vw_category_performance",
    "vw_state_customer_order_distribution",
    "vw_customer_repeat_purchase",
    "vw_repeat_customer_summary",
    "vw_payment_method_distribution",
    "vw_installment_analysis",
    "vw_delivery_performance_monthly",
    "vw_delivery_summary",
    "vw_review_score_distribution",
    "vw_review_monthly",
    "vw_seller_performance",
    "vw_weight_vs_freight",
    "vw_kpi_population",
}

# Columns the query library depends on being indexed for order-level lookups.
EXPECTED_INDEXES = {
    "idx_dim_order_customer",
    "idx_dim_order_purchase_ts",
    "idx_dim_order_status",
    "idx_fact_item_order",
    "idx_fact_item_product",
    "idx_fact_item_seller",
    "idx_bridge_payment_order",
    "idx_bridge_review_order",
    "idx_dim_customer_unique_id",
    "idx_fact_summary_purchase",
}


def test_build_report_is_ok(build_result):
    assert build_result.ok, build_result.verification["problems"]
    assert build_result.verification["integrity_check"] == "ok"
    assert build_result.verification["foreign_key_violations"] == 0


def test_every_table_present(build_result):
    assert set(build_result.table_counts) == EXPECTED_TABLES


def test_row_counts_match_source_csvs(build_result):
    for table, csv_rows in build_result.csv_counts.items():
        assert build_result.table_counts[table] == csv_rows, table


def test_all_views_created(conn):
    created = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")}
    assert created == EXPECTED_VIEWS


def test_expected_indexes_exist(conn):
    created = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert EXPECTED_INDEXES <= created, EXPECTED_INDEXES - created


def test_primary_keys_are_unique_and_declared(conn):
    for table in database.TABLE_ORDER:
        pk_cols = [
            r["name"]
            for r in conn.execute(f'PRAGMA table_info("{table}")')
            if r["pk"]
        ]
        assert pk_cols, f"{table} declares no primary key"
        cols = ", ".join(f'"{c}"' for c in pk_cols)
        total = scalar(conn, f'SELECT COUNT(*) FROM "{table}"')
        distinct = scalar(
            conn, f'SELECT COUNT(*) FROM (SELECT DISTINCT {cols} FROM "{table}")'
        )
        assert total == distinct, f"{table} duplicate primary key {pk_cols}"


def test_foreign_keys_are_declared(conn):
    expected = {
        "dim_order": {"customer_id"},
        "fact_order_item": {"order_id", "product_id", "seller_id"},
        "bridge_payment": {"order_id"},
        "bridge_review": {"order_id"},
        "fact_order_summary": {"order_id"},
        "dim_product": {"product_category_name"},
    }
    for table, wanted in expected.items():
        found = {
            r["from"]
            for r in conn.execute(f'PRAGMA foreign_key_list("{table}")')
        }
        assert wanted <= found, f"{table} missing FK to {wanted - found}"


def test_inserting_orphan_order_is_rejected(conn):
    """Referential integrity is enforced by the database, not just by the ETL."""
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        conn.execute(
            "INSERT INTO dim_order (order_id, customer_id, order_status, "
            "order_purchase_timestamp, purchase_date, is_delivered_status, "
            "is_revenue_status, missing_approval_date, missing_carrier_date, "
            "missing_delivery_date, has_line_items, has_payments, has_reviews, "
            "has_impossible_date_sequence) "
            "VALUES ('ghost-order','ghost-customer','created','2018-01-01 00:00:00',"
            "'2018-01-01',0,0,0,0,0,0,0,0,0)"
        )


def test_inserting_duplicate_primary_key_is_rejected(conn):
    real = conn.execute("SELECT order_id FROM dim_order LIMIT 1").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        conn.execute(
            "INSERT INTO bridge_review (order_id, review_id, review_score, "
            "source_review_rows, distinct_review_ids) VALUES (?, 'dup', 5, 1, 1)",
            (real,),
        )


def test_check_constraint_rejects_free_products(conn):
    order_id = conn.execute(
        "SELECT order_id FROM fact_order_item LIMIT 1"
    ).fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        conn.execute(
            "INSERT INTO fact_order_item (order_id, order_item_id, product_id, "
            "seller_id, price, freight_value, is_valid_price, is_valid_freight) "
            "SELECT ?, 999999, product_id, seller_id, 0, 0, 0, 0 "
            "FROM fact_order_item WHERE order_id = ? LIMIT 1",
            (order_id, order_id),
        )


def test_review_score_constrained_to_five_stars(conn):
    order_id = conn.execute("SELECT order_id FROM dim_order LIMIT 1").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        conn.execute(
            "INSERT INTO bridge_review (order_id, review_id, review_score, "
            "source_review_rows, distinct_review_ids) VALUES (?, 'x', 9, 1, 1)",
            (order_id,),
        )


def test_query_library_parses_and_every_query_runs(conn):
    catalogue = queries.catalogue()
    assert len(catalogue) == 16, sorted(catalogue)
    for name, spec in catalogue.items():
        frame = spec.execute(conn)
        assert len(frame) > 0, f"{name} returned no rows"
        assert len(frame.columns) >= 3, f"{name} looks malformed"


def test_kpi_population_view_reports_live_counts(conn):
    frame = queries.get("kpi_population").execute(conn)
    by_kpi = frame.set_index("kpi")
    assert int(by_kpi.loc["monthly_merchandise_sales", "rows_used"]) == 112_650
    assert int(by_kpi.loc["monthly_merchandise_sales", "source_rows"]) == 112_650
    # Delivery KPI drops orders with no delivery timestamp - population shrinks.
    assert int(by_kpi.loc["delivery_performance", "rows_used"]) < int(
        by_kpi.loc["delivery_performance", "source_rows"]
    )
    # Repeat analysis runs on customer_unique_id, not customer_id.
    assert int(by_kpi.loc["repeat_customer_analysis", "rows_used"]) == 96_096


def test_schema_file_declares_index_on_hot_join_keys():
    text = (database.config.SCHEMA_SQL).read_text(encoding="utf-8")
    for index_name in EXPECTED_INDEXES:
        assert index_name in text, index_name
