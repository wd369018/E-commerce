"""Formatting helpers shared across the ShopScope dashboard pages."""

from __future__ import annotations

import math

import pandas as pd


def _is_missing(x) -> bool:
    try:
        return x is None or (isinstance(x, float) and math.isnan(x)) or pd.isna(x)
    except (TypeError, ValueError):
        return False


def fmt_currency(x, decimals: int = 2, prefix: str = "R$ ") -> str:
    if _is_missing(x):
        return "-"
    return f"{prefix}{x:,.{decimals}f}"


def fmt_int(x) -> str:
    if _is_missing(x):
        return "-"
    return f"{int(round(x)):,}"


def fmt_number(x, decimals: int = 2) -> str:
    if _is_missing(x):
        return "-"
    return f"{x:,.{decimals}f}"


def fmt_percent(x, decimals: int = 2) -> str:
    if _is_missing(x):
        return "-"
    return f"{x:,.{decimals}f}%"


def fmt_compact_currency(x, prefix: str = "R$ ") -> str:
    if _is_missing(x):
        return "-"
    x = float(x)
    for unit, divisor in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(x) >= divisor:
            return f"{prefix}{x / divisor:,.1f}{unit}"
    return f"{prefix}{x:,.0f}"


def fmt_compact_int(x) -> str:
    if _is_missing(x):
        return "-"
    x = float(x)
    for unit, divisor in (("M", 1e6), ("K", 1e3)):
        if abs(x) >= divisor:
            return f"{x / divisor:,.1f}{unit}"
    return f"{int(round(x)):,}"


def fmt_days(x, decimals: int = 1) -> str:
    if _is_missing(x):
        return "-"
    return f"{x:,.{decimals}f} d"


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    """UTF-8 CSV bytes with a BOM so Excel on Windows opens accents correctly."""
    return df.to_csv(index=False).encode("utf-8-sig")


def fmt_bytes(n: int) -> str:
    if not n:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:,.0f} {unit}"
        n /= 1024
    return f"{n:,.1f} TB"
