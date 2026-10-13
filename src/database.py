"""
ShopScope - SQLite database layer.

Responsibilities
----------------
* Create a **reproducible** SQLite database from the cleaned CSVs in
  ``data/processed/`` (deleting any previous file first, so the schema can
  never go stale).
* Apply ``sql/schema.sql`` - primary keys, foreign keys and indexes - inside a
  transaction.
* Load every table inside a **single explicit transaction**: either all tables
  land or none of them do.
* Verify the result: row counts vs the source CSVs, ``PRAGMA integrity_check``,
  ``PRAGMA foreign_key_check``, and index/view inventory.

Usage::

    python -m src.database            # (re)build the database and print a report
    python -m src.database --keep     # rebuild but keep the existing file's rows
                                      # (schema.sql still drops/recreates tables)

Nothing here writes to the raw or cleaned CSVs - the database is an output.
"""

from __future__ import annotations

import argparse
import logging
import math
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from . import config, queries

logger = logging.getLogger(__name__)

# Load order respects foreign-key parents.
TABLE_ORDER: tuple[str, ...] = (
    "dim_customer",
    "dim_customer_identity",
    "dim_product_category",
    "dim_product",
    "dim_seller",
    "dim_geolocation",
    "dim_order",
    "fact_order_item",
    "bridge_payment",
    "bridge_review",
    "fact_order_summary",
)

# Columns deliberately not persisted: the English category label is normalised
# into dim_product_category, so keeping a copy on dim_product would be a
# redundant, diverge-prone duplicate.
DROPPED_COLUMNS: dict[str, tuple[str, ...]] = {
    "dim_product": ("product_category_name_english",),
}

# CSV columns holding counts/indices -> SQLite INTEGER (nullable where blank).
INTEGER_COLUMNS: dict[str, tuple[str, ...]] = {
    "dim_customer": ("customer_zip_code_prefix",),
    "dim_customer_identity": ("placement_count",),
    "dim_product": (
        "product_name_lenght",
        "product_description_lenght",
        "product_photos_qty",
    ),
    "dim_seller": ("seller_zip_code_prefix",),
    "dim_geolocation": (
        "geolocation_zip_code_prefix",
        "valid_coordinate_count",
        "source_row_count",
    ),
    "fact_order_item": ("order_item_id",),
    "bridge_payment": ("payment_sequential", "payment_installments"),
    "bridge_review": ("review_score", "source_review_rows", "distinct_review_ids"),
    "fact_order_summary": (
        "n_items",
        "n_distinct_products",
        "n_payments",
        "n_reviews",
    ),
}

# CSV True/False columns -> SQLite INTEGER 0/1 (NULL where the CSV is blank,
# i.e. the tri-state columns is_late_vs_estimate and is_shipping_valid).
BOOLEAN_COLUMNS: dict[str, tuple[str, ...]] = {
    "dim_customer": (),
    "dim_order": (
        "is_late_vs_estimate",
        "is_delivered_status",
        "is_revenue_status",
        "missing_approval_date",
        "missing_carrier_date",
        "missing_delivery_date",
        "has_line_items",
        "has_payments",
        "has_reviews",
        "has_impossible_date_sequence",
    ),
    "dim_product": ("category_was_missing", "is_shipping_valid"),
    "dim_product_category": ("has_english_translation",),
    "dim_geolocation": ("is_valid_coordinate",),
    "fact_order_item": ("is_valid_price", "is_valid_freight"),
    "bridge_payment": ("is_valid_payment_value", "is_valid_installments"),
}


class DatabaseBuildError(RuntimeError):
    """Raised when schema application, loading or verification fails."""


# --------------------------------------------------------------------------
# Connections
# --------------------------------------------------------------------------
def get_connection(db_path: Path | str | None = None, *, foreign_keys: bool = True):
    """Open a connection with row access by name and FK enforcement on.

    ``isolation_level=None`` puts the driver in autocommit mode so that the
    caller - not the driver - owns every BEGIN/COMMIT. That is what makes an
    explicit transaction possible around the whole load.
    """
    path = Path(db_path) if db_path else config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA foreign_keys = {'ON' if foreign_keys else 'OFF'}")
    return conn


# --------------------------------------------------------------------------
# Type conversion
# --------------------------------------------------------------------------
def _as_bool_int(value) -> int | None:
    if isinstance(value, (bool, np.bool_)):
        return 1 if value else 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1"}:
            return 1
        if lowered in {"false", "0"}:
            return 0
        return None
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return None


def _to_python(value):
    """Convert a pandas/numpy scalar into a SQLite-bindable Python value."""
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (bool, np.bool_)):
        return 1 if value else 0
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        number = float(value)
        return None if math.isnan(number) else number
    if isinstance(value, pd.Timestamp):
        return value.isoformat(sep=" ")
    if isinstance(value, str):
        return value
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def cast_frame(table: str, frame: pd.DataFrame) -> pd.DataFrame:
    """Apply the column casts declared above; returns a new frame."""
    df = frame.copy()
    for col in DROPPED_COLUMNS.get(table, ()):
        if col in df.columns:
            df = df.drop(columns=[col])
    for col in INTEGER_COLUMNS.get(table, ()):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    for col in BOOLEAN_COLUMNS.get(table, ()):
        if col in df.columns:
            mapped = df[col].map(_as_bool_int)
            df[col] = pd.to_numeric(mapped, errors="coerce").astype("Int64")
    return df


def _insert_frame(conn: sqlite3.Connection, table: str, df: pd.DataFrame) -> int:
    columns = list(df.columns)
    quoted = ", ".join(f'"{c}"' for c in columns)
    placeholders = ", ".join("?" for _ in columns)
    sql = f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders})'

    records = df.itertuples(index=False, name=None)
    batch = [tuple(_to_python(v) for v in row) for row in records]
    conn.executemany(sql, batch)
    return len(batch)


# --------------------------------------------------------------------------
# Verification helpers
# --------------------------------------------------------------------------
def row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Row count of every user table, in schema order."""
    names = [
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    return {name: conn.execute(f'SELECT COUNT(*) AS n FROM "{name}"').fetchone()["n"]
            for name in names}


def object_inventory(conn: sqlite3.Connection) -> dict[str, list[str]]:
    def _names(kind: str) -> list[str]:
        return [
            r["name"]
            for r in conn.execute(
                f"SELECT name FROM sqlite_master WHERE type='{kind}' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]

    return {"tables": _names("table"), "indexes": _names("index"), "views": _names("view")}


def verify(conn: sqlite3.Connection, expected_counts: dict[str, int] | None = None) -> dict:
    """Integrity, foreign-key and row-count verification of a built database."""
    problems: list[str] = []

    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        problems.append(f"integrity_check -> {integrity}")

    fk_violations = [dict(row) for row in conn.execute("PRAGMA foreign_key_check")]
    if fk_violations:
        problems.append(f"foreign_key_check -> {len(fk_violations)} violation(s)")

    counts = row_counts(conn)
    if expected_counts:
        for table, expected in expected_counts.items():
            actual = counts.get(table)
            if actual != expected:
                problems.append(f"{table}: {actual} rows loaded, expected {expected}")

    return {
        "integrity_check": integrity,
        "foreign_key_violations": len(fk_violations),
        "row_counts": counts,
        "problems": problems,
        "ok": not problems,
    }


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------
@dataclass
class BuildResult:
    db_path: Path
    table_counts: dict[str, int] = field(default_factory=dict)
    csv_counts: dict[str, int] = field(default_factory=dict)
    indexes: list[str] = field(default_factory=list)
    views: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    verification: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.verification.get("ok")) and not self.verification.get("problems")


def build_database(
    db_path: Path | str | None = None,
    processed_dir: Path | str | None = None,
    schema_path: Path | str | None = None,
    *,
    fresh: bool = True,
) -> BuildResult:
    """(Re)create the database from the cleaned CSVs. Reproducible by design.

    Parameters
    ----------
    fresh
        Delete the existing database file first (default). Guarantees no stale
        tables or indexes from an earlier schema can survive.
    """
    started = time.perf_counter()
    path = Path(db_path) if db_path else config.DB_PATH
    source_dir = Path(processed_dir) if processed_dir else config.PROCESSED_DIR
    schema_file = Path(schema_path) if schema_path else config.SCHEMA_SQL

    if not schema_file.is_file():
        raise DatabaseBuildError(f"schema file not found: {schema_file}")
    if not source_dir.is_dir():
        raise DatabaseBuildError(
            f"cleaned data directory not found: {source_dir} "
            "(run `python -m src.etl_pipeline` first)"
        )

    if fresh:
        for suffix in ("", "-wal", "-shm"):
            stale = Path(str(path) + suffix)
            if stale.exists():
                stale.unlink()
                logger.debug("Removed stale database file %s", stale.name)

    # ---- read + cast every source CSV before touching the database --------
    csv_counts: dict[str, int] = {}
    frames: dict[str, pd.DataFrame] = {}
    for table in TABLE_ORDER:
        csv_path = source_dir / f"{table}.csv"
        if not csv_path.is_file():
            raise DatabaseBuildError(f"missing cleaned table: {csv_path}")
        raw = pd.read_csv(csv_path)
        csv_counts[table] = len(raw)
        frames[table] = cast_frame(table, raw)
        logger.info("read %-24s %8s rows, %2d columns",
                    csv_path.name, f"{len(raw):,}", frames[table].shape[1])

    conn = get_connection(path, foreign_keys=False)
    result = BuildResult(db_path=path, csv_counts=csv_counts)
    try:
        # ---- DDL (schema.sql carries its own BEGIN/COMMIT) ----------------
        logger.info("applying schema %s", schema_file.relative_to(config.PROJECT_ROOT))
        conn.executescript(schema_file.read_text(encoding="utf-8"))
        conn.execute("PRAGMA foreign_keys = ON")

        # ---- load, all tables in ONE transaction -------------------------
        conn.execute("BEGIN")
        try:
            for table in TABLE_ORDER:
                n = _insert_frame(conn, table, frames[table])
                logger.info("loaded %-24s %8s rows", table, f"{n:,}")
            conn.execute("COMMIT")
        except Exception as exc:
            conn.execute("ROLLBACK")
            raise DatabaseBuildError(
                f"load failed and was rolled back - database left empty: {exc}"
            ) from exc

        # ---- views / analytical queries ----------------------------------
        queries.create_views(conn)

        # ---- verify ------------------------------------------------------
        inventory = object_inventory(conn)
        result.indexes = inventory["indexes"]
        result.views = inventory["views"]
        result.verification = verify(conn, csv_counts)
        result.table_counts = result.verification["row_counts"]
        result.duration_seconds = time.perf_counter() - started

        if not result.ok:
            raise DatabaseBuildError(
                "verification failed: " + "; ".join(result.verification["problems"])
            )
    except DatabaseBuildError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DatabaseBuildError(f"database build failed: {exc}") from exc
    finally:
        conn.close()

    logger.info(
        "database ready: %s (%d tables, %d indexes, %d views, %.2fs)",
        path.name, len(result.table_counts), len(result.indexes),
        len(result.views), result.duration_seconds,
    )
    return result


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.database",
        description="Build the ShopScope SQLite database from data/processed/.",
    )
    parser.add_argument("--keep", action="store_true",
                        help="do not delete an existing database file first")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        stream=sys.stdout,
        force=True,
    )

    try:
        result = build_database(fresh=not args.keep)
    except DatabaseBuildError as exc:
        logger.error("%s", exc)
        return 1

    print()
    print(f"Database : {result.db_path}")
    print(f"Duration : {result.duration_seconds:.2f}s")
    print(f"Integrity: {result.verification['integrity_check']} "
          f"| FK violations: {result.verification['foreign_key_violations']}")
    print()
    print(f"{'Table':<26} {'Rows (db)':>12} {'Rows (csv)':>12}  match")
    print("-" * 62)
    for table in TABLE_ORDER:
        db_n = result.table_counts.get(table, 0)
        csv_n = result.csv_counts.get(table, 0)
        print(f"{table:<26} {db_n:>12,} {csv_n:>12,}  {'OK' if db_n == csv_n else 'MISMATCH'}")
    print("-" * 62)
    print(f"{'TOTAL':<26} {sum(result.table_counts.values()):>12,} "
          f"{sum(result.csv_counts.values()):>12,}")
    print()
    print(f"Indexes ({len(result.indexes)}): " + ", ".join(result.indexes))
    print(f"Views   ({len(result.views)}): " + ", ".join(result.views))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
