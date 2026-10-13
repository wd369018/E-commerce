# ShopScope - Data Quality Audit (machine-readable summary)

Generated (UTC): 2026-10-11T18:22:14+00:00  |  source: `etl_pipeline`

This summary is the human-readable companion to `reports/data_quality_audit.csv` and `reports/data_quality_audit.json`. Regenerate it with:

```powershell
python -m src.quality_audit     # standalone
python -m src.etl_pipeline      # as the final ETL stage
```

> A repeated value is only a duplicate when the business key says so. Multi-item orders, multi-record payments and repeat buyers are reported as expected multiplicity, not as defects.

## Raw source files

| File | Rows | Cols | Exact dup rows | Keys duplicated | Missing cols | Orphans |
|------|-----:|-----:|---------------:|----------------:|-------------:|--------:|
| `olist_orders_dataset.csv` | 99,441 | 8 | 0 | - | 3 cols | - |
| `olist_customers_dataset.csv` | 99,441 | 5 | 0 | - | 0 cols | customer.zip |
| `olist_order_items_dataset.csv` | 112,650 | 7 | 0 | - | 0 cols | - |
| `olist_order_payments_dataset.csv` | 103,886 | 5 | 0 | - | 0 cols | - |
| `olist_order_reviews_dataset.csv` | 99,224 | 7 | 0 | review_id, order_id | 2 cols | - |
| `olist_products_dataset.csv` | 32,951 | 9 | 0 | - | 8 cols | product.category |
| `olist_sellers_dataset.csv` | 3,095 | 4 | 0 | - | 0 cols | seller.zip |
| `olist_geolocation_dataset.csv` | 1,000,163 | 5 | 261,831 | geolocation_zip_code_prefix | 0 cols | - |
| `product_category_name_translation.csv` | 71 | 2 | 0 | - | 0 cols | - |

## Cleaned tables

| File | Rows | Cols | Exact dup rows | Keys duplicated | Missing cols | Orphans |
|------|-----:|-----:|---------------:|----------------:|-------------:|--------:|
| `dim_customer` | 99,441 | 5 | 0 | - | 0 cols | - |
| `dim_customer_identity` | 96,096 | 4 | 0 | - | 0 cols | - |
| `dim_order` | 99,441 | 20 | 0 | - | 5 cols | - |
| `dim_product_category` | 74 | 3 | 0 | - | 1 cols | - |
| `dim_product` | 32,951 | 12 | 0 | - | 9 cols | - |
| `dim_seller` | 3,095 | 4 | 0 | - | 0 cols | - |
| `dim_geolocation` | 19,177 | 8 | 0 | - | 4 cols | - |
| `fact_order_item` | 112,650 | 9 | 0 | - | 0 cols | - |
| `bridge_payment` | 103,886 | 7 | 0 | - | 0 cols | - |
| `bridge_review` | 98,673 | 9 | 0 | - | 2 cols | - |
| `fact_order_summary` | 99,441 | 14 | 0 | - | 6 cols | - |

## Invalid / out-of-range values (flagged counts)

| File | Check | Count |
|------|-------|------:|
| `olist_order_payments_dataset.csv` | `payment_value_non_positive` | 9 |
| `olist_order_payments_dataset.csv` | `installments_non_positive` | 2 |
| `olist_products_dataset.csv` | `product_weight_g_non_positive` | 4 |
| `olist_geolocation_dataset.csv` | `lat_outside_brazil_box` | 31 |
| `olist_geolocation_dataset.csv` | `lng_outside_brazil_box` | 37 |

## Pipeline validation

- Checks: **64** total | **60** passed | **0** errors | **4** warnings

Failed / warning checks:

- `bridge_payment.payment_value.range` (warning): payment_value within [0.0001, 1000000000000.0] -> observed 9 out-of-range values of 103,886 (expected 0)
- `quality.product_untranslated_categories` (warning): every real product category has an English translation -> observed 13 product row(s) whose category has no English label (expected 0 (categories missing from the translation file))
- `quality.geolocation_usable` (warning): every ZIP has a usable centroid -> observed 167 ZIP(s) with NULL centroid (all coordinates quarantined or absent) (expected 0)
- `quality.geolocation_address_coverage` (warning): every address ZIP has geolocation source data -> observed 162 ZIP prefix(es) referenced by customers/sellers but absent from the geolocation source (placeholder row, NULL centroid) (expected 0 (gap present in the source data, not created by this pipeline))

## Full context

- `reports/data_quality_before_cleaning.md` / `reports/data_quality_after_cleaning.md` - human-readable before/after quality reports.
- `reports/etl_run_summary.json` - ETL run ledger (row counts, validation, quarantines).
- `reports/metrics_before_after.md` - headline KPIs computed naively on the raw files vs the cleaned database.
