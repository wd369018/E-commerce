"""
ShopScope - KPI / business-metrics layer.

Every headline number in the project is produced here, once, with:
    * an explicit **formula** (numerator / denominator),
    * the **filters** that define the eligible population,
    * the **limitations** a reader must know before quoting it.

The values come from the validated SQLite database (``data/shopscope.db``) -
never from a hardcoded constant - so re-running the ETL and rebuilding the
database changes the numbers, not the code. Merchandise sales are always
``SUM(fact_order_item.price)`` (freight excluded); freight and payment cash are
kept as separate measures.

Usage::

    python -m src.metrics              # print every KPI with its documentation
    python -m src.metrics --json       # machine-readable dump
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import config
from .database import get_connection

logger = logging.getLogger(__name__)


class MetricsError(RuntimeError):
    """Raised when a KPI cannot be computed from the database."""


# --------------------------------------------------------------------------
# Result container
# --------------------------------------------------------------------------
@dataclass
class KPI:
    """A single business metric plus everything needed to interpret it."""

    key: str
    name: str
    category: str
    formula: str
    numerator_label: str
    numerator: float | None
    denominator_label: str
    denominator: float | None
    value: float
    unit: str  # count | currency | percent | days | ratio
    filters: str
    eligible_population: str
    limitations: str
    notes: str = ""
    detail: pd.DataFrame | None = field(default=None, repr=False)

    # ---- presentation helpers -------------------------------------------
    def formatted(self, decimals: int = 2) -> str:
        if self.unit == "currency":
            return f"R$ {self.value:,.{decimals}f}"
        if self.unit == "percent":
            return f"{self.value:,.{decimals}f}%"
        if self.unit == "days":
            return f"{self.value:,.{decimals}f} days"
        if self.unit == "count":
            return f"{int(round(self.value)):,}"
        return f"{self.value:,.{decimals}f}"

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "category": self.category,
            "formula": self.formula,
            "numerator_label": self.numerator_label,
            "numerator": self.numerator,
            "denominator_label": self.denominator_label,
            "denominator": self.denominator,
            "value": self.value,
            "unit": self.unit,
            "formatted": self.formatted(),
            "filters": self.filters,
            "eligible_population": self.eligible_population,
            "limitations": self.limitations,
            "notes": self.notes,
        }


# --------------------------------------------------------------------------
# Scalar helpers
# --------------------------------------------------------------------------
def _scalar(conn: sqlite3.Connection, sql: str):
    row = conn.execute(sql).fetchone()
    if row is None:
        return None
    return row[0]


def _load_facts(conn: sqlite3.Connection) -> dict[str, float]:
    """Compute every raw quantity once, so the KPI builders only do arithmetic."""
    facts: dict[str, float] = {}

    # ---- order-universe facts -------------------------------------------
    facts["total_orders"] = _scalar(conn, "SELECT COUNT(*) FROM dim_order")
    facts["orders_with_items"] = _scalar(
        conn, "SELECT COUNT(*) FROM fact_order_summary WHERE n_items > 0"
    )
    facts["total_units"] = _scalar(conn, "SELECT COALESCE(SUM(n_items), 0) FROM fact_order_summary")

    # ---- money facts -----------------------------------------------------
    facts["merchandise_sales"] = _scalar(conn, "SELECT SUM(price) FROM fact_order_item")
    facts["freight_charges"] = _scalar(conn, "SELECT SUM(freight_value) FROM fact_order_item")
    facts["payment_value"] = _scalar(conn, "SELECT SUM(payment_value) FROM bridge_payment")

    # ---- order-status facts ---------------------------------------------
    facts["delivered_orders"] = _scalar(
        conn, "SELECT COUNT(*) FROM dim_order WHERE order_status = 'delivered'"
    )
    facts["canceled_orders"] = _scalar(
        conn, "SELECT COUNT(*) FROM dim_order WHERE order_status = 'canceled'"
    )

    # ---- delivery facts --------------------------------------------------
    facts["delivery_evaluable"] = _scalar(
        conn,
        "SELECT COUNT(*) FROM dim_order WHERE order_delivered_customer_date IS NOT NULL",
    )
    facts["delivery_missing_date"] = _scalar(
        conn, "SELECT COUNT(*) FROM dim_order WHERE missing_delivery_date = 1"
    )
    facts["late_orders"] = _scalar(
        conn, "SELECT COUNT(*) FROM dim_order WHERE is_late_vs_estimate = 1"
    )
    facts["on_time_orders"] = _scalar(
        conn, "SELECT COUNT(*) FROM dim_order WHERE is_late_vs_estimate = 0"
    )
    facts["avg_delivery_days"] = _scalar(
        conn,
        "SELECT AVG(delivery_delay_days) FROM dim_order "
        "WHERE order_delivered_customer_date IS NOT NULL",
    )

    # ---- review facts ----------------------------------------------------
    facts["review_count"] = _scalar(conn, "SELECT COUNT(*) FROM bridge_review")
    facts["avg_review_score"] = _scalar(conn, "SELECT AVG(review_score) FROM bridge_review")
    facts["review_coverage_orders"] = _scalar(
        conn, "SELECT COUNT(*) FROM dim_order WHERE has_reviews = 1"
    )

    # ---- customer facts --------------------------------------------------
    facts["distinct_customers"] = _scalar(conn, "SELECT COUNT(*) FROM dim_customer_identity")
    facts["repeat_customers"] = _scalar(
        conn, "SELECT COUNT(*) FROM dim_customer_identity WHERE placement_count >= 2"
    )
    return facts


# --------------------------------------------------------------------------
# KPI builders (one function per metric keeps every definition auditable)
# --------------------------------------------------------------------------
def compute_kpis(conn: sqlite3.Connection) -> list[KPI]:
    """Return all headline KPIs, each fully documented."""
    f = _load_facts(conn)
    kpis: list[KPI] = []

    # 1. Unique orders -----------------------------------------------------
    kpis.append(KPI(
        key="total_unique_orders",
        name="Total unique orders",
        category="Volume",
        formula="COUNT(DISTINCT order_id)",
        numerator_label="distinct orders",
        numerator=f["total_orders"],
        denominator_label="not applicable (level count)",
        denominator=None,
        value=float(f["total_orders"]),
        unit="count",
        filters="none - every row of dim_order",
        eligible_population=f"all {int(f['total_orders']):,} orders in the export",
        limitations=(
            "The export is a snapshot cut on 2018-10-17. Orders created after that "
            "date do not exist, and a small number of very recent orders are still "
            "in non-terminal states (shipped/processing)."
        ),
        notes="One order_id = one order; an order can contain several item lines.",
    ))

    # 2. Merchandise sales -------------------------------------------------
    kpis.append(KPI(
        key="total_merchandise_sales",
        name="Total merchandise sales",
        category="Revenue",
        formula="SUM(fact_order_item.price)",
        numerator_label="merchandise sales (item price only)",
        numerator=f["merchandise_sales"],
        denominator_label="not applicable (sum)",
        denominator=None,
        value=float(f["merchandise_sales"]),
        unit="currency",
        filters="every fact_order_item row; ALL order_status values included",
        eligible_population=f"all {int(f['total_units']):,} item units across {int(f['orders_with_items']):,} orders",
        limitations=(
            "Freight is excluded (it is reported separately). Non-delivered orders "
            "(canceled/unavailable) are still included as gross merchandise value, "
            "so this is list-price sales, not recognised revenue. Prices are BRL and "
            "not inflation-adjusted."
        ),
        notes=(
            "Order-level merchandise sales always come from this sum; the ETL column "
            "fact_order_summary.item_revenue is GROSS (price + freight) and is never "
            "used as sales."
        ),
    ))

    # 3. Freight charges ---------------------------------------------------
    kpis.append(KPI(
        key="total_freight_charges",
        name="Total freight charges",
        category="Revenue",
        formula="SUM(fact_order_item.freight_value)",
        numerator_label="freight charged to customers",
        numerator=f["freight_charges"],
        denominator_label="not applicable (sum)",
        denominator=None,
        value=float(f["freight_charges"]),
        unit="currency",
        filters="every fact_order_item row",
        eligible_population=f"same {int(f['total_units']):,} item units as merchandise sales",
        limitations=(
            "Freight is what the customer was charged, not the carrier's true cost. "
            "It is reported per item line, so it cannot be split by seller without "
            "aggregating item lines."
        ),
        notes=f"Freight is {100.0 * f['freight_charges'] / f['merchandise_sales']:.2f}% of merchandise sales.",
    ))

    # 4. Average order value ----------------------------------------------
    aov_item = f["merchandise_sales"] / f["orders_with_items"]
    aov_all = f["merchandise_sales"] / f["total_orders"]
    kpis.append(KPI(
        key="average_order_value",
        name="Average order value (AOV)",
        category="Revenue",
        formula="merchandise sales / orders that contain at least one item",
        numerator_label="merchandise sales",
        numerator=f["merchandise_sales"],
        denominator_label="orders with >= 1 item",
        denominator=f["orders_with_items"],
        value=aov_item,
        unit="currency",
        filters="merchandise sales exclude freight; denominator excludes orders with no line items",
        eligible_population=f"{int(f['orders_with_items']):,} of {int(f['total_orders']):,} orders",
        limitations=(
            "Orders with no line items (no product record) cannot have a basket value "
            "and are excluded. Using all orders as the denominator gives "
            f"R$ {aov_all:,.2f}, which understates a real basket because empty orders "
            "are treated as R$ 0. Freight is not part of AOV."
        ),
        notes=(
            f"Alternative denominator: all {int(f['total_orders']):,} orders -> "
            f"R$ {aov_all:,.2f}. Including freight per order would raise the figure by "
            f"about R$ {f['freight_charges'] / f['orders_with_items']:,.2f}."
        ),
    ))

    # 5. Delivered orders --------------------------------------------------
    kpis.append(KPI(
        key="delivered_order_count",
        name="Delivered orders",
        category="Fulfilment",
        formula="COUNT(orders WHERE order_status = 'delivered')",
        numerator_label="orders with status delivered",
        numerator=f["delivered_orders"],
        denominator_label="not applicable (count)",
        denominator=None,
        value=float(f["delivered_orders"]),
        unit="count",
        filters="order_status = 'delivered'",
        eligible_population=f"all {int(f['total_orders']):,} orders",
        limitations=(
            "'delivered' is the status at export time. A handful of delivered orders "
            "lack a customer-delivery timestamp in the source, so the delivered count "
            "is 2 higher than the orders with a usable delivery date."
        ),
        notes=(
            f"{int(f['delivered_orders'] - f['delivery_evaluable']):,} delivered orders "
            "have no customer-delivery date and are excluded from duration/lateness KPIs."
        ),
    ))

    # 6. Delivery completion rate -----------------------------------------
    kpis.append(KPI(
        key="delivery_completion_rate",
        name="Delivery completion rate",
        category="Fulfilment",
        formula="delivered orders / all orders",
        numerator_label="delivered orders",
        numerator=f["delivered_orders"],
        denominator_label="all orders",
        denominator=f["total_orders"],
        value=100.0 * f["delivered_orders"] / f["total_orders"],
        unit="percent",
        filters="order_status = 'delivered' over all orders",
        eligible_population=f"all {int(f['total_orders']):,} orders",
        limitations=(
            "This is a status share, not a true cohort conversion: orders created just "
            "before the export cut are still in transit and depress the rate slightly. "
            "It also counts orders that were delivered late."
        ),
    ))

    # 7. Cancellation rate -------------------------------------------------
    kpis.append(KPI(
        key="cancellation_rate",
        name="Cancellation rate",
        category="Fulfilment",
        formula="canceled orders / all orders",
        numerator_label="orders with status canceled",
        numerator=f["canceled_orders"],
        denominator_label="all orders",
        denominator=f["total_orders"],
        value=100.0 * f["canceled_orders"] / f["total_orders"],
        unit="percent",
        filters="order_status = 'canceled' over all orders",
        eligible_population=f"all {int(f['total_orders']):,} orders",
        limitations=(
            "Only orders that reached the marketplace are visible; requests cancelled "
            "before an order_id was created are not in the data. 'unavailable' orders "
            "(failed fulfilment) are a separate status and are not counted as "
            "cancellations."
        ),
    ))

    # 8. Average review score ---------------------------------------------
    kpis.append(KPI(
        key="average_review_score",
        name="Average review score",
        category="Customer",
        formula="AVG(bridge_review.review_score)",
        numerator_label="sum of review scores",
        numerator=f["avg_review_score"] * f["review_count"],
        denominator_label="reviews (one per order)",
        denominator=f["review_count"],
        value=float(f["avg_review_score"]),
        unit="ratio",
        filters="every review row; the ETL keeps one review per order (latest answer)",
        eligible_population=f"{int(f['review_count']):,} reviewed orders of {int(f['total_orders']):,}",
        limitations=(
            "Reviews are voluntary and left soon after delivery, so they over-represent "
            "early experience and under-represent silent dissatisfaction. Scores of "
            "1-2 are conventionally treated as negative."
        ),
        notes=f"Review coverage is {100.0 * f['review_count'] / f['total_orders']:.2f}% of orders.",
    ))

    # 9. On-time delivery rate --------------------------------------------
    on_time_pct = 100.0 * f["on_time_orders"] / f["delivery_evaluable"]
    kpis.append(KPI(
        key="on_time_delivery_rate",
        name="On-time delivery rate",
        category="Fulfilment",
        formula="orders delivered on/before estimate / orders with a delivery date",
        numerator_label="on-time orders",
        numerator=f["on_time_orders"],
        denominator_label="evaluable orders (delivery date present)",
        denominator=f["delivery_evaluable"],
        value=on_time_pct,
        unit="percent",
        filters=(
            "order_delivered_customer_date IS NOT NULL; is_late_vs_estimate = 0 "
            "(delivered at or before the estimated date)"
        ),
        eligible_population=f"{int(f['delivery_evaluable']):,} of {int(f['total_orders']):,} orders",
        limitations=(
            "Denominator excludes orders with no delivery timestamp (2,965), which "
            "includes some never-delivered orders; excluding them flatters the rate. "
            "The estimate itself is a commercial promise that can be padded, so a high "
            "rate is not the same as a fast delivery."
        ),
    ))

    # 10. Late delivery rate ----------------------------------------------
    kpis.append(KPI(
        key="late_delivery_rate",
        name="Late delivery rate",
        category="Fulfilment",
        formula="orders delivered after estimate / orders with a delivery date",
        numerator_label="late orders",
        numerator=f["late_orders"],
        denominator_label="evaluable orders (delivery date present)",
        denominator=f["delivery_evaluable"],
        value=100.0 * f["late_orders"] / f["delivery_evaluable"],
        unit="percent",
        filters="order_delivered_customer_date IS NOT NULL; is_late_vs_estimate = 1",
        eligible_population=f"{int(f['delivery_evaluable']):,} of {int(f['total_orders']):,} orders",
        limitations=(
            "Complement of the on-time rate; it is 'late vs the promise', not an "
            "absolute service level. Orders with no delivery record are neither on "
            "time nor late and are excluded from both."
        ),
        notes=(
            f"{int(f['delivery_missing_date']):,} orders ({100.0 * f['delivery_missing_date'] / f['total_orders']:.2f}%) "
            "have no delivery timestamp and sit outside this KPI."
        ),
    ))

    # 11. Average delivery duration ---------------------------------------
    kpis.append(KPI(
        key="average_delivery_duration",
        name="Average delivery duration",
        category="Fulfilment",
        formula="AVG(days from purchase to customer delivery)",
        numerator_label="total purchase-to-delivery days",
        numerator=f["avg_delivery_days"] * f["delivery_evaluable"],
        denominator_label="orders with a delivery date",
        denominator=f["delivery_evaluable"],
        value=float(f["avg_delivery_days"]),
        unit="days",
        filters="order_delivered_customer_date IS NOT NULL",
        eligible_population=f"{int(f['delivery_evaluable']):,} of {int(f['total_orders']):,} orders",
        limitations=(
            "Calendar days, not business days. The mean is pulled up by a long tail "
            "(up to 210 days); the median/percentiles are more robust and are shown in "
            "the findings report. Non-delivered orders are excluded."
        ),
    ))

    # 12. Repeat customer rate --------------------------------------------
    kpis.append(KPI(
        key="repeat_customer_rate",
        name="Repeat customer rate",
        category="Customer",
        formula="customers with >= 2 orders / distinct customers",
        numerator_label="repeat customers",
        numerator=f["repeat_customers"],
        denominator_label="distinct customers (customer_unique_id)",
        denominator=f["distinct_customers"],
        value=100.0 * f["repeat_customers"] / f["distinct_customers"],
        unit="percent",
        filters="grouped by customer_unique_id (never customer_id)",
        eligible_population=f"{int(f['distinct_customers']):,} distinct customers",
        limitations=(
            "customer_id is regenerated per order, so customer_unique_id is required to "
            "link a person's orders. The export window is only ~2 years, so customers "
            "who would return after the cut are not observed and the rate is a lower "
            "bound. Guests are pooled only by the identity key the source provides."
        ),
    ))

    # 13. Average items per order -----------------------------------------
    aov_items = f["total_units"] / f["orders_with_items"]
    kpis.append(KPI(
        key="average_items_per_order",
        name="Average items per order",
        category="Basket",
        formula="total item units / orders with >= 1 item",
        numerator_label="item units",
        numerator=f["total_units"],
        denominator_label="orders with >= 1 item",
        denominator=f["orders_with_items"],
        value=aov_items,
        unit="ratio",
        filters="sum of fact_order_summary.n_items over orders with items",
        eligible_population=f"{int(f['orders_with_items']):,} item-bearing orders",
        limitations=(
            "'Item' is an order line (order_item_id), not a distinct product: the same "
            "product twice in one order counts twice. Orders with no items are excluded."
        ),
    ))

    # 14. Payment method distribution -------------------------------------
    pay = pd.read_sql_query(
        "SELECT payment_type, payments, orders_touched, payment_value, pct_of_value, "
        "avg_payment_value FROM vw_payment_method_distribution ORDER BY payment_value DESC",
        conn,
    )
    cc_share = float(pay.loc[pay["payment_type"] == "credit_card", "pct_of_value"].iloc[0])
    kpis.append(KPI(
        key="payment_method_distribution",
        name="Payment method distribution",
        category="Payments",
        formula="SUM(payment_value) by payment_type / total payment value",
        numerator_label="credit-card payment value",
        numerator=float(pay.loc[pay["payment_type"] == "credit_card", "payment_value"].iloc[0]),
        denominator_label="total payment value",
        denominator=float(pay["payment_value"].sum()),
        value=cc_share,
        unit="percent",
        filters="every bridge_payment row (an order can carry several payment records)",
        eligible_population=f"{int(pay['payments'].sum()):,} payment records",
        limitations=(
            "Grain is the payment attempt, not the order: a voucher + card order appears "
            "twice, so shares by row do not sum to order shares. 'not_defined' and a few "
            "non-positive voucher rows exist and are retained, not silently dropped."
        ),
        notes="Primary value is the credit-card share of payment value; full mix is in `detail`.",
        detail=pay,
    ))

    return kpis


def kpis_to_frame(kpis: list[KPI]) -> pd.DataFrame:
    rows = []
    for k in kpis:
        d = k.as_dict()
        d.pop("formula", None)
        rows.append(d)
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# Markdown rendering
# --------------------------------------------------------------------------
def _fmt_option(value, unit: str) -> str:
    if value is None:
        return "-"
    if unit == "currency":
        return f"R$ {value:,.2f}"
    if unit == "percent":
        return f"{value:,.2f}%"
    if unit == "days":
        return f"{value:,.2f}"
    if unit == "count":
        return f"{int(round(value)):,}"
    return f"{value:,.4f}"


def kpis_to_markdown(kpis: list[KPI], population: pd.DataFrame | None = None) -> str:
    lines: list[str] = []
    lines.append("# ShopScope - Business Metrics (KPI catalogue)\n")
    lines.append(
        "All figures below are computed live from `data/shopscope.db`, which is the "
        "validated SQLite build of the cleaned CSVs. Nothing is hardcoded. "
        "**Merchandise sales** = `SUM(fact_order_item.price)` and always excludes "
        "freight; **freight** and **payment cash** are separate measures.\n"
    )
    lines.append(
        "> The database is a snapshot of the Olist export cut on **2018-10-17**. "
        "See the trend caveats in `reports/eda_findings.md`.\n"
    )

    lines.append("## Summary\n")
    lines.append("| # | KPI | Value | Unit | Population |")
    lines.append("|---|-----|-------|------|------------|")
    for i, k in enumerate(kpis, start=1):
        condition = "currency" if k.unit == "currency" else k.unit
        lines.append(
            f"| {i} | {k.name} | {k.formatted()} | {condition} | "
            f"{k.eligible_population} |"
        )
    lines.append("")

    lines.append("## KPI definitions\n")
    for i, k in enumerate(kpis, start=1):
        lines.append(f"### {i}. {k.name}\n")
        lines.append(f"- **Value:** {k.formatted()}")
        lines.append(f"- **Formula:** {k.formula}")
        lines.append(f"- **Numerator:** {k.numerator_label} = {_fmt_option(k.numerator, k.unit)}")
        lines.append(f"- **Denominator:** {k.denominator_label} = {_fmt_option(k.denominator, k.unit)}")
        lines.append(f"- **Filters:** {k.filters or 'none'}")
        lines.append(f"- **Eligible population:** {k.eligible_population}")
        lines.append(f"- **Limitations:** {k.limitations}")
        if k.notes:
            lines.append(f"- **Notes:** {k.notes}")
        if k.detail is not None:
            lines.append("")
            lines.append("Detail table:\n")
            lines.append("```")
            lines.append(k.detail.to_string(index=False))
            lines.append("```")
        lines.append("")

    if population is not None and not population.empty:
        lines.append("## Eligible populations (live counts from `vw_kpi_population`)\n")
        lines.append("```")
        lines.append(population.to_string(index=False))
        lines.append("```")
        lines.append("")

    lines.append("## Cross-checks\n")
    lines.append(
        "- Merchandise sales + freight vs gross item value vs cash collected are "
        "reconciled in `tests/test_join_behavior.py` and `tests/test_sql_vs_python.py`.\n"
    )
    lines.append(
        "- Every KPI above is recomputed independently in pandas in the test-suite; "
        "the SQL figure and the pandas figure must match.\n"
    )
    return "\n".join(lines)


def write_metrics_report(conn: sqlite3.Connection, path: Path | str | None = None) -> Path:
    kpis = compute_kpis(conn)
    population = pd.read_sql_query(
        "SELECT kpi, eligible_population, source_rows, rows_used FROM vw_kpi_population",
        conn,
    )
    report_path = Path(path) if path else config.METRICS_REPORT_MD
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(kpis_to_markdown(kpis, population), encoding="utf-8")
    logger.info("wrote %s", report_path)
    return report_path


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.metrics",
        description="Compute and document every ShopScope business KPI.",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--report", action="store_true", help="write reports/business_metrics.md")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(message)s", stream=sys.stdout)

    if not config.DB_PATH.is_file():
        print(f"database not found: {config.DB_PATH}", file=sys.stderr)
        print("build it first: python -m src.database", file=sys.stderr)
        return 1

    conn = get_connection(config.DB_PATH, foreign_keys=False)
    try:
        kpis = compute_kpis(conn)
        if args.json:
            print(json.dumps([k.as_dict() for k in kpis], indent=2, default=str))
            return 0
        if args.report:
            print(write_metrics_report(conn))
            return 0
        for k in kpis:
            print(f"{k.name:<32} {k.formatted():>18}   [{k.eligible_population}]")
    except sqlite3.Error as exc:
        print(f"query failed: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
