"""Executive Overview - marketplace health at a glance."""

from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st
import pandas as pd

from src.dashboard import charts, components as ui, data as ds
from src.dashboard import formatting as fmt

STATUS_COLORS = {
    "delivered": charts.GREEN,
    "shipped": charts.ACCENT,
    "invoiced": charts.AMBER,
    "processing": "#0891b2",
    "created": "#7c3aed",
    "approved": charts.ACCENT3,
    "canceled": charts.RED,
    "unavailable": "#9ca3af",
}


def _pct_share(numerator: pd.Series, denominator: float) -> pd.Series:
    """Percentage share of ``numerator`` over ``denominator``.

    Guards against divide-by-zero (a filter combination can legitimately yield
    a zero total, e.g. statuses with no orders); such rows show 0.00% rather
    than raising or rendering ``inf``.
    """
    denominator = float(denominator) if denominator and pd.notna(denominator) else 0.0
    if denominator == 0:
        return pd.Series(0.0, index=numerator.index)
    return (100.0 * numerator / denominator).round(2)

ui.page_header(
    "Executive Overview",
    "Marketplace health at a glance: revenue, order volume, fulfilment reliability and "
    "customer sentiment. KPI cards are computed over the full dataset; every chart below "
    "honours the sidebar filters.",
)

ui.sidebar_note("KPI cards are all-time values. Use the filters to scope the charts below.")

# --------------------------------------------------------------------------
# Sidebar filters
# --------------------------------------------------------------------------
start, end = ui.date_range_filter("exec_dates")
statuses = ui.multiselect_filter("exec_status", "Order status", ds.status_options())
ui.filter_caption(start, end, **({"Status": statuses} if statuses else {}))

# --------------------------------------------------------------------------
# KPI cards (all-time, verified layer)
# --------------------------------------------------------------------------
ui.section("Key performance indicators")
ui.kpi_grid(
    ui.cards_from_kpis(
        [
            "total_merchandise_sales",
            "total_unique_orders",
            "average_order_value",
            "delivery_completion_rate",
            "on_time_delivery_rate",
            "average_review_score",
            "repeat_customer_rate",
            "cancellation_rate",
        ]
    ),
    cols=4,
)

with st.expander("All 14 KPIs: definitions, eligible populations and limitations"):
    kdf = ui.render_kpi_detail_table(ds.kpis())
    ui.table(kdf, height=460)
    ui.download_csv(kdf, "shopscope_kpis.csv", "Download KPI catalogue (CSV)", key="exec_kpi_csv")

# --------------------------------------------------------------------------
# Monthly trend
# --------------------------------------------------------------------------
monthly = ds.monthly_series(start, end, statuses=statuses)

ui.section(
    "Monthly sales and order volume",
    "Merchandise sales (item prices only; freight shown separately). Grey bars are "
    "incomplete months outside the 2017-01 → 2018-08 window.",
)
ctx = ds.period_context()
excluded = ", ".join(ctx["excluded_months"]) or "none"
missing = ", ".join(ctx["missing_calendar_months"]) or "none"
ui.note(
    f"Complete analysis window: **{ctx['window_start']} → {ctx['window_end']}** "
    f"({ctx['complete_months']} complete months). Excluded from trends: {excluded} "
    f"({ctx['excluded_orders']:,} orders). Calendar month(s) with no orders: {missing}."
)
if ui.empty(monthly.empty, "No orders match the current filters."):
    st.stop()
ui.chart(charts.monthly_trend(monthly), key="exec_monthly")
ui.download_csv(monthly, "monthly_trend.csv", "Download monthly trend (CSV)", key="exec_monthly_csv")

# --------------------------------------------------------------------------
# Order status + top categories
# --------------------------------------------------------------------------
status_df = ds.status_breakdown(start, end, statuses=statuses)
ui.section("Order status distribution")
c1, c2 = st.columns([1.15, 1])
with c1:
    ui.chart(charts.pie(status_df, "order_status", "orders",
                        title="Orders by final status",
                        color_map=STATUS_COLORS), key="exec_status_pie")
with c2:
    ui.table(
        status_df.assign(
            status_share=_pct_share(status_df["orders"], status_df["orders"].sum())
        ),
        column_config={
            "order_status": st.column_config.TextColumn("Status"),
            "orders": st.column_config.NumberColumn("Orders", format="%d"),
            "status_share": st.column_config.NumberColumn("%", format="%.2f%%"),
            "merchandise_sales": st.column_config.NumberColumn("Sales (R$)", format="R$ %,.2f"),
            "freight_charges": st.column_config.NumberColumn("Freight (R$)", format="R$ %,.2f"),
        },
        height=280,
    )
ui.download_csv(status_df, "order_status_distribution.csv", "Download status breakdown (CSV)",
                key="exec_status_csv")

cats = ds.category_series(start, end, statuses=statuses)
ui.section("Top product categories", "Ranked by merchandise sales (item prices).")
top_c1, top_c2 = st.columns([1.35, 1])
with top_c1:
    ui.chart(charts.ranking_bar(cats, "category", "merchandise_sales", top_n=10,
                                title="Top 10 categories by sales"), key="exec_cats")
with top_c2:
    ui.table(
        cats.head(10).assign(
            share=_pct_share(cats.head(10)["merchandise_sales"],
                             cats["merchandise_sales"].sum())
        ),
        column_config={
            "category": st.column_config.TextColumn("Category"),
            "orders": st.column_config.NumberColumn("Orders", format="%d"),
            "merchandise_sales": st.column_config.NumberColumn("Sales (R$)", format="R$ %,.2f"),
            "share": st.column_config.NumberColumn("%", format="%.2f%%"),
        },
        height=280,
    )
ui.download_csv(cats, "category_performance.csv", "Download category rankings (CSV)",
                key="exec_cats_csv")

# --------------------------------------------------------------------------
# State performance
# --------------------------------------------------------------------------
state_df = ds.state_series(start, end, statuses=statuses)
ui.section("State-wise performance", "Orders and merchandise sales by customer state.")
sc1, sc2 = st.columns([1.35, 1])
with sc1:
    ui.chart(charts.ranking_bar(state_df, "customer_state", "orders", top_n=15,
                                title="Top 15 states by order volume"), key="exec_states")
with sc2:
    ui.table(
        state_df,
        column_config={
            "customer_state": st.column_config.TextColumn("State"),
            "customers": st.column_config.NumberColumn("Customers", format="%d"),
            "orders": st.column_config.NumberColumn("Orders", format="%d"),
            "merchandise_sales": st.column_config.NumberColumn("Sales (R$)", format="R$ %,.2f"),
            "freight_charges": st.column_config.NumberColumn("Freight (R$)", format="R$ %,.2f"),
        },
        height=280,
    )
ui.download_csv(state_df, "state_performance.csv", "Download state performance (CSV)",
                key="exec_state_csv")