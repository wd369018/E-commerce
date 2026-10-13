"""Tests for the reusable data-quality audit (src/quality_audit.py).

The audit logic is pure functions of DataFrames, so it is tested here with
small synthetic frames - fast, deterministic and independent of the real CSVs.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from src import quality_audit as qa


# --------------------------------------------------------------------------
# Synthetic frames (all required contract columns present)
# --------------------------------------------------------------------------
def make_orders():
    return pd.DataFrame({
        "order_id": ["o1", "o2", "o3"],
        "customer_id": ["c1", "c2", "c1"],
        "order_status": ["delivered", "delivered", "canceled"],
        "order_purchase_timestamp": pd.to_datetime(["2020-01-01", "2020-02-01", "2020-03-01"]),
        "order_approved_at": pd.to_datetime(["2020-01-02", "2020-02-02", None]),
        "order_delivered_carrier_date": [None] * 3,
        "order_delivered_customer_date": pd.to_datetime(["2020-01-10", "2020-02-10", None]),
        "order_estimated_delivery_date": pd.to_datetime(["2020-01-15", "2020-02-15", "2020-03-15"]),
    })


def make_items():
    return pd.DataFrame({
        "order_id": ["o1", "o1", "o2"],
        "order_item_id": [1, 2, 1],
        "product_id": ["p1", "p2", "p1"],
        "seller_id": ["s1", "s1", "s2"],
        "shipping_limit_date": pd.to_datetime(["2020-01-05"] * 3),
        "price": [10.0, 20.0, 5.0],
        "freight_value": [3.0, 4.0, 1.0],
    })


def make_payments():
    return pd.DataFrame({
        "order_id": ["o1", "o1", "o2"],
        "payment_sequential": [1, 2, 1],
        "payment_type": ["credit_card", "boleto", "credit_card"],
        "payment_installments": [2, 1, 1],
        "payment_value": [50.0, 40.0, 25.0],
    })


def make_reviews():
    return pd.DataFrame({
        "review_id": ["r1", "r2", "r3"],
        "order_id": ["o1", "o2", "o3"],
        "review_score": [5, 3, 1],
        "review_comment_title": [None] * 3,
        "review_comment_message": ["great", None, "bad"],
        "review_creation_date": pd.to_datetime(["2020-01-11", "2020-02-11", None]),
        "review_answer_timestamp": pd.to_datetime(["2020-01-12", "2020-02-12", None]),
    })


def make_customers():
    return pd.DataFrame({
        "customer_id": ["c1a", "c1b", "c2"],
        "customer_unique_id": ["c1", "c1", "c2"],
        "customer_zip_code_prefix": [1001, 1001, 2002],
        "customer_city": ["A", "A", "B"],
        "customer_state": ["SP", "SP", "RJ"],
    })


def tiny_raw():
    return {
        "olist_customers_dataset.csv": make_customers(),
        "olist_orders_dataset.csv": make_orders(),
        "olist_order_items_dataset.csv": make_items(),
        "olist_order_payments_dataset.csv": make_payments(),
        "olist_order_reviews_dataset.csv": make_reviews(),
    }


def tiny_cleaned():
    return {
        "dim_order": make_orders(),
        "fact_order_item": make_items(),
        "bridge_payment": make_payments(),
        "bridge_review": make_reviews(),
    }


# --------------------------------------------------------------------------
# Primitives
# --------------------------------------------------------------------------
def test_dup_key_stats_compound():
    frame = make_items()
    extra, groups = qa.dup_key_stats(frame, ["order_id", "order_item_id"])
    assert extra == 0
    assert groups == 0
    # order_id alone is legitimate multiplicity (2 rows in one group -> 1 extra).
    extra, groups = qa.dup_key_stats(frame, ["order_id"])
    assert extra == 1
    assert groups == 1


def test_dup_key_stats_drops_nan_keys():
    frame = pd.DataFrame({"id": [1, 1, None, None]})
    extra, groups = qa.dup_key_stats(frame, ["id"])
    assert extra == 1
    assert groups == 1


def test_orphan_count_detects_missing_parents():
    child = pd.DataFrame({"order_id": ["o1", "o2", "oX"]})
    parent = pd.DataFrame({"order_id": ["o1", "o2"]})
    orphans, child_rows = qa.orphan_count(child, "order_id", parent, "order_id")
    assert orphans == 1
    assert child_rows == 3


def test_orphan_count_missing_columns_returns_none():
    child = pd.DataFrame({"a": [1]})
    parent = pd.DataFrame({"b": [1]})
    # parent has no column "c" -> cannot compute -> None.
    assert qa.orphan_count(child, "a", parent, "c") is None
    # both sides resolvable -> a real count.
    assert qa.orphan_count(child, "a", parent, "b") == (0, 1)


def test_missing_value_rows_reports_percentage_and_status():
    frame = pd.DataFrame({"x": [1.0, None], "y": ["a", "b"]})
    rows = qa.missing_value_rows("f.csv", frame, ["x", "y"])
    by_check = {r["check"]: r for r in rows}
    assert by_check["missing.x"]["value"] == "1 (50.0%)"
    assert by_check["missing.x"]["status"] == qa.STATUS_FLAGGED
    assert by_check["missing.y"]["status"] == qa.STATUS_OK


def test_invalid_review_scores():
    frame = pd.DataFrame({"review_score": [1, 5, 0, 6, None]})
    rows = {r["check"]: r["status"] for r in qa.invalid_value_rows(
        "olist_order_reviews_dataset.csv", frame)}
    assert rows["invalid.review_score_outside_1_5"] == qa.STATUS_FLAGGED


def test_invalid_geo_bbox():
    frame = pd.DataFrame({"geolocation_lat": [-36.6, -23.5, 45.07],
                          "geolocation_lng": [-46.6, -101.47, 121.11]})
    rows = {r["check"]: r["status"] for r in qa.invalid_value_rows(
        "olist_geolocation_dataset.csv", frame)}
    # -36.6 and 45.07 are outside the Brazil box; -23.5 is inside.
    assert rows["invalid.lat_outside_brazil_box"] == qa.STATUS_FLAGGED
    # -101.47 and 121.11 are outside; -46.6 is inside.
    assert rows["invalid.lng_outside_brazil_box"] == qa.STATUS_FLAGGED
    # Nothing exceeds the physical limits (|lat| > 90, |lng| > 180).
    assert rows["invalid.lat_magnitude_over_90"] == qa.STATUS_OK
    assert rows["invalid.lng_magnitude_over_180"] == qa.STATUS_OK


def test_invalid_price_and_freight():
    frame = pd.DataFrame({"price": [10.0, 0.0, -1.0], "freight_value": [1.0, -2.0, 3.0]})
    rows = {r["check"]: r["status"] for r in qa.invalid_value_rows(
        "olist_order_items_dataset.csv", frame)}
    assert rows["invalid.price_non_positive"] == qa.STATUS_FLAGGED
    assert rows["invalid.freight_negative"] == qa.STATUS_FLAGGED


def test_multiplicity_is_not_reported_as_a_defect():
    """2 line items for one order = expected multiplicity, not a duplicate."""
    rows = qa.file_level_rows("olist_order_items_dataset.csv", make_items(),
                              qa.RAW_BUSINESS_KEYS["olist_order_items_dataset.csv"])
    by_check = {r["check"]: r for r in rows}
    assert by_check["business_key.order_id"]["status"] == qa.STATUS_INFO
    assert by_check["business_key.order_id,order_item_id"]["status"] == qa.STATUS_OK


def test_file_level_exact_duplicates():
    frame = pd.DataFrame({"id": [1, 1], "v": ["a", "a"]})
    rows = qa.file_level_rows("f.csv", frame, [])
    by_check = {r["check"]: r for r in rows}
    assert by_check["exact_duplicate_rows"]["value"] == "1"


# --------------------------------------------------------------------------
# Payload + writers
# --------------------------------------------------------------------------
def test_build_payload_includes_relationships_and_missing():
    payload = qa.build_payload(tiny_raw(), tiny_cleaned(), source="test")
    assert payload["source"] == "test"
    raw_orders = payload["raw"]["olist_orders_dataset.csv"]
    assert raw_orders["rows"] == 3
    assert raw_orders["missing"]["order_delivered_carrier_date"]["count"] == 3
    assert "order.customer" in raw_orders["relationships"]
    cleaned_items = payload["cleaned"]["fact_order_item"]
    assert "item.order" in cleaned_items["relationships"]
    assert cleaned_items["business_keys"]["order_id,order_item_id"]["extra_rows"] == 0


def test_write_audit_writes_three_files(tmp_path):
    paths = qa.write_audit(tiny_raw(), tiny_cleaned(), source="test",
                           out_dir=tmp_path)
    assert paths["csv"].is_file()
    assert paths["json"].is_file()
    assert paths["summary"].is_file()

    csv_df = pd.read_csv(paths["csv"])
    assert list(csv_df.columns) == qa.AUDIT_COLUMNS
    assert not csv_df.empty
    assert (csv_df["status"] == qa.STATUS_FLAGGED).any()

    with open(paths["json"], encoding="utf-8") as fh:
        payload = json.load(fh)
    assert payload["source"] == "test"
    assert "raw" in payload and "cleaned" in payload

    summary = paths["summary"].read_text(encoding="utf-8")
    assert summary.startswith("# ShopScope - Data Quality Audit")
    assert "Raw source files" in summary


def test_write_audit_includes_validation_block(tmp_path):
    from src import validation
    report = validation.ValidationReport()
    report.add(validation.CheckResult("t.1", "dummy check", validation.ERROR,
                                      False, "obs", "exp"))
    paths = qa.write_audit(tiny_raw(), tiny_cleaned(), validation=report,
                           source="test", out_dir=tmp_path)
    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert payload["validation"]["total_checks"] == 1
    assert payload["validation"]["errors"] == 1


def test_standalone_constants_refer_to_reports_dir():
    assert qa.config.AUDIT_CSV.name == "data_quality_audit.csv"
    assert qa.config.AUDIT_JSON.name == "data_quality_audit.json"
    assert qa.config.AUDIT_SUMMARY_MD.name == "data_quality_summary.md"


@pytest.mark.parametrize("filename", [
    "olist_orders_dataset.csv",
    "olist_order_items_dataset.csv",
    "olist_order_payments_dataset.csv",
    "olist_order_reviews_dataset.csv",
])
def test_invalid_rows_do_not_raise_on_expected_files(filename):
    """Guard: the invalid-value checker must never raise on the real contracts."""
    dtype = {col: "datetime64[ns]" for col in qa.config.DATE_COLUMNS.get(filename, [])}
    for col, kind in qa.config.NUMERIC_COLUMNS.get(filename, {}).items():
        dtype[col] = "Float64" if kind == "Float64" else "Int64"
    frame = pd.DataFrame({
        col: pd.Series([None, None], dtype=dtype.get(col, "object"))
        for col in qa.config.REQUIRED_COLUMNS[filename]
    })
    rows = qa.invalid_value_rows(filename, frame)
    assert isinstance(rows, list)