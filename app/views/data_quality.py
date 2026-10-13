"""Data Quality and Business Insights - DQ checks, source vs cleaned, verified findings."""

from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import json

import pandas as pd
import streamlit as st

from src import config as _cfg
from src.dashboard import components as ui, data as ds
from src.dashboard import formatting as fmt

ui.page_header(
    "Data Quality and Business Insights",
    "Missing values, duplicates, referential integrity, source vs cleaned, "
    "verified findings and recommendations with CSV downloads.",
)

# --------------------------------------------------------------------------
# Database info
# --------------------------------------------------------------------------
info = ds.db_info()
ui.section("Database", "Read-only access; app never writes to the DB or CSVs.")
st.code(json.dumps(info, indent=2), language="json")

# --------------------------------------------------------------------------
# Missing values
# --------------------------------------------------------------------------
ns = ds.null_summary()
ui.section("Missing values (key columns)")
ui.table(
    ns,
    column_config={
        "table": st.column_config.TextColumn("Table"),
        "column": st.column_config.TextColumn("Column"),
        "rows": st.column_config.NumberColumn("Rows", format="%d"),
        "nulls": st.column_config.NumberColumn("Nulls", format="%d"),
        "null_pct": st.column_config.NumberColumn("Null %", format="%.2f%%"),
    },
    height=320,
)
ui.download_csv(ns, "null_summary.csv", "Download null summary (CSV)", key="dq_null_csv")

# --------------------------------------------------------------------------
# Duplicates
# --------------------------------------------------------------------------
dup = ds.duplicate_summary()
ui.section("Duplicates (known cases)")
ui.table(dup, height=220)
ui.download_csv(dup, "duplicate_summary.csv", "Download duplicates (CSV)", key="dq_dup_csv")

# --------------------------------------------------------------------------
# Integrity
# --------------------------------------------------------------------------
ic = ds.integrity_checks()
ui.section("Referential integrity")
c1, c2 = st.columns(2)
with c1:
    st.markdown("**PRAGMA integrity_check**")
    st.write("\n".join(ic["integrity"]))
with c2:
    if ic["fk_violations"]:
        st.markdown("**PRAGMA foreign_key_check** - violations:")
        st.dataframe(ic["fk_violations"], hide_index=True, width="stretch")
    else:
        st.markdown("**PRAGMA foreign_key_check** - OK (no violations)")

# --------------------------------------------------------------------------
# Source vs cleaned
# --------------------------------------------------------------------------
ui.section("Source vs cleaned record counts")
etls = ds.etl_summary()
tabs = ds.table_counts()
if etls is not None:
    rows = etls.get("row_counts", [])
    etldf = __import__("pandas").DataFrame(rows)
    ui.table(etldf, height=300)
    ui.download_csv(etldf, "etl_row_counts.csv", "Download ETL row counts (CSV)", key="dq_etl_csv")
ui.table(tabs, height=320)
ui.download_csv(tabs, "db_table_counts.csv", "Download DB table counts (CSV)", key="dq_db_csv")

# --------------------------------------------------------------------------
# Unresolved issues
# --------------------------------------------------------------------------
ui.section("Unresolved issues (from ETL run)")
if etls is not None and etls.get("unresolved_issues"):
    for line in etls["unresolved_issues"]:
        ui.note(line, "warn")
else:
    ui.note("No unresolved issues recorded in the ETL summary.", "ok")

# --------------------------------------------------------------------------
# Reusable data-quality audit (reports/)
# --------------------------------------------------------------------------
ui.section(
    "Reusable data-quality audit (reports/)",
    "Machine-readable, regenerated on every ETL run (also standalone via "
    "`python -m src.quality_audit`): per-file and per-column missing values, "
    "duplicate rows and business keys, invalid values, and referential-integrity "
    "orphans for the raw and the cleaned data.",
)
audit_csv = _cfg.AUDIT_CSV
if audit_csv.is_file():
    audit_df = pd.read_csv(audit_csv)
    flagged = audit_df[audit_df["status"] == "flagged"]
    st.caption(
        f"{len(audit_df):,} checks recorded "
        f"({len(flagged):,} flagged across raw + cleaned data)."
    )
    ui.table(audit_df, height=300)
    ui.download_csv(audit_df, "data_quality_audit.csv", "Download audit (CSV)", key="dq_audit_csv")
else:
    ui.note("reports/data_quality_audit.csv not found - run the ETL pipeline first.", "warn")
if _cfg.AUDIT_JSON.is_file():
    ui.download_text(
        _cfg.AUDIT_JSON.read_text(encoding="utf-8"),
        "data_quality_audit.json",
        "Download audit (JSON)",
        key="dq_audit_json",
        mime="application/json",
    )
if _cfg.AUDIT_SUMMARY_MD.is_file():
    ui.download_text(
        _cfg.AUDIT_SUMMARY_MD.read_text(encoding="utf-8"),
        "data_quality_summary.md",
        "Download audit summary (Markdown)",
        key="dq_audit_md",
        mime="text/markdown",
    )
if _cfg.METRICS_BEFORE_AFTER_MD.is_file():
    ui.download_text(
        _cfg.METRICS_BEFORE_AFTER_MD.read_text(encoding="utf-8"),
        "metrics_before_after.md",
        "Download before/after metric comparison (Markdown)",
        key="dq_before_after_md",
        mime="text/markdown",
    )

# --------------------------------------------------------------------------
# Verified findings
# --------------------------------------------------------------------------
ui.section("Verified business findings & recommendations")
arep = ds.analysis()
for i, f in enumerate(arep.findings):
    ui.render_finding(f, expanded=(i < 2))
    st.divider()

ui.download_text(
    arep.to_markdown() if hasattr(arep, "to_markdown") else "\n\n".join(f.to_markdown() for f in arep.findings),
    "shopscope_eda_findings.md",
    "Download findings (Markdown)",
    key="dq_fmd",
    mime="text/markdown",
)
try:
    findings_json = ds.analysis_summary_json()
    if findings_json is not None:
        ui.download_text(
            json.dumps(findings_json, indent=2),
            "shopscope_analysis_summary.json",
            "Download analysis summary (JSON)",
            key="dq_fjson",
            mime="application/json",
        )
except Exception:
    pass

# --------------------------------------------------------------------------
# Reproducibility
# --------------------------------------------------------------------------
ui.section("Reproducibility (project-relative)")
st.code(
    "From project root (PowerShell):\n"
    ".\\scripts\\run_etl.ps1\n"
    "python -m src.quality_audit     # standalone audit (also runs inside ETL)\n"
    ".\\scripts\\run_sql.ps1\n"
    "python scripts/compare_metrics.py  # metrics before/after comparison\n"
    ".\\scripts\\run_analysis.ps1\n"
    "streamlit run app/app.py",
    language="powershell",
)
ui.note("Windows PowerShell paths. The app uses read-only connections, so it never "
        "overwrites raw sources, cleaned CSVs or the database.")
