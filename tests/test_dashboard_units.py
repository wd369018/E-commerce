"""Dashboard-level tests: imports, filters, CSV downloads, empty results.

These run against the real read-only ``data/shopscope.db`` through the same
``src.dashboard.data`` functions the Streamlit pages use, so a filter or a
download that works here is the exact code path the UI takes. They do not spin
up Streamlit (the rendered pages are covered by ``test_dashboard.py``).
"""

from __future__ import annotations

import importlib
import io
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]

from src.dashboard import data as ds  # noqa: E402
from src.dashboard import formatting as fmt  # noqa: E402


# ==========================================================================
# Modules import / files compile
# ==========================================================================
PROJECT_MODULES = [
    "src.config",
    "src.data_loader",
    "src.data_cleaning",
    "src.transform",
    "src.validation",
    "src.database",
    "src.queries",
    "src.metrics",
    "src.insights",
    "src.visualizations",
    "src.reporting",
    "src.dashboard.data",
    "src.dashboard.charts",
    "src.dashboard.components",
    "src.dashboard.formatting",
]


@pytest.mark.parametrize("module_name", PROJECT_MODULES)
def test_project_module_imports(module_name):
    assert importlib.import_module(module_name) is not None


def test_every_app_and_src_file_compiles():
    files = sorted(ROOT.joinpath("src").rglob("*.py")) + sorted(
        ROOT.joinpath("app").rglob("*.py")
    )
    assert files, "no Python files found"
    for path in files:
        compile(path.read_text(encoding="utf-8"), str(path), "exec")


def test_every_streamlit_view_file_exists():
    views = sorted(ROOT.joinpath("app", "views").glob("*.py"))
    # One page per view; the entry point registers all of them.
    assert len(views) == 7
    app_source = (ROOT / "app" / "app.py").read_text(encoding="utf-8")
    for view in views:
        assert view.name in app_source


# ==========================================================================
# Filters change metrics and agree across functions
# ==========================================================================
def _bounds():
    b = ds.date_bounds()
    return b["min_date"], b["max_date"]


def test_state_filter_reduces_sales_and_matches_state_breakdown():
    start, end = _bounds()
    total = ds.monthly_series(start, end)
    assert not total.empty
    total_sales = float(total["merchandise_sales"].sum())

    state = ds.state_options()[0]
    filtered = ds.monthly_series(start, end, states=(state,))
    assert not filtered.empty
    filtered_sales = float(filtered["merchandise_sales"].sum())

    assert 0 < filtered_sales <= total_sales + 0.01
    by_state = ds.state_series(start, end).set_index("customer_state")
    assert abs(float(by_state.loc[state, "merchandise_sales"]) - filtered_sales) <= 1.0


def test_status_filter_restricts_results():
    start, end = _bounds()
    every = ds.status_breakdown(start, end)
    status = every.iloc[0]["order_status"]
    only = ds.status_breakdown(start, end, statuses=(status,))
    assert set(only["order_status"]) == {status}
    assert int(only["orders"].sum()) <= int(every["orders"].sum())


def test_category_filter_reduces_sales():
    start, end = _bounds()
    total_sales = float(ds.monthly_series(start, end)["merchandise_sales"].sum())
    category = ds.category_options()[0]
    filtered = ds.monthly_series(start, end, categories=(category,))
    filtered_sales = float(filtered["merchandise_sales"].sum())
    assert 0 < filtered_sales <= total_sales + 0.01


# ==========================================================================
# Empty results and missing values must not crash
# ==========================================================================
def test_reversed_date_range_returns_empty_not_error():
    start, end = _bounds()
    # start > end -> no order can match both predicates.
    assert ds.monthly_series(end, start).empty
    assert ds.category_series(end, start).empty
    assert ds.status_breakdown(end, start).empty
    assert ds.delivery_orders(end, start).empty


def test_null_and_duplicate_summaries_have_expected_shape():
    nulls = ds.null_summary()
    assert {"table", "column", "rows", "nulls", "null_pct"} <= set(nulls.columns)
    assert (nulls["rows"] > 0).all()
    assert nulls["null_pct"].between(0, 100).all()

    duplicates = ds.duplicate_summary()
    assert {"check", "count"} <= set(duplicates.columns)
    assert len(duplicates) >= 1


# ==========================================================================
# CSV downloads
# ==========================================================================
def test_csv_download_bytes_roundtrip():
    start, end = _bounds()
    frame = ds.monthly_series(start, end)
    payload = fmt.to_csv_bytes(frame)
    assert payload.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM for Excel on Windows
    reloaded = pd.read_csv(io.BytesIO(payload))
    assert list(reloaded.columns) == list(frame.columns)
    assert len(reloaded) == len(frame)


def test_csv_download_of_empty_frame_is_just_a_header():
    empty = pd.DataFrame({"a": [], "b": []})
    payload = fmt.to_csv_bytes(empty)
    reloaded = pd.read_csv(io.BytesIO(payload))
    assert list(reloaded.columns) == ["a", "b"]
    assert reloaded.empty


# ==========================================================================
# Geolocation performance guard
# ==========================================================================
def test_geolocation_is_aggregated_not_raw():
    rows = int(ds.run_sql("SELECT COUNT(*) AS n FROM dim_geolocation").iloc[0]["n"])
    # One row per ZIP prefix (~19k) - never the ~1,000,163 raw observations.
    assert 0 < rows < 100_000
    source = (ROOT / "src" / "dashboard" / "data.py").read_text(encoding="utf-8")
    assert "olist_geolocation" not in source


# ==========================================================================
# Missing-database handling
# ==========================================================================
def test_missing_database_is_reported_not_crashed(monkeypatch, tmp_path):
    from src import config

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "missing.db")
    assert ds.database_exists() is False
    assert ds.db_info()["exists"] is False
    with pytest.raises(FileNotFoundError, match="database not found|Database not found"):
        ds._connect()
