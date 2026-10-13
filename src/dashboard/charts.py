"""Reusable Plotly chart builders for the ShopScope dashboard.

Each function returns a fully styled ``plotly.graph_objects.Figure``; pages
only supply data. Keeping this separate from the pages means identical
formatting, tooltips and colour semantics everywhere, and no duplicated chart
code.

Charts read the active app theme from ``src.dashboard.theme`` while the figure
is built, so paper/plot backgrounds, fonts, grids and hover styles always match
the Light/Dark toggle instead of being hardcoded to ``plotly_white``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from src.dashboard import theme as themes

ACCENT = "#2563eb"
ACCENT2 = "#f97316"
ACCENT3 = "#7c3aed"
GREEN = "#059669"
RED = "#dc2626"
AMBER = "#d97706"
GREY = "#cbd5e1"

FONT = "Inter, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"

PLOTLY_CONFIG = {
    "displayModeBar": False,
    "responsive": True,
    "scrollZoom": False,
}


def _apply(fig: go.Figure, height: int = 380, title: str | None = None,
           show_legend: bool = True) -> go.Figure:
    pt = themes.plotly_tokens()
    dark = themes.get_active_theme() == "dark"
    fig.update_layout(
        template="plotly_dark" if dark else "plotly_white",
        height=height,
        margin=dict(l=10, r=10, t=48 if title else 18, b=10),
        title=dict(text=title, font=dict(size=15, color=pt["title"])) if title else None,
        font=dict(family=FONT, size=13, color=pt["text"]),
        legend=dict(orientation="h", yanchor="bottom", y=1.0,
                    xanchor="right", x=1, title=None,
                    font=dict(color=pt["text"])) if show_legend else dict(),
        showlegend=show_legend,
        hoverlabel=dict(bgcolor=pt["hover_bg"], bordercolor=pt["hover_border"],
                        font=dict(family=FONT, size=12, color=pt["text"])),
        plot_bgcolor=pt["plot"],
        paper_bgcolor=pt["paper"],
        colorway=[ACCENT, ACCENT2, ACCENT3, GREEN, AMBER, RED, "#0891b2", "#db2777"],
    )
    fig.update_xaxes(showgrid=False, linecolor=pt["axis"], ticks="outside",
                     tickcolor=pt["axis"], tickfont=dict(color=pt["tick"]))
    fig.update_yaxes(gridcolor=pt["grid"], zeroline=False, linecolor=pt["axis"],
                     tickfont=dict(color=pt["tick"]))
    return fig


# --------------------------------------------------------------------------
# Core builders
# --------------------------------------------------------------------------
def bar(df: pd.DataFrame, x: str, y: str, *, horizontal: bool = False,
        color: str | None = None, title: str | None = None, height: int = 380,
        color_map: dict | None = None, x_title: str | None = None,
        y_title: str | None = None, text: str | None = None) -> go.Figure:
    fig = px.bar(df, x=y if horizontal else x, y=x if horizontal else y,
                 orientation="h" if horizontal else "v", color=color,
                 text=text, color_discrete_map=color_map)
    if horizontal:
        fig.update_layout(yaxis=dict(autorange="reversed"))
        fig.update_traces(marker_line_width=0)
    if text:
        fig.update_traces(textposition="outside", cliponaxis=False)
    _apply(fig, height, title)
    if x_title or y_title:
        fig.update_xaxes(title=x_title)
        fig.update_yaxes(title=y_title)
    return fig


def line(df: pd.DataFrame, x: str, y: str | list[str], *, title: str | None = None,
         height: int = 380, y_title: str | None = None, markers: bool = True,
         colors: list[str] | None = None) -> go.Figure:
    fig = px.line(df, x=x, y=y, markers=markers,
                  color_discrete_sequence=colors or [ACCENT, ACCENT2, ACCENT3])
    _apply(fig, height, title)
    if y_title:
        fig.update_yaxes(title=y_title)
    return fig


def bar_line(df: pd.DataFrame, x: str, bar_y: str, line_y: str, *,
             bar_name: str, line_name: str, title: str | None = None,
             height: int = 400, bar_colors=None, line_color: str = ACCENT2,
             bar_title: str | None = None, line_title: str | None = None) -> go.Figure:
    fig = go.Figure()
    if bar_colors is None:
        bar_colors = ACCENT
    fig.add_bar(x=df[x], y=df[bar_y], name=bar_name, marker_color=bar_colors,
                marker_line_width=0)
    fig.add_scatter(x=df[x], y=df[line_y], name=line_name, yaxis="y2",
                    mode="lines+markers", line=dict(color=line_color, width=2.4),
                    marker=dict(size=6))
    _apply(fig, height, title)
    fig.update_layout(
        yaxis=dict(title=bar_title or bar_name),
        yaxis2=dict(title=line_title or line_name, overlaying="y", side="right",
                    showgrid=False),
        bargap=0.28,
    )
    return fig


def pie(df: pd.DataFrame, names: str, values: str, *, title: str | None = None,
        height: int = 360, hole: float = 0.55, color_map: dict | None = None) -> go.Figure:
    pt = themes.plotly_tokens()
    fig = px.pie(df, names=names, values=values, hole=hole,
                 color=names, color_discrete_map=color_map)
    fig.update_traces(textposition="inside", textinfo="percent+label",
                      marker=dict(line=dict(color=pt["paper"], width=1.5)))
    _apply(fig, height, title)
    return fig


def histogram(values, *, title: str | None = None, height: int = 360,
              bins: int = 40, x_title: str | None = None, log_x: bool = False,
              color: str = ACCENT) -> go.Figure:
    series = pd.Series(values).dropna()
    fig = px.histogram(x=series, nbins=bins, color_discrete_sequence=[color])
    fig.update_traces(marker_line_width=0)
    _apply(fig, height, title, show_legend=False)
    if x_title:
        fig.update_xaxes(title=x_title)
    if log_x:
        fig.update_xaxes(type="log")
    return fig


def scatter(df: pd.DataFrame, x: str, y: str, *, title: str | None = None,
            height: int = 420, hover: list[str] | None = None,
            color: str | None = None, size: str | None = None,
            x_title: str | None = None, y_title: str | None = None,
            opacity: float = 0.65) -> go.Figure:
    fig = px.scatter(df, x=x, y=y, hover_data=hover, color=color,
                     size=size, opacity=opacity)
    _apply(fig, height, title)
    if x_title:
        fig.update_xaxes(title=x_title)
    if y_title:
        fig.update_yaxes(title=y_title)
    return fig


def box(df: pd.DataFrame, x: str | None, y: str, *, title: str | None = None,
        height: int = 380, color: str | None = None, points: str = "outliers",
        y_title: str | None = None) -> go.Figure:
    fig = px.box(df, x=x, y=y, color=color, points=points)
    _apply(fig, height, title)
    if y_title:
        fig.update_yaxes(title=y_title)
    return fig


def heatmap(matrix: pd.DataFrame, *, title: str | None = None, height: int = 460,
            x_title: str | None = None, y_title: str | None = None,
            color_scale: str = "Blues", text_format: str = ".0f") -> go.Figure:
    fig = px.imshow(matrix, text_auto=text_format, aspect="auto",
                    color_continuous_scale=color_scale)
    _apply(fig, height, title, show_legend=False)
    fig.update_layout(coloraxis_colorbar=dict(title=None, thickness=12))
    if x_title:
        fig.update_xaxes(title=x_title)
    if y_title:
        fig.update_yaxes(title=y_title)
    return fig


def waterfall_or_bar(df: pd.DataFrame, x: str, y: str, *, title=None,
                     height: int = 360, horizontal: bool = False) -> go.Figure:
    return bar(df, x, y, title=title, height=height, horizontal=horizontal,
               y_title=None, x_title=None)


# --------------------------------------------------------------------------
# Specialised compositions
# --------------------------------------------------------------------------
def monthly_trend(df: pd.DataFrame, *, title: str | None = "Monthly sales and order volume") -> go.Figure:
    """Sales bars (incomplete months greyed) with an order-volume line."""
    if df.empty:
        return _apply(go.Figure(), 400, title)
    colors = [ACCENT if c else GREY for c in df["is_complete"]]
    fig = bar_line(
        df, "sales_month", "merchandise_sales", "orders",
        bar_name="Merchandise sales (R$)", line_name="Orders",
        bar_colors=colors, title=title, height=420,
        bar_title="Merchandise sales (R$)", line_title="Orders",
    )
    fig.update_xaxes(tickangle=-45)
    return fig


def dual_axis(df: pd.DataFrame, x: str, bar_y: str, line_y: str, *,
              bar_name: str, line_name: str, title: str | None = None,
              height: int = 400, line_color: str = ACCENT2,
              bar_title: str | None = None, line_title: str | None = None) -> go.Figure:
    return bar_line(df, x, bar_y, line_y, bar_name=bar_name, line_name=line_name,
                    title=title, height=height, line_color=line_color,
                    bar_title=bar_title, line_title=line_title)


def ranking_bar(df: pd.DataFrame, label: str, value: str, *, top_n: int = 10,
                title: str | None = None, height: int = 420) -> go.Figure:
    data = df.head(top_n).iloc[::-1]
    return bar(data, label, value, horizontal=True, title=title, height=height)
