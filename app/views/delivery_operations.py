"""Delivery and Operations - actual vs estimated, punctuality, seller performance."""

from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import numpy as np
import pandas as pd
import streamlit as st

from src.dashboard import charts, components as ui, data as ds
from src.dashboard import formatting as fmt

ui.page_header(
    "Delivery and Operations",
    "Delivery punctuality, actual versus estimated lead times and late rate. "
    "The 2,965 orders with no delivery date are excluded from on-time/late rates "
    "(unknown), following the documented methodology.",
)

start, end = ui.date_range_filter("del_dates", default_window=True)
states = ui.multiselect_filter("del_states", "Customer state", ds.state_options())
statuses = ui.multiselect_filter("del_status", "Order status", ds.status_options(),
                                 help_text="Typically delivered/shipped; affects eligible orders")
ui.filter_caption(start, end,
                  **({"States": states} if states else {}),
                  **({"Status": statuses} if statuses else {}))

# --------------------------------------------------------------------------
# Delivery orders (order grain)
# --------------------------------------------------------------------------
dord = ds.delivery_orders(start, end, states=states, statuses=statuses)
dord = dord[dord["days_vs_estimate"].notna()].copy() if "days_vs_estimate" in dord else dord

ui.section("Punctuality: on-time vs late")
on = int((dord["is_late_vs_estimate"] == 0).sum()) if not dord.empty else 0
late = int((dord["is_late_vs_estimate"] == 1).sum()) if not dord.empty else 0
elig = on + late
on_pct = 100 * on / elig if elig else 0
late_pct = 100 * late / elig if elig else 0
mean_delay = float(dord["days_vs_estimate"].mean()) if elig else np.nan

c1, c2, c3, c4 = st.columns(4)
with c1:
    ui.kpi_card("Eligible delivered orders", fmt.fmt_int(elig))
with c2:
    ui.kpi_card("On-time", fmt.fmt_percent(on_pct), caption=f"{fmt.fmt_int(on)} orders")
with c3:
    ui.kpi_card("Late", fmt.fmt_percent(late_pct), caption=f"{fmt.fmt_int(late)} orders")
with c4:
    ui.kpi_card("Avg days vs estimate", fmt.fmt_number(mean_delay, 2), caption="(delivered - estimated)")

ui.note("Rates are calculated over **delivered orders with known dates** only. Orders with "
        "no delivery timestamp (2,965) are excluded - not misclassified as late or on-time.")

if ui.empty(elig == 0):
    st.stop()

# --------------------------------------------------------------------------
# Monthly delivery trend
# --------------------------------------------------------------------------
dm = ds.delivery_monthly(start, end, states=states, statuses=statuses)
ui.section("Delivery performance by month")
if not dm.empty:
    dm["on_time_pct"] = 100 * dm["on_time_orders"] / (dm["on_time_orders"] + dm["late_orders"]).replace(0, pd.NA)
    dm["avg_days_vs_estimate"] = dm["avg_days_vs_estimate"].round(2)
    fig = charts.dual_axis(dm, "sales_month", "on_time_orders", "on_time_pct",
                           bar_name="Delivered orders (known dates)", line_name="On-time %",
                           bar_title="Delivered", line_title="On-time %")
    ui.chart(fig, key="del_monthly")
    ui.download_csv(dm, "delivery_monthly.csv", "Download delivery monthly (CSV)", key="del_dm_csv")
else:
    ui.note("No delivered orders with known dates in the selected window.", "warn")

# --------------------------------------------------------------------------
# Actual vs estimated (distributions)
# --------------------------------------------------------------------------
ui.section("Actual vs estimated delivery times")
dord_filt = dord.copy()
c1, c2 = st.columns(2)
with c1:
    ui.chart(charts.histogram(dord_filt["days_vs_estimate"].dropna(), bins=40,
                              x_title="Days (delivered - estimated)", title="Days vs estimate"),
             key="del_days_vs_est")
with c2:
    ui.chart(charts.histogram(dord_filt["delivery_delay_days"].dropna(), bins=40,
                              x_title="Days from purchase to delivery", title="Purchase→delivery (days)"),
             key="del_delay")

# --------------------------------------------------------------------------
# Delivery delay vs review score
# --------------------------------------------------------------------------
rev = ds.reviews_context(start, end, states=states, statuses=statuses)
rev = rev[rev["delivery_delay_days"].notna()].copy()
ui.section(
    "Delivery delay versus review score",
    "Average review score and negative-review share by purchase-to-delivery duration "
    "(order grain). Association only - see the correlation caveat below.",
)
if ui.empty(rev.empty, "No reviewed delivered orders with a known duration under the "
                       "current filters."):
    pass  # keep the page going; other sections may still have data
else:
    bins = [-0.01, 7, 14, 21, 30, 45, float("inf")]
    labels = ["0-7 d", "8-14 d", "15-21 d", "22-30 d", "31-45 d", "46+ d"]
    rev["delay_bucket"] = pd.cut(rev["delivery_delay_days"], bins=bins, labels=labels)
    delay_review = (
        rev.groupby("delay_bucket", observed=True)
        .agg(
            orders=("review_score", "size"),
            avg_review_score=("review_score", "mean"),
            negative_pct=("review_score", lambda s: 100.0 * (s <= 2).mean()),
        )
        .reset_index()
    )
    delay_review["avg_review_score"] = delay_review["avg_review_score"].round(3)
    delay_review["negative_pct"] = delay_review["negative_pct"].round(2)
    rho = float(rev["delivery_delay_days"].corr(rev["review_score"], method="spearman"))

    c1, c2 = st.columns([1.25, 1])
    with c1:
        ui.chart(
            charts.bar_line(
                delay_review, "delay_bucket", "orders", "avg_review_score",
                bar_name="Reviewed orders", line_name="Avg review score",
                bar_title="Reviewed orders", line_title="Avg review score",
                title="Orders and average review score by delivery duration",
                line_color=charts.RED, height=380,
            ),
            key="del_delay_review",
        )
    with c2:
        ui.table(
            delay_review,
            column_config={
                "delay_bucket": st.column_config.TextColumn("Delivery duration"),
                "orders": st.column_config.NumberColumn("Reviewed orders", format="%d"),
                "avg_review_score": st.column_config.NumberColumn("Avg review", format="%.3f"),
                "negative_pct": st.column_config.NumberColumn("1-2 star %", format="%.2f%%"),
            },
            height=260,
        )
    ui.note(
        f"Spearman correlation between delivery duration (days) and review score across "
        f"{fmt.fmt_int(len(rev))} reviewed delivered orders = **{rho:,.3f}**. This is a "
        "correlation, not proof that a slower delivery alone causes the lower score - "
        "distance, seller and product mix are confounders."
    )
    ui.download_csv(delay_review, "delivery_delay_vs_review.csv",
                    "Download delay vs review (CSV)", key="del_dr_csv")

# --------------------------------------------------------------------------
# State performance
# --------------------------------------------------------------------------
ds_state = ds.delivery_by_state(start, end, statuses=statuses)
ui.section("State and seller performance")
sc1, sc2 = st.columns([1.15, 0.95])
with sc1:
    ui.chart(charts.ranking_bar(ds_state, "customer_state", "late_pct", top_n=12,
                                title="Late delivery rate (%) by customer state (eligible delivered)"),
             key="del_state_late")
with sc2:
    ui.table(
        ds_state,
        column_config={
            "customer_state": st.column_config.TextColumn("State"),
            "delivered_orders": st.column_config.NumberColumn("Delivered", format="%d"),
            "avg_days_to_deliver": st.column_config.NumberColumn("Avg days", format="%.2f"),
            "late_pct": st.column_config.NumberColumn("Late %", format="%.2f%%"),
        },
        height=280,
    )
ui.download_csv(ds_state, "delivery_by_state.csv", "Download delivery by state (CSV)", key="del_state_csv")

# --------------------------------------------------------------------------
# Seller performance (full)
# --------------------------------------------------------------------------
sp = ds.seller_performance()
sp = sp[(sp["orders"] >= 30) & (sp["late_pct"].notna())].copy()
ui.section("Seller punctuality (sellers with >= 30 orders, eligible late rate known)")
sp_c1, sp_c2 = st.columns([1.2, 1])
with sp_c1:
    ui.chart(charts.scatter(sp, "late_pct", "avg_review_score",
                            hover=["seller_id", "orders", "late_pct", "avg_review_score"],
                            x_title="Late % (delivered orders, >=30)", y_title="Avg review score",
                            title="Seller: late rate vs average review score"), key="del_seller_scatter")
with sp_c2:
    ui.table(
        sp.sort_values("late_pct", ascending=False).head(30),
        column_config={
            "seller_id": st.column_config.TextColumn("Seller ID"),
            "orders": st.column_config.NumberColumn("Orders", format="%d"),
            "late_pct": st.column_config.NumberColumn("Late %", format="%.2f%%"),
            "avg_review_score": st.column_config.NumberColumn("Avg review", format="%.3f"),
            "avg_sales_per_order": st.column_config.NumberColumn("Avg sales (R$)", format="R$ %,.2f"),
        },
        height=380,
    )
ui.download_csv(sp, "seller_performance_full.csv", "Download seller performance (CSV)",
                key="del_seller_csv")