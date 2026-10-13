"""Tests for dashboard utilities and smoke-checks for the Streamlit pages.

Note: AppTest runs pages in isolation; views don't call set_page_config, so each
view file is testable standalone.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from streamlit.testing.v1 import AppTest  # type: ignore

from src.dashboard import data as ds
from src.dashboard import formatting as fmt


def test_dashboard_formatting():
    assert fmt.fmt_currency(1000) == "R$ 1,000.00"
    assert "1,234" in fmt.fmt_int(1234)
    assert fmt.fmt_percent(12.34) == "12.34%"
    assert len(fmt.to_csv_bytes(ds.monthly_series(*_bounds()))) > 10


def test_dashboard_data_functions():
    s, e = _bounds()
    assert len(ds.state_options()) > 5
    assert len(ds.status_options()) > 3
    assert len(ds.category_options()) > 3
    assert len(ds.monthly_series(s, e)) > 3
    assert len(ds.kpis()) == 14
    arep = ds.analysis()
    assert len(arep.findings) >= 17
    assert "monthly" in arep.frames


def test_dashboard_views_load():
    for view in _view_paths():
        at = AppTest.from_file(view.as_posix()).run(timeout=300)
        # In current Streamlit, AppTest.exception is an (often empty) ElementList
        # rather than None, so test for truthiness, not identity.
        assert not at.exception, f"Exceptions in {view.name}: {list(at.exception)}"


def _bounds():
    b = ds.date_bounds()
    return b["min_date"], b["max_date"]


def _view_paths():
    root = Path(__file__).resolve().parents[1] / "app" / "views"
    return sorted(root.glob("*.py"))


@pytest.mark.parametrize("key", [
    "total_merchandise_sales",
    "average_order_value",
    "delivery_completion_rate",
    "on_time_delivery_rate",
    "average_review_score",
])
def test_kpi_presence(key):
    kmap = ds.kpi_map()
    assert key in kmap
    assert kmap[key].value >= 0
    assert kmap[key].eligible_population  # non-empty


def test_view_widget_keys_are_unique_within_each_page():
    """Regression: no page may reuse an explicit Streamlit widget key.

    Duplicate keys raise StreamlitDuplicateElementKey at runtime (this was the
    cause of the customer-analytics failure: the state multiselect and a chart
    both used key='cust_states'). Scanning the AST catches it before runtime.
    """
    import ast

    for view in _view_paths():
        tree = ast.parse(view.read_text(encoding="utf-8"), filename=str(view))
        keys: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if (kw.arg == "key" and isinstance(kw.value, ast.Constant)
                            and isinstance(kw.value.value, str)):
                        keys.append(kw.value.value)
        duplicates = {k for k in keys if keys.count(k) > 1}
        assert not duplicates, f"Duplicate widget keys in {view.name}: {sorted(duplicates)}"


def test_chart_and_download_calls_always_pass_a_key():
    """Every ui.chart / ui.download_csv call must have an explicit key.

    Without a key Streamlit derives one from the element parameters, so two
    identically-styled charts would collide. Requiring an explicit key keeps
    each element stable and unique.
    """
    import ast

    must_key = {"chart", "download_csv", "download_text"}
    for view in _view_paths():
        tree = ast.parse(view.read_text(encoding="utf-8"), filename=str(view))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in must_key:
                has_key = any(kw.arg == "key" for kw in node.keywords)
                assert has_key, (
                    f"{view.name}: {func.attr}() called without a key at line {node.lineno}"
                )
