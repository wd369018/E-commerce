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

from src.dashboard import components as ui
from src.dashboard import data as ds
from src.dashboard import theme

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
    st.error(
        "The analytics database was not found. From the project root run "
        "`python -m src.database` (builds SQLite) and `python -m src.queries` "
        "(applies the analytical views), then reload this page."
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