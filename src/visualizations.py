"""
ShopScope - charts for the EDA / findings layer.

Every function takes a tidy DataFrame (produced by :mod:`src.insights` from the
validated database) and writes one PNG into ``reports/figures/``. The figures
are deliberately plain, labelled, and honest about incomplete periods (the
ramp-up and truncated months are shaded / marked, never silently dropped).

Matplotlib runs headless (``Agg``); no interactive backend is required.

Usage::

    python -m src.visualizations          # renders a demo chart
"""

from __future__ import annotations

import logging
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402

from . import config  # noqa: E402

logger = logging.getLogger(__name__)

# Consistent, colour-blind-friendly palette.
BLUE = "#2b6cb0"
ORANGE = "#dd6b20"
GREEN = "#2f855a"
RED = "#c53030"
GREY = "#a0aec0"
PURPLE = "#6b46c1"


def _ensure_dir() -> Path:
    config.FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    return config.FIGURES_DIR


def _save(fig, name: str) -> Path:
    path = _ensure_dir() / f"{name}.png"
    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    logger.info("wrote figure %s", path)
    return path


def _money(value: float) -> str:
    return f"R$ {value:,.0f}"


# --------------------------------------------------------------------------
# 1. Monthly sales trend (with incomplete months marked)
# --------------------------------------------------------------------------
def monthly_sales_trend(monthly: "pd.DataFrame", name: str = "monthly_sales_trend") -> Path:
    import pandas as pd  # noqa: F401  (docstring typing only)

    fig, ax1 = plt.subplots(figsize=(11, 4.5))
    x = range(len(monthly))
    labels = list(monthly["sales_month"])
    complete = list(monthly["is_complete"])

    bars = ax1.bar(x, monthly["merchandise_sales"], color=[
        BLUE if c else GREY for c in complete
    ])
    ax1.set_ylabel("Merchandise sales (R$)", color=BLUE)
    ax1.set_xlabel("Month")
    ax1.set_xticks(list(x))
    ax1.set_xticklabels(labels, rotation=90, fontsize=7)

    ax2 = ax1.twinx()
    ax2.plot(x, monthly["orders"], color=ORANGE, marker="o", linewidth=1.5,
             markersize=3, label="orders")
    ax2.set_ylabel("Orders", color=ORANGE)

    ax1.set_title("Monthly merchandise sales and order volume (grey = incomplete/ramp-up month)")
    for i, c in enumerate(complete):
        if not c:
            ax1.axvspan(i - 0.5, i + 0.5, color=RED, alpha=0.06)
    return _save(fig, name)


# --------------------------------------------------------------------------
# 2. Quarterly sales
# --------------------------------------------------------------------------
def quarterly_sales(quarterly: "pd.DataFrame", name: str = "quarterly_sales") -> Path:
    fig, ax = plt.subplots(figsize=(9, 4.5))
    colors = [BLUE if c else GREY for c in quarterly["is_complete"]]
    bars = ax.bar(quarterly["quarter"], quarterly["merchandise_sales"], color=colors)
    ax.set_ylabel("Merchandise sales (R$)")
    ax.set_title("Quarterly merchandise sales (grey = incomplete/ramp-up quarter)")
    plt.xticks(rotation=45, ha="right")
    for rect, value in zip(bars, quarterly["merchandise_sales"]):
        ax.annotate(_money(value), (rect.get_x() + rect.get_width() / 2, value),
                    ha="center", va="bottom", fontsize=6.5)
    return _save(fig, name)


# --------------------------------------------------------------------------
# 3. Category performance
# --------------------------------------------------------------------------
def category_performance(cat: "pd.DataFrame", top_n: int = 12,
                         name: str = "category_performance") -> Path:
    top = cat.sort_values("merchandise_sales", ascending=False).head(top_n)
    bottom = cat[cat["merchandise_sales"] > 0].sort_values("merchandise_sales").head(top_n)

    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))
    axes[0].barh(top["category_label"][::-1],
                 top["merchandise_sales"][::-1], color=GREEN)
    axes[0].set_title(f"Top {top_n} categories by merchandise sales")
    axes[0].set_xlabel("Merchandise sales (R$)")
    axes[1].barh(bottom["category_label"][::-1],
                 bottom["merchandise_sales"][::-1], color=ORANGE)
    axes[1].set_title(f"Bottom {top_n} (non-zero) categories")
    axes[1].set_xlabel("Merchandise sales (R$)")
    fig.suptitle("Product category performance", y=1.02)
    return _save(fig, name)


# --------------------------------------------------------------------------
# 4. Customer distribution by state
# --------------------------------------------------------------------------
def state_distribution(state: "pd.DataFrame", top_n: int = 15,
                       name: str = "state_distribution") -> Path:
    top = state.sort_values("orders", ascending=False).head(top_n)
    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.bar(top["customer_state"], top["orders"], color=BLUE)
    ax.set_ylabel("Orders")
    ax.set_title(f"Orders by customer state (top {top_n}); SP share "
                 f"{100 * state.loc[state['customer_state'] == 'SP', 'orders'].sum() / state['orders'].sum():.1f}%")
    for i, (orders, sales) in enumerate(zip(top["orders"], top["merchandise_sales"])):
        ax.annotate(_money(sales), (i, orders), ha="center", va="bottom",
                    fontsize=6, rotation=90)
    return _save(fig, name)


# --------------------------------------------------------------------------
# 5. Repeat-purchase segments
# --------------------------------------------------------------------------
def repeat_segments(seg: "pd.DataFrame", name: str = "repeat_segments") -> Path:
    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.bar(seg["segment"], seg["customers"], color=[BLUE, GREEN, ORANGE, RED][: len(seg)])
    ax.set_ylabel("Customers")
    ax.set_title("Customers by number of orders (customer_unique_id)")
    for i, (cust, spend) in enumerate(zip(seg["customers"], seg["merchandise_spend"])):
        ax.annotate(_money(spend), (i, cust), ha="center", va="bottom", fontsize=7)
    ax.set_yscale("log")
    return _save(fig, name)


# --------------------------------------------------------------------------
# 6. Payment method distribution
# --------------------------------------------------------------------------
def payment_methods(pay: "pd.DataFrame", name: str = "payment_methods") -> Path:
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.bar(pay["payment_type"], pay["pct_of_value"], color=BLUE)
    ax.set_ylabel("% of payment value")
    ax.set_title("Payment method share of value")
    for i, v in enumerate(pay["pct_of_value"]):
        ax.annotate(f"{v:.1f}%", (i, v), ha="center", va="bottom", fontsize=8)
    plt.xticks(rotation=20, ha="right")
    return _save(fig, name)


# --------------------------------------------------------------------------
# 7. Installments
# --------------------------------------------------------------------------
def installments(inst: "pd.DataFrame", name: str = "installments") -> Path:
    inst = inst.sort_values("bucket_order")
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.bar(inst["installment_bucket"], inst["payments"], color=PURPLE)
    ax.set_ylabel("Payment records")
    ax.set_title("Installment plan usage (payment records)")
    for i, v in enumerate(inst["payments"]):
        ax.annotate(f"{v:,}", (i, v), ha="center", va="bottom", fontsize=7)
    plt.xticks(rotation=20, ha="right")
    return _save(fig, name)


# --------------------------------------------------------------------------
# 8. Late delivery vs review score
# --------------------------------------------------------------------------
def late_vs_review(table: "pd.DataFrame", name: str = "late_vs_review") -> Path:
    fig, ax = plt.subplots(figsize=(7, 4.5))
    labels = ["On time", "Late"]
    values = list(table["avg_review_score"])
    counts = list(table["orders"])
    bars = ax.bar(labels, values, color=[GREEN, RED])
    ax.set_ylabel("Average review score")
    ax.set_ylim(0, 5)
    ax.set_title("Average review score: on-time vs late deliveries")
    for rect, v, n in zip(bars, values, counts):
        ax.annotate(f"{v:.2f}\n(n={n:,})", (rect.get_x() + rect.get_width() / 2, v),
                    ha="center", va="bottom", fontsize=8)
    return _save(fig, name)


# --------------------------------------------------------------------------
# 9. Seller late-rate distribution
# --------------------------------------------------------------------------
def seller_late_distribution(sellers: "pd.DataFrame", name: str = "seller_late_distribution") -> Path:
    fig, ax = plt.subplots(figsize=(8, 4.5))
    data = sellers["late_pct"].dropna()
    ax.hist(data, bins=40, color=ORANGE, edgecolor="white")
    ax.axvline(data.mean(), color=RED, linestyle="--",
               label=f"mean {data.mean():.1f}%")
    ax.set_xlabel("Seller late-delivery rate (%)")
    ax.set_ylabel("Sellers")
    ax.set_title(f"Distribution of seller late rates (n={len(data):,} sellers with evaluable orders)")
    ax.legend()
    return _save(fig, name)


# --------------------------------------------------------------------------
# 10. Weight vs freight
# --------------------------------------------------------------------------
def weight_freight(wf: "pd.DataFrame", name: str = "weight_freight") -> Path:
    wf = wf[wf["weight_bucket"] != "unknown/invalid"].copy()
    x = range(len(wf))
    fig, ax1 = plt.subplots(figsize=(9, 4.5))
    ax1.bar(x, wf["avg_freight"], color=BLUE, label="avg freight (R$)")
    ax1.set_ylabel("Average freight (R$)", color=BLUE)
    ax1.set_xticks(list(x))
    ax1.set_xticklabels(wf["weight_bucket"], rotation=20, ha="right")

    ax2 = ax1.twinx()
    ax2.plot(x, wf["avg_freight_per_kg"], color=ORANGE, marker="o", label="freight / kg")
    ax2.set_ylabel("Freight per kg (R$)", color=ORANGE)
    ax1.set_title("Freight cost vs product weight bucket")
    return _save(fig, name)


# --------------------------------------------------------------------------
# 11. Review score distribution
# --------------------------------------------------------------------------
def review_distribution(reviews: "pd.DataFrame", name: str = "review_distribution") -> Path:
    reviews = reviews.sort_values("review_score")
    scores = [int(s) for s in reviews["review_score"]]
    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.bar(scores, reviews["pct_of_orders"], color=BLUE)
    ax.set_xlabel("Review score")
    ax.set_ylabel("% of reviewed orders")
    ax.set_xticks(scores)
    ax.set_title("Review score distribution")
    for i, v in zip(scores, reviews["pct_of_orders"]):
        ax.annotate(f"{v:.1f}%", (i, v), ha="center", va="bottom", fontsize=8)
    return _save(fig, name)


# --------------------------------------------------------------------------
# 12. Monthly on-time delivery rate
# --------------------------------------------------------------------------
def delivery_trend(monthly: "pd.DataFrame", name: str = "delivery_trend") -> Path:
    fig, axes = plt.subplots(2, 1, figsize=(11, 6.5), sharex=True)
    x = range(len(monthly))
    labels = list(monthly["sales_month"])
    axes[0].plot(x, monthly["on_time_pct"], color=GREEN, marker="o", markersize=3)
    axes[0].set_ylabel("On-time %")
    axes[0].set_title("Monthly on-time delivery rate and average delivery days")
    axes[1].bar(x, monthly["avg_days_purchase_to_delivery"], color=BLUE)
    axes[1].set_ylabel("Avg days")
    axes[1].set_xticks(list(x))
    axes[1].set_xticklabels(labels, rotation=90, fontsize=7)
    return _save(fig, name)


# --------------------------------------------------------------------------
# Demo
# --------------------------------------------------------------------------
def main() -> int:
    import pandas as pd

    _ensure_dir()
    demo = pd.DataFrame({"sales_month": ["2017-01"], "is_complete": [True],
                         "merchandise_sales": [100.0], "orders": [1]})
    print(monthly_sales_trend(demo, name="_demo"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
