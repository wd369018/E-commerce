"""Customer Satisfaction - review distribution, by category, low-score orders, timing."""

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
    "Customer Satisfaction",
    "Review score distribution, category-level satisfaction, low-score orders, "
    "on-time vs late reviews. Portuguese comment themes are explored where feasible.",
)

start, end = ui.date_range_filter("sat_dates", default_window=True)
states = ui.multiselect_filter("sat_states", "Customer state", ds.state_options())
score_min, score_max = st.sidebar.slider("Review score filter (low-score table)", 1, 5, (1, 2),
                                         key="sat_score_range")
ui.filter_caption(start, end, **({"States": states} if states else {}))

# --------------------------------------------------------------------------
# Review distribution + KPIs
# --------------------------------------------------------------------------
rev = ds.reviews_context(start, end, states=states)
ui.section("Review score distribution")

if ui.empty(rev.empty):
    st.stop()

dist = rev["review_score"].value_counts().sort_index().reset_index()
dist.columns = ["review_score", "orders"]
dist["pct"] = 100 * dist["orders"] / dist["orders"].sum()
avg_rev = float(rev["review_score"].mean())

c1, c2 = st.columns([1.1, 0.9])
with c1:
    ui.chart(charts.bar(dist, "review_score", "orders",
                        title="Orders by review score (1–5)"), key="sat_dist")
with c2:
    ui.kpi_card("Average review score", fmt.fmt_number(avg_rev, 3),
                caption=f"{fmt.fmt_int(len(rev))} reviewed orders")
    low = rev[rev["review_score"] <= 2]
    ui.kpi_card("Low-score (≤2)", fmt.fmt_int(len(low)),
                caption=f"{fmt.fmt_percent(100*len(low)/len(rev))} of reviewed orders")

ui.download_csv(rev, "reviews_context.csv", "Download review context (CSV)", key="sat_rev_csv")

# --------------------------------------------------------------------------
# Category-level satisfaction
# --------------------------------------------------------------------------
sat_cat = ds.category_satisfaction(start, end, states=states)
ui.section("Category-level satisfaction",
           "Avg review score and late % per category (order-grain for reviews/late rates).")
ui.table(
    sat_cat[sat_cat["reviewed_orders"] > 0].sort_values("avg_review_score", ascending=False),
    column_config={
        "category": st.column_config.TextColumn("Category"),
        "orders": st.column_config.NumberColumn("Orders", format="%d"),
        "reviewed_orders": st.column_config.NumberColumn("Reviewed", format="%d"),
        "avg_review_score": st.column_config.NumberColumn("Avg review", format="%.3f"),
        "late_pct": st.column_config.NumberColumn("Late %", format="%.2f%%"),
    },
    height=360,
)
ui.download_csv(sat_cat, "category_satisfaction.csv", "Download category satisfaction (CSV)",
                key="sat_cat_csv")

# --------------------------------------------------------------------------
# Low-score orders
# --------------------------------------------------------------------------
low_tbl = rev[(rev["review_score"] >= score_min) & (rev["review_score"] <= score_max)].copy()
low_tbl = low_tbl.sort_values("purchase_date", ascending=False)
ui.section("Low-score orders", f"Showing orders with review score between {score_min} and {score_max} "
           f"(inclusive).")
ui.table(
    low_tbl[["order_id", "purchase_date", "review_score", "is_late_vs_estimate",
             "delivery_delay_days", "customer_state", "order_status"]],
    column_config={
        "order_id": st.column_config.TextColumn("Order ID"),
        "purchase_date": st.column_config.DateColumn("Purchase date"),
        "review_score": st.column_config.NumberColumn("Score", format="%d"),
        "is_late_vs_estimate": st.column_config.NumberColumn("Late vs estimate (1/0)", format="%d"),
        "delivery_delay_days": st.column_config.NumberColumn("Delivery days", format="%.2f"),
        "customer_state": st.column_config.TextColumn("State"),
        "order_status": st.column_config.TextColumn("Status"),
    },
    height=320,
)
ui.download_csv(low_tbl, "low_score_orders.csv", "Download low-score orders (CSV)", key="sat_low_csv")

# --------------------------------------------------------------------------
# On-time vs late review scores
# --------------------------------------------------------------------------
g = rev[rev["is_late_vs_estimate"].notna()].groupby("is_late_vs_estimate")["review_score"].mean().reset_index()
g["label"] = g["is_late_vs_estimate"].map({0: "On-time", 1: "Late"})
ui.section("Review scores for on-time vs late deliveries",
           "Mean review score for delivered orders where punctuality is known.")
c1, c2 = st.columns([0.8, 1.2])
with c1:
    ui.chart(charts.bar(g, "label", "review_score", title="On-time vs late: avg review score"), key="sat_otl")
with c2:
    t = rev[rev["is_late_vs_estimate"].notna()].copy()
    t["label"] = t["is_late_vs_estimate"].map({0: "On-time", 1: "Late"})
    ui.chart(charts.box(t, "label", "review_score", title="Review score distribution by punctuality",
                        y_title="Review score"), key="sat_otl_box")

ui.note("This is an **association**. Correlation (not causation): late delivery tends to "
        "co-occur with lower scores, but other factors (product, seller, expectations) "
        "can contribute. See the verified findings for measured correlation values.")

# --------------------------------------------------------------------------
# Comment themes (lightweight)
# --------------------------------------------------------------------------
try:
    from src.dashboard import data as ds  # already imported

    comments = ds.review_comments(start, end, states=states).copy()
except Exception:
    comments = pd.DataFrame()

ui.section("Review comment themes (exploratory)",
           "Portuguese token frequency for low-score (≤2) vs high-score (≥4). Uses a small "
           "stopword set and no stemming; results are indicative.")
if not comments.empty:
    import re

    STOP = {"de", "da", "do", "das", "dos", "em", "no", "na", "um", "uma", "os", "as", "e",
            "a", "o", "que", "para", "por", "com", "sem", "eu", "me", "ao", "à", "muito",
            "mais", "mas", "nao", "não", "foi", "dos", "das", "se", "te", "bem", "bom",
            "boa", "tudo", "toda", "todos", "elas", "eles", "pelo", "pela", "recebi",
            "produto", "chegou", "entrega", "prazo", "recomendo", "já", "so", "só", "pra"}
    low_c = comments[comments["review_score"] <= 2]
    high_c = comments[comments["review_score"] >= 4]
    for label, sub in [("low2", low_c), ("high4", high_c)]:
        tokens = []
        for text in sub["review_comment_message"].fillna("").tolist():
            tokens.extend(re.findall(r"[\w]+", str(text).lower()))
        tokens = [t for t in tokens if t not in STOP and len(t) > 3]
        series = pd.Series(tokens).value_counts().head(10).reset_index()
        series.columns = ["term", "count"]
        with st.expander(f"Top terms - {label} ({len(sub)} comments)"):
            ui.table(series, height=240)
else:
    ui.note("Not enough non-empty Portuguese comments for theme extraction under current filters.", "warn")