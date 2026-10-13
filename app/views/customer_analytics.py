"""Customer Analytics - unique customers, new vs repeat, frequency, state and cohorts."""

from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd
import streamlit as st

from src.dashboard import charts, components as ui, data as ds
from src.dashboard import formatting as fmt

ui.page_header(
    "Customer Analytics",
    "Purchase behaviour by customer_unique_id (the correct retention grain). "
    "Repeat rate, purchase frequency, state distribution and cohort retention.",
)

start, end = ui.date_range_filter("cust_dates")
states = ui.multiselect_filter("cust_states", "Customer state", ds.state_options())
ui.filter_caption(start, end, **({"States": states} if states else {}))

# --------------------------------------------------------------------------
# KPIs
# --------------------------------------------------------------------------
cust = ds.customer_summary(start, end, states=states)
nvr = ds.new_vs_repeat(start, end, states=states)
freq = ds.purchase_frequency()

c1, c2, c3, c4 = st.columns(4)
with c1:
    ui.kpi_card("Unique customers (filtered)", fmt.fmt_int(cust["customers"]))
with c2:
    ui.kpi_card("Orders (filtered)", fmt.fmt_int(cust["orders"]))
with c3:
    repeat_cust = int(freq.loc[freq["segment"] != "1 order", "customers"].sum())
    total_cust_all = int(freq["customers"].sum())
    rep_rate = 100 * repeat_cust / total_cust_all if total_cust_all else 0
    ui.kpi_card("Repeat rate (all-time)", fmt.fmt_percent(rep_rate),
                caption=f"{fmt.fmt_int(repeat_cust)} of {fmt.fmt_int(total_cust_all)} customers")
with c4:
    ui.kpi_card("Orders/customer (filtered)", fmt.fmt_number(cust["orders_per_customer"], 3))

ui.note("Repeat rate is shown all-time (customer identity spans full history); other KPIs "
        "respect the selected date/state filters. Cohort analysis also uses the full history.")

# --------------------------------------------------------------------------
# New vs repeat monthly
# --------------------------------------------------------------------------
ui.section("New vs repeat customers by month",
           "Customers ordering for the first time (first purchase date = month) vs returning.")
if ui.empty(nvr.empty):
    st.stop()
nvr["repeat_customers"] = nvr["active_customers"] - nvr["new_customers"]
nvr["repeat_share_pct"] = 100 * nvr["repeat_customers"] / nvr["active_customers"].replace(0, pd.NA)
fig = charts.dual_axis(nvr, "sales_month", "active_customers", "new_customers",
                       bar_name="Active customers", line_name="New customers",
                       bar_title="Active customers", line_title="New customers",
                       title="Active customers (new vs returning)")
ui.chart(fig, key="cust_nvr")
ui.download_csv(nvr, "new_vs_repeat_monthly.csv", "Download new vs repeat (CSV)", key="cust_nvr_csv")

# --------------------------------------------------------------------------
# Frequency
# --------------------------------------------------------------------------
ui.section("Purchase frequency", "Distribution of placement_count across all customers.")
c1, c2 = st.columns([1.1, 1.4])
with c1:
    ui.chart(charts.pie(freq, "segment", "customers",
                        title="Customers by purchase frequency (all-time)"), key="cust_freq_pie")
with c2:
    ui.table(
        freq.assign(orders_per_segment=lambda d: d["orders"] / d["customers"].replace(0, pd.NA)).round(3),
        column_config={
            "segment": st.column_config.TextColumn("Segment"),
            "customers": st.column_config.NumberColumn("Customers", format="%d"),
            "orders": st.column_config.NumberColumn("Total orders", format="%d"),
            "orders_per_segment": st.column_config.NumberColumn("Orders/customer", format="%.3f"),
        },
        height=260,
    )
ui.download_csv(freq, "purchase_frequency.csv", "Download frequency (CSV)", key="cust_freq_csv")


# --------------------------------------------------------------------------
# State distribution (customers)
# --------------------------------------------------------------------------
state_cust = ds.state_series(start, end, statuses=())
ui.section("State-wise customer distribution")
ui.chart(
    charts.ranking_bar(
        state_cust, "customer_state", "customers", top_n=15,
        title="Top 15 states by unique customers (filtered)"
    ),
    key="cust_states_chart"
)
ui.download_csv(state_cust, "customers_by_state.csv", "Download customers by state (CSV)",
                key="cust_state_csv")


# --------------------------------------------------------------------------
# Cohort retention
# --------------------------------------------------------------------------
ui.section("Cohort analysis", "Monthly cohorts (first purchase month). Values = % of cohort "
           "customers who placed an order in month N. Sparse for many cohorts (low repeat).")
cohort = ds.cohort_retention()
if not cohort.empty:
    cohort["cohort"] = pd.Categorical(cohort["cohort"], sorted(cohort["cohort"].unique()))
    # cohort size
    size = cohort.groupby("cohort")["customers"].max().rename("cohort_size")
    matrix = cohort.pivot(index="cohort", columns="active_month", values="customers")
    pct = matrix.div(size, axis=0) * 100
    pct = pct.reindex(sorted(matrix.columns), axis=1)
    ui.note(f"Cohorts: {len(pct.index)}. Cohort sizes vary widely; interpret percentages cautiously.")
    ui.chart(charts.heatmap(pct, title="Cohort retention (%)", color_scale="Blues",
                            text_format=".1f"), key="cohort_heat")
    ui.download_csv(pct.reset_index(), "cohort_retention_pct.csv", "Download cohort matrix (CSV)",
                    key="cohort_csv")
else:
    ui.note("No cohort data for selected filters.", "warn")