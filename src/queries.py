"""
ShopScope - analytical query catalogue.

``sql/analytical_queries.sql`` holds two sections: the ``CREATE VIEW``
statements (applied by :mod:`src.database`) and a **query library** of named
report queries delimited by ``-- @query: name`` / ``-- @end``. This module
parses that file so SQL lives in one place and Python only references it by
name - there is no second copy of any query to drift out of sync.

Usage::

    python -m src.queries --list              # list the query library
    python -m src.queries                     # run every query
    python -m src.queries monthly_merchandise_sales seller_performance
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from . import config

logger = logging.getLogger(__name__)

QUERY_LIBRARY_MARKER = "-- ==== QUERY LIBRARY"
QUERY_START = "-- @query:"
QUERY_END = "-- @end"


class QueryError(RuntimeError):
    """Raised for a malformed query file or an unknown query name."""


@dataclass(frozen=True)
class QuerySpec:
    name: str
    description: str
    sql: str

    def execute(self, conn: sqlite3.Connection) -> pd.DataFrame:
        return pd.read_sql_query(self.sql, conn)


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
def split_analytical_sql(text: str) -> tuple[str, list[QuerySpec]]:
    """Return ``(views_sql, query_specs)`` from the analytical SQL file."""
    marker_at = text.find(QUERY_LIBRARY_MARKER)
    if marker_at < 0:
        raise QueryError(
            f"analytical SQL is missing the '{QUERY_LIBRARY_MARKER.strip()}' marker"
        )
    views_sql = text[:marker_at].rstrip()
    library = text[marker_at:]

    specs: list[QuerySpec] = []
    lines = library.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line.startswith(QUERY_START):
            index += 1
            continue

        name = line[len(QUERY_START):].strip()
        if not name:
            raise QueryError(f"query declaration without a name at line {index + 1}")

        index += 1
        description_lines: list[str] = []
        while index < len(lines) and not lines[index].strip().startswith(QUERY_END):
            comment = lines[index].strip()
            if comment.startswith("--"):
                description_lines.append(comment.lstrip("- ").strip())
            index += 1
        if index >= len(lines):
            raise QueryError(f"query '{name}' is missing its '{QUERY_END}' terminator")
        index += 1  # consume @end

        sql_lines: list[str] = []
        while index < len(lines) and not lines[index].strip().startswith(QUERY_START):
            sql_lines.append(lines[index])
            index += 1

        sql = "\n".join(sql_lines).strip().rstrip(";").strip()
        if not sql:
            raise QueryError(f"query '{name}' has no SQL")
        specs.append(QuerySpec(name=name, description=" ".join(description_lines), sql=sql))

    if not specs:
        raise QueryError("the query library section contains no queries")
    return views_sql, specs


def load_analytical_sql(path: Path | str | None = None) -> tuple[str, list[QuerySpec]]:
    sql_path = Path(path) if path else config.ANALYTICAL_SQL
    if not sql_path.is_file():
        raise QueryError(f"analytical SQL file not found: {sql_path}")
    return split_analytical_sql(sql_path.read_text(encoding="utf-8"))


def create_views(conn: sqlite3.Connection, path: Path | str | None = None) -> int:
    """Apply every CREATE VIEW statement. Returns the number of views."""
    views_sql, specs = load_analytical_sql(path)
    before = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")
    }
    try:
        conn.executescript("BEGIN;\n" + views_sql + "\nCOMMIT;")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass
        raise
    after = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")
    }
    created = len(after)
    logger.info("created %d analytical views (%d query-library entries)", created, len(specs))
    return created


# --------------------------------------------------------------------------
# Catalogue access
# --------------------------------------------------------------------------
def specs(path: Path | str | None = None) -> list[QuerySpec]:
    return load_analytical_sql(path)[1]


def catalogue(path: Path | str | None = None) -> dict[str, QuerySpec]:
    return {spec.name: spec for spec in specs(path)}


def get(name: str, path: Path | str | None = None) -> QuerySpec:
    spec = catalogue(path).get(name)
    if spec is None:
        known = ", ".join(sorted(catalogue(path)))
        raise QueryError(f"unknown query '{name}'. Available: {known}")
    return spec


def run(conn: sqlite3.Connection, name: str) -> pd.DataFrame:
    return get(name).execute(conn)


def run_all(conn: sqlite3.Connection) -> dict[str, pd.DataFrame]:
    return {spec.name: spec.execute(conn) for spec in specs()}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.queries",
        description="Run the ShopScope analytical query library.",
    )
    parser.add_argument("names", nargs="*", help="query names (default: all)")
    parser.add_argument("--list", action="store_true", help="list available queries")
    parser.add_argument("--rows", type=int, default=40, help="rows to display")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(message)s", stream=sys.stdout)

    try:
        catalogue_all = catalogue()
    except QueryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.list:
        for name, spec in catalogue_all.items():
            print(f"{name:<38} {spec.description}")
        return 0

    names = args.names or list(catalogue_all)
    unknown = [n for n in names if n not in catalogue_all]
    if unknown:
        print(f"unknown query/queries: {', '.join(unknown)}", file=sys.stderr)
        print("use --list to see the catalogue", file=sys.stderr)
        return 1

    # Import here so `queries` stays importable without a database.
    from .database import get_connection  # noqa: PLC0415

    if not config.DB_PATH.is_file():
        print(f"database not found: {config.DB_PATH}", file=sys.stderr)
        print("build it first: python -m src.database", file=sys.stderr)
        return 1

    conn = get_connection(config.DB_PATH, foreign_keys=False)
    try:
        for name in names:
            spec = catalogue_all[name]
            frame = spec.execute(conn)
            print(f"\n=== {name} ===")
            print(f"-- {spec.description}")
            print(f"-- {len(frame)} row(s)")
            with pd.option_context("display.width", 200, "display.max_columns", 30,
                                   "display.max_rows", args.rows):
                print(frame.head(args.rows).to_string(index=False))
    except sqlite3.Error as exc:
        print(f"query failed: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
