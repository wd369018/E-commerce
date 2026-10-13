# ShopScope - E-Commerce Intelligence Platform

Streamlit analytical platform over the Brazilian **Olist** e-commerce dataset.
Raw CSVs are cleaned by a reproducible ETL pipeline into conformed tables, loaded
into a validated SQLite database, and served to seven Streamlit pages. **No page,
chart or KPI reads a raw CSV directly** - everything comes from the validated DB.

## Quick start (PowerShell, project root)

```powershell
# 1. One-time environment (CPython 3.14, versions in requirements.txt)
py -3.14 -m pip install -r requirements.txt

# 2. Clean + validate + write data/processed/ and the quality reports
.\scripts\run_etl.ps1                        # or: py -3.14 -m src.etl_pipeline

# 3. Build the SQLite database from the cleaned CSVs
.\scripts\run_sql.ps1                        # or: py -3.14 -m src.database

# 4. (Optional) headline KPIs before vs after cleaning
py -3.14 scripts\compare_metrics.py

# 5. Runs the app
py -3.14 -m streamlit run app/app.py         # -> http://localhost:8501
```

Recommended first visit: **Data Quality and Business Insights** page (the audit
artefacts live in `reports/`).

## Project structure

```
app/                Streamlit app (7 pages, entry point app/app.py)
src/                data_loader -> data_cleaning -> transform -> validation
                    -> database -> queries -> metrics -> insights -> dashboard
sql/schema.sql      SQLite schema (PKs, FKs, check constraints, indexes, views)
sql/analytical_queries.sql   CREATE VIEWs + named query library (src/queries)
data/raw/ or root   raw Olist CSVs (read-only, never modified)
data/processed/     cleaned conformed tables (ETL output)
data/quarantine/    flagged bad dates / out-of-Brazil coordinates
data/shopscope.db   validated SQLite build (the only data the app reads)
reports/            quality reports, audits, metrics, figures
scripts/            PowerShell runners + compare_metrics.py
tests/              pytest suite incl. Streamlit AppTest page checks
```

## Documentation

| Doc | Content |
|-----|---------|
| `docs/project_plan.md` | planning, the C1-C18 cleaning rule catalogue |
| `docs/data_cleaning.md` | **how cleaning runs, rules, findings, before/after, limitations** |
| `docs/sql_layer.md` | schema, views, query library |
| `docs/analysis_layer.md` | metrics, findings, visualizations |
| `reports/data_quality_summary.md` | machine-readable audit summary (per CSV) |
| `reports/metrics_before_after.md` | headline KPIs raw vs cleaned |
| `reports/business_metrics.md` | the 14 KPIs with formula, filters, limitations |

## Re-usable audit (Phase-2 deliverable)

Regenerated on every ETL run **and** standalone:

```powershell
py -3.14 -m src.quality_audit
```

Writes `reports/data_quality_audit.csv` (one row per file/check), `.json`
(structured) and `data_quality_summary.md` - missing values by column and %,
duplicate rows, duplicate business keys, invalid values, primary-key
uniqueness and foreign-key orphans for the raw and cleaned data.

## Verify / test

```powershell
py -3.14 -m pytest -q
py -3.14 -m src.queries          # runs every analytical query against the DB
py -3.14 -m src.metrics          # prints the KPI catalogue
```

> Note: the raw order-items file may show an updated timestamp through OneDrive
> sync; the ETL re-hashes every source file on each run, so content changes are
> detected, cosmetic timestamp changes are not an issue.