"""
Tests for the analysis layer (:mod:`src.metrics`, :mod:`src.insights`,
:mod:`src.visualizations`).

The KPI values are recomputed independently in pandas straight from the cleaned
CSVs and must match the SQL/Python analysis layer. The findings report is
checked for completeness (every required field present) and the period
completeness rules are pinned.

Run from the project root::

    python -m pytest tests/test_metrics_insights.py -q
"""

from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")

import pandas as pd
import pytest

from src import config, insights, metrics, visualizations

ALLOWED_UNITS = {"count", "currency", "percent", "days", "ratio"}
REQUIRED_FINDING_FIELDS = (
    "business_question",
    "method",
    "result",
    "period_population",
    "interpretation",
    "recommendation",
    "limitations",
)


# --------------------------------------------------------------------------
# KPI layer
# --------------------------------------------------------------------------
def test_kpi_catalogue_is_complete(conn):
    kpis = metrics.compute_kpis(conn)
    assert len(kpis) == 14
    keys = {k.key for k in kpis}
    assert keys == {
        "total_unique_orders",
        "total_merchandise_sales",
        "total_freight_charges",
        "average_order_value",
        "delivered_order_count",
        "delivery_completion_rate",
        "cancellation_rate",
        "average_review_score",
        "on_time_delivery_rate",
        "late_delivery_rate",
        "average_delivery_duration",
        "repeat_customer_rate",
        "average_items_per_order",
        "payment_method_distribution",
    }
    for k in kpis:
        assert k.unit in ALLOWED_UNITS
        for field_name in ("name", "formula", "filters", "eligible_population", "limitations"):
            assert getattr(k, field_name), f"{k.key}.{field_name} is empty"
    # Payment distribution ships a detail frame.
    pay = next(k for k in kpis if k.key == "payment_method_distribution")
    assert pay.detail is not None and not pay.detail.empty


def test_kpi_values_match_pandas(conn, csv_frames):
    kpis = {k.key: k.value for k in metrics.compute_kpis(conn)}

    orders = csv_frames["dim_order"]
    items = csv_frames["fact_order_item"]
    reviews = csv_frames["bridge_review"]
    summary = csv_frames["fact_order_summary"]
    identity = csv_frames["dim_customer_identity"]

    orders_with_items = int((summary["n_items"] > 0).sum())
    merch = float(items["price"].sum())
    freight = float(items["freight_value"].sum())

    assert kpis["total_unique_orders"] == pytest.approx(len(orders))
    assert kpis["total_merchandise_sales"] == pytest.approx(merch)
    assert kpis["total_freight_charges"] == pytest.approx(freight)
    assert kpis["average_order_value"] == pytest.approx(merch / orders_with_items)
    assert kpis["average_items_per_order"] == pytest.approx(
        float(summary["n_items"].sum()) / orders_with_items)

    delivered = int((orders["order_status"] == "delivered").sum())
    canceled = int((orders["order_status"] == "canceled").sum())
    assert kpis["delivered_order_count"] == pytest.approx(delivered)
    assert kpis["delivery_completion_rate"] == pytest.approx(100.0 * delivered / len(orders))
    assert kpis["cancellation_rate"] == pytest.approx(100.0 * canceled / len(orders))

    assert kpis["average_review_score"] == pytest.approx(float(reviews["review_score"].mean()))

    evaluable = orders[orders["order_delivered_customer_date"].notna()]
    on_time = int((evaluable["is_late_vs_estimate"] == 0).sum())
    late = int((evaluable["is_late_vs_estimate"] == 1).sum())
    assert kpis["on_time_delivery_rate"] == pytest.approx(100.0 * on_time / len(evaluable))
    assert kpis["late_delivery_rate"] == pytest.approx(100.0 * late / len(evaluable))
    assert kpis["average_delivery_duration"] == pytest.approx(
        float(evaluable["delivery_delay_days"].mean()))

    repeat = int((identity["placement_count"] >= 2).sum())
    assert kpis["repeat_customer_rate"] == pytest.approx(100.0 * repeat / len(identity))


def test_money_measures_are_separated(conn, csv_frames):
    kpis = {k.key: k.value for k in metrics.compute_kpis(conn)}
    items = csv_frames["fact_order_item"]
    # Merchandise sales must never be the gross item value (price + freight).
    gross = float((items["price"] + items["freight_value"]).sum())
    assert kpis["total_merchandise_sales"] < gross
    assert kpis["total_merchandise_sales"] + kpis["total_freight_charges"] == pytest.approx(gross)


# --------------------------------------------------------------------------
# Insights layer
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def analysis(conn):
    return insights.analyse(conn)


def test_findings_are_complete(analysis):
    assert len(analysis.findings) >= 15
    for finding in analysis.findings:
        for field_name in REQUIRED_FINDING_FIELDS:
            value = getattr(finding, field_name)
            assert value and value.strip(), f"{finding.key}.{field_name} is empty"


def test_findings_cover_every_required_theme(analysis):
    sections = {f.section for f in analysis.findings}
    assert {
        "Sales trends",
        "Product categories",
        "Geography",
        "Customer retention",
        "Payments",
        "Delivery and satisfaction",
        "Seller operations",
        "Weight and freight",
        "Data quality",
    } <= sections


def test_findings_do_not_contain_placeholder_values(analysis):
    for finding in analysis.findings:
        assert "TODO" not in finding.result
        assert "example" not in finding.result.lower()
        assert "lorem" not in finding.result.lower()


def test_correlation_findings_carry_a_caveat(analysis):
    # Delivery, seller and weight findings are relational and must state that
    # the relationship is not causal.
    relational = {"late_vs_review_score", "seller_lateness_dispersion",
                  "weight_freight_relationship"}
    for finding in analysis.findings:
        if finding.key in relational:
            assert finding.correlation_note, f"{finding.key} lacks a correlation caveat"
            assert "caus" in finding.correlation_note.lower()


def test_period_completeness(analysis):
    period = analysis.context["period"]
    assert period["window_start"] == config.ANALYSIS_WINDOW_START
    assert period["window_end"] == config.ANALYSIS_WINDOW_END
    assert period["complete_months"] == 20
    # Ramp-up and export-tail months must be excluded and reported, never dropped.
    assert "2016-09" in period["excluded_months"]
    assert "2018-10" in period["excluded_months"]
    # The export has no orders in November 2016.
    assert "2016-11" in period["missing_calendar_months"]
    monthly = analysis.frames["monthly"]
    assert int(monthly["is_complete"].sum()) == period["complete_months"]


# --------------------------------------------------------------------------
# Visualizations & report writers
# --------------------------------------------------------------------------
def test_visualizations_render(analysis, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FIGURES_DIR", tmp_path)
    frames = analysis.frames
    produced = [
        visualizations.monthly_sales_trend(frames["monthly"]),
        visualizations.quarterly_sales(frames["quarterly"]),
        visualizations.category_performance(frames["category"]),
        visualizations.state_distribution(frames["state"]),
        visualizations.repeat_segments(frames["repeat_segments"]),
        visualizations.payment_methods(frames["payment"]),
        visualizations.installments(frames["installments"]),
        visualizations.late_vs_review(frames["late_review"]),
        visualizations.review_distribution(frames["review_distribution"]),
        visualizations.seller_late_distribution(frames["sellers"]),
        visualizations.weight_freight(frames["weight_freight"]),
        visualizations.delivery_trend(
            frames["monthly"].merge(frames["delivery_monthly"], on="sales_month", how="inner")),
    ]
    assert len(produced) == 12
    for path in produced:
        assert path.is_file()
        assert path.stat().st_size > 1000


def test_report_writers(analysis, conn, tmp_path):
    kpis = metrics.compute_kpis(conn)
    metrics_path = tmp_path / "business_metrics.md"
    findings_path = tmp_path / "eda_findings.md"
    summary_path = tmp_path / "analysis_summary.json"

    metrics.write_metrics_report(conn, metrics_path)
    insights.write_findings_report(analysis, kpis, {}, findings_path)
    insights.write_summary_json(analysis, kpis, {}, summary_path)

    text = findings_path.read_text(encoding="utf-8")
    assert "## Executive summary" in text
    assert "Correlation versus causation" in text
    assert "How to present this in the web application" in text

    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    assert len(payload["kpis"]) == 14
    assert len(payload["findings"]) == len(analysis.findings)
    assert payload["period"]["window_start"] == config.ANALYSIS_WINDOW_START
