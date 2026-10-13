"""Payments & Product Intelligence - payment mix, installments, weight/freight, seller performance."""

from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st

from src.dashboard import charts, components as ui, data as ds
from src.dashboard import formatting as fmt

ui.page_header(
    "Payments and Product Intelligence",
    "Payment methods and installments, payment-value distribution, product categories, "
    "product weight vs freight costs and seller performance.",
)

start, end = ui.date_range_filter("pay_dates", default_window=True)
states = ui.multiselect_filter("pay_states", "Customer state", ds.state_options())
categories = ui.multiselect_filter("pay_cats", "Product category", ds.category_options())
statuses = ui.multiselect_filter("pay_status", "Order status", ds.status_options())
ui.filter_caption(start, end,
                  **({"States": states} if states else {}),
                  **({"Categories": categories} if categories else {}),
                  **({"Status": statuses} if statuses else {}))

# --------------------------------------------------------------------------
# Payment methods
# --------------------------------------------------------------------------
pm = ds.payment_methods(start, end, states=states, statuses=statuses)
ui.section("Payment methods", "Grain: bridge_payment (an order using multiple methods appears "
           "multiple times). Value shares shown below are by payment value.")

c1, c2 = st.columns([1.15, 1])
with c1:
    ui.chart(charts.pie(pm, "payment_type", "payment_value",
                        title="Payment value by method"), key="pay_pie")
with c2:
    pm_tbl = pm.assign(pct=lambda d: (100 * d["payment_value"] / d["payment_value"].sum()).round(2))
    ui.table(
        pm_tbl,
        column_config={
            "payment_type": st.column_config.TextColumn("Payment type"),
            "payments": st.column_config.NumberColumn("Payment records", format="%d"),
            "orders": st.column_config.NumberColumn("Orders touched", format="%d"),
            "payment_value": st.column_config.NumberColumn("Value (R$)", format="R$ %,.2f"),
            "pct": st.column_config.NumberColumn("%", format="%.2f%%"),
            "avg_payment_value": st.column_config.NumberColumn("Avg (R$)", format="R$ %,.2f"),
        },
        height=260,
    )
ui.download_csv(pm, "payment_methods.csv", "Download payment methods (CSV)", key="pay_pm_csv")

# --------------------------------------------------------------------------
# Installments
# --------------------------------------------------------------------------
ib = ds.installment_buckets(start, end, states=states, statuses=statuses)
ui.section("Installments")
c1, c2 = st.columns([1.2, 0.9])
with c1:
    ui.chart(charts.bar(ib, "installment_bucket", "total_payment_value",
                        title="Payment value by installment bucket"), key="pay_inst_bar")
with c2:
    ui.table(
        ib.assign(pay_count_pct=lambda d: (100 * d["payments"] / d["payments"].sum()).round(2)),
        column_config={
            "installment_bucket": st.column_config.TextColumn("Bucket"),
            "payments": st.column_config.NumberColumn("Payments", format="%d"),
            "pay_count_pct": st.column_config.NumberColumn("% of payments", format="%.2f%%"),
            "avg_payment_value": st.column_config.NumberColumn("Avg (R$)", format="R$ %,.2f"),
            "total_payment_value": st.column_config.NumberColumn("Total (R$)", format="R$ %,.2f"),
        },
        height=260,
    )
ui.download_csv(ib, "installment_buckets.csv", "Download installments (CSV)", key="pay_inst_csv")

# --------------------------------------------------------------------------
# Payment value distribution
# --------------------------------------------------------------------------
pv = ds.payment_values(start, end, states=states, statuses=statuses)
ui.section("Payment value distribution")
ui.chart(charts.histogram(pv["payment_value"].dropna(), bins=40, x_title="Payment value (R$)",
                          title="Distribution of individual payment values"), key="pay_pv_hist")
ui.download_csv(pv, "payment_values.csv", "Download payment values (CSV)", key="pay_pv_csv")

# --------------------------------------------------------------------------
# Product categories
# --------------------------------------------------------------------------
cats = ds.category_series(start, end, states=states, statuses=statuses)
ui.section("Product categories (by sales)")
ui.chart(charts.ranking_bar(cats, "category", "merchandise_sales", top_n=10,
                            title="Top 10 categories by merchandise sales"), key="pay_cats_bar")
ui.table(
    cats.head(20),
    column_config={
        "category": st.column_config.TextColumn("Category"),
        "orders": st.column_config.NumberColumn("Orders", format="%d"),
        "units": st.column_config.NumberColumn("Units", format="%d"),
        "merchandise_sales": st.column_config.NumberColumn("Sales (R$)", format="R$ %,.2f"),
        "avg_unit_price": st.column_config.NumberColumn("Avg unit (R$)", format="R$ %,.2f"),
        "freight_charges": st.column_config.NumberColumn("Freight (R$)", format="R$ %,.2f"),
    },
    height=320,
)
ui.download_csv(cats, "product_categories_pay.csv", "Download categories (CSV)", key="pay_cats_csv")

# --------------------------------------------------------------------------
# Weight vs freight
# --------------------------------------------------------------------------
wf = ds.weight_freight(start, end, categories=categories, states=states)
ui.section("Product weight versus freight costs")
c1, c2 = st.columns([1.2, 0.9])
with c1:
    ui.chart(charts.bar(wf[wf["weight_bucket"] != "unknown/invalid"], "weight_bucket", "avg_freight_per_kg",
                        title="Average freight per kg by weight bucket"), key="wf_perkg")
with c2:
    ui.table(
        wf,
        column_config={
            "weight_bucket": st.column_config.TextColumn("Weight bucket"),
            "units": st.column_config.NumberColumn("Units", format="%d"),
            "avg_unit_price": st.column_config.NumberColumn("Avg unit (R$)", format="R$ %,.2f"),
            "avg_freight": st.column_config.NumberColumn("Avg freight (R$)", format="R$ %,.2f"),
            "avg_freight_per_kg": st.column_config.NumberColumn("Freight/kg (R$)", format="R$ %,.4f"),
        },
        height=260,
    )
ui.note("Known weights only; 'unknown/invalid' are units excluded from freight/kg. "
        "Freight is per item. Methodology matches the analysis layer (vw_weight_vs_freight).")

# --------------------------------------------------------------------------
# Seller performance
# --------------------------------------------------------------------------
sp = ds.seller_performance()
sp = sp[sp["orders"] >= 30].copy()
ui.section("Seller performance (≥30 orders)")
ui.chart(charts.scatter(sp, "late_pct", "avg_review_score",
                        hover=["seller_id", "orders", "late_pct", "avg_review_score"],
                        x_title="Late %", y_title="Avg review score",
                        title="Late rate vs average review score for sellers (≥30 orders)"),
         key="pay_seller_scatter")
ui.download_csv(sp[sp["orders"] >= 30], "sellers_geq30.csv", "Download sellers (≥30) CSV",
                key="pay_seller_csv")