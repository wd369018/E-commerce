"""ShopScope - E-Commerce Intelligence Platform (Streamlit entry point).

Run from the project root:

    streamlit run app/app.py

The app reads ``data/shopscope.db`` exclusively in read-only mode, so it can
never modify the database, the cleaned CSVs or the raw exports. All queries and
KPIs reuse the existing ``src`` analysis layer.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st

from src import config
from src.dashboard import components as ui
from src.dashboard import data as ds
from src.dashboard import theme


def _bootstrap_database() -> None:
    """Create the analytics database (and its analytical views) if missing.

    Mirrors the manual sequence exactly:

        python -m src.etl_pipeline   # only when data/processed/ is absent
        python -m src.database       # build data/shopscope.db (schema + load)
        python -m src.queries        # apply the CREATE VIEW statements

    If the cleaned tables are absent but ``data/processed.tar.gz`` is present
    (the compressed copy shipped so the repository stays small), it is
    extracted first. Every path is taken from :mod:`src.config`, which derives
    everything from the project root (``Path(__file__).resolve().parents[1]``),
    so this works unchanged on a fresh clone or a Streamlit Cloud deployment -
    no absolute, machine-specific paths are used anywhere.
    """
    # Imported lazily so a missing database does not pay the pandas/sqlite
    # import cost until it is actually needed.
    import tarfile

    from src import database, etl_pipeline, queries

    processed = config.PROCESSED_DIR
    have_cleaned_tables = processed.is_dir() and any(processed.glob("*.csv"))
    if not have_cleaned_tables:
        archive = config.DATA_DIR / "processed.tar.gz"
        if archive.is_file():
            # Ship the cleaned tables compressed; unpack them on first run.
            processed.mkdir(parents=True, exist_ok=True)
            with tarfile.open(archive, "r:gz") as tar:
                tar.extractall(processed, filter="data")
        else:
            # No archive: regenerate the cleaned tables from the raw CSVs.
            if etl_pipeline.run() != 0:
                raise RuntimeError(
                    "no cleaned tables found and the ETL stage failed - provide "
                    "either data/processed.tar.gz or the raw Olist CSVs in the "
                    "project root (see reports/etl.log)"
                )

    # build_database() applies sql/schema.sql, loads every table inside one
    # transaction and already creates the analytical views.
    database.build_database(fresh=True)

    # Belt and braces: guarantee the views exist even if a database file was
    # shipped or restored without them.
    conn = database.get_connection()
    try:
        n_views = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'view'"
        ).fetchone()[0]
        if n_views == 0:
            queries.create_views(conn)
    finally:
        conn.close()

st.set_page_config(
    page_title="ShopScope - E-Commerce Intelligence",
    page_icon="\U0001F3EC",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Render the Light/Dark toggle first so the session carries the user's choice
# before any CSS, chart or page reads it.
theme.render_theme_control()
ui.inject_css()

if not ds.database_exists():
    # Run the bootstrap once per session; a rerun (e.g. toggling the theme)
    # must not rebuild the database again.
    if not st.session_state.get("_db_bootstrap_attempted", False):
        st.session_state["_db_bootstrap_attempted"] = True
        with st.status(
            "Preparing the analytics database (first run)...", expanded=True
        ) as status:
            try:
                st.write("Cleaning source data (if needed) and building SQLite...")
                _bootstrap_database()
            except Exception as exc:  # noqa: BLE001
                st.session_state["_db_bootstrap_error"] = str(exc)
                status.update(label="Database build failed", state="error")
            else:
                st.session_state.pop("_db_bootstrap_error", None)
                status.update(label="Analytics database ready", state="complete")

    if not ds.database_exists():
        st.error(
            "The analytics database could not be built.\n\n"
            f"Reason: {st.session_state.get('_db_bootstrap_error', 'unknown error')}\n\n"
            "Build it manually from the project root:\n"
            "`py -3.14 -m src.etl_pipeline` then `py -3.14 -m src.database`, "
            "then reload this page. The ETL needs the raw Olist CSVs in the "
            "project root or `data/raw/`."
        )
        st.stop()

PAGES = [
    st.Page(
        "views/executive_overview.py",
        title="Executive Overview",
        icon=":material/space_dashboard:",
        default=True,
        url_path="overview",
    ),
    st.Page(
        "views/sales_analytics.py",
        title="Sales Analytics",
        icon=":material/monitoring:",
        url_path="sales",
    ),
    st.Page(
        "views/customer_analytics.py",
        title="Customer Analytics",
        icon=":material/groups:",
        url_path="customers",
    ),
    st.Page(
        "views/delivery_operations.py",
        title="Delivery and Operations",
        icon=":material/local_shipping:",
        url_path="delivery",
    ),
    st.Page(
        "views/customer_satisfaction.py",
        title="Customer Satisfaction",
        icon=":material/star_half:",
        url_path="satisfaction",
    ),
    st.Page(
        "views/payments_products.py",
        title="Payments & Product Intelligence",
        icon=":material/account_balance_wallet:",
        url_path="payments",
    ),
    st.Page(
        "views/data_quality.py",
        title="Data Quality & Business Insights",
        icon=":material/fact_check:",
        url_path="data-quality",
    ),
]

nav = st.navigation(
    {
        "Overview": [PAGES[0]],
        "Analysis": PAGES[1:6],
        "Governance": [PAGES[6]],
    },
    position="sidebar",
)
nav.run()