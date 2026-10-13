# ShopScope — Analysis layer (metrics, insights, visualizations)

Turns the validated SQLite database into documented KPIs, evidence-backed
findings, charts, and reports. It is a **read-only** consumer of
`data/shopscope.db`; it never changes the raw sources, the cleaned CSVs, or the
database.

## Files

| File | Purpose |
| --- | --- |
| `src/metrics.py` | The 14 headline KPIs. Each carries formula, numerator/denominator, filters, eligible population and limitations. CLI: prints or JSON-dumps them. |
| `src/insights.py` | The exploratory analyses. Produces `Finding` objects (business question, method, result, period/population, interpretation, recommendation, limitations, correlation caveat) and writes all reports. Orchestrator CLI. |
| `src/visualizations.py` | Matplotlib (headless `Agg`) chart builders; each writes one PNG to `reports/figures/`. |
| `scripts/run_analysis.ps1` | Run the whole analysis layer + its tests in one command. |
| `tests/test_metrics_insights.py` | KPI values re-derived in pandas from the cleaned CSVs; findings completeness; period rules; chart/report output. |
| `reports/business_metrics.md` | Generated KPI catalogue. |
| `reports/eda_findings.md` | Generated findings report. |
| `reports/analysis_summary.json` | Generated machine-readable KPIs + findings (for the future web app). |
| `reports/figures/*.png` | Generated charts. |

## Design rules

* **No hardcoded results.** Every value is computed from the database at run
  time; re-running the ETL and rebuilding the DB changes the numbers, not the
  code.
* **Money measures stay separate.** Merchandise sales = `SUM(fact_order_item.price)`;
  freight and payment cash are reported on their own. The gross
  `fact_order_summary.item_revenue` is never used as sales.
* **Every KPI is documented.** A metric is never shown without its formula,
  filters and eligible population.
* **Periods are handled explicitly.** Trend statements use the complete window
  (`config.ANALYSIS_WINDOW_START`..`ANALYSIS_WINDOW_END`, currently 2017-01 to
  2018-08). The 2016 ramp-up and the 2018-09/10 export tail are excluded from
  trend metrics but counted and reported. The export has no orders in 2016-11.
* **Correlation ≠ causation.** Relational findings state the association and its
  strength and explicitly say the data cannot establish cause. Recommendations
  that imply cause are framed as hypotheses to test.
* **Eligible populations are live.** `metrics` reports the exact population
  behind each number (e.g. on-time rate excludes orders with no delivery date).

## The 14 KPIs

1. Total unique orders — `COUNT(order_id)`
2. Total merchandise sales — `SUM(price)`, freight excluded
3. Total freight charges — `SUM(freight_value)`
4. Average order value — merchandise sales / item-bearing orders
5. Delivered orders — `order_status = 'delivered'`
6. Delivery completion rate — delivered / all orders
7. Cancellation rate — canceled / all orders
8. Average review score — mean of one-review-per-order scores
9. On-time delivery rate — on/before estimate / orders with a delivery date
10. Late delivery rate — after estimate / orders with a delivery date
11. Average delivery duration — mean purchase-to-delivery days
12. Repeat customer rate — customers with ≥ 2 orders / distinct customers
13. Average items per order — item units / item-bearing orders
14. Payment method distribution — payment value by `payment_type`

Full definitions and limitations are in `reports/business_metrics.md`.

## Findings

`src/insights.py` runs nine analyses, each producing one or more findings:
sales trends, product categories, geography, customer retention, payments and
installments, delivery vs satisfaction, seller operations, weight and freight,
and data-quality impact.

## Rerun

```powershell
# Full analysis + its tests
.\scripts\run_analysis.ps1

# Just regenerate reports (skip figures / tests)
python -m src.insights
python -m src.insights --no-figures

# Print or dump the KPIs
python -m src.metrics
python -m src.metrics --json
```

Prerequisite: `data\shopscope.db` exists (`.\scripts\run_sql.ps1` or
`python -m src.database`).
