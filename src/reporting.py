"""
ShopScope - data quality reporting.

Produces two Markdown reports per run plus a machine-readable JSON summary:

* ``reports/data_quality_before_cleaning.md``  - state of the raw files as loaded
* ``reports/data_quality_after_cleaning.md``   - state after cleaning, the
  input->output row-count ledger, and every validation check
* ``reports/etl_run_summary.json``             - the same facts, machine readable

All reports are overwritten on each run (never appended to), so re-running the
pipeline cannot accumulate duplicate report content.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import config

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Profiling helpers
# --------------------------------------------------------------------------
def profile_frame(name: str, frame: pd.DataFrame) -> dict:
    """Structural profile of one frame: shape, duplicates, nulls, key ranges."""
    n = len(frame)
    missing = frame.isna().sum()
    columns = []
    for col in frame.columns:
        series = frame[col]
        entry = {
            "column": col,
            "dtype": str(series.dtype),
            "non_null": int(series.notna().sum()),
            "missing": int(missing[col]),
            "missing_pct": round(float(missing[col]) / n * 100, 2) if n else 0.0,
            "unique": int(series.nunique(dropna=True)),
        }
        if pd.api.types.is_datetime64_any_dtype(series):
            valid = series.dropna()
            entry["min"] = str(valid.min()) if len(valid) else "-"
            entry["max"] = str(valid.max()) if len(valid) else "-"
        columns.append(entry)

    return {
        "file": name,
        "rows": n,
        "columns": frame.shape[1],
        "exact_duplicate_rows": int(frame.duplicated().sum()),
        "cells": int(n * frame.shape[1]),
        "missing_cells": int(missing.sum()),
        "columns_with_missing": int((missing > 0).sum()),
        "column_detail": columns,
    }


def profile_many(frames: dict[str, pd.DataFrame]) -> list[dict]:
    return [profile_frame(name, frame) for name, frame in frames.items()]


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
def _md_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return out


def _summary_table(profiles: list[dict]) -> list[str]:
    return _md_table(
        ["Source file", "Rows", "Cols", "Exact dup rows", "Cols w/ missing", "Missing cells"],
        [
            [
                p["file"],
                f"{p['rows']:,}",
                str(p["columns"]),
                f"{p['exact_duplicate_rows']:,}",
                str(p["columns_with_missing"]),
                f"{p['missing_cells']:,} ({p['missing_cells'] / p['cells'] * 100:.2f}%)"
                if p["cells"] else "0",
            ]
            for p in profiles
        ],
    )


def _missing_table(profiles: list[dict]) -> list[str]:
    rows = []
    for p in profiles:
        for col in p["column_detail"]:
            if col["missing"] > 0:
                rows.append([
                    p["file"], f"`{col['column']}`", col["dtype"],
                    f"{col['missing']:,}", f"{col['missing_pct']:.2f}%",
                    f"{col['unique']:,}",
                ])
    if not rows:
        return ["_No missing values found in any column._", ""]
    return _md_table(
        ["File", "Column", "Inferred dtype", "Missing", "Missing %", "Unique"], rows
    )


def _ranges_table(profiles: list[dict]) -> list[str]:
    rows = []
    for p in profiles:
        for col in p["column_detail"]:
            if "min" in col:
                rows.append([p["file"], f"`{col['column']}`", col["min"], col["max"]])
    return _md_table(["File", "Datetime column", "Min", "Max"], rows)


def write_before_report(
    profiles: list[dict],
    loaded_meta: list[dict],
    invalid_values: dict[str, dict[str, int]],
    path: Path = config.BEFORE_REPORT_MD,
) -> Path:
    """Raw-state report: what was read, from where, and what is wrong with it."""
    lines: list[str] = [
        "# ShopScope - Data Quality Report (BEFORE cleaning)",
        "",
        f"- Generated: `{datetime.now(timezone.utc).isoformat(timespec='seconds')}`",
        f"- Stage: **ingestion complete, no transformation applied**",
        f"- Source files are read-only; SHA-256 recorded below is re-verified at the end of the run.",
        "",
        "## 1. Files ingested",
        "",
    ]
    lines += _md_table(
        ["File", "Bytes", "Rows read", "Cols", "SHA-256 (first 16)", "Type-cast failures"],
        [
            [
                m["file"],
                f"{m['file_bytes']:,}",
                f"{m['source_rows']:,}",
                str(m["source_columns"]),
                f"`{m['sha256'][:16]}`",
                str(sum(m["parse_failures"].values())),
            ]
            for m in loaded_meta
        ],
    )
    lines += ["", "### Type-cast failures (values that could not become dates/numbers)", ""]
    any_fail = False
    for m in loaded_meta:
        if m["parse_failures"]:
            any_fail = True
            for col, count in m["parse_failures"].items():
                lines.append(f"- `{m['file']}` :: **{col}** -> {count:,} value(s)")
    if not any_fail:
        lines.append("_None - every declared date and numeric column parsed cleanly._")

    lines += ["", "## 2. Structural profile", ""]
    lines += _summary_table(profiles)
    lines += ["", "## 3. Missing values", ""]
    lines += _missing_table(profiles)
    lines += ["", "## 4. Datetime ranges", ""]
    lines += _ranges_table(profiles)

    lines += ["", "## 5. Invalid and inconsistent values (measured on the raw values)", ""]
    rows = []
    for group, checks in invalid_values.items():
        for check, count in checks.items():
            rows.append([group, check, f"{count:,}"])
    lines += _md_table(["Area", "Check", "Count"], rows) if rows else ["_None._", ""]

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Wrote %s", path.relative_to(config.PROJECT_ROOT))
    return path


def write_after_report(
    profiles: list[dict],
    ledger: list[dict],
    validation_md_rows: list[list[str]],
    unresolved: list[str],
    path: Path = config.AFTER_REPORT_MD,
) -> Path:
    """Cleaned-state report: profile + row-count ledger + validation results."""
    lines: list[str] = [
        "# ShopScope - Data Quality Report (AFTER cleaning)",
        "",
        f"- Generated: `{datetime.now(timezone.utc).isoformat(timespec='seconds')}`",
        "- Stage: **cleaning + validation complete**",
        "",
        "## 1. Record count ledger (input -> output)",
        "",
        "Every difference is explained. Nothing was dropped silently.",
        "",
    ]
    lines += _md_table(
        ["Table", "Input rows", "Output rows", "Excluded", "Reason / transformation"],
        [
            [
                f"`{r['table']}`",
                f"{r['input_rows']:,}",
                f"{r['output_rows']:,}",
                f"{r['excluded_rows']:,}" if r["excluded_rows"] else "-",
                r["exclusion_reason"] or r["note"],
            ]
            for r in ledger
        ],
    )

    lines += ["", "## 2. Cleaning actions and measured issues", ""]
    rows = []
    for r in ledger:
        for issue, count in r.get("issues", {}).items():
            rows.append([f"`{r['table']}`", "issue", issue, f"{count:,}"])
        for action, count in r.get("actions", {}).items():
            rows.append([f"`{r['table']}`", "action", action, f"{count:,}"])
    lines += _md_table(["Table", "Type", "Detail", "Count"], rows)

    lines += ["", "## 3. Structural profile after cleaning", ""]
    lines += _summary_table(profiles)
    lines += ["", "## 4. Missing values after cleaning (NULL = unknown, never 0)", ""]
    lines += _missing_table(profiles)

    lines += ["", "## 5. Validation results", ""]
    lines += _md_table(
        ["Check", "Description", "Severity", "Result", "Observed", "Expected"],
        validation_md_rows,
    )

    lines += ["", "## 6. Unresolved issues", ""]
    if unresolved:
        lines += [f"{i + 1}. {item}" for i, item in enumerate(unresolved)]
    else:
        lines.append("_None._")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Wrote %s", path.relative_to(config.PROJECT_ROOT))
    return path


def write_json_summary(payload: dict, path: Path = config.RUN_SUMMARY_JSON) -> Path:
    """Overwrite the machine-readable run summary (idempotent)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    logger.info("Wrote %s", path.relative_to(config.PROJECT_ROOT))
    return path
