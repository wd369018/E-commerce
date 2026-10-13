"""Reusable UI components for the ShopScope dashboard.

These keep the pages short and guarantee a consistent look: one KPI card
style, one table style, one filter style, one place to change the CSS.

Theming
-------
``components.CSS`` is built per rerun from ``src.dashboard.theme``: the shared
``_base_css`` stylesheet uses CSS custom properties (``--ss-*``) whose values
change with the active theme, and a scoped ``html[data-theme="dark"]`` block
re-colours Streamlit's native chrome (sidebar navigation, widgets, dataframes)
for dark mode. ``inject_css()`` also prints the tiny script that tags the
document root with ``data-theme`` so both themes can never bleed into each
other. Charts read the same tokens in ``src.dashboard.charts``.
"""

from __future__ import annotations

import html as _html

import pandas as pd
import streamlit as st

from src.dashboard import data as ds
from src.dashboard import formatting as fmt
from src.dashboard import theme as themes

PLOTLY_CONFIG = {
    "displayModeBar": False,
    "responsive": True,
}


def _base_css() -> str:
    """Layout + project component styles. All colours come from --ss-* vars."""
    return """
<style>
/* ---- Shared surface variables (light defaults; dark overrides below) ---- */
html {
  --ss-canvas:#f6f8fb; --ss-sidebar:#f8fafc; --ss-card:#ffffff;
  --ss-border:#e2e8f0; --ss-divider:#e6eaf0;
  --ss-text:#111827; --ss-muted:#5b6b81; --ss-faint:#8b98a9;
  --ss-accent:#2563eb; --ss-accent-text:#1d4ed8; --ss-accent-soft:#eff6ff;
  --ss-input-bg:#ffffff; --ss-input-border:#d4dbe6;
  --ss-note-bg:#f8fafc; --ss-warn-bg:#fffbeb; --ss-ok-bg:#ecfdf5; --ss-err-bg:#fef2f2;
  --ss-warn-border:#f59e0b; --ss-ok-border:#10b981; --ss-err-border:#ef4444;
  --ss-code-bg:#f8fafc; --ss-table-header:#f8fafc;
  --ss-nav-active-bg:#e3edfd; --ss-nav-hover-bg:#eef2f7; --ss-hover-bg:#eef2f7;
  --ss-shadow:0 1px 2px rgba(16,24,40,.05);
}

/* ---- App frame ---- */
[data-testid="stMainBlockContainer"], .block-container {
  padding-top: 1.35rem; padding-bottom: 3.5rem; max-width: 1640px; margin: 0 auto;
}
h1, h2, h3, h4, h5, h6 { color: var(--ss-text); letter-spacing: -0.015em; }
[data-testid="stMarkdownContainer"] hr { border-color: var(--ss-divider); }

/* ---- Header ---- */
.ss-header { margin: .1rem 0 .75rem 0; }
.ss-header .ss-eyebrow { color: var(--ss-accent); font-weight: 700; font-size: .78rem;
  letter-spacing: .1em; text-transform: uppercase; }
.ss-header h1 { font-size: 1.7rem; margin: .1rem 0 .2rem 0; font-weight: 700; }
.ss-header .ss-sub { color: var(--ss-muted); font-size: .93rem; max-width: 950px;
  line-height: 1.5; }

/* ---- Section titles ---- */
.ss-section { margin: 1.2rem 0 .35rem 0; }
.ss-section h3 { font-size: 1.1rem; margin: 0; font-weight: 650; }
.ss-section .ss-desc { color: var(--ss-muted); font-size: .84rem; margin-top: .1rem; }

/* ---- KPI grid: responsive CSS grid, equal cells on every viewport ---- */
.ss-kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(228px, 1fr));
  gap: .8rem; margin: .65rem 0 1rem 0; }
.ss-kpi { background: var(--ss-card); border: 1px solid var(--ss-border); border-radius: 14px;
  padding: 14px 16px 12px; box-shadow: var(--ss-shadow); box-sizing: border-box;
  display: flex; flex-direction: column; height: 100%; min-height: 116px; }
.ss-kpi .ss-kpi-label { color: var(--ss-muted); font-size: .76rem; font-weight: 650;
  text-transform: uppercase; letter-spacing: .045em; line-height: 1.45; }
.ss-kpi .ss-kpi-value { font-size: clamp(1.12rem, 1.5vw + .45rem, 1.52rem); font-weight: 700;
  color: var(--ss-text); margin: .22rem 0 .3rem 0; line-height: 1.2;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  font-variant-numeric: tabular-nums; }
.ss-kpi .ss-kpi-sub { color: var(--ss-muted); font-size: .78rem; line-height: 1.4; }
.ss-kpi .ss-kpi-help { color: var(--ss-muted); font-size: .72rem; line-height: 1.4;
  margin-top: auto; padding-top: .35rem; border-top: 1px dashed var(--ss-border); }

/* ---- Notes / callouts ---- */
.ss-note { background: var(--ss-note-bg); border: 1px solid var(--ss-border);
  border-left: 4px solid var(--ss-accent); border-radius: 8px; padding: .6rem .85rem;
  color: var(--ss-text); font-size: .85rem; margin: .3rem 0 .6rem 0; line-height: 1.5; }
.ss-warn { border-left-color: var(--ss-warn-border); background: var(--ss-warn-bg); }
.ss-ok { border-left-color: var(--ss-ok-border); background: var(--ss-ok-bg); }

/* ---- Tables ---- */
[data-testid="stDataFrame"] { border: 1px solid var(--ss-border); border-radius: 10px; }
[data-testid="stDataFrame"] {
  --gdg-bg-cell:#ffffff; --gdg-bg-header:#f8fafc; --gdg-bg-header-has-focus:#eef2f7;
  --gdg-bg-bubble:#f1f5f9; --gdg-text-dark:#111827; --gdg-text-medium:#475569;
  --gdg-text-light:#64748b; --gdg-text-group-header:#475569; --gdg-border-color:#e2e8f0;
  --gdg-accent-color:#2563eb; --gdg-accent-light:#dbe7fb; --gdg-accent-fg:#ffffff;
  --gdg-link-color:#3b82f6;
}

/* ---- Sidebar ---- */
[data-testid="stSidebar"] { background: var(--ss-sidebar); }

/* ---- Filter captions ---- */
.stCaption, [data-testid="stCaptionContainer"] { color: var(--ss-muted); font-size: .8rem; }
</style>
"""


def _dark_css() -> str:
    """Native-chrome overrides for dark mode, scoped under html[data-theme=dark].

    Every selector is prefixed with the attribute so light mode is never
    touched. Widget surfaces are re-coloured explicitly because Streamlit 1.57
    paints them from a JS theme object (CSS-in-JS), not from CSS variables.
    """
    return """
<style>
html[data-theme="dark"] {
  --ss-canvas:#0b1220; --ss-sidebar:#0f1830; --ss-card:#131f36;
  --ss-border:#26344e; --ss-divider:#22304a;
  --ss-text:#e7ecf5; --ss-muted:#9aa8c0; --ss-faint:#7d8ba6;
  --ss-accent:#60a5fa; --ss-accent-text:#93c5fd; --ss-accent-soft:#1d2a44;
  --ss-input-bg:#0e1930; --ss-input-border:#33415f;
  --ss-note-bg:#111c33; --ss-warn-bg:#31250f; --ss-ok-bg:#0f2a20; --ss-err-bg:#331724;
  --ss-warn-border:#f59e0b; --ss-ok-border:#34d399; --ss-err-border:#f87171;
  --ss-code-bg:#0e1930; --ss-table-header:#13203a;
  --ss-nav-active-bg:#1d2f4f; --ss-nav-hover-bg:#16223c; --ss-hover-bg:#16223c;
  --ss-shadow:0 1px 2px rgba(0,0,0,.35);
  color-scheme: dark;
}

/* ---- App chrome ---- */
html[data-theme="dark"] [data-testid="stAppViewContainer"] { background-color: var(--ss-canvas) !important; }
html[data-theme="dark"] [data-testid="stSidebar"] { background-color: var(--ss-sidebar) !important; }
html[data-theme="dark"] [data-testid="stSidebarContent"],
html[data-theme="dark"] [data-testid="stSidebarUserContent"] { background: transparent; }
html[data-theme="dark"] [data-testid="stHeader"] { background: transparent !important; }

/* ---- Global text ---- */
html[data-theme="dark"] [data-testid="stMarkdownContainer"],
html[data-theme="dark"] [data-testid="stMarkdownContainer"] p,
html[data-theme="dark"] [data-testid="stMarkdownContainer"] li,
html[data-theme="dark"] [data-testid="stMarkdownContainer"] a,
html[data-theme="dark"] h1, html[data-theme="dark"] h2, html[data-theme="dark"] h3,
html[data-theme="dark"] h4, html[data-theme="dark"] h5, html[data-theme="dark"] h6 {
  color: var(--ss-text) !important;
}
html[data-theme="dark"] [data-testid="stCaptionContainer"],
html[data-theme="dark"] .stCaption { color: var(--ss-muted) !important; }

/* ---- Sidebar navigation (labels, icons, hover, selected, disabled) ---- */
html[data-theme="dark"] [data-testid="stSidebarNavLink"] {
  color: var(--ss-text) !important;
  background: transparent;
}
html[data-theme="dark"] [data-testid="stSidebarNavLink"]:hover {
  background: var(--ss-nav-hover-bg) !important;
}
html[data-theme="dark"] [data-testid="stSidebarNavLink"][aria-current="page"] {
  color: var(--ss-accent-text) !important;
  background: var(--ss-nav-active-bg) !important;
}
html[data-theme="dark"] [data-testid="stSidebarNavLink"] .material-symbols-rounded,
html[data-theme="dark"] [data-testid="stSidebarNavLink"] span { color: inherit !important; }
html[data-theme="dark"] [data-testid="stSidebarNavLink"][aria-disabled="true"] {
  color: var(--ss-faint) !important;
}
html[data-theme="dark"] [data-testid="stNavSectionHeader"] { color: var(--ss-muted) !important; }
html[data-theme="dark"] [data-testid="stSidebarCollapseButton"],
html[data-theme="dark"] [data-testid="stExpandSidebarButton"] { color: var(--ss-muted) !important; }

/* ---- Toolbar / main menu ---- */
html[data-theme="dark"] [data-testid="stToolbar"] [data-testid="stToolbarActionButton"],
html[data-theme="dark"] [data-testid="stMainMenuButton"] { color: var(--ss-muted) !important; }
html[data-theme="dark"] [data-testid="stToolbar"] [data-testid="stToolbarActionButton"]:hover,
html[data-theme="dark"] [data-testid="stMainMenuButton"]:hover { background: var(--ss-hover-bg) !important; }

/* ---- Expanders ---- */
html[data-theme="dark"] [data-testid="stExpander"] {
  background: var(--ss-card) !important; border: 1px solid var(--ss-border) !important;
}
html[data-theme="dark"] [data-testid="stExpander"] summary,
html[data-theme="dark"] [data-testid="stExpander"] details,
html[data-theme="dark"] [data-testid="stExpander"] [data-testid="stMarkdownContainer"] {
  color: var(--ss-text) !important;
}
html[data-theme="dark"] [data-testid="stExpander"] svg { color: var(--ss-muted) !important; }

/* ---- Text / number / date / text-area inputs ---- */
html[data-theme="dark"] [data-baseweb="input"],
html[data-theme="dark"] [data-testid="stTextInput"] input,
html[data-theme="dark"] [data-testid="stNumberInput"] input,
html[data-theme="dark"] [data-testid="stDateInput"] input,
html[data-theme="dark"] [data-baseweb="textarea"],
html[data-theme="dark"] [data-testid="stTextArea"] textarea {
  color: var(--ss-text) !important;
  background-color: var(--ss-input-bg) !important;
  border-color: var(--ss-input-border) !important;
}
html[data-theme="dark"] [data-baseweb="input"] input::placeholder,
html[data-theme="dark"] [data-testid="stDateInput"] input::placeholder { color: var(--ss-faint) !important; }
html[data-theme="dark"] [data-testid="stNumberInput"] button { color: var(--ss-muted) !important; }

/* ---- Selectbox / Multiselect ---- */
html[data-theme="dark"] [data-testid="stSelectbox"] [data-baseweb="select"] > div,
html[data-theme="dark"] [data-testid="stMultiSelect"] [data-baseweb="select"] > div {
  color: var(--ss-text) !important;
  background-color: var(--ss-input-bg) !important;
  border-color: var(--ss-input-border) !important;
}
html[data-theme="dark"] [data-testid="stSelectbox"] svg,
html[data-theme="dark"] [data-testid="stMultiSelect"] svg { color: var(--ss-muted) !important; }
html[data-theme="dark"] [data-testid="stMultiSelect"] [data-baseweb="tag"] {
  background-color: var(--ss-hover-bg) !important;
  color: var(--ss-text) !important;
  border-color: var(--ss-input-border) !important;
}
html[data-theme="dark"] [data-testid="stMultiSelect"] [data-baseweb="tag"] span { color: var(--ss-text) !important; }
html[data-theme="dark"] [data-testid="stMultiSelect"] [data-baseweb="tag"] svg { color: var(--ss-muted) !important; }

/* ---- Dropdown menu popover ---- */
html[data-theme="dark"] [data-baseweb="popover"] [data-baseweb="menu"],
html[data-theme="dark"] [data-baseweb="popover"] [data-baseweb="listbox"],
html[data-theme="dark"] [data-baseweb="popover"] [role="listbox"] {
  background-color: var(--ss-card) !important;
  color: var(--ss-text) !important;
}
html[data-theme="dark"] [data-baseweb="popover"] [role="option"] { color: var(--ss-text) !important; }
html[data-theme="dark"] [data-baseweb="popover"] [role="option"]:hover,
html[data-theme="dark"] [data-baseweb="popover"] [role="option"][aria-selected="true"]:not([aria-checked="true"]) {
  background-color: var(--ss-hover-bg) !important;
}
html[data-theme="dark"] [role="option"][aria-selected="true"] {
  background-color: var(--ss-accent-soft) !important;
  color: var(--ss-accent-text) !important;
}

/* ---- Radio / checkbox / segmented control labels ---- */
html[data-theme="dark"] [data-testid="stRadio"] label,
html[data-theme="dark"] [data-testid="stCheckbox"] label,
html[data-theme="dark"] [data-testid="stButtonGroup"] [role="radio"],
html[data-theme="dark"] [data-testid="stButtonGroup"] { color: var(--ss-text) !important; }
html[data-theme="dark"] [data-testid="stButtonGroup"] [role="radio"]:hover { background: var(--ss-hover-bg) !important; }
html[data-theme="dark"] [data-testid="stButtonGroup"] [role="radio"][aria-checked="true"] {
  background: var(--ss-accent-soft) !important; color: var(--ss-accent-text) !important;
}

/* ---- Slider (rails/handles follow the accent on the dark track) ---- */
html[data-theme="dark"] [data-testid="stSlider"] [data-baseweb="slider"] > div {
  background-color: var(--ss-input-border) !important;
}
html[data-theme="dark"] [data-testid="stSlider"] [data-testid="stSliderThumbValue"], 
html[data-theme="dark"] [data-testid="stSlider"] span { color: var(--ss-text) !important; }

/* ---- Buttons ---- */
html[data-theme="dark"] [data-testid^="stBaseButton-secondary"],
html[data-theme="dark"] [data-testid^="stBaseButton-tertiary"],
html[data-theme="dark"] [data-testid^="stBaseButton-minimal"] {
  background-color: var(--ss-card) !important;
  color: var(--ss-text) !important;
  border-color: var(--ss-input-border) !important;
}
html[data-theme="dark"] [data-testid^="stBaseButton-primary"] {
  background-color: var(--ss-accent) !important;
  border-color: var(--ss-accent) !important;
  color: #ffffff !important;
}

/* ---- Code blocks ---- */
html[data-theme="dark"] [data-testid="stCode"] {
  background-color: var(--ss-code-bg) !important; border: 1px solid var(--ss-border) !important;
}
html[data-theme="dark"] [data-testid="stCode"] pre,
html[data-theme="dark"] [data-testid="stCode"] code { color: var(--ss-text) !important; }

/* ---- Dataframes (Glide Data Grid tokens) ---- */
html[data-theme="dark"] [data-testid="stDataFrame"] {
  --gdg-bg-cell:#0e1930; --gdg-bg-header:#14213a; --gdg-bg-header-has-focus:#182642;
  --gdg-bg-bubble:#1d2b46; --gdg-text-dark:#e7ecf5; --gdg-text-medium:#b6c2d4;
  --gdg-text-light:#8a99b3; --gdg-text-group-header:#b6c2d4; --gdg-border-color:#2a3854;
  --gdg-accent-color:#60a5fa; --gdg-accent-light:#24406b; --gdg-accent-fg:#0b1220;
  --gdg-link-color:#93c5fd;
}

/* ---- Misc state widgets ---- */
html[data-theme="dark"] [data-testid="stAlert"] { color: var(--ss-text) !important; }
html[data-theme="dark"] [data-testid="stAlert"] p { color: var(--ss-text) !important; }
html[data-theme="dark"] [data-testid="stSpinner"] { color: var(--ss-muted) !important; }
html[data-theme="dark"] [data-testid="stBottom"] { background: transparent; }
</style>
"""


def build_css() -> str:
    """Full stylesheet for the active theme."""
    css = _base_css()
    if themes.get_active_theme() == "dark":
        css += _dark_css()
    return css


def inject_css() -> None:
    st.markdown(build_css() + themes.theme_script(themes.get_active_theme()),
                unsafe_allow_html=True)


def page_header(title: str, subtitle: str, eyebrow: str = "ShopScope") -> None:
    st.markdown(
        f"""
        <div class="ss-header">
            <div class="ss-eyebrow">{_html.escape(eyebrow)}</div>
            <h1>{_html.escape(title)}</h1>
            <div class="ss-sub">{_html.escape(subtitle)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def section(title: str, description: str | None = None) -> None:
    desc = f'<div class="ss-desc">{_html.escape(description)}</div>' if description else ""
    st.markdown(
        f'<div class="ss-section"><h3>{_html.escape(title)}</h3>{desc}</div>',
        unsafe_allow_html=True,
    )


def note(text: str, kind: str = "info") -> None:
    cls = {"info": "", "warn": " ss-warn", "ok": " ss-ok"}.get(kind, "")
    st.markdown(f'<div class="ss-note{cls}">{text}</div>', unsafe_allow_html=True)


def _kpi_card_html(label: str, value: str, help_text: str | None = None,
                   caption: str | None = None) -> str:
    """One KPI card as HTML (escaped text) - shared by kpi_card and kpi_grid."""
    safe = lambda v: _html.escape(str(v)) if v else ""
    help_html = f'<div class="ss-kpi-help">{safe(help_text)}</div>' if help_text else ""
    cap_html = f'<div class="ss-kpi-sub">{safe(caption)}</div>' if caption else ""
    return (
        f'<div class="ss-kpi">'
        f'  <div class="ss-kpi-label">{safe(label)}</div>'
        f'  <div class="ss-kpi-value">{safe(value)}</div>'
        f"  {cap_html}{help_html}"
        f"</div>"
    )


def kpi_card(label: str, value: str, help_text: str | None = None,
             caption: str | None = None) -> None:
    st.markdown(
        _kpi_card_html(label, value, help_text, caption),
        unsafe_allow_html=True,
    )


def kpi_grid(cards: list[dict], cols: int = 4) -> None:
    """Responsive KPI grid: equal-width, equal-height cards on every screen.

    One HTML block backed by a CSS grid (``auto-fit minmax(228px, 1fr)``) - it
    lays out 4 cards per row on desktop, 3-2 on tablets and 1 on phones instead
    of relying on fixed ``st.columns``.
    """
    if not cards:
        return
    inner = "\n".join(_kpi_card_html(**card) for card in cards)
    st.markdown(f'<div class="ss-kpi-grid">{inner}</div>', unsafe_allow_html=True)


def cards_from_kpis(keys: list[str], *, help_from_limitations: bool = True) -> list[dict]:
    kmap = ds.kpi_map()
    cards = []
    for key in keys:
        k = kmap.get(key)
        if k is None:
            continue
        help_text = None
        if help_from_limitations:
            help_text = f"{k.formula}"
        cards.append(
            {
                "label": k.name,
                "value": k.formatted(),
                "caption": k.eligible_population[:90] + ("..." if len(k.eligible_population) > 90 else ""),
                "help_text": help_text,
            }
        )
    return cards


def chart(fig, key: str | None = None) -> None:
    st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG, key=key)


def empty(condition: bool, message: str = "No data matches the current filters.") -> bool:
    if condition:
        note(message, "warn")
        return True
    return False


def table(df: pd.DataFrame, *, column_config: dict | None = None, height: int | None = None,
          hide_index: bool = True, use_container_width: bool = True) -> None:
    if df.empty:
        note("No rows to display.", "warn")
        return
    st.dataframe(
        df,
        column_config=column_config,
        height=height,
        hide_index=hide_index,
        width="stretch" if use_container_width else "content",
    )


def download_csv(df: pd.DataFrame, filename: str, label: str, *, key: str) -> None:
    st.download_button(
        label=label,
        data=fmt.to_csv_bytes(df),
        file_name=filename,
        mime="text/csv",
        key=key,
        width="content",
    )


def download_text(text: str, filename: str, label: str, *, key: str,
                  mime: str = "text/plain") -> None:
    st.download_button(label=label, data=text.encode("utf-8"), file_name=filename,
                       mime=mime, key=key)


# --------------------------------------------------------------------------
# Filters
# --------------------------------------------------------------------------
def date_range_filter(key: str = "date_range", *, label: str = "Purchase date range",
                      default_window: bool = False):
    bounds = ds.date_bounds()
    lo, hi = bounds["min_date"], bounds["max_date"]
    if default_window:
        import datetime as _dt

        lo = _dt.date.fromisoformat(bounds["window_start"] + "-01")
    value = st.sidebar.date_input(
        label,
        value=(lo, hi),
        min_value=bounds["min_date"],
        max_value=bounds["max_date"],
        key=key,
    )
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return value[0], value[1]
    return lo, hi


def multiselect_filter(key: str, label: str, options: list[str],
                       default: list[str] | None = None,
                       help_text: str | None = None) -> tuple[str, ...]:
    chosen = st.sidebar.multiselect(label, options=options,
                                    default=default or [], key=key, help=help_text)
    return tuple(chosen)


def filter_caption(start, end, **selections) -> None:
    parts = [f"**{start} → {end}**"]
    for name, values in selections.items():
        if values:
            shown = ", ".join(map(str, values)) if len(values) <= 4 else f"{len(values)} selected"
            parts.append(f"{name}: {shown}")
    st.caption("Filters - " + " | ".join(parts))


# --------------------------------------------------------------------------
# Findings / KPI detail renderers
# --------------------------------------------------------------------------
def render_finding(finding, *, expanded: bool = False) -> None:
    st.markdown(f"**{finding.title}**")
    if finding.headline:
        st.markdown(f"> {finding.headline}")
    with st.expander("Details", expanded=expanded):
        st.markdown(f"**Business question.** {finding.business_question}")
        st.markdown(f"**Calculation method.** {finding.method}")
        st.markdown(f"**Actual result.** {finding.result}")
        st.markdown(f"**Period and population.** {finding.period_population}")
        st.markdown(f"**Interpretation.** {finding.interpretation}")
        st.markdown(f"**Recommendation.** {finding.recommendation}")
        st.markdown(f"**Limitations.** {finding.limitations}")
        if finding.correlation_note:
            st.markdown(f"**Correlation vs causation.** {finding.correlation_note}")


def render_kpi_detail_table(kpis_list) -> pd.DataFrame:
    rows = []
    for k in kpis_list:
        rows.append(
            {
                "KPI": k.name,
                "Category": k.category,
                "Value": k.formatted(),
                "Formula": k.formula,
                "Numerator": f"{k.numerator_label} = {fmt.fmt_number(k.numerator)}"
                if k.numerator is not None else k.numerator_label,
                "Denominator": f"{k.denominator_label} = {fmt.fmt_number(k.denominator)}"
                if k.denominator is not None else k.denominator_label,
                "Filters": k.filters,
                "Eligible population": k.eligible_population,
                "Limitations": k.limitations,
            }
        )
    return pd.DataFrame(rows)


def sidebar_note(text: str) -> None:
    st.sidebar.caption(text)