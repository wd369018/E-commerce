"""
ShopScope - SQLite KPI snapshot.

Reads ``data/shopscope.db`` read-only and prints the headline figures behind the
analytical views, so the numbers in the write-up can be reproduced with one
command. Nothing here writes to the database.

Usage::

    python -m scripts.sql_kpi_snapshot
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config, database, queries  # noqa: E402


def _show(title: str, frame: pd.DataFrame, rows: int | None = None) -> None:
    print(f"\n=== {title} ===")
    display = frame if rows is None else frame.head(rows)
    with pd.option_context("display.width", 220, "display.max_columns", 30):
        print(display.to_string(index=False))


def main() -> int:
    if not config.DB_PATH.is_file():
        print(f"database not found: {config.DB_PATH}")
        print("build it first: python -m src.database")
        return 1

    conn = database.get_connection(config.DB_PATH, foreign_keys=False)
    try:
        inventory = database.object_inventory(conn)
        counts = database.row_counts(conn)
        print("ShopScope SQLite snapshot")
        print(f"database : {config.DB_PATH}")
        print(f"tables   : {len(inventory['tables'])}, indexes: {len(inventory['indexes'])}, "
              f"views: {len(inventory['views'])}")
        print(f"rows     : {sum(counts.values()):,} across {len(counts)} tables")

        money = pd.read_sql_query(
            """
            SELECT (SELECT ROUND(SUM(price), 2) FROM fact_order_item)            AS merchandise_sales,
                   (SELECT ROUND(SUM(freight_value), 2) FROM fact_order_item)    AS freight_charges,
                   (SELECT ROUND(SUM(payment_value), 2) FROM bridge_payment)     AS payment_value,
                   (SELECT ROUND(SUM(price) + SUM(freight_value), 2)
                      FROM fact_order_item)                                     AS gross_item_value
            """,
            conn,
        )
        _show("Money measures (kept separate)", money)

        _show("Orders, monthly sales and review trend",
              queries.get("monthly_merchandise_sales").execute(conn).head(3),
              rows=None)
        _show("Order status distribution",
              queries.get("order_status_distribution").execute(conn))
        _show("Repeat-purchase segments",
              queries.get("repeat_customer_summary").execute(conn))
        _show("Payment method distribution",
              queries.get("payment_method_distribution").execute(conn))
        _show("Installment analysis",
              queries.get("installment_analysis").execute(conn))
        _show("Review score distribution",
              queries.get("review_score_distribution").execute(conn))
        _show("Top 5 categories by merchandise sales",
              queries.get("category_performance").execute(conn), rows=5)
        _show("Top 5 sellers by merchandise sales",
              queries.get("seller_performance").execute(conn), rows=5)
        _show("Weight vs freight",
              queries.get("weight_vs_freight").execute(conn))
        _show("Delivery summary",
              queries.get("delivery_summary").execute(conn))
        _show("KPI eligible populations",
              queries.get("kpi_population").execute(conn))
    except sqlite3.Error as exc:
        print(f"query failed: {exc}")
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
