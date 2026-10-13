"""Unit tests for the ingestion, cleaning, transform and validation layers.

These are **pure-function** tests with small synthetic frames. They exist to pin
the behaviour the integration tests cannot isolate:

* date / numeric parsing and the "count, never hide" conversion-failure rule;
* contract breaches (missing / duplicate columns) fail fast;
* missing values stay NULL (not 0, not an empty string);
* duplicate handling (one review per order, geolocation collapsed to one row
  per ZIP) and the exact tie-break used;
* one-to-many joins never fan out through :func:`transform.build_order_summary`;
* the key / range / reconciliation gates in :mod:`src.validation`.

Synthetic data here is used only to exercise edge cases. No business finding is
derived from it - real figures always come from ``data/shopscope.db``.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src import data_cleaning, data_loader, transform, validation


# ==========================================================================
# Ingestion - data_loader
# ==========================================================================
def _orders_dates_frame():
    return pd.DataFrame(
        {
            "order_id": ["o1", "o2", "o3"],
            "order_purchase_timestamp": ["2018-01-01 10:00:00", "not-a-date", None],
            "order_approved_at": ["2018-01-01 11:00:00", None, None],
            "order_delivered_carrier_date": ["2018-01-02 10:00:00", None, None],
            "order_delivered_customer_date": ["2018-01-05 10:00:00", None, None],
            "order_estimated_delivery_date": ["2018-01-06 10:00:00", None, None],
        }
    )


def _orders_all_valid():
    frame = _orders_dates_frame()
    frame["order_purchase_timestamp"] = ["2018-01-01 10:00:00", "2018-01-02 10:00:00", None]
    return frame


class TestApplyDtypes:
    def test_valid_dates_parse_to_datetime(self):
        frame = _orders_all_valid()
        failures = data_loader.apply_dtypes("olist_orders_dataset.csv", frame)
        assert failures == {}
        assert pd.api.types.is_datetime64_any_dtype(frame["order_purchase_timestamp"])
        assert frame["order_purchase_timestamp"].iloc[0] == pd.Timestamp("2018-01-01 10:00:00")

    def test_unparseable_date_is_counted_not_dropped(self):
        frame = _orders_dates_frame()
        failures = data_loader.apply_dtypes("olist_orders_dataset.csv", frame)
        assert failures.get("order_purchase_timestamp (unparseable date)") == 1
        # The row is kept; only the unparseable cell becomes NaT.
        assert len(frame) == 3
        assert pd.isna(frame["order_purchase_timestamp"].iloc[1])

    def test_blank_date_is_missing_not_a_failure(self):
        frame = _orders_dates_frame()
        failures = data_loader.apply_dtypes("olist_orders_dataset.csv", frame)
        # A blank / None value is genuinely missing, not a conversion failure.
        assert failures.get("order_approved_at (unparseable date)") is None
        assert pd.isna(frame["order_approved_at"].iloc[1])

    def test_numeric_coercion_and_failure_count(self):
        # customers has a numeric zip column and no date columns.
        frame = pd.DataFrame({"customer_zip_code_prefix": ["123", "x", None]})
        failures = data_loader.apply_dtypes("olist_customers_dataset.csv", frame)
        assert failures == {"customer_zip_code_prefix (unparseable number)": 1}
        assert str(frame["customer_zip_code_prefix"].dtype) == "Int64"
        assert frame["customer_zip_code_prefix"].iloc[0] == 123
        assert pd.isna(frame["customer_zip_code_prefix"].iloc[1])


class TestContractValidation:
    def test_missing_required_column_raises(self):
        frame = pd.DataFrame({"order_id": ["o1"]})
        with pytest.raises(data_loader.DataLoadError, match="missing required column"):
            data_loader.validate_contract("olist_orders_dataset.csv", frame)

    def test_duplicate_header_raises(self):
        cols = list(data_loader.config.REQUIRED_COLUMNS["olist_orders_dataset.csv"])
        frame = pd.DataFrame([[0] * (len(cols) + 1)], columns=cols + [cols[0]])
        with pytest.raises(data_loader.DataLoadError, match="duplicate column header"):
            data_loader.validate_contract("olist_orders_dataset.csv", frame)

    def test_required_columns_pass(self):
        cols = data_loader.config.REQUIRED_COLUMNS["olist_customers_dataset.csv"]
        frame = pd.DataFrame([["x"] * len(cols)], columns=cols)
        data_loader.validate_contract("olist_customers_dataset.csv", frame)  # no raise


class TestRawFileIntegrity:
    def test_find_raw_file_missing_raises(self):
        with pytest.raises(data_loader.DataLoadError, match="not found"):
            data_loader.find_raw_file("does_not_exist_dataset.csv")

    def test_sha256_is_stable_and_detects_change(self, tmp_path):
        path = tmp_path / "x.csv"
        path.write_text("a,b\n1,2\n", encoding="utf-8")
        first = data_loader.sha256_of(path)
        assert first == data_loader.sha256_of(path)
        path.write_text("a,b\n1,3\n", encoding="utf-8")
        assert data_loader.sha256_of(path) != first

    def test_verify_raw_unchanged(self, tmp_path, monkeypatch):
        path = tmp_path / "x.csv"
        path.write_text("a\n1\n", encoding="utf-8")
        monkeypatch.setattr(data_loader, "find_raw_file", lambda name: path)
        before = {"x.csv": data_loader.sha256_of(path)}
        assert data_loader.verify_raw_unchanged(before) == []
        path.write_text("a\n2\n", encoding="utf-8")
        assert data_loader.verify_raw_unchanged(before) == ["x.csv"]


# ==========================================================================
# Cleaning - data_cleaning
# ==========================================================================
def _orders_raw():
    return pd.DataFrame(
        {
            "order_id": ["o1", "o2", "o3", "o4"],
            "customer_id": ["c1", "c2", "c3", "c4"],
            "order_status": ["delivered", "delivered", "canceled", "delivered"],
            "order_purchase_timestamp": pd.to_datetime(
                ["2018-01-01 10:00", "2018-01-02 10:00", "2018-01-03 10:00", "2018-01-04 10:00"]
            ),
            "order_approved_at": pd.to_datetime(
                ["2018-01-01 11:00", "2018-01-02 11:00", None, "2017-12-31 09:00"]
            ),
            "order_delivered_carrier_date": pd.to_datetime(
                ["2018-01-02 10:00", "2018-01-03 10:00", None, "2018-01-05 10:00"]
            ),
            "order_delivered_customer_date": pd.to_datetime(
                ["2018-01-05 10:00", "2018-01-04 10:00", None, "2018-01-06 10:00"]
            ),
            "order_estimated_delivery_date": pd.to_datetime(
                ["2018-01-06 10:00", "2018-01-03 10:00", None, "2018-01-06 10:00"]
            ),
        }
    )


class TestCleanOrders:
    def setup_method(self):
        # o1 on time, o2 late, o3 never delivered, o4 impossible (approved before
        # purchase). Item/payment/review coverage: o1..o3 have items, o1..o2 pay.
        self.result = data_cleaning.clean_orders(
            _orders_raw(), {"o1", "o2", "o3"}, {"o1", "o2"}, {"o1", "o2"}
        )
        self.frame = self.result.frame

    def test_rows_never_dropped(self):
        assert self.result.input_rows == 4
        assert self.result.output_rows == 4
        assert self.result.excluded_rows == 0

    def test_delivery_delay_is_null_when_never_delivered(self):
        assert self.frame.loc[0, "delivery_delay_days"] == 4
        assert self.frame.loc[1, "delivery_delay_days"] == 2
        assert pd.isna(self.frame.loc[2, "delivery_delay_days"])

    def test_late_flag_is_tri_state(self):
        assert bool(self.frame.loc[0, "is_late_vs_estimate"]) is False
        assert bool(self.frame.loc[1, "is_late_vs_estimate"]) is True
        assert pd.isna(self.frame.loc[2, "is_late_vs_estimate"])

    def test_coverage_flags(self):
        assert self.frame["has_line_items"].tolist() == [True, True, True, False]
        assert self.frame["has_payments"].tolist() == [True, True, False, False]
        assert self.frame["has_reviews"].tolist() == [True, True, False, False]

    def test_impossible_sequence_is_flagged_and_quarantined(self):
        assert bool(self.frame.loc[3, "has_impossible_date_sequence"]) is True
        assert self.result.issues["impossible_date_sequence"] == 1
        assert self.result.quarantine is not None
        assert self.result.quarantine["order_id"].tolist() == ["o4"]

    def test_missing_delivery_date_flag(self):
        assert bool(self.frame.loc[2, "missing_delivery_date"]) is True
        assert bool(self.frame.loc[0, "missing_delivery_date"]) is False


class TestCleanReviews:
    def _raw(self):
        return pd.DataFrame(
            {
                "review_id": ["r1", "r2", "r3"],
                "order_id": ["o1", "o1", "o2"],
                "review_score": [5, 2, 4],
                "review_comment_title": [None, None, "ok"],
                "review_comment_message": ["old", None, "good"],
                "review_creation_date": pd.to_datetime(
                    ["2018-01-06", "2018-01-07", "2018-01-05"]
                ),
                "review_answer_timestamp": pd.to_datetime(
                    ["2018-01-06 12:00", "2018-01-07 12:00", "2018-01-05 12:00"]
                ),
            }
        )

    def test_collapses_to_latest_review_per_order(self):
        result = data_cleaning.clean_reviews(self._raw())
        frame = result.frame
        assert len(frame) == 2
        assert frame["order_id"].is_unique
        # o1 had two answers; the later one (score 2) wins.
        assert frame.set_index("order_id").loc["o1", "review_score"] == 2
        assert result.excluded_rows == 1
        assert result.issues["orders_with_conflicting_duplicate_scores"] == 1

    def test_provenance_and_missing_comments(self):
        frame = data_cleaning.clean_reviews(self._raw()).frame
        row = frame.set_index("order_id").loc["o1"]
        assert row["source_review_rows"] == 2
        assert row["distinct_review_ids"] == 2
        # Missing comments stay NULL, never become an empty string.
        assert pd.isna(frame.set_index("order_id").loc["o1", "review_comment_message"])


class TestCleanFacts:
    def test_order_items_flag_invalid_price_and_repeated_product(self):
        raw = pd.DataFrame(
            {
                "order_id": ["o1", "o1", "o2"],
                "order_item_id": [1, 2, 1],
                "product_id": ["p1", "p1", "p2"],
                "seller_id": ["s1", "s1", "s2"],
                "shipping_limit_date": ["2018-01-03"] * 3,
                "price": [10.0, 0.0, 5.0],
                "freight_value": [1.0, 1.0, -1.0],
            }
        )
        result = data_cleaning.clean_order_items(raw)
        assert len(result.frame) == 3  # retained, only flagged
        assert result.frame["is_valid_price"].tolist() == [True, False, True]
        assert result.frame["is_valid_freight"].tolist() == [True, True, False]
        assert result.issues["repeated_product_within_order"] == 1
        assert result.issues["non_positive_price"] == 1
        assert result.issues["negative_freight"] == 1

    def test_payments_flag_non_positive(self):
        raw = pd.DataFrame(
            {
                "order_id": ["o1", "o1", "o2"],
                "payment_sequential": [1, 2, 1],
                "payment_type": ["credit_card", "voucher", "boleto"],
                "payment_installments": [2, 1, 0],
                "payment_value": [10.0, -5.0, 0.0],
            }
        )
        result = data_cleaning.clean_payments(raw)
        assert result.frame["is_valid_payment_value"].tolist() == [True, False, False]
        assert result.frame["is_valid_installments"].tolist() == [True, True, False]
        assert result.issues["non_positive_payment_value"] == 2

    def test_customers_identity_is_one_row_per_person(self):
        raw = pd.DataFrame(
            {
                "customer_id": ["c1", "c2", "c3"],
                "customer_unique_id": ["u1", "u1", "u2"],
                "customer_zip_code_prefix": [1001, 1001, 1002],
                "customer_city": ["sao paulo ", "sao paulo ", "rio"],
                "customer_state": ["SP", "SP", "RJ"],
            }
        )
        primary, identity = data_cleaning.clean_customers(raw)
        assert len(primary.frame) == 3
        assert len(identity.frame) == 2
        row = identity.frame.set_index("customer_unique_id").loc["u1"]
        assert int(row["placement_count"]) == 2
        # city is stripped
        assert primary.frame.loc[0, "customer_city"] == "sao paulo"

    def test_products_unknown_category_and_tri_state_shipping(self):
        translation = pd.DataFrame(
            {
                "product_category_name": ["brinquedos"],
                "product_category_name_english": ["toys"],
            }
        )
        category_lookup = data_cleaning.clean_categories(translation, {"brinquedos"}).frame
        raw = pd.DataFrame(
            {
                "product_id": ["p1", "p2", "p3"],
                "product_category_name": [None, "brinquedos", "brinquedos"],
                "product_name_lenght": [10, 20, 30],
                "product_description_lenght": [1, 2, 3],
                "product_photos_qty": [1, 1, 1],
                "product_weight_g": [None, 1000.0, 0.0],
                "product_length_cm": [10.0, 10.0, 10.0],
                "product_height_cm": [10.0, 10.0, 10.0],
                "product_width_cm": [10.0, 10.0, 10.0],
            }
        )
        result = data_cleaning.clean_products(raw, category_lookup)
        frame = result.frame
        assert frame.loc[0, "product_category_name"] == "unknown"
        assert bool(frame.loc[0, "category_was_missing"]) is True
        # p1 weight missing -> unknown; p2 valid; p3 weight 0 -> invalid.
        assert pd.isna(frame.loc[0, "is_shipping_valid"])
        assert bool(frame.loc[1, "is_shipping_valid"]) is True
        assert bool(frame.loc[2, "is_shipping_valid"]) is False
        assert result.issues["missing_category_name"] == 1

    def test_geolocation_dedup_quarantine_and_placeholders(self):
        raw = pd.DataFrame(
            {
                "geolocation_zip_code_prefix": [1001, 1001, 1002, 9999],
                "geolocation_lat": [-23.5, -23.5, -22.9, 88.0],
                "geolocation_lng": [-46.6, -46.6, -43.2, 12.0],
                "geolocation_city": ["sp", "sp", "rio", "x"],
                "geolocation_state": ["SP", "SP", "RJ", "XX"],
            }
        )
        result = data_cleaning.clean_geolocation(raw, referenced_zips={1001, 1002, 7777})
        frame = result.frame
        assert len(frame) == 4  # 1001, 1002 + placeholder 7777 + unusable 9999
        assert set(frame["geolocation_zip_code_prefix"]) == {1001, 1002, 7777, 9999}
        assert result.issues["exact_duplicate_rows"] == 1
        assert result.issues["coordinates_outside_brazil"] == 1
        row9999 = frame.set_index("geolocation_zip_code_prefix").loc[9999]
        assert pd.isna(row9999["geolocation_lat"])
        assert bool(row9999["is_valid_coordinate"]) is False
        row7777 = frame.set_index("geolocation_zip_code_prefix").loc[7777]
        assert int(row7777["source_row_count"]) == 0
        assert bool(row7777["is_valid_coordinate"]) is False
        assert result.quarantine is not None and len(result.quarantine) == 1


class TestCleaningRepeatability:
    """Pure cleaners must be deterministic: same input -> identical output."""

    def test_reviews_cleaning_is_deterministic(self):
        raw = TestCleanReviews()._raw()
        a = data_cleaning.clean_reviews(raw).frame
        b = data_cleaning.clean_reviews(raw).frame
        pd.testing.assert_frame_equal(a, b)

    def test_orders_cleaning_is_deterministic(self):
        raw = _orders_raw()
        a = data_cleaning.clean_orders(raw, {"o1"}, {"o1"}, {"o1"}).frame
        b = data_cleaning.clean_orders(raw, {"o1"}, {"o1"}, {"o1"}).frame
        pd.testing.assert_frame_equal(a, b)


class TestEmptyInputs:
    def test_clean_orders_empty(self):
        frame = data_cleaning.clean_orders(
            _orders_raw().iloc[0:0], set(), set(), set()
        ).frame
        assert frame.empty

    def test_clean_reviews_empty(self):
        raw = TestCleanReviews()._raw().iloc[0:0]
        assert data_cleaning.clean_reviews(raw).frame.empty

    def test_clean_geolocation_empty(self):
        raw = pd.DataFrame(
            {
                "geolocation_zip_code_prefix": pd.Series([], dtype="Int64"),
                "geolocation_lat": pd.Series([], dtype="Float64"),
                "geolocation_lng": pd.Series([], dtype="Float64"),
                "geolocation_city": pd.Series([], dtype="object"),
                "geolocation_state": pd.Series([], dtype="object"),
            }
        )
        assert data_cleaning.clean_geolocation(raw).frame.empty

    def test_clean_items_and_payments_empty(self):
        items = pd.DataFrame(
            {c: pd.Series([], dtype="float64") for c in
             ["price", "freight_value"]}
        )
        items["order_id"] = pd.Series([], dtype="object")
        items["order_item_id"] = pd.Series([], dtype="Int64")
        items["product_id"] = pd.Series([], dtype="object")
        items["seller_id"] = pd.Series([], dtype="object")
        items["shipping_limit_date"] = pd.Series([], dtype="object")
        assert data_cleaning.clean_order_items(items).frame.empty

        payments = pd.DataFrame(
            {
                "order_id": pd.Series([], dtype="object"),
                "payment_sequential": pd.Series([], dtype="Int64"),
                "payment_type": pd.Series([], dtype="object"),
                "payment_installments": pd.Series([], dtype="Int64"),
                "payment_value": pd.Series([], dtype="float64"),
            }
        )
        assert data_cleaning.clean_payments(payments).frame.empty


# ==========================================================================
# Transform - build_order_summary
# ==========================================================================
def _transform_inputs():
    orders = pd.DataFrame(
        {
            "order_id": ["o1", "o2"],
            "order_status": ["delivered", "canceled"],
            "is_revenue_status": [True, False],
            "purchase_date": pd.to_datetime(["2018-01-01", "2018-01-02"]),
            "delivery_delay_days": [4, None],
            "is_late_vs_estimate": [False, None],
        }
    )
    items = pd.DataFrame(
        {
            "order_id": ["o1", "o1"],
            "order_item_id": [1, 2],
            "product_id": ["p1", "p2"],
            "price": [100.0, 50.0],
            "freight_value": [10.0, 5.0],
        }
    )
    payments = pd.DataFrame(
        {"order_id": ["o1"], "payment_sequential": [1], "payment_value": [120.0]}
    )
    reviews = pd.DataFrame({"order_id": ["o1"], "review_score": [5]})
    return orders, items, payments, reviews


class TestBuildOrderSummary:
    def test_one_row_per_order_no_fanout(self):
        summary = transform.build_order_summary(*_transform_inputs())
        assert len(summary) == 2
        assert summary["order_id"].is_unique
        assert summary["order_id"].tolist() == ["o1", "o2"]

    def test_aggregates_and_null_for_empty_order(self):
        summary = transform.build_order_summary(*_transform_inputs()).set_index("order_id")
        o1 = summary.loc["o1"]
        assert int(o1["n_items"]) == 2
        assert int(o1["n_distinct_products"]) == 2
        assert float(o1["item_revenue"]) == pytest.approx(165.0)  # 100+10 + 50+5
        assert float(o1["payment_collected"]) == pytest.approx(120.0)
        assert int(o1["n_reviews"]) == 1
        assert float(o1["avg_review_score"]) == pytest.approx(5.0)

        o2 = summary.loc["o2"]
        assert int(o2["n_items"]) == 0
        # An order with no items keeps a NULL revenue measurement, not 0.
        assert pd.isna(o2["item_revenue"])
        assert pd.isna(o2["payment_collected"])
        assert pd.isna(o2["payment_minus_item_revenue"])

    def test_payment_minus_revenue_is_signed(self):
        summary = transform.build_order_summary(*_transform_inputs()).set_index("order_id")
        assert float(summary.loc["o1", "payment_minus_item_revenue"]) == pytest.approx(-45.0)

    def test_empty_orders(self):
        o, i, p, r = _transform_inputs()
        assert transform.build_order_summary(o.iloc[0:0], i.iloc[0:0], p.iloc[0:0], r.iloc[0:0]).empty


# ==========================================================================
# Validation gates
# ==========================================================================
class TestValidationChecks:
    def test_primary_key_detects_nulls_and_duplicates(self):
        report = validation.ValidationReport()
        frame = pd.DataFrame({"k": [1, 2, 2, None]})
        validation.check_primary_key(report, "t", frame, ["k"])
        by_id = {c.check_id: c for c in report.checks}
        assert by_id["t.pk_not_null"].passed is False
        assert by_id["t.pk_unique"].passed is False
        assert report.passed is False

    def test_primary_key_missing_column_fails(self):
        report = validation.ValidationReport()
        validation.check_primary_key(report, "t", pd.DataFrame({"a": [1]}), ["k"])
        assert report.checks[0].passed is False

    def test_foreign_key_detects_orphans(self):
        report = validation.ValidationReport()
        child = pd.DataFrame({"fk": [1, 2, 99, None]})
        parent = pd.DataFrame({"pk": [1, 2, 3]})
        validation.check_foreign_key(report, "c", child, "fk", parent, "pk")
        check = report.checks[0]
        assert check.passed is False
        assert "1 orphan" in check.observed

    def test_foreign_key_clean_passes(self):
        report = validation.ValidationReport()
        child = pd.DataFrame({"fk": [1, 2, None]})
        parent = pd.DataFrame({"pk": [1, 2]})
        validation.check_foreign_key(report, "c", child, "fk", parent, "pk")
        assert report.checks[0].passed is True

    def test_check_equals_tolerance(self):
        report = validation.ValidationReport()
        validation.check_equals(report, "eq", "close", 1.0, 1.004, tolerance=0.01)
        validation.check_equals(report, "eq2", "far", 1.0, 1.5, tolerance=0.01)
        assert report.checks[0].passed is True
        assert report.checks[1].passed is False

    def test_check_value_range(self):
        report = validation.ValidationReport()
        validation.check_value_range(report, "vr", pd.DataFrame({"x": [1, 2, 10]}), "x", 0, 10)
        validation.check_value_range(report, "vr2", pd.DataFrame({"x": [1, 99]}), "x", 0, 10)
        assert report.checks[0].passed is True
        assert report.checks[1].passed is False

    def test_check_no_null(self):
        report = validation.ValidationReport()
        validation.check_no_null(report, "nn", pd.DataFrame({"x": [1, None]}), ["x"])
        assert report.checks[0].passed is False

    def test_check_row_count(self):
        report = validation.ValidationReport()
        validation.check_row_count(report, "rc", 5, 5)
        validation.check_row_count(report, "rc2", 5, 6)
        assert report.checks[0].passed is True
        assert report.checks[1].passed is False

    def test_warning_only_report_still_passes(self):
        report = validation.ValidationReport()
        validation.check_equals(report, "warn", "warning", 1, 2, severity=validation.WARNING)
        assert report.passed is True
        assert len(report.warnings) == 1
