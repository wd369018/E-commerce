"""Validate every analytical KPI against an independent Python calculation.

Nothing here reads a SQL view to produce the "expected" value: each test
recomputes the KPI from the cleaned CSVs with pandas, using a different code
path than the SQL, then compares. Counts must match exactly; rounded floats
are compared with an explicit tolerance.

Money rules asserted throughout:
  * merchandise sales = sum of item ``price`` (freight excluded)
  * freight is reported separately and never added to sales
  * payment values are aggregated per order before any join
  * repeat customers use ``customer_unique_id``
"""

from __future__ import annotations

import math

import pandas as pd

# ---------------------------------------------------------------- tolerances
COUNT = 0          # integers must match exactly
MONEY = 0.01       # currency rounded to cents
PCT = 0.01         # percentages rounded to 2 dp
MEAN3 = 0.0011     # averages rounded to 3 dp
MEAN2 = 0.011      # averages rounded to 2 dp
MEAN4 = 0.00011    # averages rounded to 4 dp
SEGMENT_MONEY = 0.05  # per-customer rounding before summation


def _num(value):
    """Normalise SQL NULL / NaN / pd.NA to None, everything else to float."""
    if value is None or value is pd.NA:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (int, float)):
        return float(value)
    return float(value)


def _assert_same(actual, expected, tol, label):
    a, e = _num(actual), _num(expected)
    if a is None or e is None:
        assert a is None and e is None, f"{label}: SQL={a!r} python={e!r}"
        return
    assert abs(a - e) <= tol, f"{label}: SQL={a!r} python={e!r} diff={abs(a - e)}"


def _to01(series: pd.Series) -> pd.Series:
    """CSV True/False/blank -> 1/0/None."""
    return series.map(
        lambda v: 1
        if v in (True, "True", 1)
        else (0 if v in (False, "False", 0) else None)
    )


# ==========================================================================
# 1. Monthly merchandise sales
# ==========================================================================
def test_monthly_merchandise_sales(run_query, csv_frames):
    sql = run_query("monthly_merchandise_sales").set_index("sales_month")

    items = csv_frames["fact_order_item"].merge(
        csv_frames["dim_order"][
            ["order_id", "order_purchase_timestamp", "order_status"]
        ],
        on="order_id",
        validate="many_to_one",
    )
    items["sales_month"] = items["order_purchase_timestamp"].str.slice(0, 7)
    py = items.groupby("sales_month").agg(
        orders=("order_id", "nunique"),
        item_lines=("order_id", "size"),
        merchandise_sales=("price", "sum"),
        freight_charges=("freight_value", "sum"),
    )
    revenue = items[items["order_status"].isin(["delivered", "shipped"])]
    py_rev = revenue.groupby("sales_month")["price"].sum()

    assert set(sql.index) == set(py.index), "month coverage differs"

    for month, expected in py.iterrows():
        row = sql.loc[month]
        assert int(row["orders"]) == int(expected["orders"]), month
        assert int(row["item_lines"]) == int(expected["item_lines"]), month
        _assert_same(
            row["merchandise_sales"], expected["merchandise_sales"], MONEY,
            f"{month} merchandise_sales",
        )
        _assert_same(
            row["freight_charges"], expected["freight_charges"], MONEY,
            f"{month} freight_charges",
        )
        _assert_same(
            row["merchandise_sales_revenue_status"], py_rev.loc[month], MONEY,
            f"{month} merchandise_sales_revenue_status",
        )
        # gross = sales + freight, computed from the two columns, never conflated
        _assert_same(
            row["gross_item_value"],
            float(expected["merchandise_sales"]) + float(expected["freight_charges"]),
            0.05,
            f"{month} gross_item_value",
        )


def test_month_over_month_growth_is_derived_not_stored(run_query):
    frame = run_query("monthly_merchandise_sales").sort_values("sales_month").reset_index(drop=True)
    values = frame["merchandise_sales"].astype(float)
    for i in range(1, len(frame)):
        expected = 100.0 * (values[i] - values[i - 1]) / values[i - 1]
        _assert_same(frame.loc[i, "mom_growth_pct"], expected, PCT,
                     f"mom growth {frame.loc[i, 'sales_month']}")


# ==========================================================================
# 2. Monthly order volume
# ==========================================================================
def test_monthly_order_volume(run_query, csv_frames):
    sql = run_query("monthly_order_volume")

    orders = csv_frames["dim_order"].copy()
    orders["sales_month"] = orders["order_purchase_timestamp"].str.slice(0, 7)
    py = orders.groupby(["sales_month", "order_status"]).size().rename("orders")
    month_totals = orders.groupby("sales_month").size()

    assert len(sql) == len(py), "row count differs"

    for row in sql.itertuples(index=False):
        key = (row.sales_month, row.order_status)
        assert key in py.index, key
        assert int(row.orders) == int(py.loc[key]), key
        expected_pct = 100.0 * int(py.loc[key]) / int(month_totals.loc[row.sales_month])
        _assert_same(row.pct_of_month, expected_pct, PCT, f"{key} pct")

    # every month's statuses sum back to the month's order count
    totals = sql.groupby("sales_month")["orders"].sum()
    for month, n in month_totals.items():
        assert int(totals.loc[month]) == int(n)


# ==========================================================================
# 3. Order status distribution
# ==========================================================================
def test_order_status_distribution(run_query, csv_frames):
    sql = run_query("order_status_distribution").set_index("order_status")

    by_order = csv_frames["fact_order_item"].groupby("order_id").agg(
        merchandise_sales=("price", "sum"),
        freight_charges=("freight_value", "sum"),
    )
    orders = csv_frames["dim_order"][["order_id", "order_status"]].merge(
        csv_frames["fact_order_summary"][["order_id", "payment_collected", "n_items"]],
        on="order_id",
        validate="one_to_one",
    ).merge(by_order, on="order_id", how="left", validate="one_to_one")

    py = orders.groupby("order_status").agg(
        orders=("order_id", "size"),
        merchandise_sales=("merchandise_sales", "sum"),
        freight_charges=("freight_charges", "sum"),
        payment_collected=("payment_collected", "sum"),
        avg_items_per_order=("n_items", "mean"),
    )
    assert set(sql.index) == set(py.index)

    grand_total = len(orders)
    for status, expected in py.iterrows():
        row = sql.loc[status]
        assert int(row["orders"]) == int(expected["orders"]), status
        _assert_same(row["pct_of_orders"], 100.0 * int(expected["orders"]) / grand_total,
                     PCT, f"{status} pct")
        _assert_same(row["merchandise_sales"], expected["merchandise_sales"], MONEY,
                     f"{status} merchandise_sales")
        _assert_same(row["freight_charges"], expected["freight_charges"], MONEY,
                     f"{status} freight_charges")
        _assert_same(row["payment_collected"], expected["payment_collected"], MONEY,
                     f"{status} payment_collected")
        _assert_same(row["avg_items_per_order"], expected["avg_items_per_order"], MEAN2,
                     f"{status} avg_items")

    # the distributions sum back to the source, and sales exclude freight
    assert int(sql["orders"].sum()) == grand_total
    _assert_same(sql["merchandise_sales"].sum(),
                 csv_frames["fact_order_item"]["price"].sum(), 0.5,
                 "status-level sales total")
    _assert_same(sql["freight_charges"].sum(),
                 csv_frames["fact_order_item"]["freight_value"].sum(), 0.5,
                 "status-level freight total")


# ==========================================================================
# 4. Product category performance
# ==========================================================================
def test_category_performance(run_query, csv_frames):
    sql = run_query("category_performance").set_index("product_category_name")

    items = csv_frames["fact_order_item"].merge(
        csv_frames["dim_product"][["product_id", "product_category_name"]],
        on="product_id",
        validate="many_to_one",
    )
    py_units = items.groupby("product_category_name").agg(
        units=("order_id", "size"),
        orders=("order_id", "nunique"),
        merchandise_sales=("price", "sum"),
        freight_charges=("freight_value", "sum"),
    )

    # (category, order) grain, then attach the one-row-per-order review
    cat_order = items.groupby(["product_category_name", "order_id"]).agg(
        units=("order_id", "size"), price=("price", "sum")
    ).reset_index()
    reviews = csv_frames["bridge_review"][["order_id", "review_score"]]
    cat_order = cat_order.merge(reviews, on="order_id", how="left")
    py_review = cat_order.groupby("product_category_name")["review_score"].mean()

    assert set(sql.index) == set(py_units.index)

    for category, expected in py_units.iterrows():
        row = sql.loc[category]
        assert int(row["units"]) == int(expected["units"]), category
        assert int(row["orders"]) == int(expected["orders"]), category
        _assert_same(row["merchandise_sales"], expected["merchandise_sales"], MONEY,
                     f"{category} sales")
        _assert_same(row["freight_charges"], expected["freight_charges"], MONEY,
                     f"{category} freight")
        _assert_same(row["avg_review_score"], py_review.loc[category], MEAN3,
                     f"{category} avg_review")
        assert int(row["reviewed_orders"]) == int(
            cat_order.loc[cat_order["product_category_name"] == category, "review_score"]
            .notna().sum()
        ), category


def test_category_sales_reconcile_to_source(run_query, csv_frames):
    sql = run_query("category_performance")
    _assert_same(sql["merchandise_sales"].sum(),
                 csv_frames["fact_order_item"]["price"].sum(), 0.5,
                 "all-category sales total")
    assert len(sql) == len(csv_frames["dim_product_category"])
    # the 'unknown' category must be present, never silently dropped
    assert (sql["product_category_name"] == "unknown").any()


def test_category_english_names_come_from_the_category_dimension(run_query, csv_frames):
    sql = run_query("category_performance").set_index("product_category_name")
    lookup = csv_frames["dim_product_category"].set_index("product_category_name")[
        "product_category_name_english"
    ]
    for name, row in sql.iterrows():
        expected = lookup.loc[name]
        actual = row["product_category_name_english"]
        if pd.isna(expected):
            assert pd.isna(actual), f"{name} should have NULL english name"
        else:
            assert actual == expected, name


# ==========================================================================
# 5. State-wise customer and order distribution
# ==========================================================================
def test_state_customer_order_distribution(run_query, csv_frames):
    sql = run_query("state_customer_order_distribution").set_index("customer_state")

    by_order = csv_frames["fact_order_item"].groupby("order_id").agg(
        merchandise_sales=("price", "sum"),
        freight_charges=("freight_value", "sum"),
    )
    orders = (
        csv_frames["dim_order"]
        .merge(
            csv_frames["fact_order_summary"][["order_id", "payment_collected"]],
            on="order_id",
            validate="one_to_one",
        )
        .merge(by_order, on="order_id", how="left", validate="one_to_one")
        .merge(
            csv_frames["dim_customer"][["customer_id", "customer_unique_id", "customer_state"]],
            on="customer_id",
            validate="one_to_one",
        )
    )

    py = orders.groupby("customer_state").agg(
        customers=("customer_unique_id", "nunique"),
        order_placements=("customer_id", "nunique"),
        orders=("order_id", "nunique"),
        merchandise_sales=("merchandise_sales", "sum"),
        freight_charges=("freight_charges", "sum"),
        payment_collected=("payment_collected", "sum"),
    )

    assert set(sql.index) == set(py.index)
    total_orders = len(orders)
    for state, expected in py.iterrows():
        row = sql.loc[state]
        for column in ("customers", "order_placements", "orders"):
            assert int(row[column]) == int(expected[column]), f"{state} {column}"
        _assert_same(row["pct_of_orders"], 100.0 * int(expected["orders"]) / total_orders,
                     PCT, f"{state} pct")
        _assert_same(row["merchandise_sales"], expected["merchandise_sales"], MONEY,
                     f"{state} sales")
        _assert_same(row["freight_charges"], expected["freight_charges"], MONEY,
                     f"{state} freight")
        _assert_same(row["payment_collected"], expected["payment_collected"], MONEY,
                     f"{state} cash")

    assert int(sql["orders"].sum()) == total_orders
    unique_customers = csv_frames["dim_customer"]["customer_unique_id"].nunique()
    # A repeat customer who moved between states is counted once per state, so
    # the per-state counts can exceed the global distinct count.
    assert int(sql["customers"].sum()) >= unique_customers
    assert int(sql["customers"].sum()) <= unique_customers + 100


# ==========================================================================
# 6. Customer repeat purchases
# ==========================================================================
def _python_customer_orders(csv_frames):
    by_order = (
        csv_frames["fact_order_item"].groupby("order_id")["price"].sum().round(2).rename("spend")
    )
    orders = csv_frames["dim_order"].merge(
        csv_frames["dim_customer"][["customer_id", "customer_unique_id"]],
        on="customer_id",
        validate="one_to_one",
    ).merge(by_order, on="order_id", how="left", validate="one_to_one")
    per_customer = orders.groupby("customer_unique_id").agg(
        orders=("order_id", "nunique"),
        spend=("spend", "sum"),
    )
    per_customer["spend"] = per_customer["spend"].round(2)
    return per_customer


def _segment(n: int) -> str:
    if n == 1:
        return "1 order"
    if n == 2:
        return "2 orders"
    if n == 3:
        return "3 orders"
    return "4+ orders"


def test_repeat_customer_summary(run_query, csv_frames):
    sql = run_query("repeat_customer_summary").set_index("segment")
    per_customer = _python_customer_orders(csv_frames)

    expected = per_customer.copy()
    expected["segment"] = expected["orders"].map(_segment)
    py = expected.groupby("segment").agg(
        customers=("orders", "size"),
        total_orders=("orders", "sum"),
        avg_orders=("orders", "mean"),
        merchandise_spend=("spend", "sum"),
    )

    assert set(sql.index) == set(py.index)
    for segment, row_expected in py.iterrows():
        row = sql.loc[segment]
        assert int(row["customers"]) == int(row_expected["customers"]), segment
        assert int(row["total_orders"]) == int(row_expected["total_orders"]), segment
        _assert_same(row["avg_orders"], row_expected["avg_orders"], MEAN3, f"{segment} avg")
        _assert_same(row["merchandise_spend"], row_expected["merchandise_spend"],
                     SEGMENT_MONEY, f"{segment} spend")
        _assert_same(
            row["pct_customers"],
            100.0 * int(row_expected["customers"]) / len(per_customer),
            PCT, f"{segment} pct",
        )

    assert int(sql["customers"].sum()) == len(per_customer)
    assert int(sql["total_orders"].sum()) == len(csv_frames["dim_order"])


def test_repeat_customer_detail_uses_unique_id(run_query, csv_frames):
    sql = run_query("repeat_customer_detail")
    unique_ids = set(csv_frames["dim_customer_identity"]["customer_unique_id"])
    assert len(sql) == 20
    assert set(sql["customer_unique_id"]) <= unique_ids

    per_customer = _python_customer_orders(csv_frames)
    for row in sql.itertuples(index=False):
        expected = per_customer.loc[row.customer_unique_id]
        assert int(row.orders) == int(expected["orders"])
        _assert_same(row.merchandise_spend, expected["spend"], 0.02,
                     f"{row.customer_unique_id} spend")


def test_single_order_customers_dominate_and_are_labelled_correctly(run_query, csv_frames):
    per_customer = _python_customer_orders(csv_frames)
    single = int((per_customer["orders"] == 1).sum())
    sql = run_query("repeat_customer_summary").set_index("segment")
    assert int(sql.loc["1 order", "customers"]) == single


# ==========================================================================
# 7. Payment method distribution
# ==========================================================================
def test_payment_method_distribution(run_query, csv_frames):
    sql = run_query("payment_method_distribution").set_index("payment_type")
    payments = csv_frames["bridge_payment"].copy()
    payments["payment_value"] = pd.to_numeric(payments["payment_value"], errors="coerce")

    py = payments.groupby("payment_type").agg(
        payments=("order_id", "size"),
        orders_touched=("order_id", "nunique"),
        payment_value=("payment_value", "sum"),
        avg_payment_value=("payment_value", "mean"),
    )
    py["non_positive_values"] = (
        payments.assign(bad=payments["payment_value"] <= 0)
        .groupby("payment_type")["bad"].sum()
    )

    assert set(sql.index) == set(py.index)
    total_value = payments["payment_value"].sum()
    for method, expected in py.iterrows():
        row = sql.loc[method]
        assert int(row["payments"]) == int(expected["payments"]), method
        assert int(row["orders_touched"]) == int(expected["orders_touched"]), method
        _assert_same(row["payment_value"], expected["payment_value"], MONEY, f"{method} value")
        _assert_same(row["avg_payment_value"], expected["avg_payment_value"], MEAN2,
                     f"{method} avg")
        _assert_same(row["pct_of_value"], 100.0 * float(expected["payment_value"]) / total_value,
                     PCT, f"{method} pct")
        assert int(row["non_positive_values"]) == int(expected["non_positive_values"]), method

    # payment rows are a superset of orders: an order may carry several methods
    assert int(sql["payments"].sum()) == len(payments)
    assert int(sql["orders_touched"].sum()) >= csv_frames["fact_order_summary"]["order_id"].nunique()


def test_payment_value_never_equals_merchandise_sales(run_query, csv_frames):
    """Cash collected is not sales; the two must be reported separately."""
    sql = run_query("payment_method_distribution")
    payments_total = float(csv_frames["bridge_payment"]["payment_value"].sum())
    sales_total = float(csv_frames["fact_order_item"]["price"].sum())
    _assert_same(sql["payment_value"].sum(), payments_total, 0.5, "payment total")
    assert abs(payments_total - sales_total) > 1_000_000


# ==========================================================================
# 8. Installment analysis
# ==========================================================================
def _installment_bucket(value) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "unknown"
    if value <= 1:
        return "1 (full payment)"
    if value <= 3:
        return "2-3"
    if value <= 6:
        return "4-6"
    if value <= 12:
        return "7-12"
    return "13+"


def test_installment_analysis(run_query, csv_frames):
    sql = run_query("installment_analysis").set_index("installment_bucket")
    payments = csv_frames["bridge_payment"].copy()
    payments["payment_value"] = pd.to_numeric(payments["payment_value"], errors="coerce")
    payments["payment_installments"] = pd.to_numeric(
        payments["payment_installments"], errors="coerce"
    )
    payments["bucket"] = payments["payment_installments"].map(_installment_bucket)

    py = payments.groupby("bucket").agg(
        payments=("order_id", "size"),
        orders_touched=("order_id", "nunique"),
        avg_payment_value=("payment_value", "mean"),
        total_payment_value=("payment_value", "sum"),
        avg_installments=("payment_installments", "mean"),
    )

    assert set(sql.index) == set(py.index)
    for bucket, expected in py.iterrows():
        row = sql.loc[bucket]
        assert int(row["payments"]) == int(expected["payments"]), bucket
        assert int(row["orders_touched"]) == int(expected["orders_touched"]), bucket
        _assert_same(row["total_payment_value"], expected["total_payment_value"], MONEY,
                     f"{bucket} total")
        _assert_same(row["avg_payment_value"], expected["avg_payment_value"], MEAN2,
                     f"{bucket} avg value")
        if pd.isna(expected["avg_installments"]):
            assert pd.isna(row["avg_installments"]), bucket
        else:
            _assert_same(row["avg_installments"], expected["avg_installments"], MEAN2,
                         f"{bucket} avg installments")

    assert int(sql["payments"].sum()) == len(payments)
    # buckets are ordered: 1-installment bucket first, 13+ last
    ordered = sql.reset_index().sort_values("bucket_order")["installment_bucket"].tolist()
    assert ordered[0] == "1 (full payment)"
    assert ordered[-1] == "13+"


# ==========================================================================
# 9. Delivery performance
# ==========================================================================
def test_delivery_performance_monthly(run_query, csv_frames):
    sql = run_query("delivery_performance_monthly").set_index("sales_month")

    orders = csv_frames["dim_order"].copy()
    orders["sales_month"] = orders["order_purchase_timestamp"].str.slice(0, 7)
    orders["late_flag"] = _to01(orders["is_late_vs_estimate"])
    orders["delivery_delay_days"] = pd.to_numeric(
        orders["delivery_delay_days"], errors="coerce"
    )
    orders["delivered_ts"] = pd.to_datetime(orders["order_delivered_customer_date"],
                                            errors="coerce")
    orders["estimated_ts"] = pd.to_datetime(orders["order_estimated_delivery_date"],
                                            errors="coerce")

    eligible = orders[orders["delivered_ts"].notna()].copy()
    eligible["vs_estimate"] = (
        eligible["delivered_ts"] - eligible["estimated_ts"]
    ).dt.total_seconds() / 86_400

    py = eligible.groupby("sales_month").agg(
        delivered_orders=("order_id", "size"),
        avg_days_purchase_to_delivery=("delivery_delay_days", "mean"),
        avg_days_vs_estimate=("vs_estimate", "mean"),
        max_days_purchase_to_delivery=("delivery_delay_days", "max"),
    )
    py["on_time_orders"] = eligible[eligible["late_flag"] == 0].groupby("sales_month").size()
    py["late_orders"] = eligible[eligible["late_flag"] == 1].groupby("sales_month").size()
    py = py.fillna({"on_time_orders": 0, "late_orders": 0})

    assert set(sql.index) == set(py.index)
    for month, expected in py.iterrows():
        row = sql.loc[month]
        assert int(row["delivered_orders"]) == int(expected["delivered_orders"]), month
        assert int(row["on_time_orders"]) == int(expected["on_time_orders"]), month
        assert int(row["late_orders"]) == int(expected["late_orders"]), month
        _assert_same(row["avg_days_purchase_to_delivery"],
                     expected["avg_days_purchase_to_delivery"], MEAN2, f"{month} avg days")
        _assert_same(row["avg_days_vs_estimate"], expected["avg_days_vs_estimate"], MEAN2,
                     f"{month} vs estimate")
        on_time_pct = 100.0 * int(expected["on_time_orders"]) / int(expected["delivered_orders"])
        _assert_same(row["on_time_pct"], on_time_pct, PCT, f"{month} on time %")

    # eligible population: orders without a delivery date are excluded everywhere
    assert int(sql["delivered_orders"].sum()) == len(eligible)
    excluded = int(orders["delivered_ts"].isna().sum())
    assert excluded > 0, "expected some orders without a delivery date"


def test_delivery_summary_reports_its_exclusions(run_query, csv_frames):
    sql = run_query("delivery_summary").set_index("order_status")
    orders = csv_frames["dim_order"].copy()
    orders["late_flag"] = _to01(orders["is_late_vs_estimate"])

    py = orders.groupby("order_status").agg(
        orders=("order_id", "size"),
        excluded_no_delivery_date=(
            "order_delivered_customer_date",
            lambda s: int(s.isna().sum()),
        ),
        eligible_delivered=(
            "order_delivered_customer_date",
            lambda s: int(s.notna().sum()),
        ),
    )
    py["on_time_orders"] = orders[orders["late_flag"] == 0].groupby("order_status").size()
    py["late_orders"] = orders[orders["late_flag"] == 1].groupby("order_status").size()
    py = py.fillna({"on_time_orders": 0, "late_orders": 0})

    assert set(sql.index) == set(py.index)
    for status, expected in py.iterrows():
        row = sql.loc[status]
        assert int(row["orders"]) == int(expected["orders"]), status
        assert int(row["eligible_delivered"]) == int(expected["eligible_delivered"]), status
        assert int(row["excluded_no_delivery_date"]) == int(
            expected["excluded_no_delivery_date"]
        ), status
        assert int(row["on_time_orders"]) == int(expected["on_time_orders"]), status
        assert int(row["late_orders"]) == int(expected["late_orders"]), status

    assert int(sql["orders"].sum()) == len(orders)
    assert int(sql["eligible_delivered"].sum()) == int(sql["orders"].sum()) - int(
        sql["excluded_no_delivery_date"].sum()
    )


def test_on_time_plus_late_plus_unknown_equals_all_orders(run_query, csv_frames):
    """The three delivery outcomes partition the population exactly once."""
    total = len(csv_frames["dim_order"])
    summary = run_query("delivery_summary")
    on_time = int(summary["on_time_orders"].sum())
    late = int(summary["late_orders"].sum())
    unknown = int(summary["excluded_no_delivery_date"].sum())
    # a delivered order with no estimate is also 'unknown' for the late flag
    orders = csv_frames["dim_order"]
    both_dates = orders[
        orders["order_delivered_customer_date"].notna()
        & orders["order_estimated_delivery_date"].notna()
    ]
    known = len(both_dates)
    assert on_time + late == known
    assert known + (total - known) == total


# ==========================================================================
# 10. Customer review scores
# ==========================================================================
def test_review_score_distribution(run_query, csv_frames):
    sql = run_query("review_score_distribution").set_index("review_score")
    reviews = csv_frames["bridge_review"]
    py = reviews.groupby("review_score").size()

    assert set(int(i) for i in sql.index) == set(int(i) for i in py.index)
    for score, n in py.items():
        row = sql.loc[score]
        assert int(row["orders"]) == int(n), score
        _assert_same(row["pct_of_orders"], 100.0 * int(n) / len(reviews), PCT,
                     f"score {score} pct")
    assert int(sql["orders"].sum()) == len(reviews)
    # one review per order: never more reviews than orders
    assert len(reviews) <= len(csv_frames["dim_order"])


def test_review_monthly_trend(run_query, csv_frames):
    sql = run_query("review_monthly_trend").set_index("sales_month")
    merged = csv_frames["bridge_review"].merge(
        csv_frames["dim_order"][["order_id", "order_purchase_timestamp"]],
        on="order_id",
        validate="one_to_one",
    )
    merged["sales_month"] = merged["order_purchase_timestamp"].str.slice(0, 7)
    merged["negative"] = merged["review_score"] <= 2

    py = merged.groupby("sales_month").agg(
        reviews=("order_id", "size"),
        avg_review_score=("review_score", "mean"),
        negative_reviews=("negative", "sum"),
    )

    assert set(sql.index) == set(py.index)
    for month, expected in py.iterrows():
        row = sql.loc[month]
        assert int(row["reviews"]) == int(expected["reviews"]), month
        assert int(row["negative_reviews"]) == int(expected["negative_reviews"]), month
        _assert_same(row["avg_review_score"], expected["avg_review_score"], MEAN3,
                     f"{month} avg score")
        _assert_same(row["negative_pct"],
                     100.0 * int(expected["negative_reviews"]) / int(expected["reviews"]),
                     PCT, f"{month} negative %")


def test_averaging_reviews_by_order_matches_source(run_query, csv_frames):
    """The ETL kept one review per order; SQL must not resurrect duplicates."""
    orders_with_review = int(csv_frames["bridge_review"]["order_id"].nunique())
    assert orders_with_review == len(csv_frames["bridge_review"])
    sql_total = int(run_query("review_score_distribution")["orders"].sum())
    assert sql_total == orders_with_review


# ==========================================================================
# 11. Seller performance
# ==========================================================================
def test_seller_performance_matches_line_level_python(run_query, csv_frames):
    sql = run_query("seller_performance").set_index("seller_id")

    items = csv_frames["fact_order_item"]
    py = items.groupby("seller_id").agg(
        orders=("order_id", "nunique"),
        units=("order_id", "size"),
        distinct_products=("product_id", "nunique"),
        merchandise_sales=("price", "sum"),
        freight_charges=("freight_value", "sum"),
    )

    # The catalogue query returns the top 50 sellers by sales.
    assert set(sql.index) <= set(py.index)
    assert len(sql) == 50

    # (seller, order) grain, then attach the single review / lateness flag
    seller_order = items.groupby(["seller_id", "order_id"]).size().rename("units").reset_index()
    seller_order = seller_order.merge(
        csv_frames["bridge_review"][["order_id", "review_score"]],
        on="order_id", how="left", validate="many_to_one",
    )
    seller_order = seller_order.merge(
        csv_frames["dim_order"][["order_id", "is_late_vs_estimate"]],
        on="order_id", how="left", validate="many_to_one",
    )
    seller_order["late_flag"] = _to01(seller_order["is_late_vs_estimate"])
    py_review = seller_order.groupby("seller_id")["review_score"].mean()
    py_late = (
        seller_order[seller_order["late_flag"].notna()]
        .groupby("seller_id")
        .apply(lambda g: 100.0 * (g["late_flag"] == 1).sum() / len(g),
               include_groups=False)
    )

    for seller in sql.index:
        expected = py.loc[seller]
        row = sql.loc[seller]
        assert int(row["orders"]) == int(expected["orders"]), seller
        assert int(row["units"]) == int(expected["units"]), seller
        assert int(row["distinct_products"]) == int(expected["distinct_products"]), seller
        _assert_same(row["merchandise_sales"], expected["merchandise_sales"], MONEY,
                     f"{seller} sales")
        _assert_same(row["freight_charges"], expected["freight_charges"], MONEY,
                     f"{seller} freight")
        _assert_same(row["avg_sales_per_order"],
                     float(expected["merchandise_sales"]) / int(expected["orders"]),
                     0.01, f"{seller} avg order")
        _assert_same(row["avg_review_score"], py_review.loc[seller], MEAN3,
                     f"{seller} review")
        if seller in py_late.index:
            _assert_same(row["late_pct"], py_late.loc[seller], PCT, f"{seller} late %")
        else:
            assert pd.isna(row["late_pct"]), seller

    assert int(sql["units"].sum()) <= len(items)


def test_seller_league_table_is_sorted_by_sales(run_query):
    frame = run_query("seller_performance")
    sales = frame["merchandise_sales"].astype(float).tolist()
    assert sales == sorted(sales, reverse=True)
    assert len(frame) == 50


# ==========================================================================
# 12. Product weight versus freight cost
# ==========================================================================
def _weight_bucket(weight) -> str:
    if weight is None or weight != weight or weight <= 0:
        return "unknown/invalid"
    if weight <= 200:
        return "1. 0-200 g"
    if weight <= 500:
        return "2. 201-500 g"
    if weight <= 1000:
        return "3. 501-1000 g"
    if weight <= 2000:
        return "4. 1001-2000 g"
    if weight <= 5000:
        return "5. 2001-5000 g"
    return "6. 5001+ g"


def test_weight_vs_freight(run_query, csv_frames):
    sql = run_query("weight_vs_freight").set_index("weight_bucket")

    items = csv_frames["fact_order_item"].merge(
        csv_frames["dim_product"][["product_id", "product_weight_g"]],
        on="product_id",
        validate="many_to_one",
    )
    items["freight_value"] = pd.to_numeric(items["freight_value"], errors="coerce")
    items["product_weight_g"] = pd.to_numeric(items["product_weight_g"], errors="coerce")
    items = items[items["freight_value"] >= 0].copy()
    items["bucket"] = items["product_weight_g"].map(_weight_bucket)

    py = items.groupby("bucket").agg(
        units=("order_id", "size"),
        products=("product_id", "nunique"),
        avg_weight_g=("product_weight_g", "mean"),
        avg_unit_price=("price", "mean"),
        avg_freight=("freight_value", "mean"),
        pct_of_freight=("freight_value", "sum"),
    )
    items["freight_per_kg"] = items["freight_value"] / (items["product_weight_g"] / 1000.0)
    py["avg_freight_per_kg"] = items.groupby("bucket")["freight_per_kg"].mean()

    assert set(sql.index) == set(py.index)
    total_freight = float(items["freight_value"].sum())

    for bucket, expected in py.iterrows():
        row = sql.loc[bucket]
        assert int(row["units"]) == int(expected["units"]), bucket
        assert int(row["products"]) == int(expected["products"]), bucket
        _assert_same(row["avg_freight"], expected["avg_freight"], MEAN2, f"{bucket} avg freight")
        _assert_same(row["avg_unit_price"], expected["avg_unit_price"], MEAN2,
                     f"{bucket} avg price")
        _assert_same(row["pct_of_freight"], 100.0 * float(expected["pct_of_freight"]) / total_freight,
                     PCT, f"{bucket} freight share")
        if bucket == "unknown/invalid":
            # no usable weight -> neither weight nor cost-per-kg can be averaged
            assert pd.isna(row["avg_freight_per_kg"])
            _assert_same(row["avg_weight_g"], expected["avg_weight_g"], 0.11,
                         f"{bucket} avg weight")
        else:
            _assert_same(row["avg_weight_g"], expected["avg_weight_g"], 0.11,
                         f"{bucket} avg weight")
            _assert_same(row["avg_freight_per_kg"], expected["avg_freight_per_kg"], MEAN4,
                         f"{bucket} freight per kg")

    assert int(sql["units"].sum()) == len(items)


def test_heavier_buckets_cost_more_freight_per_kilo(run_query):
    """Directional sanity check on the KPI, not a stored number."""
    frame = run_query("weight_vs_freight")
    measurable = frame[frame["weight_bucket"] != "unknown/invalid"].sort_values(
        "weight_bucket"
    )
    per_kg = measurable["avg_freight_per_kg"].astype(float).tolist()
    assert per_kg == sorted(per_kg, reverse=True), (
        "freight cost per kilogram should fall as products get heavier"
    )


# ==========================================================================
# Cross-cutting: money separation and population documentation
# ==========================================================================
def test_three_money_totals_are_distinct_and_reconcile(run_query, csv_frames):
    """Merchandise sales, freight and payment value are separate measures."""
    sales_query = run_query("monthly_merchandise_sales")
    status_query = run_query("order_status_distribution")
    payment_query = run_query("payment_method_distribution")

    sales = float(csv_frames["fact_order_item"]["price"].sum())
    freight = float(csv_frames["fact_order_item"]["freight_value"].sum())
    cash = float(csv_frames["bridge_payment"]["payment_value"].sum())

    _assert_same(sales_query["merchandise_sales"].sum(), sales, 0.5, "SQL merchandise sales")
    _assert_same(sales_query["freight_charges"].sum(), freight, 0.5, "SQL freight")
    _assert_same(payment_query["payment_value"].sum(), cash, 0.5, "SQL payment value")
    _assert_same(status_query["merchandise_sales"].sum(), sales, 0.5,
                 "status-level merchandise sales")

    # each measure is meaningfully different - none is a copy of another
    assert abs(sales - freight) > 1_000_000
    assert abs(sales - cash) > 100_000


def test_kpi_population_counts_match_the_source(run_query, csv_frames):
    population = run_query("kpi_population").set_index("kpi")

    assert int(population.loc["monthly_merchandise_sales", "rows_used"]) == len(
        csv_frames["fact_order_item"]
    )
    assert int(population.loc["monthly_order_volume", "rows_used"]) == len(
        csv_frames["dim_order"]
    )
    assert int(population.loc["payment_method_distribution", "rows_used"]) == len(
        csv_frames["bridge_payment"]
    )
    assert int(population.loc["review_scores", "rows_used"]) == len(
        csv_frames["bridge_review"]
    )
    assert int(population.loc["repeat_customer_analysis", "rows_used"]) == len(
        csv_frames["dim_customer_identity"]
    )
    eligible = int(
        csv_frames["dim_order"]["order_delivered_customer_date"].notna().sum()
    )
    assert int(population.loc["delivery_performance", "rows_used"]) == eligible
    assert int(population.loc["delivery_performance", "source_rows"]) == len(
        csv_frames["dim_order"]
    )

    # (category, order) grain used by the category KPI
    items = csv_frames["fact_order_item"].merge(
        csv_frames["dim_product"][["product_id", "product_category_name"]],
        on="product_id",
        validate="many_to_one",
    )
    grain = items.drop_duplicates(["product_category_name", "order_id"])
    assert int(population.loc["category_performance", "rows_used"]) == len(grain)

    # (seller, order) grain used by the seller KPI
    seller_order = csv_frames["fact_order_item"].drop_duplicates(["seller_id", "order_id"])
    assert int(population.loc["seller_performance", "rows_used"]) == len(seller_order)
