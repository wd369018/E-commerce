"""Shared fixtures for the ShopScope test suite.

Run from the project root::

    python -m pytest -q

The database used by the tests is built once into a cache directory
(``.pytest_shopscope_cache/``) and reused while it is newer than the cleaned
CSVs and the SQL files. Set ``SHOPSCOPE_TEST_DB_FRESH=1`` to force a rebuild.
Tests never touch ``data/shopscope.db``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import pytest

from src import config, database, queries

CACHE_DB = ROOT / ".pytest_shopscope_cache" / "shopscope_test.db"


def _newest_source_mtime() -> float:
    newest = 0.0
    for table in database.TABLE_ORDER:
        path = config.PROCESSED_DIR / f"{table}.csv"
        if path.is_file():
            newest = max(newest, path.stat().st_mtime)
    for sql in (config.SCHEMA_SQL, config.ANALYTICAL_SQL):
        if sql.is_file():
            newest = max(newest, sql.stat().st_mtime)
    return newest


def _count_csv_rows(path: Path) -> int:
    # Not a line count: quoted review comments contain embedded newlines, so the
    # only reliable record count comes from a real CSV parse.
    return len(pd.read_csv(path, usecols=[0]))


def _cached_build() -> database.BuildResult:
    """Build the test database, reusing a cache when it is up to date."""
    csv_counts = {
        table: _count_csv_rows(config.PROCESSED_DIR / f"{table}.csv")
        for table in database.TABLE_ORDER
    }
    forced = os.environ.get("SHOPSCOPE_TEST_DB_FRESH") == "1"
    if not forced and CACHE_DB.is_file() and CACHE_DB.stat().st_mtime > _newest_source_mtime():
        connection = database.get_connection(CACHE_DB)
        try:
            verification = database.verify(connection, csv_counts)
        finally:
            connection.close()
        return database.BuildResult(
            db_path=CACHE_DB,
            table_counts=verification["row_counts"],
            csv_counts=csv_counts,
            verification=verification,
        )

    CACHE_DB.parent.mkdir(parents=True, exist_ok=True)
    return database.build_database(db_path=CACHE_DB, fresh=True)


@pytest.fixture(scope="session")
def build_result() -> database.BuildResult:
    return _cached_build()


@pytest.fixture(scope="session")
def conn(build_result):
    connection = database.get_connection(build_result.db_path, foreign_keys=True)
    yield connection
    connection.close()


@pytest.fixture(scope="session")
def csv_frames() -> dict[str, pd.DataFrame]:
    """The cleaned CSVs - the independent source for SQL-vs-Python checks."""
    return {
        table: pd.read_csv(config.PROCESSED_DIR / f"{table}.csv")
        for table in database.TABLE_ORDER
    }


@pytest.fixture(scope="session")
def run_query(conn):
    """Run a catalogue query once and memoise the resulting frame."""
    cache: dict[str, pd.DataFrame] = {}

    def _run(name: str) -> pd.DataFrame:
        if name not in cache:
            cache[name] = queries.get(name).execute(conn).copy()
        return cache[name].copy()

    return _run


def scalar(conn, sql: str, params: tuple = ()):
    return conn.execute(sql, params).fetchone()[0]


def rows(conn, sql: str, params: tuple = ()):
    return [tuple(r) for r in conn.execute(sql, params)]
