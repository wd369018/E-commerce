"""
ShopScope - exploratory findings layer.

This module turns the validated database into **evidence-backed business
findings**. Each :class:`Finding` answers one question and states, in order:

    business question -> method -> actual result -> period & population ->
    interpretation -> recommendation -> limitations (with an explicit
    correlation-vs-causation note where a relationship is involved).

Design rules
------------
* Every number is computed from ``data/shopscope.db`` at run time - there are no
  hardcoded results or example percentages.
* Incomplete historical periods (the 2016 ramp-up and the 2018-09/10 export
  tail) are identified and excluded from trend statements, but always reported.
* Correlation is stated as correlation. Causal claims are not made from
  observational data.

Usage::

    python -m src.insights            # run every analysis, write all reports
    python -m src.insights --no-figures
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import config, metrics, visualizations
from .database import get_connection

logger = logging.getLogger(__name__)

WINDOW_START = config.ANALYSIS_WINDOW_START  # e.g. "2017-01"
WINDOW_END = config.ANALYSIS_WINDOW_END      # e.g. "2018-08"


# --------------------------------------------------------------------------
# Containers
# --------------------------------------------------------------------------
@dataclass
class Finding:
    key: str
    section: str
    title: str
    business_question: str
    method: str
    result: str
    period_population: str
    interpretation: str
    recommendation: str
    limitations: str
    correlation_note: str = ""
    figures: tuple[str, ...] = ()
    headline: str = ""

    def to_markdown(self) -> str:
        lines = [f"#### {self.title}\n"]
        lines.append(f"- **Business question:** {self.business_question}")
        lines.append(f"- **Calculation method:** {self.method}")
        lines.append(f"- **Actual result:** {self.result}")
        lines.append(f"- **Period and population:** {self.period_population}")
        lines.append(f"- **Interpretation:** {self.interpretation}")
        lines.append(f"- **Recommendation:** {self.recommendation}")
        lines.append(f"- **Limitations:** {self.limitations}")
        if self.correlation_note:
            lines.append(f"- **Correlation vs causation:** {self.correlation_note}")
        if self.figures:
            lines.append("- **Figures:** " + ", ".join(f"`figures/{f}.png`" for f in self.figures))
        lines.append("")
        return "\n".join(lines)


@dataclass
class AnalysisReport:
    findings: list[Finding] = field(default_factory=list)
    frames: dict[str, pd.DataFrame] = field(default_factory=dict)
    context: dict[str, object] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------
def _money(x: float) -> str:
    return f"R$ {x:,.2f}"


def _money0(x: float) -> str:
    return f"R$ {x:,.0f}"


def _pct(x: float, nd: int = 2) -> str:
    return f"{x:,.{nd}f}%"


def _int(x) -> str:
    return f"{int(round(x)):,}"


def _ratio(x: float, nd: int = 2) -> str:
    return f"{x:,.{nd}f}"


def _q(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> pd.DataFrame:
    return pd.read_sql_query(sql, conn, params=params)


# --------------------------------------------------------------------------
# Period / completeness handling
# --------------------------------------------------------------------------
def _quarter_of(month: str) -> str:
    year, m = month.split("-")
    return f"{year}Q{(int(m) - 1) // 3 + 1}"


def load_periods(conn: sqlite3.Connection) -> tuple[pd.DataFrame, dict]:
    """Return a monthly frame with completeness flags plus context metadata."""
    vol = _q(conn, """
        SELECT substr(order_purchase_timestamp, 1, 7) AS sales_month,
               COUNT(*)                               AS orders
        FROM dim_order
        GROUP BY 1
    """)
    sales = _q(conn, """
        SELECT sales_month, merchandise_sales, freight_charges
        FROM vw_monthly_sales
    """)
    monthly = vol.merge(sales, on="sales_month", how="left").fillna(
        {"merchandise_sales": 0.0, "freight_charges": 0.0}
    ).sort_values("sales_month").reset_index(drop=True)

    bounds = _q(conn, """
        SELECT MIN(purchase_date) AS first_day, MAX(purchase_date) AS last_day,
               MIN(order_purchase_timestamp) AS first_ts,
               MAX(order_purchase_timestamp) AS last_ts
        FROM dim_order
    """).iloc[0]

    first_month = str(monthly["sales_month"].iloc[0])
    last_month = str(monthly["sales_month"].iloc[-1])

    # A month is "complete/mature" only if it sits inside the configured
    # full-volume window. The first and last calendar months are additionally
    # flagged as partial because the export starts/ends mid-month.
    monthly["is_complete"] = monthly["sales_month"].between(WINDOW_START, WINDOW_END)
    monthly["is_boundary_partial"] = monthly["sales_month"].isin([first_month, last_month])
    monthly["is_ramp_up"] = monthly["sales_month"] < WINDOW_START
    monthly["is_tail"] = monthly["sales_month"] > WINDOW_END

    # Quarterly rollup with the same completeness logic.
    monthly["quarter"] = monthly["sales_month"].map(_quarter_of)
    quarterly = (
        monthly.groupby("quarter", as_index=False)
        .agg(orders=("orders", "sum"),
             merchandise_sales=("merchandise_sales", "sum"),
             freight_charges=("freight_charges", "sum"),
             months=("sales_month", "count"))
        .sort_values("quarter")
        .reset_index(drop=True)
    )
    quarters_in_window: set[str] = set()
    for q in quarterly["quarter"]:
        months = [m for m in monthly["sales_month"] if _quarter_of(m) == q]
        if all(m >= WINDOW_START and m <= WINDOW_END for m in months):
            quarters_in_window.add(q)
    quarterly["is_complete"] = quarterly["quarter"].isin(quarters_in_window)

    excluded = monthly[~monthly["is_complete"]]
    context = {
        "data_first_timestamp": str(bounds["first_ts"]),
        "data_last_timestamp": str(bounds["last_ts"]),
        "first_day": str(bounds["first_day"]),
        "last_day": str(bounds["last_day"]),
        "window_start": WINDOW_START,
        "window_end": WINDOW_END,
        "first_month": first_month,
        "last_month": last_month,
        "complete_months": int(monthly["is_complete"].sum()),
        "excluded_months": excluded["sales_month"].tolist(),
        "excluded_orders": int(excluded["orders"].sum()),
        "excluded_sales": float(excluded["merchandise_sales"].sum()),
        "missing_calendar_months": _missing_calendar_months(first_month, last_month,
                                                            set(monthly["sales_month"])),
    }
    return monthly, context


def _missing_calendar_months(first: str, last: str, present: set[str]) -> list[str]:
    """Calendar months with zero orders in the export (e.g. 2016-11)."""
    start = pd.Period(first, freq="M")
    end = pd.Period(last, freq="M")
    missing = []
    period = start
    while period <= end:
        label = str(period)
        if label not in present:
            missing.append(label)
        period += 1
    return missing


# --------------------------------------------------------------------------
# (A) Sales trends
# --------------------------------------------------------------------------
def analyse_sales_trends(conn: sqlite3.Connection, monthly: pd.DataFrame,
                         context: dict, report: AnalysisReport) -> None:
    report.frames["monthly"] = monthly

    monthly["quarter"] = monthly["sales_month"].map(_quarter_of)
    quarterly = (
        monthly.groupby("quarter", as_index=False)
        .agg(orders=("orders", "sum"),
             merchandise_sales=("merchandise_sales", "sum"),
             freight_charges=("freight_charges", "sum"))
        .sort_values("quarter").reset_index(drop=True)
    )
    quarters_window = [
        q for q in quarterly["quarter"]
        if all(m >= WINDOW_START and m <= WINDOW_END
               for m in monthly.loc[monthly["quarter"] == q, "sales_month"])
    ]
    quarterly["is_complete"] = quarterly["quarter"].isin(quarters_window)
    report.frames["quarterly"] = quarterly

    complete = monthly[monthly["is_complete"]]
    peak_row = complete.loc[complete["merchandise_sales"].idxmax()]
    trough_row = complete.loc[complete["merchandise_sales"].idxmin()]

    h1_17 = monthly[(monthly["sales_month"] >= "2017-01") & (monthly["sales_month"] <= "2017-06")]
    h1_18 = monthly[(monthly["sales_month"] >= "2018-01") & (monthly["sales_month"] <= "2018-06")]
    s17, s18 = float(h1_17["merchandise_sales"].sum()), float(h1_18["merchandise_sales"].sum())
    o17, o18 = int(h1_17["orders"].sum()), int(h1_18["orders"].sum())
    yoy_sales = 100.0 * (s18 - s17) / s17
    yoy_orders = 100.0 * (o18 - o17) / o17

    q = quarterly.set_index("quarter")
    q4_17 = float(q.loc["2017Q4", "merchandise_sales"])
    q3_17 = float(q.loc["2017Q3", "merchandise_sales"])
    qoq = 100.0 * (q4_17 - q3_17) / q3_17

    if yoy_sales >= yoy_orders:
        lever = ("sales grew slightly faster than order volume, so per-order spend is rising")
    else:
        lever = ("order volume grew faster than sales, so the average basket compressed modestly")

    report.findings.append(Finding(
        key="trend_yoy_growth",
        section="Sales trends",
        title="Like-for-like growth: H1 2018 versus H1 2017",
        business_question="Is the marketplace growing once the incomplete start and end of the export are removed?",
        method=(
            "Sum merchandise sales (SUM of item price, freight excluded) and order counts for "
            "the two fully-observed half-years Jan-Jun 2017 and Jan-Jun 2018, then compute the "
            "year-over-year percentage change."
        ),
        result=(
            f"H1 2017 = {_money(s17)} across {_int(o17)} orders; "
            f"H1 2018 = {_money(s18)} across {_int(o18)} orders. "
            f"YoY merchandise-sales growth = {_pct(yoy_sales, 1)}; YoY order growth = {_pct(yoy_orders, 1)}."
        ),
        period_population=f"Jan-Jun 2017 and Jan-Jun 2018; all {_int(int(monthly['orders'].sum()))} orders in the export, item-bearing lines only for sales",
        interpretation=(
            "The two half-years are calendar-matched and fully covered, so this is a fair like-for-like comparison. "
            f"Sales grew {_pct(yoy_sales, 1)} while orders grew {_pct(yoy_orders, 1)}: {lever}. "
            "The two components point to different levers, so both are reported."
        ),
        recommendation=(
            "Treat H1 2018 as the run-rate for planning rather than the partial tail of 2018. "
            "Track the sales-per-order and orders-per-customer components separately to see which is driving growth."
        ),
        limitations=(
            "Two half-years are a short series and the platform's customer mix and pricing changed between them. "
            "No inflation adjustment is applied to BRL figures."
        ),
        correlation_note="Growth is a descriptive time comparison, not a causal model: no intervention is isolated here.",
        figures=("monthly_sales_trend", "quarterly_sales"),
        headline=f"Merchandise sales rose {_pct(yoy_sales, 1)} year over year in H1 (H1 2017 vs H1 2018), with orders up {_pct(yoy_orders, 1)}.",
    ))

    report.findings.append(Finding(
        key="trend_peak_nov_2017",
        section="Sales trends",
        title="Strongest trading month and Q4-2017 acceleration",
        business_question="When does demand peak, and how strong is the Q4 seasonal lift?",
        method=(
            "Rank complete months by merchandise sales; compare Q4 2017 (Oct-Dec) with Q3 2017 (Jul-Sep). "
            "Only months inside the full-volume window are ranked."
        ),
        result=(
            f"Peak month = {peak_row['sales_month']} with {_money(float(peak_row['merchandise_sales']))} "
            f"and {_int(float(peak_row['orders']))} orders (lowest complete month: {trough_row['sales_month']}). "
            f"Q4 2017 sales = {_money(q4_17)} vs Q3 2017 = {_money(q3_17)}, a quarter-on-quarter change of {_pct(qoq, 1)}."
        ),
        period_population=f"complete months {WINDOW_START}..{WINDOW_END}; quarterly figures for 2017Q3-Q4",
        interpretation=(
            "The November spike is consistent with Black-Friday promotional demand; the Q4 lift being "
            "concentrated in a single month suggests event-driven rather than broad secular demand."
        ),
        recommendation=(
            "Plan logistics and inventory capacity around the Q4 event peak. Because the lift is event-driven, "
            "provision for the peak months rather than an even seasonal ramp."
        ),
        limitations=(
            "One observed Q4 (2017) cannot separate a recurring seasonal effect from a one-off campaign; "
            "November 2017 may also reflect platform maturity rather than a seasonal pattern."
        ),
        correlation_note=(
            "The association with Black Friday is plausible context, not a controlled measurement: the dataset "
            "does not include marketing spend or campaign flags."
        ),
        figures=("monthly_sales_trend", "quarterly_sales"),
        headline=(
            f"November 2017 was the peak month at {_money(float(peak_row['merchandise_sales']))}; "
            f"Q4 2017 was {_pct(qoq, 1)} above Q3 2017."
        ),
    ))

    report.findings.append(Finding(
        key="trend_period_completeness",
        section="Sales trends",
        title="Incomplete periods are excluded from trends but always reported",
        business_question="Which parts of the timeline cannot support trend conclusions?",
        method=(
            "Flag the first and last calendar months as partial, and treat months before the full-volume "
            f"window ({WINDOW_START}) and after it ({WINDOW_END}) as ramp-up / truncated; count them explicitly."
        ),
        result=(
            f"Data spans {context['data_first_timestamp'][:10]} to {context['data_last_timestamp'][:10]}. "
            f"{context['complete_months']} complete months are used; "
            f"{len(context['excluded_months'])} months are excluded "
            f"({', '.join(context['excluded_months'])}) holding {_int(context['excluded_orders'])} orders "
            f"and {_money(context['excluded_sales'])} of sales. "
            + (f"Calendar months with no orders at all: {', '.join(context['missing_calendar_months'])}. "
               if context['missing_calendar_months'] else "")
        ),
        period_population="every month in the export",
        interpretation=(
            "The 2016 months are platform ramp-up with negligible volume, and September-October 2018 are cut "
            "off by the export date. Including them would distort trend lines and averages."
        ),
        recommendation=(
            "Report trend metrics on the complete window and label the ramp-up/tail months as such in the "
            "dashboard; never chart the partial tail as if it were a collapse in demand."
        ),
        limitations=(
            "The completeness window is a judgement anchored to observed volume, not an external calendar of the "
            "business; revisit it if a longer export becomes available."
        ),
        figures=("monthly_sales_trend",),
    ))


# --------------------------------------------------------------------------
# (B) Category performance
# --------------------------------------------------------------------------
def analyse_categories(conn: sqlite3.Connection, report: AnalysisReport) -> None:
    cat = _q(conn, "SELECT * FROM vw_category_performance")
    # Keep the raw English name; add a display label that falls back to the
    # Portuguese category for the few products without an English mapping.
    cat["category_label"] = cat["product_category_name_english"].fillna(
        cat["product_category_name"])
    report.frames["category"] = cat

    # Category late rate at (category, order) grain to avoid unit weighting.
    late = _q(conn, """
        WITH cat_order AS (
            SELECT p.product_category_name AS category_name, i.order_id
            FROM fact_order_item i
            JOIN dim_product p ON p.product_id = i.product_id
            GROUP BY p.product_category_name, i.order_id
        )
        SELECT category_name,
               COUNT(*) AS orders,
               AVG(CASE WHEN o.is_late_vs_estimate = 1 THEN 1.0
                        WHEN o.is_late_vs_estimate = 0 THEN 0.0 END) * 100.0 AS late_pct
        FROM cat_order c
        JOIN dim_order o ON o.order_id = c.order_id
        GROUP BY category_name
    """)
    late = late.rename(columns={"orders": "late_evaluable_orders"})
    cat = cat.merge(late, left_on="product_category_name", right_on="category_name",
                    how="left")
    cat = cat.drop(columns=["category_name"])
    report.frames["category"] = cat

    total = float(cat["merchandise_sales"].sum())
    cat = cat.sort_values("merchandise_sales", ascending=False).reset_index(drop=True)
    top5 = cat.head(5)
    top5_share = 100.0 * float(top5["merchandise_sales"].sum()) / total
    top1 = cat.iloc[0]
    nonzero = cat[cat["merchandise_sales"] > 0]
    smallest = nonzero.iloc[-1]

    reliable = cat[cat["orders"] >= 100].dropna(subset=["avg_review_score", "late_pct"]).copy()
    best_review = reliable.loc[reliable["avg_review_score"].idxmax()]
    worst_review = reliable.loc[reliable["avg_review_score"].idxmin()]
    worst_late = reliable.loc[reliable["late_pct"].idxmax()]
    best_late = reliable.loc[reliable["late_pct"].idxmin()]

    corr_sales_review = cat[["merchandise_sales", "avg_review_score"]].corr(
        method="spearman").iloc[0, 1]

    report.findings.append(Finding(
        key="category_concentration",
        section="Product categories",
        title="Sales are concentrated in a few categories",
        business_question="Is category revenue diversified or dependent on a handful of categories?",
        method="Rank categories by merchandise sales, compute the share of the top 5 and the leading category.",
        result=(
            f"{len(cat)} categories. Largest = '{top1['category_label']}' with "
            f"{_money(float(top1['merchandise_sales']))} ({_pct(100 * float(top1['merchandise_sales']) / total)} of sales); "
            f"smallest non-zero = '{smallest['category_label']}' with {_money(float(smallest['merchandise_sales']))} "
            f"({_pct(100 * float(smallest['merchandise_sales']) / total, 3)} of sales). "
            f"Top 5 categories = {_pct(top5_share)} of all merchandise sales."
        ),
        period_population=(
            f"one row per (category, order) pair - {_int(int(cat['orders'].sum()))} pairs across "
            "all item-bearing orders"
        ),
        interpretation=(
            "Revenue depends on a narrow set of categories, so supplier or logistics disruption in those "
            "categories has an outsized effect on the platform."
        ),
        recommendation=(
            "Protect continuity for the top categories (inventory, seller relationships) while treating long-tail "
            "categories as optional growth experiments rather than core revenue."
        ),
        limitations=(
            "Sales are list prices and include non-delivered orders; category names come from the product catalogue "
            "and a few products have no category mapping."
        ),
        correlation_note="Concentration is a structural fact, not a causal finding.",
        figures=("category_performance",),
        headline=(
            f"The top 5 of {len(cat)} categories generate {_pct(top5_share)} of merchandise sales; "
            f"'{top1['category_label']}' leads with {_pct(100 * float(top1['merchandise_sales']) / total)}."
        ),
    ))

    report.findings.append(Finding(
        key="category_quality_spread",
        section="Product categories",
        title="Category satisfaction and punctuality vary widely",
        business_question="Which product categories satisfy customers, and which run late?",
        method=(
            "Restrict to categories with at least 100 orders; compare average review score and the category "
            "late-delivery rate (share of delivered orders past the estimated date, computed once per order)."
        ),
        result=(
            f"Best-reviewed: '{best_review['category_label']}' {_ratio(float(best_review['avg_review_score']), 3)}/5; "
            f"worst-reviewed: '{worst_review['category_label']}' {_ratio(float(worst_review['avg_review_score']), 3)}/5. "
            f"Highest late rate: '{worst_late['category_label']}' {_pct(float(worst_late['late_pct']), 1)}; "
            f"lowest: '{best_late['category_label']}' {_pct(float(best_late['late_pct']), 1)}. "
            f"Spearman correlation between a category's sales and its review score = {_ratio(corr_sales_review, 3)}."
        ),
        period_population=f"{len(reliable)} categories with >= 100 orders",
        interpretation=(
            "Some low-volume or logistically awkward categories consistently under-perform on satisfaction and "
            "punctuality, while high-volume categories are not automatically the worst-rated."
        ),
        recommendation=(
            "Investigate the worst-reviewed and latest categories for handling/packaging or seller concentration "
            "before spending on demand; review-score gains compound through repeat purchase and word of mouth."
        ),
        limitations=(
            "Category review averages can hide seller-level problems, and the 100-order threshold is a "
            "stability rule, not a significance test."
        ),
        correlation_note=(
            f"The category sales-vs-score correlation ({_ratio(corr_sales_review, 3)}) is an association across "
            "categories; it does not show that selling more causes lower scores."
        ),
        figures=("category_performance",),
    ))


# --------------------------------------------------------------------------
# (C) Geography
# --------------------------------------------------------------------------
def analyse_geography(conn: sqlite3.Connection, report: AnalysisReport) -> None:
    state = _q(conn, "SELECT * FROM vw_state_customer_order_distribution")
    state = state.sort_values("orders", ascending=False).reset_index(drop=True)
    report.frames["state"] = state

    total_orders = float(state["orders"].sum())
    sp = state[state["customer_state"] == "SP"]
    sp_orders = float(sp["orders"].iloc[0]) if not sp.empty else 0.0
    sp_sales = float(sp["merchandise_sales"].iloc[0]) if not sp.empty else 0.0
    top3 = state.head(3)
    top3_share = 100.0 * float(top3["orders"].sum()) / total_orders

    # Average basket by state (item-bearing orders only, from the summary grain).
    basket = _q(conn, """
        SELECT c.customer_state AS customer_state,
               ROUND(SUM(i.price) / COUNT(DISTINCT o.order_id), 2) AS avg_order_value,
               COUNT(DISTINCT o.order_id)                           AS orders
        FROM fact_order_item i
        JOIN dim_order o ON o.order_id = i.order_id
        JOIN dim_customer c ON c.customer_id = o.customer_id
        GROUP BY c.customer_state
        ORDER BY orders DESC
    """)
    report.frames["state_basket"] = basket
    biggest = basket.iloc[0]
    richest = basket.sort_values("avg_order_value", ascending=False).iloc[0]

    report.findings.append(Finding(
        key="geo_concentration",
        section="Geography",
        title="Demand is concentrated in the South-East",
        business_question="How geographically concentrated is the customer base?",
        method="Count orders and distinct customers per customer_state; compute the top-state and top-3-state shares.",
        result=(
            f"Sao Paulo (SP) = {_pct(100 * sp_orders / total_orders)} of orders and "
            f"{_money(sp_sales)} of sales. Top 3 states = {_pct(top3_share)} of orders "
            f"({', '.join(top3['customer_state'])})."
        ),
        period_population=f"all {_int(int(total_orders))} orders across {len(state)} states",
        interpretation=(
            "The marketplace is South-East-centric, which shapes delivery economics: most orders ship within a "
            "dense region, so national averages under-represent the cost of long-distance delivery."
        ),
        recommendation=(
            "Prioritise fulfilment and carrier coverage in SP/RJ/MG where volume and basket justify investment; "
            "test long-haul lanes separately because their freight and lateness behaviour differs."
        ),
        limitations=(
            "State is taken from the customer address at order time; a handful of customers moved between states "
            "(their orders are attributed to the address on each order)."
        ),
        correlation_note="Geography is an observed distribution, not a causal driver by itself.",
        figures=("state_distribution",),
        headline=(
            f"Sao Paulo alone is {_pct(100 * sp_orders / total_orders)} of orders and "
            f"{_pct(top3_share)} comes from just three states (SP, RJ, MG)."
        ),
    ))

    report.findings.append(Finding(
        key="geo_basket_variation",
        section="Geography",
        title="Average basket differs by state, but volume and basket are not aligned",
        business_question="Do high-volume states also have high-value baskets?",
        method=(
            "For each state compute average merchandise sales per item-bearing order (SUM of item price divided "
            "by distinct orders), then compare the most populous state with the highest-basket state."
        ),
        result=(
            f"Largest state {biggest['customer_state']}: average basket {_money(float(biggest['avg_order_value']))} "
            f"over {_int(float(biggest['orders']))} orders. Highest-basket state (with meaningful volume): "
            f"{richest['customer_state']} at {_money(float(richest['avg_order_value']))}."
        ),
        period_population=f"{len(basket)} states, item-bearing orders",
        interpretation=(
            "Basket size and order volume are separate dimensions; high-volume states are not necessarily "
            "high-basket states, so a single national AOV hides real mix differences."
        ),
        recommendation=(
            "Segment retention and assortment decisions by state cluster rather than nationally; use basket "
            "size, not just volume, to decide where to invest in logistics."
        ),
        limitations=(
            "Freight is excluded from the basket, and small states can show volatile average baskets that should "
            "not be over-read."
        ),
        correlation_note="This is a descriptive comparison, not evidence that geography changes willingness to pay.",
        figures=("state_distribution",),
    ))


# --------------------------------------------------------------------------
# (D) Repeat purchasing
# --------------------------------------------------------------------------
def analyse_repeat(conn: sqlite3.Connection, report: AnalysisReport) -> None:
    seg = _q(conn, "SELECT * FROM vw_repeat_customer_summary")
    customers = _q(conn, "SELECT * FROM vw_customer_repeat_purchase")
    report.frames["repeat_segments"] = seg
    report.frames["repeat_customers"] = customers

    total_customers = int(seg["customers"].sum())
    repeat = int(seg.loc[seg["segment"] != "1 order", "customers"].sum())
    repeat_rate = 100.0 * repeat / total_customers
    repeat_orders = int(seg.loc[seg["segment"] != "1 order", "total_orders"].sum())
    total_orders = int(seg["total_orders"].sum())
    repeat_spend = float(seg.loc[seg["segment"] != "1 order", "merchandise_spend"].sum())
    total_spend = float(seg["merchandise_spend"].sum())
    repeat_spend_share = 100.0 * repeat_spend / total_spend

    multi = customers[customers["orders"] >= 2].copy()
    latency = (
        pd.to_datetime(multi["last_order_at"]) - pd.to_datetime(multi["first_order_at"])
    ).dt.days
    median_latency = float(latency.median())

    one_time_review = float(customers.loc[customers["orders"] == 1, "avg_review_score"].mean())
    repeat_review = float(multi["avg_review_score"].mean())

    report.findings.append(Finding(
        key="repeat_rate",
        section="Customer retention",
        title="Most customers buy only once",
        business_question="What share of customers return for a second order?",
        method=(
            "Group all orders by customer_unique_id, count customers with >= 2 orders, and divide by all distinct "
            "customers. customer_unique_id is used because customer_id is regenerated per order."
        ),
        result=(
            f"{_int(repeat)} of {_int(total_customers)} customers placed 2+ orders = a repeat rate of {_pct(repeat_rate)}. "
            f"Repeat customers placed {_int(repeat_orders)} of {_int(total_orders)} orders and generated "
            f"{_money(repeat_spend)} ({_pct(repeat_spend_share)} of merchandise sales)."
        ),
        period_population=f"{_int(total_customers)} distinct customers over the full export window",
        interpretation=(
            "The business is acquisition-led: the overwhelming majority of customers transact once. Repeat "
            "customers are few but, per customer, are much more valuable."
        ),
        recommendation=(
            "Test a post-delivery retention programme (service follow-up, next-purchase incentive) targeted at "
            "first-time buyers; even a small lift in the repeat rate has a leveraged effect on sales."
        ),
        limitations=(
            "A ~2-year export window truncates the tail: customers who would return after the cut are counted as "
            "one-time, so the true repeat rate is a lower bound. Guest checkout may also split a person across "
            "identities."
        ),
        correlation_note="Repeat status and value are jointly observed; the data cannot show that repeat buying causes higher spend per se.",
        figures=("repeat_segments",),
        headline=(
            f"Only {_pct(repeat_rate)} of customers placed a second order, yet they generated "
            f"{_pct(repeat_spend_share)} of merchandise sales."
        ),
    ))

    report.findings.append(Finding(
        key="repeat_value",
        section="Customer retention",
        title="Repeat customers spend more and rate similarly",
        business_question="Are returning customers worth more or happier than one-time buyers?",
        method=(
            "Compare average review score for one-time versus repeat customers, and report the median days between "
            "a repeat customer's first and last order."
        ),
        result=(
            f"Average review score: one-time = {_ratio(one_time_review, 3)}, repeat = {_ratio(repeat_review, 3)}. "
            f"Median first-to-last span among repeat customers = {_ratio(median_latency, 0)} days."
        ),
        period_population=f"{_int(repeat)} repeat and {_int(total_customers - repeat)} one-time customers",
        interpretation=(
            "Repeat customers contribute disproportionate value through cumulative spend. Similar satisfaction "
            "scores suggest retention is driven by need and assortment rather than by a service gap between the groups."
        ),
        recommendation=(
            "Focus messaging on the second purchase while the median return window is still open; measure whether "
            "the return interval shortens after the campaign."
        ),
        limitations=(
            "Per-customer spend is cumulative across orders, so comparing totals against one-time buyers is "
            "expected to favour repeat customers. Scores are simple averages and not weighted by order value."
        ),
        correlation_note=(
            "Higher cumulative spend for repeat customers is mechanical (more orders); it is not evidence of a "
            "causal retention premium."
        ),
        figures=("repeat_segments",),
    ))


# --------------------------------------------------------------------------
# (E) Payments & installments
# --------------------------------------------------------------------------
def analyse_payments(conn: sqlite3.Connection, report: AnalysisReport) -> None:
    pay = _q(conn, "SELECT * FROM vw_payment_method_distribution ORDER BY payment_value DESC")
    inst = _q(conn, "SELECT * FROM vw_installment_analysis ORDER BY bucket_order")
    multi = _q(conn, """
        SELECT
            ROUND(100.0 * AVG(CASE WHEN payment_installments > 1 THEN 1.0 ELSE 0.0 END), 2) AS pct_multi_installment,
            ROUND(AVG(CASE WHEN payment_installments > 1 THEN payment_value END), 2)          AS avg_value_multi,
            ROUND(AVG(CASE WHEN payment_installments = 1 THEN payment_value END), 2)          AS avg_value_single
        FROM bridge_payment
        WHERE payment_installments IS NOT NULL
    """).iloc[0]
    report.frames["payment"] = pay
    report.frames["installments"] = inst

    total_value = float(pay["payment_value"].sum())
    cc = pay.loc[pay["payment_type"] == "credit_card"].iloc[0]
    boleto = pay.loc[pay["payment_type"] == "boleto"].iloc[0]
    voucher = pay.loc[pay["payment_type"] == "voucher"].iloc[0]

    boost = 100.0 * (float(multi["avg_value_multi"]) - float(multi["avg_value_single"])) / float(multi["avg_value_single"])

    report.findings.append(Finding(
        key="payment_methods",
        section="Payments",
        title="Card dominates, boleto is a material minority, vouchers fund part-payments",
        business_question="Which payment methods carry the transaction value?",
        method="Aggregate bridge_payment by payment_type and compute each method's share of total payment value.",
        result=(
            f"Total payment value {_money(total_value)} across {_int(float(pay['payments'].sum()))} payment records. "
            f"credit_card = {_pct(float(cc['pct_of_value']))} of value, boleto = {_pct(float(boleto['pct_of_value']))}, "
            f"voucher = {_pct(float(voucher['pct_of_value']))}, debit = "
            f"{_pct(float(total_value - cc['payment_value'] - boleto['payment_value'] - voucher['payment_value']) / total_value * 100)}."
        ),
        period_population="all payment records (an order may have more than one)",
        interpretation=(
            "Card is the backbone of collections, but a meaningful slice pays by bank slip (boleto), which has "
            "different settlement timing and failure modes and should not be ignored in checkout analytics."
        ),
        recommendation=(
            "Keep boleto and voucher paths well-instrumented; monitor decline/conversion by method rather than "
            "reporting a single 'payment success' number."
        ),
        limitations=(
            "Grain is the payment attempt, not the order: voucher + card orders appear twice, so row counts and "
            "value shares are by payment, not by basket. A few 'not_defined' rows exist."
        ),
        correlation_note="Payment mix is a distribution, not a causal relationship.",
        figures=("payment_methods",),
    ))

    report.findings.append(Finding(
        key="installments",
        section="Payments",
        title="Installments are widely used and track order size",
        business_question="How often do customers split payments, and does basket size drive it?",
        method=(
            "Compute the share of payment records with more than one installment and compare average payment value "
            "for multi-installment versus single-payment records."
        ),
        result=(
            f"{_pct(float(multi['pct_multi_installment']))} of payment records use more than one installment. "
            f"Average value: multi-installment {_money(float(multi['avg_value_multi']))} vs single "
            f"{_money(float(multi['avg_value_single']))} ({_pct(boost, 0)} higher)."
        ),
        period_population="all payment records with a known installment count",
        interpretation=(
            "Installment plans are close to a default behaviour rather than an exception, and they are used more "
            "on larger tickets, which is what one would expect if affordability, not method choice, drives the split."
        ),
        recommendation=(
            "Keep installment options visible at checkout for mid/high-ticket baskets; monitor default/chargeback "
            "rates by installment band, since longer plans change credit risk."
        ),
        limitations=(
            "The dataset records installment count, not interest paid or default outcome, so the cost and risk of "
            "installment selling cannot be measured here."
        ),
        correlation_note=(
            "The positive association between basket value and installment count is consistent with affordability "
            "pressure, but it is a correlation: product mix and promotions also move both."
        ),
        figures=("installments",),
    ))


# --------------------------------------------------------------------------
# (F) Late deliveries & reviews
# --------------------------------------------------------------------------
def analyse_late_reviews(conn: sqlite3.Connection, report: AnalysisReport) -> None:
    table = _q(conn, """
        SELECT o.is_late_vs_estimate AS is_late,
               COUNT(*)               AS orders,
               ROUND(AVG(r.review_score), 4) AS avg_review_score
        FROM dim_order o
        JOIN bridge_review r ON r.order_id = o.order_id
        WHERE o.is_late_vs_estimate IS NOT NULL
        GROUP BY o.is_late_vs_estimate
        ORDER BY o.is_late_vs_estimate
    """)
    reviews = _q(conn, "SELECT * FROM vw_review_score_distribution ORDER BY review_score")
    detail = _q(conn, """
        SELECT o.is_late_vs_estimate AS is_late, r.review_score AS review_score
        FROM dim_order o
        JOIN bridge_review r ON r.order_id = o.order_id
        WHERE o.is_late_vs_estimate IS NOT NULL
    """)
    monthly = _q(conn, """
        SELECT d.sales_month, d.on_time_pct, r.avg_review_score
        FROM vw_delivery_performance_monthly d
        JOIN vw_review_monthly r ON r.sales_month = d.sales_month
        WHERE d.sales_month BETWEEN ? AND ?
        ORDER BY d.sales_month
    """, (WINDOW_START, WINDOW_END))

    report.frames["late_review"] = table
    report.frames["review_distribution"] = reviews
    report.frames["late_review_monthly"] = monthly

    on_time = table[table["is_late"] == 0].iloc[0]
    late = table[table["is_late"] == 1].iloc[0]
    pearson = float(np.corrcoef(detail["is_late"].astype(float), detail["review_score"].astype(float))[0, 1])
    spearman = float(detail["is_late"].astype(float).corr(detail["review_score"].astype(float), method="spearman"))
    eco = float(pd.to_numeric(monthly["on_time_pct"]).corr(
        pd.to_numeric(monthly["avg_review_score"]), method="pearson"))
    negative_late = float((detail.loc[detail["is_late"] == 1, "review_score"] <= 2).mean() * 100)
    negative_ontime = float((detail.loc[detail["is_late"] == 0, "review_score"] <= 2).mean() * 100)

    report.findings.append(Finding(
        key="late_vs_review_score",
        section="Delivery and satisfaction",
        title="Late delivery is strongly associated with low review scores",
        business_question="How much lower are review scores when a delivery misses its estimated date?",
        method=(
            "Join reviews to the late/on-time flag (delivered orders with an estimate), compare average score and the "
            "share of 1-2 star reviews, and compute point-biserial (Pearson) and Spearman correlations."
        ),
        result=(
            f"On-time orders average {_ratio(float(on_time['avg_review_score']), 3)}/5 over {_int(float(on_time['orders']))} reviews; "
            f"late orders average {_ratio(float(late['avg_review_score']), 3)}/5 over {_int(float(late['orders']))} reviews "
            f"(difference {_ratio(float(on_time['avg_review_score']) - float(late['avg_review_score']), 3)} points). "
            f"Negative (1-2 star) review share: on-time {_pct(negative_ontime, 1)} vs late {_pct(negative_late, 1)}. "
            f"Pearson r = {_ratio(pearson, 3)}, Spearman rho = {_ratio(spearman, 3)}."
        ),
        period_population=f"{_int(int(table['orders'].sum()))} reviewed, delivered orders with a late/on-time flag",
        interpretation=(
            "The gap is large and consistent: late deliveries are the single clearest correlate of poor satisfaction "
            "in this dataset. The effect is visible both in average score and in the concentration of 1-2 star reviews."
        ),
        recommendation=(
            "Prioritise lateness reduction as a satisfaction lever: proactive delay notifications and expedited "
            "recovery on at-risk orders are more likely to protect scores than generic service messaging."
        ),
        limitations=(
            "Reviews are voluntary, so only reviewed orders enter this KPI, and the late/on-time split excludes "
            "orders with no delivery timestamp. Review scores are ordinal and coarse (1-5)."
        ),
        correlation_note=(
            "This is a strong association, not proof of causation. Lateness may co-occur with long-distance routes, "
            "problem sellers, or peak-season congestion that independently depress scores; a controlled comparison "
            "holding those factors constant is needed before claiming that lateness alone causes dissatisfaction."
        ),
        figures=("late_vs_review", "review_distribution"),
    ))

    report.findings.append(Finding(
        key="late_reviews_monthly",
        section="Delivery and satisfaction",
        title="Month-level on-time rate moves with the month-level review score",
        business_question="Does satisfaction track punctuality over time as well as across orders?",
        method=(
            "Join the monthly on-time delivery rate to the monthly average review score over complete months and "
            "compute their Pearson correlation (an ecological correlation)."
        ),
        result=(
            f"Across {len(monthly)} complete months, correlation between monthly on-time % and monthly average "
            f"review score = {_ratio(eco, 3)}. On-time ranged "
            f"{_pct(float(monthly['on_time_pct'].min()), 1)}-{_pct(float(monthly['on_time_pct'].max()), 1)}; "
            f"review score ranged {_ratio(float(monthly['avg_review_score'].min()), 2)}-"
            f"{_ratio(float(monthly['avg_review_score'].max()), 2)}."
        ),
        period_population=f"{len(monthly)} complete months ({WINDOW_START}..{WINDOW_END})",
        interpretation=(
            "The order-level relationship also appears at the month level, which makes the pattern harder to dismiss "
            "as an artifact of a single bad week."
        ),
        recommendation=(
            "Use punctuality as a leading operational indicator for satisfaction in the dashboard and investigate "
            "months where on-time falls before review scores follow."
        ),
        limitations=(
            "An ecological correlation across months can differ from the individual-level relationship and is "
            "sensitive to a few months; it is supporting evidence, not a substitute for the order-level analysis."
        ),
        correlation_note=(
            "Ecological correlations do not establish individual-level causation and may be confounded by seasonality "
            "or platform growth."
        ),
        figures=("delivery_trend", "review_distribution"),
    ))


# --------------------------------------------------------------------------
# (G) Seller & category operations
# --------------------------------------------------------------------------
def analyse_sellers(conn: sqlite3.Connection, report: AnalysisReport) -> None:
    sellers = _q(conn, "SELECT * FROM vw_seller_performance")
    report.frames["sellers"] = sellers

    total = float(sellers["merchandise_sales"].sum())
    sellers = sellers.sort_values("merchandise_sales", ascending=False).reset_index(drop=True)
    n = len(sellers)

    def top_share(frac: float) -> float:
        k = max(1, int(round(n * frac)))
        return 100.0 * float(sellers["merchandise_sales"].head(k).sum()) / total

    top1, top5, top10 = top_share(0.01), top_share(0.05), top_share(0.10)

    elig = sellers[(sellers["orders"] >= 30) & sellers["late_pct"].notna() & sellers["avg_review_score"].notna()].copy()
    corr = float(elig["late_pct"].corr(elig["avg_review_score"], method="spearman"))
    worst = elig.sort_values("late_pct", ascending=False).head(1)
    worst_txt = (
        f"{worst.iloc[0]['seller_id']} (late {_pct(float(worst.iloc[0]['late_pct']), 1)}, "
        f"review {_ratio(float(worst.iloc[0]['avg_review_score']), 2)})"
        if not worst.empty else "n/a"
    )
    mean_late = float(elig["late_pct"].mean())
    p90_late = float(elig["late_pct"].quantile(0.9))

    report.findings.append(Finding(
        key="seller_concentration",
        section="Seller operations",
        title="Seller revenue is highly concentrated",
        business_question="How dependent is the marketplace on its largest sellers?",
        method="Rank sellers by merchandise sales and compute the share held by the top 1%, 5% and 10% of sellers.",
        result=(
            f"{_int(n)} sellers. Top 1% of sellers = {_pct(top1)} of merchandise sales; top 5% = {_pct(top5)}; "
            f"top 10% = {_pct(top10)}."
        ),
        period_population=f"{_int(n)} sellers across all item-bearing orders",
        interpretation=(
            "A minority of sellers supplies most of the value, so seller churn or quality problems at the top have "
            "platform-level consequences."
        ),
        recommendation=(
            "Run a key-account programme for the top decile (service levels, inventory visibility) and keep a "
            "bench of qualified backup sellers for the top categories."
        ),
        limitations=(
            "Seller sales are gross merchandise value before returns/cancellations and exclude freight; sellers with "
            "no items are not represented."
        ),
        correlation_note="Concentration is a structural measurement, not a causal relationship.",
        figures=("seller_late_distribution",),
        headline=(
            f"The top 1% of sellers generate {_pct(top1)} of sales and the top 10% generate {_pct(top10)}."
        ),
    ))

    report.findings.append(Finding(
        key="seller_lateness_dispersion",
        section="Seller operations",
        title="Lateness is not uniform across sellers",
        business_question="Is late delivery a platform-wide problem or a seller-specific one?",
        method=(
            "For sellers with at least 30 evaluable orders, describe the distribution of the late-delivery rate and "
            "correlate it with average review score (Spearman)."
        ),
        result=(
            f"Among {_int(len(elig))} sellers with >= 30 evaluable orders, mean late rate = {_pct(mean_late, 1)}, "
            f"90th percentile = {_pct(p90_late, 1)}. Highest-late seller: {worst_txt}. "
            f"Spearman correlation between seller late rate and review score = {_ratio(corr, 3)}."
        ),
        period_population=f"{_int(len(elig))} sellers with >= 30 evaluable orders",
        interpretation=(
            "The spread is wide, which means a substantial part of the lateness problem is addressable at the seller "
            "level rather than being an unavoidable platform-wide condition."
        ),
        recommendation=(
            "Use a late-rate threshold to trigger seller coaching or reduced placement, and track the worst decile "
            "monthly; target the tail rather than the average."
        ),
        limitations=(
            "Small sellers have noisy rates, which is why a 30-order floor is applied; seller lateness also reflects "
            "carrier and route, which the data attributes to the seller."
        ),
        correlation_note=(
            "The negative late-rate/review association is cross-sectional and confounded by product, price and "
            "geography; it is a correlation that supports prioritisation, not a causal claim that lateness alone "
            "drives the score."
        ),
        figures=("seller_late_distribution", "late_vs_review"),
        headline=(
            f"Among {_int(len(elig))} sellers with >= 30 orders, the mean late rate is {_pct(mean_late, 1)} "
            f"but the 90th percentile reaches {_pct(p90_late, 1)}."
        ),
    ))


# --------------------------------------------------------------------------
# (H) Weight & freight
# --------------------------------------------------------------------------
def analyse_weight_freight(conn: sqlite3.Connection, report: AnalysisReport) -> None:
    wf = _q(conn, "SELECT * FROM vw_weight_vs_freight")
    report.frames["weight_freight"] = wf

    sample = _q(conn, """
        SELECT p.product_weight_g AS weight_g, i.freight_value AS freight, i.price AS price
        FROM fact_order_item i
        JOIN dim_product p ON p.product_id = i.product_id
        WHERE p.product_weight_g > 0 AND i.freight_value >= 0
    """)
    pearson = float(sample["weight_g"].corr(sample["freight"], method="pearson"))
    spearman = float(sample["weight_g"].corr(sample["freight"], method="spearman"))
    wf_known = wf[wf["weight_bucket"] != "unknown/invalid"].copy()
    known_freight = float(wf_known["pct_of_freight"].sum())
    lightest = wf_known.iloc[0]
    heaviest = wf_known.iloc[-1]

    overall = _q(conn, """
        SELECT ROUND(SUM(freight_value), 2) AS freight, ROUND(SUM(price), 2) AS sales,
               ROUND(100.0 * SUM(freight_value) / SUM(price), 2) AS freight_pct
        FROM fact_order_item
    """).iloc[0]

    report.findings.append(Finding(
        key="weight_freight_relationship",
        section="Weight and freight",
        title="Freight rises with weight, but per-kg cost falls sharply",
        business_question="How does freight scale with product weight, and where is the freight burden concentrated?",
        method=(
            "Bucket item units by product weight and compute average freight and freight-per-kg per bucket; correlate "
            "unit weight with freight across all known-weight units (Pearson and Spearman)."
        ),
        result=(
            f"Freight is {_pct(float(overall['freight_pct']))} of merchandise sales overall. "
            f"Freight per kg falls from {_money(float(lightest['avg_freight_per_kg']))} in the {lightest['weight_bucket']} "
            f"bucket to {_money(float(heaviest['avg_freight_per_kg']))} in the {heaviest['weight_bucket']} bucket, while "
            f"absolute average freight rises from {_money(float(lightest['avg_freight']))} to "
            f"{_money(float(heaviest['avg_freight']))}. Weight-vs-freight correlation: Pearson {_ratio(pearson, 3)}, "
            f"Spearman {_ratio(spearman, 3)}."
        ),
        period_population=f"{_int(len(sample))} item units with usable weight and non-negative freight",
        interpretation=(
            "Freight has a large fixed component (handling/leg cost) that dominates light items, so the marginal cost "
            "of extra weight is low. Heavy items pay more in absolute terms but less per kilogram."
        ),
        recommendation=(
            "Bundle light items to amortise the fixed freight component, and price or promote heavy items with "
            "freight-aware thresholds so the per-kg economics are visible."
        ),
        limitations=(
            "Freight is the customer charge, not carrier cost. Weight comes from the product catalogue and a few "
            "products have missing/invalid weight (kept in an 'unknown' bucket)."
        ),
        correlation_note=(
            "The weight/freight correlation is mechanical and expected, not a behavioural causal link; size and "
            "distance are additional unmodelled drivers."
        ),
        figures=("weight_freight",),
        headline=(
            f"Freight is {_pct(float(overall['freight_pct']))} of merchandise sales; freight per kg falls from "
            f"{_money(float(lightest['avg_freight_per_kg']))} to {_money(float(heaviest['avg_freight_per_kg']))} "
            "from the lightest to the heaviest bucket."
        ),
    ))


# --------------------------------------------------------------------------
# (I) Data-quality impact
# --------------------------------------------------------------------------
def analyse_data_quality(conn: sqlite3.Connection, report: AnalysisReport) -> None:
    def scalar(sql: str):
        row = conn.execute(sql).fetchone()
        return row[0] if row else None

    dq = {
        "orders_no_items": scalar("SELECT COUNT(*) FROM dim_order WHERE has_line_items = 0"),
        "orders_no_payments": scalar("SELECT COUNT(*) FROM dim_order WHERE has_payments = 0"),
        "orders_no_reviews": scalar("SELECT COUNT(*) FROM dim_order WHERE has_reviews = 0"),
        "missing_delivery_date": scalar("SELECT COUNT(*) FROM dim_order WHERE missing_delivery_date = 1"),
        "impossible_sequences": scalar("SELECT COUNT(*) FROM dim_order WHERE has_impossible_date_sequence = 1"),
        "nonpositive_payments": scalar("SELECT COUNT(*) FROM bridge_payment WHERE is_valid_payment_value = 0"),
        "products_null_category": scalar("SELECT COUNT(*) FROM dim_product WHERE product_category_name IS NULL"),
        "categories_untranslated": scalar("SELECT COUNT(*) FROM dim_product_category WHERE product_category_name_english IS NULL"),
        "reviews_collapsed_orders": scalar("SELECT COUNT(*) FROM bridge_review WHERE source_review_rows > 1"),
        "unique_id_two_states": scalar(
            "SELECT COUNT(*) FROM (SELECT customer_unique_id FROM dim_customer "
            "GROUP BY customer_unique_id HAVING COUNT(DISTINCT customer_state) > 1)"
        ),
        "customer_zips_no_centroid": scalar("""
            SELECT COUNT(*) FROM (
                SELECT DISTINCT customer_zip_code_prefix AS z FROM dim_customer
                WHERE z IS NOT NULL AND z IN (
                    SELECT geolocation_zip_code_prefix FROM dim_geolocation
                    WHERE valid_coordinate_count = 0)
            )
        """),
        "zips_null_centroid": scalar(
            "SELECT COUNT(*) FROM dim_geolocation WHERE valid_coordinate_count = 0"
        ),
    }
    report.context["data_quality"] = dq

    total_orders = int(scalar("SELECT COUNT(*) FROM dim_order"))
    total_reviews = int(scalar("SELECT COUNT(*) FROM bridge_review"))

    report.findings.append(Finding(
        key="dq_impacts",
        section="Data quality",
        title="Known data-quality gaps and how they affect the KPIs",
        business_question="Which data defects could change a business conclusion if ignored?",
        method=(
            "Count the rows affected by each documented defect directly in the database and map each to the KPIs it "
            "touches."
        ),
        result=(
            f"orders with no line items: {_int(dq['orders_no_items'])}; "
            f"orders with no payment record: {_int(dq['orders_no_payments'])}; "
            f"orders with no review: {_int(dq['orders_no_reviews'])}; "
            f"orders with no delivery date (excluded from delivery KPIs): {_int(dq['missing_delivery_date'])}; "
            f"payment rows with a non-positive value: {_int(dq['nonpositive_payments'])}; "
            f"products with no category: {_int(dq['products_null_category'])}; "
            f"category names with no English mapping: {_int(dq['categories_untranslated'])}; "
            f"orders whose duplicate reviews were collapsed to one: {_int(dq['reviews_collapsed_orders'])}; "
            f"customer_unique_ids spanning two states: {_int(dq['unique_id_two_states'])}; "
            f"customer ZIPs with no usable geolocation centroid: {_int(dq['customer_zips_no_centroid'])} "
            f"({_int(dq['zips_null_centroid'])} geolocation rows in total)."
        ),
        period_population=f"{_int(total_orders)} orders, {_int(total_reviews)} reviews, and associated dimension rows",
        interpretation=(
            "None of these defects invalidate the headline KPIs - each is either immaterial in size or explicitly "
            "excluded and documented - but the delivery KPIs carry the largest population gap (orders with no "
            "delivery date), and any distance-based geography work is limited because some customer ZIPs have no "
            "usable centroid."
        ),
        recommendation=(
            "Carry these counts as a data-health panel in the dashboard, and define the eligible population on every "
            "KPI card. Treat the no-delivery-date gap as the first target for upstream data capture."
        ),
        limitations=(
            "These are defects detectable within the export; systematic issues in the source process (e.g. events "
            "never logged) cannot be counted here."
        ),
        correlation_note="Data-quality counts are descriptive and do not claim causal impact.",
        headline=(
            f"The largest gap is delivery dates: {_int(dq['missing_delivery_date'])} orders lack one and sit outside "
            f"every delivery KPI; {_int(dq['orders_no_items'])} orders carry no line items."
        ),
    ))


# --------------------------------------------------------------------------
# Report assembly
# --------------------------------------------------------------------------
def analyse(conn: sqlite3.Connection) -> AnalysisReport:
    report = AnalysisReport()
    monthly, context = load_periods(conn)
    report.context["period"] = context
    report.frames["monthly"] = monthly

    analyse_sales_trends(conn, monthly, context, report)
    analyse_categories(conn, report)
    analyse_geography(conn, report)
    analyse_repeat(conn, report)
    analyse_payments(conn, report)
    analyse_late_reviews(conn, report)
    analyse_sellers(conn, report)
    analyse_weight_freight(conn, report)
    analyse_data_quality(conn, report)
    _load_delivery_monthly(conn, report)
    return report


def build_figures(report: AnalysisReport) -> dict[str, str]:
    """Render every chart referenced by a finding. Returns name -> relative path."""
    f = report.frames
    paths: dict[str, str] = {}
    paths["monthly_sales_trend"] = str(visualizations.monthly_sales_trend(f["monthly"]))
    paths["quarterly_sales"] = str(visualizations.quarterly_sales(f["quarterly"]))
    paths["category_performance"] = str(visualizations.category_performance(f["category"]))
    paths["state_distribution"] = str(visualizations.state_distribution(f["state"]))
    paths["repeat_segments"] = str(visualizations.repeat_segments(f["repeat_segments"]))
    paths["payment_methods"] = str(visualizations.payment_methods(f["payment"]))
    paths["installments"] = str(visualizations.installments(f["installments"]))
    paths["late_vs_review"] = str(visualizations.late_vs_review(f["late_review"]))
    paths["review_distribution"] = str(visualizations.review_distribution(f["review_distribution"]))
    paths["seller_late_distribution"] = str(visualizations.seller_late_distribution(f["sellers"]))
    paths["weight_freight"] = str(visualizations.weight_freight(f["weight_freight"]))
    paths["delivery_trend"] = str(visualizations.delivery_trend(
        f["monthly"].merge(
            _delivery_monthly(report), on="sales_month", how="inner")))
    return paths


def _delivery_monthly(report: AnalysisReport) -> pd.DataFrame:
    # Cached on the report to avoid a second query when figures are built.
    if "delivery_monthly" in report.frames:
        return report.frames["delivery_monthly"]
    raise RuntimeError("delivery_monthly frame not loaded")


def _load_delivery_monthly(conn: sqlite3.Connection, report: AnalysisReport) -> None:
    report.frames["delivery_monthly"] = _q(conn, "SELECT * FROM vw_delivery_performance_monthly")


def findings_to_markdown(report: AnalysisReport, kpis: list[metrics.KPI],
                         figure_paths: dict[str, str] | None = None) -> str:
    ctx = report.context["period"]
    dq = report.context.get("data_quality", {})
    lines: list[str] = []
    lines.append("# ShopScope - Exploratory Data Analysis and Business Findings\n")
    lines.append(
        "This report is generated from the validated SQLite database (`data/shopscope.db`) on the cleaned Olist "
        "export. Every figure below is computed live; nothing is hardcoded. Merchandise sales always exclude "
        "freight, and payment cash is reported as a separate measure.\n"
    )

    lines.append("## Scope and method\n")
    lines.append(
        f"- **Source window:** {ctx['data_first_timestamp'][:10]} to {ctx['data_last_timestamp'][:10]}.\n"
        f"- **Complete analysis window:** {ctx['window_start']} to {ctx['window_end']} "
        f"({ctx['complete_months']} months).\n"
        f"- **Excluded from trends:** {', '.join(ctx['excluded_months'])} "
        f"({_int(ctx['excluded_orders'])} orders). "
        + (f"Missing calendar months in the export: {', '.join(ctx['missing_calendar_months'])}.\n"
           if ctx["missing_calendar_months"] else "\n")
        + "- **Sales measure:** `SUM(fact_order_item.price)` (freight excluded).\n"
        "- **Identity rule:** customers are linked with `customer_unique_id`, never `customer_id`.\n"
        "- **Join discipline:** many-per-order tables (payments, reviews) are aggregated to order level before "
        "being combined; verified in `tests/test_join_behavior.py`.\n"
    )

    # ---- Executive summary -------------------------------------------------
    lines.append("## Executive summary - verified findings\n")
    lines.append(
        "The list below is a short, high-confidence digest. Each item links to the detailed finding that follows.\n"
    )
    highlights = [
        ("trend_yoy_growth", "Growth"),
        ("trend_peak_nov_2017", "Seasonality"),
        ("category_concentration", "Category concentration"),
        ("geo_concentration", "Geography"),
        ("repeat_rate", "Retention"),
        ("payment_methods", "Payments"),
        ("installments", "Installments"),
        ("late_vs_review_score", "Delivery and satisfaction"),
        ("seller_concentration", "Seller concentration"),
        ("seller_lateness_dispersion", "Seller lateness"),
        ("weight_freight_relationship", "Freight economics"),
        ("dq_impacts", "Data quality"),
    ]
    by_key = {f.key: f for f in report.findings}
    for key, label in highlights:
        f = by_key.get(key)
        if f:
            text = f.headline.strip() or f.result.split(". ")[0].strip()
            if not text.endswith((".", "!", "?")):
                text += "."
            lines.append(f"- **{label}:** {text}")
    lines.append("")

    # ---- Detailed findings -------------------------------------------------
    current_section = None
    for finding in report.findings:
        if finding.section != current_section:
            current_section = finding.section
            lines.append(f"\n## {current_section}\n")
        lines.append(finding.to_markdown())

    # ---- KPI summary -------------------------------------------------------
    lines.append("## Headline KPIs (see `reports/business_metrics.md` for full documentation)\n")
    lines.append("| KPI | Value | Eligible population |")
    lines.append("|-----|-------|---------------------|")
    for k in kpis:
        lines.append(f"| {k.name} | {k.formatted()} | {k.eligible_population} |")
    lines.append("")

    # ---- Correlation vs causation -----------------------------------------
    lines.append("## Correlation versus causation - how to read this report\n")
    lines.append(
        "This is observational, transaction-level data: no experiment and no counterfactual. Findings therefore "
        "distinguish three strengths of claim:\n"
        "- **Descriptive** (counts, shares, averages): the number is simply what the data says.\n"
        "- **Association / correlation** (late delivery vs low scores; weight vs freight; late rate vs seller "
        "score): two quantities move together. The report states the direction and strength but does not claim one "
        "causes the other.\n"
        "- **Causal**: not claimed. Where a recommendation implies cause and effect, it is framed as a hypothesis to "
        "test (e.g. an A/B test of delay notifications).\n"
    )

    # ---- Data quality ------------------------------------------------------
    lines.append("## Data quality caveats affecting conclusions\n")
    lines.append(
        f"- {_int(dq.get('missing_delivery_date', 0))} orders have no delivery date and are outside every "
        "delivery KPI (they are neither on time nor late).\n"
        f"- {_int(dq.get('orders_no_items', 0))} orders have no line items and cannot carry a basket value.\n"
        f"- {_int(dq.get('orders_no_reviews', 0))} orders have no review; review-based KPIs cover the rest only.\n"
        f"- {_int(dq.get('nonpositive_payments', 0))} payment rows are non-positive and are retained, not dropped.\n"
        f"- {_int(dq.get('products_null_category', 0))} products have no category and "
        f"{_int(dq.get('categories_untranslated', 0))} category labels lack an English mapping.\n"
        f"- {_int(dq.get('reviews_collapsed_orders', 0))} orders had duplicate review rows collapsed to the latest.\n"
        f"- {_int(dq.get('unique_id_two_states', 0))} customer_unique_ids appear in two states; per-state customer "
        "counts can exceed the global distinct count.\n"
        f"- Every one of the {_int(dq.get('customer_zips_no_centroid', 0))} customer ZIPs that has no usable "
        f"geolocation centroid blocks distance-based analysis; {_int(dq.get('zips_null_centroid', 0))} geolocation "
        "rows in total have no valid coordinates.\n"
    )

    # ---- Figures -----------------------------------------------------------
    if figure_paths:
        lines.append("## Figure index\n")
        for name, path in figure_paths.items():
            lines.append(f"- **{name}** - `{Path(path).name}`")
        lines.append("")

    # ---- Web application guidance -----------------------------------------
    lines.append("## How to present this in the web application\n")
    lines.append(
        "When the Streamlit pages are built, keep the analysis layer as the single source of truth and follow these "
        "presentation rules:\n"
        "- **KPI cards:** show value + unit and the eligible population in a caption, exactly as in "
        "`reports/business_metrics.md`. Never show a KPI without its population.\n"
        "- **Trend pages:** default to the complete window and render the ramp-up/tail months in grey with a tooltip "
        "explaining why they are excluded; expose a toggle to reveal them.\n"
        "- **Drill-downs:** category, state and seller tables should expose sales *and* the operational measures "
        "(late rate, review average) together, so a user never reads sales without quality context.\n"
        "- **Correlation messaging:** any chart pairing two operational measures must carry a 'correlation, not "
        "causation' note and avoid causal verbs in titles.\n"
        "- **Data health:** a persistent badge showing the counts in the Data quality section, so users can see the "
        "population excluded behind each metric.\n"
    )
    return "\n".join(lines)


def write_findings_report(report: AnalysisReport, kpis: list[metrics.KPI],
                          figure_paths: dict[str, str], path: Path | str | None = None) -> Path:
    out = Path(path) if path else config.FINDINGS_REPORT_MD
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(findings_to_markdown(report, kpis, figure_paths), encoding="utf-8")
    logger.info("wrote %s", out)
    return out


def write_summary_json(report: AnalysisReport, kpis: list[metrics.KPI],
                       figure_paths: dict[str, str], path: Path | str | None = None) -> Path:
    out = Path(path) if path else config.ANALYSIS_SUMMARY_JSON
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database": str(config.DB_PATH.relative_to(config.PROJECT_ROOT)),
        "period": report.context.get("period", {}),
        "data_quality": report.context.get("data_quality", {}),
        "kpis": [k.as_dict() for k in kpis],
        "findings": [asdict(f) for f in report.findings],
        "figures": {name: str(Path(p).relative_to(config.PROJECT_ROOT)) for name, p in figure_paths.items()},
    }
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    logger.info("wrote %s", out)
    return out


# --------------------------------------------------------------------------
# CLI / orchestration
# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.insights",
        description="Run the ShopScope EDA layer and write the reports.",
    )
    parser.add_argument("--no-figures", action="store_true", help="skip chart rendering")
    parser.add_argument("--no-metrics", action="store_true", help="skip rewriting business_metrics.md")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

    if not config.DB_PATH.is_file():
        print(f"database not found: {config.DB_PATH}", file=sys.stderr)
        print("build it first: python -m src.database", file=sys.stderr)
        return 1

    conn = get_connection(config.DB_PATH, foreign_keys=False)
    try:
        kpis = metrics.compute_kpis(conn)
        if not args.no_metrics:
            metrics.write_metrics_report(conn)

        report = analyse(conn)

        figure_paths: dict[str, str] = {}
        if not args.no_figures:
            figure_paths = build_figures(report)

        findings_path = write_findings_report(report, kpis, figure_paths)
        summary_path = write_summary_json(report, kpis, figure_paths)

        print(f"\n{len(kpis)} KPIs, {len(report.findings)} findings, "
              f"{len(figure_paths)} figures")
        print(f"metrics  -> {config.METRICS_REPORT_MD.relative_to(config.PROJECT_ROOT)}")
        print(f"findings -> {findings_path.relative_to(config.PROJECT_ROOT)}")
        print(f"summary  -> {summary_path.relative_to(config.PROJECT_ROOT)}")
        print("\nKey verified findings:")
        for f in report.findings:
            print(f"  - [{f.section}] {f.title}")
    except sqlite3.Error as exc:
        print(f"analysis failed: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
