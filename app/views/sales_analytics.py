"""Sales Analytics - trends, categories, price, freight and rankings."""

from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import numpy as np
import streamlit as st

from src.dashboard import charts, components as ui, data as ds
from src.dashboard import formatting as fmt

ui.page_header(
    "Sales Analytics",
    "Time-series revenue/volume, category mix, price and freight analysis. "
    "All figures use merchandise sales (fact_order_item.price); freight is separate.",
)

start, end = ui.date_range_filter("sales_dates")
states = ui.multiselect_filter("sales_states", "Customer state", ds.state_options())
categories = ui.multiselect_filter("sales_cats", "Product category", ds.category_options())
statuses = ui.multiselect_filter("sales_status", "Order status", ds.status_options())
ui.filter_caption(start, end,
                  **({"States": states} if states else {}),
                  **({"Categories": categories} if categories else {}),
                  **({"Status": statuses} if statuses else {}))

# --------------------------------------------------------------------------
# Monthly & quarterly
# --------------------------------------------------------------------------
monthly = ds.monthly_series(start, end, states=states, categories=categories, statuses=statuses)
quarterly = ds.quarterly_series(start, end, states=states, categories=categories, statuses=statuses)

ui.section("Monthly and quarterly trends")
if ui.empty(monthly.empty):
    st.stop()

c1, c2 = st.columns([1.2, 0.9])
with c1:
    ui.chart(charts.monthly_trend(monthly), key="sales_monthly")
with c2:
    ui.table(
        quarterly,
        column_config={
            "quarter": st.column_config.TextColumn("Quarter"),
            "orders": st.column_config.NumberColumn("Orders", format="%d"),
            "merchandise_sales": st.column_config.NumberColumn("Sales (R$)", format="R$ %,.2f"),
            "months": st.column_config.NumberColumn("Months", format="%d"),
            "is_complete": st.column_config.CheckboxColumn("All months in window"),
        },
        height=320,
    )
ui.download_csv(monthly, "sales_monthly.csv", "Download monthly (CSV)", key="sales_monthly_csv")
ui.download_csv(quarterly, "sales_quarterly.csv", "Download quarterly (CSV)", key="sales_q_csv")

# --------------------------------------------------------------------------
# AOV & growth
# --------------------------------------------------------------------------
m = monthly.copy()
m["mom_growth_pct"] = m["merchandise_sales"].pct_change() * 100
ui.section("Order volume and average order value (AOV)")
c1, c2, c3 = st.columns(3)
with c1:
    total_orders = int(m["orders"].sum())
    total_sales = float(m["merchandise_sales"].sum())
    aov = round(total_sales / total_orders, 2) if total_orders else 0
    ui.kpi_card("Filtered orders", fmt.fmt_int(total_orders),
                caption="Sum over shown months")
with c2:
    ui.kpi_card("Filtered sales", fmt.fmt_currency(total_sales),
                caption="Merchandise sales (price)")
with c3:
    ui.kpi_card("AOV (filtered)", fmt.fmt_currency(aov),
                caption="Sales / orders")

ui.chart(charts.dual_axis(m, "sales_month", "merchandise_sales", "avg_order_value",
                          bar_name="Sales (R$)", line_name="AOV (R$)",
                          bar_title="Sales (R$)", line_title="AOV (R$)",
                          title="Monthly sales and average order value"), key="sales_aov")

# --------------------------------------------------------------------------
# Price & freight
# --------------------------------------------------------------------------
pf = ds.price_freight_rows(start, end, states=states, categories=categories, statuses=statuses)
ui.section("Price and freight analysis",
           "Item prices and freight per line item. Freight is charged per item; totals "
           "match the item grain.")
ui.note("Histograms reflect the item lines that match filters (not necessarily orders).")

p1, p2 = st.columns(2)
with p1:
    ui.chart(charts.histogram(pf["price"].dropna(), bins=40, x_title="Item price (R$)",
                              title="Distribution of item prices"), key="price_hist")
with p2:
    ui.chart(charts.histogram(pf["freight_value"].dropna(), bins=40, x_title="Freight per item (R$)",
                              title="Distribution of freight per item"), key="freight_hist")

p3, p4 = st.columns(2)
with p3:
    freight_ratio = (pf["freight_value"].fillna(0) / pf["price"].where(pf["price"] > 0, np.nan)).dropna()
    ui.chart(charts.histogram(freight_ratio * 100, bins=40, x_title="Freight as % of price",
                              title="Freight/price ratio (excl. zero price)"), key="ratio_hist")
with p4:
    ui.chart(charts.box(pf, x=None, y="price", title="Item price - boxplot"), key="price_box")

st.caption("Price/freight grain: fact_order_item (one row per order+item). A single order "
           "with three items produces three rows.")

# --------------------------------------------------------------------------
# Category rankings
# --------------------------------------------------------------------------
cats = ds.category_series(start, end, states=states, statuses=statuses)
ui.section("Category rankings", "Sortable table ranked by merchandise sales. Filter to "
           "a single category to inspect it in detail.")
ui.table(
    cats.assign(share=lambda d: (100 * d["merchandise_sales"] / cats["merchandise_sales"].sum()).round(2)),
    column_config={
        "category": st.column_config.TextColumn("Category"),
        "orders": st.column_config.NumberColumn("Orders", format="%d"),
        "units": st.column_config.NumberColumn("Units", format="%d"),
        "merchandise_sales": st.column_config.NumberColumn("Sales (R$)", format="R$ %,.2f"),
        "avg_unit_price": st.column_config.NumberColumn("Avg unit price (R$)", format="R$ %,.2f"),
        "share": st.column_config.NumberColumn("% of sales", format="%.2f%%"),
    },
    height=400,
)
ui.download_csv(cats, "sales_category_rankings.csv", "Download category rankings (CSV)",
                key="sales_cats_csv")