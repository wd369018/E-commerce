"""Application-level theme engine for ShopScope.

Why this module exists
----------------------
Streamlit 1.57 serves one pinned theme per session:

* the ``theme.*`` config options are **not scriptable** (``theme.base`` etc. are
  registered with ``scriptable=False``), so they cannot be flipped at runtime
  with ``st.config.set_option``, and ``st.set_page_config`` has no ``theme``
  parameter;
* without a ``.streamlit/config.toml`` Streamlit silently follows the OS
  ``prefers-color-scheme``. On a dark-mode machine the whole app rendered with
  Streamlit's *dark* palette while the old custom CSS forced a light sidebar,
  producing the white-on-white navigation bug;
* the native "..." menu theme switcher is a viewer option - it cannot be read
  from Python, so the app's own components and Plotly charts could never follow
  it.

The app therefore implements its own theme strategy:

1. ``.streamlit/config.toml`` pins ``[theme] base = "light"`` plus the brand
   palette. The baseline is now deterministic on every machine and the native
   navigation/typography contract (dark text) is guaranteed.
2. A compact segmented control ("Appearance") is rendered at the top of the
   sidebar; its value lives in ``st.session_state`` under
   ``THEME_WIDGET_KEY``, so it survives every rerun and switching themes is a
   plain rerun - no loops, no full-page refresh, no duplicate keys.
3. ``theme_script()`` returns a tiny ``<script>`` that tags the document root
   with ``data-theme="light"|"dark"`` (Streamlit 1.57 re-executes ``<script>``
   nodes from ``st.markdown(unsafe_allow_html=True)``). Every CSS override is
   scoped under ``html[data-theme="dark"]`` so the two themes can never bleed
   into each other.
4. Plotly charts read ``plotly_tokens()`` while the figure is built, so paper/
   plot backgrounds, fonts, grids and hover styles match the active theme.

Everything else in the app reads the same session-state value through
``get_active_theme()`` - a single source of truth for components, charts and
pages.
"""

from __future__ import annotations

import streamlit as st

#: Unique widget key for the theme control. Never reused by any page filter.
THEME_WIDGET_KEY = "app_theme_control"

OPTION_LABELS = ("Light", "Dark")

# --------------------------------------------------------------------------
# Colour tokens
# --------------------------------------------------------------------------
# Each theme defines the exact variables the shared CSS consumes. Values are
# chosen for WCAG AA contrast on their own surfaces.
LIGHT = {
    # surfaces
    "canvas": "#f6f8fb",
    "sidebar": "#f8fafc",
    "card": "#ffffff",
    "input_bg": "#ffffff",
    "note_bg": "#f8fafc",
    "warn_bg": "#fffbeb",
    "ok_bg": "#ecfdf5",
    "err_bg": "#fef2f2",
    "code_bg": "#f8fafc",
    "table_header": "#f8fafc",
    "nav_active_bg": "#e3edfd",
    "nav_hover_bg": "#eef2f7",
    "hover_bg": "#eef2f7",
    # borders & dividers
    "border": "#e2e8f0",
    "input_border": "#d4dbe6",
    "warn_border": "#f59e0b",
    "ok_border": "#10b981",
    "err_border": "#ef4444",
    "divider": "#e6eaf0",
    # text
    "text": "#111827",
    "muted": "#5b6b81",
    "faint": "#8b98a9",
    "accent_text": "#1d4ed8",
    # accent
    "accent": "#2563eb",
    "accent_soft": "#eff6ff",
    "shadow": "0 1px 2px rgba(16, 24, 40, 0.05)",
}

DARK = {
    # surfaces
    "canvas": "#0b1220",
    "sidebar": "#0f1830",
    "card": "#131f36",
    "input_bg": "#0e1930",
    "note_bg": "#111c33",
    "warn_bg": "#31250f",
    "ok_bg": "#0f2a20",
    "err_bg": "#331724",
    "code_bg": "#0e1930",
    "table_header": "#13203a",
    "nav_active_bg": "#1d2f4f",
    "nav_hover_bg": "#16223c",
    "hover_bg": "#16223c",
    # borders & dividers
    "border": "#26344e",
    "input_border": "#33415f",
    "warn_border": "#f59e0b",
    "ok_border": "#34d399",
    "err_border": "#f87171",
    "divider": "#22304a",
    # text
    "text": "#e7ecf5",
    "muted": "#9aa8c0",
    "faint": "#7d8ba6",
    "accent_text": "#93c5fd",
    # accent
    "accent": "#60a5fa",
    "accent_soft": "#1d2a44",
    "shadow": "0 1px 2px rgba(0, 0, 0, 0.35)",
}

# Plotly chart tokens.
PLOTLY = {
    "light": {
        "paper": "#ffffff",
        "plot": "#ffffff",
        "grid": "#eef2f7",
        "axis": "#d5dce5",
        "tick": "#3f4a5a",
        "text": "#374151",
        "title": "#111827",
        "hover_bg": "#ffffff",
        "hover_border": "#e5e7eb",
    },
    "dark": {
        "paper": "#0f172a",
        "plot": "#0f172a",
        "grid": "#22304a",
        "axis": "#32415e",
        "tick": "#b8c4d8",
        "text": "#cbd5e1",
        "title": "#eef2f8",
        "hover_bg": "#16213a",
        "hover_border": "#33415f",
    },
}

# Glide Data Grid (st.dataframe) CSS variables.
GDG = {
    "light": {
        "--gdg-bg-cell": "#ffffff",
        "--gdg-bg-header": "#f8fafc",
        "--gdg-bg-header-has-focus": "#eef2f7",
        "--gdg-bg-bubble": "#f1f5f9",
        "--gdg-text-dark": "#111827",
        "--gdg-text-medium": "#475569",
        "--gdg-text-light": "#64748b",
        "--gdg-text-group-header": "#475569",
        "--gdg-border-color": "#e2e8f0",
        "--gdg-accent-color": "#2563eb",
        "--gdg-accent-light": "#dbe7fb",
        "--gdg-accent-fg": "#ffffff",
        "--gdg-link-color": "#3b82f6",
    },
    "dark": {
        "--gdg-bg-cell": "#0e1930",
        "--gdg-bg-header": "#14213a",
        "--gdg-bg-header-has-focus": "#182642",
        "--gdg-bg-bubble": "#1d2b46",
        "--gdg-text-dark": "#e7ecf5",
        "--gdg-text-medium": "#b6c2d4",
        "--gdg-text-light": "#8a99b3",
        "--gdg-text-group-header": "#b6c2d4",
        "--gdg-border-color": "#2a3854",
        "--gdg-accent-color": "#60a5fa",
        "--gdg-accent-light": "#24406b",
        "--gdg-accent-fg": "#0b1220",
        "--gdg-link-color": "#93c5fd",
    },
}


def get_active_theme() -> str:
    """Return ``"light"`` or ``"dark"`` - the single source of truth.

    Safe to call before the theme control has ever rendered (bare mode, tests,
    cached chart builders): it falls back to the light default.
    """
    value = st.session_state.get(THEME_WIDGET_KEY, "light")
    if isinstance(value, str) and value.lower() == "dark":
        return "dark"
    return "light"


def tokens() -> dict:
    """Colour tokens for the active theme."""
    return DARK if get_active_theme() == "dark" else LIGHT


def plotly_tokens() -> dict:
    """Plotly layout tokens for the active theme."""
    return PLOTLY[get_active_theme()]


def gdg_vars_css() -> str:
    """``--gdg-*`` declarations for the active theme's dataframes."""
    return "\n".join(f"  {k}: {v};" for k, v in GDG[get_active_theme()].items())


def theme_script(theme: str) -> str:
    """Inline script that tags ``<html data-theme=...>`` so CSS stays scoped.

    Streamlit 1.57 re-executes ``<script>`` nodes rendered through
    ``st.markdown(unsafe_allow_html=True)`` on every rerun, so the attribute
    always reflects the latest theme choice.
    """
    return (
        "<script type=\"text/javascript\">\n"
        "  // Scope every theme-specific CSS rule under the html root.\n"
        f'  document.documentElement.setAttribute("data-theme", "{theme}");\n'
        "</script>\n"
    )


def render_theme_control() -> None:
    """Compact segmented control pinned to the top of the sidebar.

    Uses ``st.session_state[THEME_WIDGET_KEY]`` directly - switching the widget
    is a plain Streamlit rerun, and every component (CSS, charts) reads the same
    key, so there is exactly one state source and no switching loop.
    """
    with st.sidebar:
        st.segmented_control(
            "Appearance",
            options=OPTION_LABELS,
            default=OPTION_LABELS[0],
            key=THEME_WIDGET_KEY,
            help="Switch between Light and Dark mode. Applies to the whole app "
            "and all charts; your choice is kept while the session is open.",
            width="stretch",
        )
        st.caption(f"Current theme: {get_active_theme().title()}")