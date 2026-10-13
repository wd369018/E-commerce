# ShopScope — SQL layer

SQLite database built from the cleaned tables in `data/processed/`, with
analytical views, a reusable query library, and a validation suite that checks
every KPI against an independent Python calculation.

## Files

| File | Purpose |
| --- | --- |
| `sql/schema.sql` | DDL: tables, primary keys, foreign keys, check constraints, indexes. Idempotent (drops and recreates). |
| `sql/analytical_queries.sql` | Two sections: `CREATE VIEW` statements, then a `-- @query: name` query library. |
| `src/database.py` | Creates the database from the cleaned CSVs, loads every table in one transaction, verifies the result. |
| `src/queries.py` | Parses the query library, applies the views, runs named queries. |
| `scripts/sql_kpi_snapshot.py` | Read-only KPI printout for reproducibility. |
| `scripts/run_sql.ps1` | Build + test + snapshot in one command. |
| `tests/test_database_schema.py` | Row counts, keys, constraints, indexes, views. |
| `tests/test_join_behavior.py` | Fan-out and money-double-counting guards. |
| `tests/test_sql_vs_python.py` | SQL vs independently computed Python for every KPI. |

## Design rules

* **One table = one grain.** Each table's grain is stated in `schema.sql`.
* **Payments are aggregated per order before any join.** `bridge_payment` is
  many-to-one with `dim_order`; joining it to `fact_order_item` multiplies rows
  (99,441 orders explode past 112,650 item units). Use the one-row-per-order
  `fact_order_summary`, or `vw_order_item_rollup` for order-level money.
* **Merchandise sales = `SUM(fact_order_item.price)`** — freight is never added.
  Freight (`SUM(freight_value)`) and cash (`SUM(bridge_payment.payment_value)`)
  are separate measures.
* **Repeat customers use `customer_unique_id`** (96,096 people), never
  `customer_id` (99,441 order placements).
* **Booleans are `INTEGER 0/1`**, nullable where the source is tri-state
  (`is_late_vs_estimate`, `is_shipping_valid`).
* **Timestamps are ISO-8601 TEXT**, so lexicographic order is chronological.

> **Naming caveat.** `fact_order_summary.item_revenue` (produced by the ETL) is
> the **gross** item value (`price + freight`), not merchandise sales. The
> analytics layer derives sales from `fact_order_item.price` via
> `vw_order_item_rollup` and never treats `item_revenue` as sales.

## Tables (11) and relationships

| Table | Grain | PK | Foreign keys |
| --- | --- | --- | --- |
| `dim_customer` | `customer_id` | `customer_id` | — |
| `dim_customer_identity` | `customer_unique_id` | `customer_unique_id` | — |
| `dim_product_category` | category | `product_category_name` | — |
| `dim_product` | `product_id` | `product_id` | `product_category_name` → `dim_product_category` |
| `dim_seller` | `seller_id` | `seller_id` | — |
| `dim_geolocation` | ZIP prefix | `geolocation_zip_code_prefix` | — |
| `dim_order` | `order_id` | `order_id` | `customer_id` → `dim_customer` |
| `fact_order_item` | `order_id` + `order_item_id` | composite | `order_id`, `product_id`, `seller_id` |
| `bridge_payment` | `order_id` + `payment_sequential` | composite | `order_id` → `dim_order` |
| `bridge_review` | `order_id` | `order_id` | `order_id` → `dim_order` |
| `fact_order_summary` | `order_id` | `order_id` | `order_id` → `dim_order` |

`dim_product.product_category_name_english` is **not** stored: the translation
is normalised into `dim_product_category` and reached by join.

### Indexes (26)

Join/lookup keys are indexed: `dim_order(customer_id, order_purchase_timestamp,
purchase_date, order_status, order_delivered_customer_date)`,
`fact_order_item(order_id, product_id, seller_id)`,
`bridge_payment(order_id, payment_type, payment_installments)`,
`bridge_review(order_id, review_score)`, `dim_customer(customer_unique_id,
customer_state)`, `dim_product(product_category_name, product_weight_g)`,
`fact_order_summary(order_status, purchase_date, is_revenue_status,
is_late_vs_estimate)`, plus the category/seller/geo helper indexes.

## Views and eligible populations

| View | Eligible population |
| --- | --- |
| `vw_order_item_rollup` | orders with ≥1 item (98,666 of 99,441) |
| `vw_monthly_sales` | all 112,650 item units (sales) + revenue-status subset |
| `vw_monthly_order_volume` | all 99,441 orders, by month × status |
| `vw_order_status_distribution` | all orders (sales from items, cash from summary) |
| `vw_category_performance` | units grouped to (category, order) before the review join |
| `vw_state_customer_order_distribution` | customers joined to their own orders (1:1) |
| `vw_customer_repeat_purchase` | grouped by `customer_unique_id` (96,096) |
| `vw_repeat_customer_summary` | segments derived from the view above |
| `vw_payment_method_distribution` | individual payment rows (103,886), not orders |
| `vw_installment_analysis` | individual payment rows, bucketed by instalments |
| `vw_delivery_performance_monthly` | orders with a delivery timestamp (96,476 of 99,441) |
| `vw_delivery_summary` | all orders, with the excluded (no delivery date) shown |
| `vw_review_score_distribution` | one review per order (98,673) |
| `vw_review_monthly` | reviews joined to purchase month |
| `vw_seller_performance` | units aggregated to (seller, order) before the review join |
| `vw_weight_vs_freight` | units with known weight and non-negative freight |
| `vw_kpi_population` | live row counts of every population above |

`vw_kpi_population` is executable documentation: it reports the live
`source_rows` and `rows_used` for each KPI, so no eligibility number is
hard-coded.

## Query library (16)

`monthly_merchandise_sales`, `monthly_order_volume`,
`order_status_distribution`, `category_performance`,
`state_customer_order_distribution`, `repeat_customer_summary`,
`repeat_customer_detail`, `payment_method_distribution`,
`installment_analysis`, `delivery_performance_monthly`, `delivery_summary`,
`review_score_distribution`, `review_monthly_trend`, `seller_performance`,
`weight_vs_freight`, `kpi_population`.

```powershell
python -m src.queries --list
python -m src.queries monthly_merchandise_sales seller_performance
```

## Validation

`python -m pytest -q` — **58 tests**. Every KPI is recomputed from the cleaned
CSVs with pandas (a different code path than the SQL) and compared: integer
counts must match exactly, rounded money/percent/average values within an
explicit tolerance. Join-discipline tests additionally prove that the naive
item × payment join inflates rows and double-counts sales, while the approved
aggregate-first path reconciles exactly.

## Known limitations (unresolved, documented, not silently dropped)

* `fact_order_summary.item_revenue` is gross (price + freight); a future ETL
  change should rename it to `gross_item_value`.
* 9 payment rows have non-positive values (`not_defined`, vouchers); kept and
  counted, not excluded.
* 13 products in `pc_gamer` and `portateis_cozinha_e_preparadores_de_alimentos`
  have no English category name (`NULL`, visible in `category_performance`).
* 162 address ZIPs have no source geolocation row, and 167 ZIPs have a `NULL`
  centroid.
* 775 orders have no line items → `NULL` order-level sales; status/state
  distributions show those statuses with `0` sales rather than an error.
* `delivery_performance` excludes 2,965 orders with no delivery timestamp; the
  counts are visible in `vw_delivery_summary`.
* 40 `customer_unique_id`s appear in two states, so per-state customer counts
  sum to slightly more than the global distinct count.
* `data/shopscope.db` is ~181 MB. It is a build output and should not be
  version-controlled or synced; rebuild it with `python -m src.database`.
