# ShopScope - Data Cleaning & Validation

> This document describes how the cleaning pipeline runs, what it changes and
> why, what the data actually looks like before and after, and what remains
> unresolved. It complements `docs/project_plan.md` (rule catalogue C1-C18),
> `docs/sql_layer.md` and `docs/analysis_layer.md`.

## 1. Where the data lives

| Kind | Location | Written by |
|------|----------|------------|
| Raw Olist CSVs (9 files) | project root (also accepted in `data/raw/`) | **never written** - opened read-only, SHA-256 hashed before and after every run |
| Cleaned, conformed tables (11 CSVs) | `data/processed/` | `src.etl_pipeline` |
| Quarantine copies (bad dates, out-of-Brazil coordinates) | `data/quarantine/` | `src.etl_pipeline` |
| SQLite database (what the app reads) | `data/shopscope.db` | `python -m src.database` |
| Reports | `reports/` | pipeline + analysis + audit scripts |

The application reads **only** `data/shopscope.db` (read-only connections).
No page, chart or KPI loads a raw CSV directly.

## 2. How to run everything

From the project root (PowerShell):

```powershell
# 1. Clean + validate + write data/processed/ and the quality reports
.\scripts\run_etl.ps1                      # or: py -3.14 -m src.etl_pipeline

# 2. (Optional) regenerate the machine-readable audit standalone
py -3.14 -m src.quality_audit

# 3. Build / rebuild the SQLite database from the cleaned CSVs
.\scripts\run_sql.ps1                      # or: py -3.14 -m src.database

# 4. Headline KPIs before vs after cleaning
py -3.14 scripts\compare_metrics.py

# 5. Business metrics + findings + figures + machine-readable summary
.\scripts\run_analysis.ps1

# 6. Launch the app
streamlit run app/app.py                     # Windows: py -3.14 -m streamlit run app/app.py
```

Every step is **idempotent**: outputs are overwritten, never appended, so a
re-run cannot corrupt or duplicate earlier results. The ETL exits with code 1
if any critical validation check fails or a raw source file changes mid-run.

## 3. The pipeline (`src/etl_pipeline.py`)

```
load (read-only, hashed)
 -> profile BEFORE (reports/data_quality_before_cleaning.md)
 -> clean       (src/data_cleaning.py: 9 entities + identity/geo masters)
 -> transform   (src/transform.py: fact_order_summary - one row per order)
 -> validate    (src/validation.py: schema, keys, grain, reconciliation)
 -> write       (data/processed/ + quarantine)
 -> profile AFTER + ledger + 64 validation checks
 -> machine-readable audit (reports/data_quality_audit.{csv,json} + summary.md)
 -> re-hash sources (proves the raw CSVs were not modified)
```

### 3.1 Cleaning rules in force (measured effects)

| # | Rule | Why | Rows affected (measured) |
|---|------|-----|--------------------------|
| C1 | Never join `order_items` × `order_payments` × `order_reviews` in one query. `fact_order_summary` pre-aggregates each child to one row per `order_id`. | Join fan-out inflates order-level KPIs ~1.2x | Naive inner join = **117,329 rows** vs 99,441 real orders (1.18x); worst order = 63 rows at plan time |
| C2 | `order_id` = grain of the order fact; `order_item_id` = grain of line items. | Line-item revenue must not sum twice | 9,803 orders with >1 item; 2,961 with >1 payment; 547 with >1 review |
| C3 | Deduplicate `olist_geolocation_dataset` to one centroid per zip. | Joining raw geo multiplies rows up to 1000x | 1,000,163 → **19,177** zips; 261,831 exact duplicates; worst zip had 1,146 rows |
| C4 | Quarantine coordinates outside Brazil (lat -33.75..5.27, lng -73.99..-34.79). | Distance/ETA and maps get garbage inputs | 31 lat + 37 lng rows quarantined → `data/quarantine/quarantine_geolocation_outliers.csv` |
| C5 | Revenue metrics use `order_status` correctly and keep statuses intact. | Cancellations / unfinished orders must not fake sales | no rows deleted - statuses preserved |
| C6 | Missing dates **stay NULL** (never 0 days, never "on time"). | NULL means unknown, not fast | 2,965 orders missing delivery date; 8 marked delivered without a timestamp |
| C7 | Impossible date sequences flagged (`has_impossible_date_sequence`) + quarantined copy. | Non-monotonic timestamps are data errors, not delays | 1,382 orders |
| C8 | Missing category becomes the sentinel `unknown`; untranslated categories keep NULL label. | INNER joins must not silently drop products | 610 products without category; 13 products in 2 categories with no English label |
| C9 | Second review on the same order collapsed to one (latest answer kept). | A duplicated review must not double-weight CSAT | 551 rows; 202 orders had conflicting scores |
| C10 | Non-positive payments kept and flagged, excluded from payment-mix rates. | `payment_value ≤ 0` or `installments ≤ 0` corrupt rates | 9 value + 2 installment rows |
| C11 | ZIP prefixes as nullable integers (leading zeros preserved by `Int64`); placeholder geo row for addresses absent from the geo source. | Raw zip joins silently drop 285 addresses | 162 referenced zips absent from geo source → placeholder with NULL centroid |
| C12 | Pre-2017 / post-2018-08 months excluded from trend windows but always reported. | Ramp-up and cut-off months distort trends | 329 + 20 orders outside the analysis window |
| C13 | Repeat buyers identified on `customer_unique_id` (dim_customer_identity). | Repeat-purchase maths must use people, not placements | 99,441 placements → 96,096 distinct people; 2,997 people ordered more than once (3,345 extra placement rows) |
| C14 | No invented data: no imputation of customers, payments, deliveries or reviews. | Fabricated values would fake accuracy | n/a |
| C15 | `payment_value` totals are **not** multiplied by installment counts. | Installments split a charge; they do not create money | n/a |
| C16 | Orders with no line items keep NULL item revenue (775 orders). | AOV must not silently exclude them | 775 orders |
| C17 | FK validation: every child key resolves to a parent (raw where possible). | Orphans corrupt joins | raw orphans: 278 customer zips, 7 seller zips missing from geo source |
| C18 | Reconciliation: measures on cleaned data equal measures on raw data. | The pipeline must not *change* what it cannot justify | verified per run |

The rules are implemented once in `src/data_cleaning.py` and enforced by
`src/validation.py` (schema, key, grain, reconciliation). Nothing is hardcoded:
expected values are derived from the source frames at run time, so the same
pipeline works if Olist ships a newer export.

## 4. Data-quality findings (measured, latest run)

Complete machine-readable detail: `reports/data_quality_audit.csv` (one row per
file/check) and `reports/data_quality_audit.json`. Summary: `reports/data_quality_summary.md`.

**Raw files:**

| File | Rows | Exact dup rows | Duplicate business keys | Missing cols |
|------|-----:|---------------:|-------------------------|-------------:|
| orders | 99,441 | 0 | none (order_id unique) | 3 (approved/carrier/delivered dates) |
| customers | 99,441 | 0 | customer_unique_id (repeat buyers - expected) | 0 |
| order_items | 112,650 | 0 | order_id (multi-item orders - expected) | 0 |
| payments | 103,886 | 0 | order_id (multi-payment orders - expected) | 0 |
| reviews | 99,224 | 0 | review_id (none) + order_id (551 second reviews) | 2 |
| products | 32,951 | 0 | none | 8 |
| sellers | 3,095 | 0 | none | 0 |
| geolocation | 1,000,163 | **261,831** | geolocation_zip_code_prefix | 0 |
| translation | 71 | 0 | none | 0 |

**Invalid / out-of-range values (flagged, raw):** 9 non-positive payments,
2 non-positive installment counts, 4 products with weight/dimension ≤ 0,
31 + 37 geo coordinates outside the Brazil box.

**Referential-integrity orphans (raw):** 278 customer ZIPs and 7 seller ZIPs
absent from the geolocation source; 13 product rows whose category has no
English translation.

**After cleaning:** every primary key unique on all 11 tables; row ledger
input→output per table in `reports/etl_run_summary.json`; `PRAGMA
foreign_key_check` clean on `data/shopscope.db`; 64 validation checks per full
run (60 pass, 4 known warnings - see section 6).

## 5. Before vs after: headline metrics

`reports/metrics_before_after.md` (regenerated by `py scripts\compare_metrics.py`)
compares the naive raw-file numbers with the cleaned KPIs. Headlines
(measured live):

| Metric | Raw (naive) | Cleaned | Note |
|--------|-------------|---------|------|
| Total orders | 99,441 | 99,441 | unchanged (order grain preserved) |
| Naive 4-way join rows | 117,329 | 99,441 | fan-out removed (1.18x) |
| Merchandise sales (price) | R$ 13,591,643.70 | R$ 13,591,643.70 | unchanged (no items dropped) |
| Freight | R$ 2,251,909.54 | R$ 2,251,909.54 | unchanged |
| Payment cash | R$ 16,008,872.12 | R$ 16,008,872.12 | unchanged (not multiplied by installments) |
| AOV | R$ 137.75 | R$ 137.75 | unchanged |
| Distinct customers | 96,096 | 96,096 | unchanged |
| Repeat customer rate | 3.12% | 3.12% | same numerator/denominator |
| Average review score | 4.0864 (99,224 rows) | 4.0864 (98,673 orders) | duplicate reviews no longer double-weighted |
| On-time delivery rate | 94.96% (missing = on time) | 91.89% | missing timestamps excluded from the cleaned rate |
| Items per order | 1.1417 | 1.1417 | unchanged |

The headline "before" and "after" numbers are close by design (the pipeline
repairs the data, it does not re-engineer it). The meaningful corrections are
the join fan-out, the duplicate-review removal, and the honest treatment of
missing delivery timestamps.

## 6. Remaining issues & limitations (known, honest)

1. **Delivery-date gaps (2,965 orders)**: orders without a delivery timestamp
   (incl. some never delivered) are excluded from delivery-delay metrics, which
   flatters the on-time rate. Reported, not hidden.
2. **Estimates are a commercial promise**: "on time" = within the seller's
   estimated window, which can be padded. Not an absolute service level.
3. **163 referenced ZIPs have no usable centroid** (source data gap): dim_geolocation
   keeps placeholder rows with NULL coordinates; distance-ETAs for those areas
   are impossible.
4. **13 products have no English category label** (2 categories absent from the
   translation file); NULL kept, no guess.
5. **Reviews over-represent early experience**; scores 1-2 interpreted as
   negative is a convention, not a fact.
6. **No predictive model exists**: all pages/findings are rule-based analytics.
   No ML was added because none existed; prediction accuracy is never claimed.
7. The exact plan-time fan-out figure (119,143) differs from the live inner-join
   measurement (117,329) by join definition/period; conclusions are identical.

## 7. Tests

Run from the project root:

```powershell
py -3.14 -m pytest -q
```

Covers: loading/type contract (`test_etl_units.py`), cleaning rules and
reproducibility (`test_cleaning.py`, `test_join_behavior.py`), DB schema and FK
constraints (`test_database_schema.py`, `test_database_init.py`), KPI and join
correctness vs SQL (`test_metrics_insights.py`, `test_sql_vs_python.py`),
dashboard page integrity (`test_dashboard.py`) and the audit/baseline modules
(`test_quality_audit.py`, `test_baseline_metrics.py`). Full suite: **179 tests, all
passing** (155 pre-change + 24 new), latest run 489s on this machine.

## 8. Reproducibility guarantees

- Raw sources are hashed before the run and re-hashed after it; any change
  aborts with code 1.
- All outputs are deleted/overwritten, never appended to.
- The audit and the comparison reports are pure functions of the frames and the
  database; re-running them changes nothing outside `reports/`.
- Test runs never touch `data/shopscope.db` (a cached copy is built under
  `.pytest_shopscope_cache/`).