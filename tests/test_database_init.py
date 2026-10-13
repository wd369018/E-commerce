"""Database initialisation, integrity and ETL-repeatability tests.

A tiny synthetic data warehouse (11 tables, a handful of rows) is written to a
temporary ``processed/`` directory and built with the real
:func:`src.database.build_database`. This exercises the genuine schema, foreign
keys, check constraints and view creation without touching the production
``data/shopscope.db`` or the real cleaned CSVs.

The synthetic rows only prove *mechanics* (keys, repeatability, failure modes).
Real figures are always asserted against ``data/shopscope.db`` elsewhere.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from src import database


# --------------------------------------------------------------------------
# Synthetic warehouse (schema-shaped, referentially consistent)
# --------------------------------------------------------------------------
def _frames() -> dict[str, pd.DataFrame]:
    return {
        "dim_customer": pd.DataFrame({
            "customer_id": ["c1", "c2", "c3"],
            "customer_unique_id": ["u1", "u2", "u3"],
            "customer_zip_code_prefix": ["1001", "1002", "1001"],
            "customer_city": ["sao paulo", "rio", "sao paulo"],
            "customer_state": ["SP", "RJ", "SP"],
        }),
        "dim_customer_identity": pd.DataFrame({
            "customer_unique_id": ["u1", "u2", "u3"],
            "placement_count": [1, 2, 1],
            "first_customer_id": ["c1", "c2", "c3"],
            "states": ["SP", "RJ", "SP"],
        }),
        "dim_product_category": pd.DataFrame({
            "product_category_name": ["unknown", "brinquedos"],
            "product_category_name_english": [None, "toys"],
            "has_english_translation": [False, True],
        }),
        "dim_product": pd.DataFrame({
            "product_id": ["p1", "p2"],
            "product_category_name": ["unknown", "brinquedos"],
            "product_name_lenght": [10, 20],
            "product_description_lenght": [5, 6],
            "product_photos_qty": [1, 2],
            "product_weight_g": [500.0, 1000.0],
            "product_length_cm": [10.0, 20.0],
            "product_height_cm": [5.0, 5.0],
            "product_width_cm": [5.0, 5.0],
            "category_was_missing": [True, False],
            "is_shipping_valid": [True, True],
        }),
        "dim_seller": pd.DataFrame({
            "seller_id": ["s1", "s2"],
            "seller_zip_code_prefix": ["1001", "1002"],
            "seller_city": ["sp", "rio"],
            "seller_state": ["SP", "RJ"],
        }),
        "dim_geolocation": pd.DataFrame({
            "geolocation_zip_code_prefix": [1001, 1002],
            "geolocation_lat": [-23.5, -22.9],
            "geolocation_lng": [-46.6, -43.2],
            "geolocation_city": ["sp", "rio"],
            "geolocation_state": ["SP", "RJ"],
            "valid_coordinate_count": [3, 2],
            "source_row_count": [5, 4],
            "is_valid_coordinate": [True, True],
        }),
        "dim_order": pd.DataFrame({
            "order_id": ["o1", "o2", "o3"],
            "customer_id": ["c1", "c2", "c3"],
            "order_status": ["delivered", "delivered", "canceled"],
            "order_purchase_timestamp": [
                "2018-01-01 10:00:00", "2018-01-02 10:00:00", "2018-01-03 10:00:00"],
            "order_approved_at": ["2018-01-01 11:00:00", "2018-01-02 11:00:00", None],
            "order_delivered_carrier_date": [
                "2018-01-02 10:00:00", "2018-01-03 10:00:00", None],
            "order_delivered_customer_date": [
                "2018-01-05 10:00:00", "2018-01-04 10:00:00", None],
            "order_estimated_delivery_date": [
                "2018-01-06 10:00:00", "2018-01-03 10:00:00", None],
            "delivery_delay_days": [4, 2, None],
            "is_late_vs_estimate": [False, True, None],
            "purchase_date": ["2018-01-01", "2018-01-02", "2018-01-03"],
            "is_delivered_status": [True, True, False],
            "is_revenue_status": [True, True, False],
            "missing_approval_date": [False, False, True],
            "missing_carrier_date": [False, False, True],
            "missing_delivery_date": [False, False, True],
            "has_line_items": [True, True, True],
            "has_payments": [True, True, False],
            "has_reviews": [True, True, False],
            "has_impossible_date_sequence": [False, False, False],
        }),
        "fact_order_item": pd.DataFrame({
            "order_id": ["o1", "o1", "o2"],
            "order_item_id": [1, 2, 1],
            "product_id": ["p1", "p2", "p1"],
            "seller_id": ["s1", "s2", "s1"],
            "shipping_limit_date": ["2018-01-03", "2018-01-03", "2018-01-04"],
            "price": [100.0, 50.0, 80.0],
            "freight_value": [10.0, 5.0, 8.0],
            "is_valid_price": [True, True, True],
            "is_valid_freight": [True, True, True],
        }),
        "bridge_payment": pd.DataFrame({
            "order_id": ["o1", "o1", "o2"],
            "payment_sequential": [1, 2, 1],
            "payment_type": ["credit_card", "voucher", "boleto"],
            "payment_installments": [3, 1, 1],
            "payment_value": [120.0, 5.0, 88.0],
            "is_valid_payment_value": [True, True, True],
            "is_valid_installments": [True, True, True],
        }),
        "bridge_review": pd.DataFrame({
            "order_id": ["o1", "o2"],
            "review_id": ["r1", "r2"],
            "review_score": [5, 4],
            "review_comment_title": [None, "ok"],
            "review_comment_message": [None, "good"],
            "review_creation_date": ["2018-01-06", "2018-01-05"],
            "review_answer_timestamp": ["2018-01-06 12:00:00", "2018-01-05 12:00:00"],
            "source_review_rows": [1, 1],
            "distinct_review_ids": [1, 1],
        }),
        "fact_order_summary": pd.DataFrame({
            "order_id": ["o1", "o2", "o3"],
            "n_items": [2, 1, 0],
            "n_distinct_products": [2, 1, 0],
            "item_revenue": [165.0, 88.0, None],
            "n_payments": [2, 1, 0],
            "payment_collected": [125.0, 88.0, None],
            "n_reviews": [1, 1, 0],
            "avg_review_score": [5.0, 4.0, None],
            "payment_minus_item_revenue": [-40.0, 0.0, None],
            "order_status": ["delivered", "delivered", "canceled"],
            "is_revenue_status": [True, True, False],
            "purchase_date": ["2018-01-01", "2018-01-02", "2018-01-03"],
            "delivery_delay_days": [4, 2, None],
            "is_late_vs_estimate": [False, True, None],
        }),
    }


@pytest.fixture(scope="module")
def synthetic_processed(tmp_path_factory) -> Path:
    directory = tmp_path_factory.mktemp("shopscope_processed")
    for name, frame in _frames().items():
        frame.to_csv(directory / f"{name}.csv", index=False, lineterminator="\n")
    return directory


@pytest.fixture()
def synthetic_db(tmp_path, synthetic_processed) -> database.BuildResult:
    return database.build_database(
        db_path=tmp_path / "test.db", processed_dir=synthetic_processed
    )


# --------------------------------------------------------------------------
# Initialisation
# --------------------------------------------------------------------------
class TestDatabaseInitialisation:
    def test_build_succeeds_and_matches_csvs(self, synthetic_db):
        assert synthetic_db.ok, synthetic_db.verification["problems"]
        assert synthetic_db.verification["integrity_check"] == "ok"
        assert synthetic_db.verification["foreign_key_violations"] == 0
        assert synthetic_db.table_counts == synthetic_db.csv_counts

    def test_all_tables_and_views_created(self, synthetic_db):
        assert set(synthetic_db.table_counts) == set(database.TABLE_ORDER)
        assert len(synthetic_db.views) == 17
        # Spot-check the indexes the query layer depends on.
        for index in ("idx_fact_item_order", "idx_dim_order_customer",
                      "idx_bridge_payment_order"):
            assert index in synthetic_db.indexes

    def test_foreign_keys_are_enforced(self, synthetic_db):
        orphan = (
            "INSERT INTO dim_order (order_id, customer_id, order_status, "
            "order_purchase_timestamp, purchase_date, is_delivered_status, "
            "is_revenue_status, missing_approval_date, missing_carrier_date, "
            "missing_delivery_date, has_line_items, has_payments, has_reviews, "
            "has_impossible_date_sequence) VALUES "
            "('zz', 'no-such-customer', 'delivered', '2018-01-01', '2018-01-01', "
            "1, 1, 0, 0, 0, 0, 0, 0, 0)"
        )
        on = database.get_connection(synthetic_db.db_path, foreign_keys=True)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                on.execute(orphan)
        finally:
            on.close()

        off = database.get_connection(synthetic_db.db_path, foreign_keys=False)
        try:
            off.execute(orphan)  # accepted when FK enforcement is disabled
            assert off.execute(
                "SELECT COUNT(*) FROM dim_order WHERE order_id = 'zz'"
            ).fetchone()[0] == 1
        finally:
            off.close()

    def test_get_connection_creates_parent_directory(self, tmp_path):
        nested = tmp_path / "a" / "b" / "new.db"
        conn = database.get_connection(nested)
        try:
            assert nested.is_file()
        finally:
            conn.close()

    def test_verify_detects_wrong_expected_count(self, synthetic_db):
        conn = database.get_connection(synthetic_db.db_path)
        try:
            report = database.verify(conn, {"dim_order": 999})
        finally:
            conn.close()
        assert report["ok"] is False
        assert any("dim_order" in p for p in report["problems"])


class TestBuildFailures:
    def test_missing_processed_dir_raises(self, tmp_path):
        with pytest.raises(database.DatabaseBuildError, match="cleaned data directory not found"):
            database.build_database(
                db_path=tmp_path / "x.db", processed_dir=tmp_path / "nope"
            )

    def test_missing_schema_raises(self, tmp_path, synthetic_processed):
        with pytest.raises(database.DatabaseBuildError, match="schema file not found"):
            database.build_database(
                db_path=tmp_path / "x.db",
                processed_dir=synthetic_processed,
                schema_path=tmp_path / "nope.sql",
            )


class TestRepeatability:
    def test_two_builds_have_identical_counts(self, tmp_path, synthetic_processed):
        first = database.build_database(
            db_path=tmp_path / "a.db", processed_dir=synthetic_processed
        )
        second = database.build_database(
            db_path=tmp_path / "b.db", processed_dir=synthetic_processed
        )
        assert first.table_counts == second.table_counts
        assert first.views == second.views

    def test_fresh_build_removes_stale_objects(self, synthetic_db, synthetic_processed):
        conn = database.get_connection(synthetic_db.db_path)
        try:
            conn.execute("CREATE TABLE stale_marker (x INTEGER)")
        finally:
            conn.close()

        rebuilt = database.build_database(
            db_path=synthetic_db.db_path, processed_dir=synthetic_processed
        )
        conn = database.get_connection(synthetic_db.db_path)
        try:
            names = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
        assert "stale_marker" not in names
        assert rebuilt.ok
