# ShopScope - Metrics Before vs After Cleaning

Headline KPIs computed **naively on the raw CSVs** (the numbers a dashboard fed directly with raw data would show) compared with the **cleaned KPIs** from `data/shopscope.db`. Every number is measured live by `scripts/compare_metrics.py`; none is hardcoded.

| Metric | Raw (naive) | Cleaned | Difference | Why they differ |
|--------|-------------|---------|------------|-----------------|
| Total orders (unique order_id) | 99,441 | 99,441.0 | +0.00% | Same grain and same rows: cleaning never deletes orders, so this is identical by design. |
| Naive orders × items × payments × reviews join | 117,329 | 99,441 | -15.25% | The fan-out artefact of joining the three 1:N tables (each order appears once per item-payment-review combination) is removed: fact_order_summary pre-aggregates to exactly one row per order (99,441). A naive dashboard fed raw joins would have counted 117,329 'rows', which is 1.18x the 99,441 real orders. |
| Naive items × payments × reviews join (no orders table) | 117,329 | 99,441 | -15.25% | The classic three-way join from docs/project_plan.md, measured live. The plan quoted 119,143 rows at planning time; the exact figure depends on join type (inner vs left) and period, and the conclusion is unchanged: joining any two of the 1:N children multiplies rows. |
| Merchandise sales (item price, freight excluded) | R$ 13,591,643.70 | R$ 13,591,643.70 | +0.00% | Both sum item price over the same item rows (no item is dropped by cleaning), so the totals agree. |
| Freight charges | R$ 2,251,909.54 | R$ 2,251,909.54 | +0.00% | Same population as merchandise sales. |
| Payment cash (payment_value total) | R$ 16,008,872.12 | R$ 16,008,872.12 | +0.00% | payment_value is the full amount of each payment record (installments split the charge, they do not multiply it). Cleaned output keeps and flags non-positive rows instead of deleting them, so the pre/post totals agree. |
| Payment records | 103,886 | 103,886 | +0.00% | No payment row is deleted by cleaning. |
| Average order value (AOV) | R$ 137.75 | R$ 137.75 | +0.00% | Both use price / item-bearing orders, so they agree. |
| Distinct customers (customer_unique_id) | 96,096 | 96,096 | +0.00% | Cleaning collapses each person to one identity row; no customers are invented or removed, so the distinct count is unchanged. |
| Repeat customer rate | 3.12% | 3.12% | +0.00 pp | Same numerator (2,997 people with 2+ placements) over the same denominator; the remaining difference is only rounding in report formatting. |
| Average review score | 4.0864 over 99,224 review rows | 4.0864 over 98,673 reviewed orders | +0.00% | The naive mean weights every review row once (99,224), including the 551 second-reviews on the same order (202 of those orders had conflicting scores). The cleaned score is one row per reviewed order (98,673), so duplicate reviews can no longer bias the average. |
| Delivered orders (status = delivered) | 96,478 | 96,478.0 | +0.00% | Order status is source truth; cleaning preserves it. |
| On-time delivery rate | naive (missing = on time): 94.96% | 91.89% | -3.07 pp | A naive dashboard treats the 2,965 orders with no delivery timestamp as on time. The cleaned metric excludes them from the denominator (96,476 orders with a real timestamp) so 'on time' means 'measured on time'. |
| Items per order | 1.1417 | 1.1417 | +0.00% | Same item rows and item-bearing orders. |

## What the differences mean

- **Order-level KPIs (orders, AOV, item orders) are unchanged.** The grain is `order_id` before and after; cleaning repairs the data it does not re-engineer it.
- **The join fan-out is the single biggest correctness fix.** Counting or summing over a naive orders×items×payments×reviews join inflates every order-level number by the merge multiplicity. `fact_order_summary` pre-aggregates each child to one row per order, so downstream analytics never multiply rows by accident.
- **Review-based metrics get stricter, not 'better'.** Collapsing second reviews to one per order removes the double weighting of CSAT; any move in the average is a correction, not an improvement.
- **Delivery metrics stop rewarding missing data.** Orders without a delivery timestamp keep NULL and are excluded from delay calculations, instead of silently counting as on time.
- **Payment cash is not multiplied by installments.** `payment_value` is the full amount of that payment record; installments only split the charge. The pipeline never sums installment-count × value.

## Method notes

- Raw numbers come from `src.baseline_metrics.naive_metrics()` on the files loaded read-only by `src.data_loader`.
- Cleaned numbers come from `src.metrics.compute_kpis()` (the same source as `reports/business_metrics.md`) against the validated DB.
- Division-by-zero cases return `-` rather than a fabricated rate.

## Regenerate

```powershell
python -m src.etl_pipeline       # 1. clean + validate
python -m src.database           # 2. rebuild data/shopscope.db
python scripts/compare_metrics.py # 3. rewrite this report
```
