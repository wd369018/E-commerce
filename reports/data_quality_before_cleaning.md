# ShopScope - Data Quality Report (BEFORE cleaning)

- Generated: `2026-10-11T18:21:30+00:00`
- Stage: **ingestion complete, no transformation applied**
- Source files are read-only; SHA-256 recorded below is re-verified at the end of the run.

## 1. Files ingested

| File | Bytes | Rows read | Cols | SHA-256 (first 16) | Type-cast failures |
|---|---|---|---|---|---|
| olist_orders_dataset.csv | 17,654,914 | 99,441 | 8 | `8df58ef3d2d7e994` | 0 |
| olist_customers_dataset.csv | 9,033,957 | 99,441 | 5 | `983a422239e1712d` | 0 |
| olist_order_items_dataset.csv | 14,617,431 | 112,650 | 7 | `f2b268e31a9bb130` | 0 |
| olist_order_payments_dataset.csv | 5,777,138 | 103,886 | 5 | `4f713964f2815dbb` | 0 |
| olist_order_reviews_dataset.csv | 14,451,670 | 99,224 | 7 | `012b61c7593e34f5` | 0 |
| olist_products_dataset.csv | 2,379,446 | 32,951 | 9 | `3e6569628a17fbc7` | 0 |
| olist_sellers_dataset.csv | 174,703 | 3,095 | 4 | `1f643d2b950373b8` | 0 |
| olist_geolocation_dataset.csv | 61,273,883 | 1,000,163 | 5 | `b514f6fc991b9566` | 0 |
| product_category_name_translation.csv | 2,613 | 71 | 2 | `a81f0d1f27b27e72` | 0 |

### Type-cast failures (values that could not become dates/numbers)

_None - every declared date and numeric column parsed cleanly._

## 2. Structural profile

| Source file | Rows | Cols | Exact dup rows | Cols w/ missing | Missing cells |
|---|---|---|---|---|---|
| olist_orders_dataset.csv | 99,441 | 8 | 0 | 3 | 4,908 (0.62%) |
| olist_customers_dataset.csv | 99,441 | 5 | 0 | 0 | 0 (0.00%) |
| olist_order_items_dataset.csv | 112,650 | 7 | 0 | 0 | 0 (0.00%) |
| olist_order_payments_dataset.csv | 103,886 | 5 | 0 | 0 | 0 (0.00%) |
| olist_order_reviews_dataset.csv | 99,224 | 7 | 0 | 2 | 145,903 (21.01%) |
| olist_products_dataset.csv | 32,951 | 9 | 0 | 8 | 2,448 (0.83%) |
| olist_sellers_dataset.csv | 3,095 | 4 | 0 | 0 | 0 (0.00%) |
| olist_geolocation_dataset.csv | 1,000,163 | 5 | 261,831 | 0 | 0 (0.00%) |
| product_category_name_translation.csv | 71 | 2 | 0 | 0 | 0 (0.00%) |

## 3. Missing values

| File | Column | Inferred dtype | Missing | Missing % | Unique |
|---|---|---|---|---|---|
| olist_orders_dataset.csv | `order_approved_at` | datetime64[us] | 160 | 0.16% | 90,733 |
| olist_orders_dataset.csv | `order_delivered_carrier_date` | datetime64[us] | 1,783 | 1.79% | 81,018 |
| olist_orders_dataset.csv | `order_delivered_customer_date` | datetime64[us] | 2,965 | 2.98% | 95,664 |
| olist_order_reviews_dataset.csv | `review_comment_title` | str | 87,656 | 88.34% | 4,527 |
| olist_order_reviews_dataset.csv | `review_comment_message` | str | 58,247 | 58.70% | 36,159 |
| olist_products_dataset.csv | `product_category_name` | str | 610 | 1.85% | 73 |
| olist_products_dataset.csv | `product_name_lenght` | Int64 | 610 | 1.85% | 66 |
| olist_products_dataset.csv | `product_description_lenght` | Int64 | 610 | 1.85% | 2,960 |
| olist_products_dataset.csv | `product_photos_qty` | Int64 | 610 | 1.85% | 19 |
| olist_products_dataset.csv | `product_weight_g` | Float64 | 2 | 0.01% | 2,204 |
| olist_products_dataset.csv | `product_length_cm` | Float64 | 2 | 0.01% | 99 |
| olist_products_dataset.csv | `product_height_cm` | Float64 | 2 | 0.01% | 102 |
| olist_products_dataset.csv | `product_width_cm` | Float64 | 2 | 0.01% | 95 |

## 4. Datetime ranges

| File | Datetime column | Min | Max |
|---|---|---|---|
| olist_orders_dataset.csv | `order_purchase_timestamp` | 2016-09-04 21:15:19 | 2018-10-17 17:30:18 |
| olist_orders_dataset.csv | `order_approved_at` | 2016-09-15 12:16:38 | 2018-09-03 17:40:06 |
| olist_orders_dataset.csv | `order_delivered_carrier_date` | 2016-10-08 10:34:01 | 2018-09-11 19:48:28 |
| olist_orders_dataset.csv | `order_delivered_customer_date` | 2016-10-11 13:46:32 | 2018-10-17 13:22:46 |
| olist_orders_dataset.csv | `order_estimated_delivery_date` | 2016-09-30 00:00:00 | 2018-11-12 00:00:00 |
| olist_order_items_dataset.csv | `shipping_limit_date` | 2016-09-19 00:15:00 | 2020-04-09 22:35:00 |
| olist_order_reviews_dataset.csv | `review_creation_date` | 2016-10-02 00:00:00 | 2018-08-31 00:00:00 |
| olist_order_reviews_dataset.csv | `review_answer_timestamp` | 2016-10-07 18:32:28 | 2018-10-29 12:27:35 |

## 5. Invalid and inconsistent values (measured on the raw values)

| Area | Check | Count |
|---|---|---|
| dim_customer | repeat_buyer_placements | 3,345 |
| dim_order | delivered_before_purchase | 0 |
| dim_order | approved_before_purchase | 0 |
| dim_order | carrier_before_approval | 1,359 |
| dim_order | delivered_before_carrier | 23 |
| dim_order | delivered_after_estimate | 7,827 |
| dim_order | status_delivered_but_no_delivery_date | 8 |
| dim_order | not_delivered_status_but_has_delivery_date | 6 |
| dim_order | missing_approval_date | 160 |
| dim_order | missing_carrier_date | 1,783 |
| dim_order | missing_delivery_date | 2,965 |
| dim_order | no_line_items | 775 |
| dim_order | no_payments | 1 |
| dim_order | no_reviews | 768 |
| dim_order | impossible_date_sequence | 1,382 |
| dim_product_category | categories_used_without_english_translation | 2 |
| dim_product_category | translation_rows_total | 71 |
| dim_product | missing_category_name | 610 |
| dim_product | missing_name_length | 610 |
| dim_product | missing_description_length | 610 |
| dim_product | missing_photos_qty | 610 |
| dim_product | missing_weight | 2 |
| dim_product | non_positive_weight | 4 |
| dim_product | missing_any_dimension | 2 |
| dim_product | product_rows_with_untranslated_category | 13 |
| dim_product | shipping_invalid | 6 |
| dim_seller | missing_city | 0 |
| dim_seller | missing_state | 0 |
| fact_order_item | missing_price | 0 |
| fact_order_item | non_positive_price | 0 |
| fact_order_item | negative_freight | 0 |
| fact_order_item | missing_shipping_limit_date | 0 |
| fact_order_item | repeated_product_within_order | 10,225 |
| bridge_payment | non_positive_payment_value | 9 |
| bridge_payment | missing_payment_value | 0 |
| bridge_payment | non_positive_installments | 2 |
| bridge_payment | zero_or_negative_installments_and_value | 11 |
| bridge_review | source_rows | 99,224 |
| bridge_review | duplicate_review_id_rows | 814 |
| bridge_review | duplicate_order_id_rows | 551 |
| bridge_review | orders_with_conflicting_duplicate_scores | 202 |
| bridge_review | missing_comment_title | 87,123 |
| bridge_review | missing_comment_message | 57,898 |
| bridge_review | missing_score | 0 |
| bridge_review | score_out_of_range | 0 |
| bridge_review | review_id_reused_across_orders_after_dedup | 578 |
| dim_geolocation | source_rows | 1,000,163 |
| dim_geolocation | exact_duplicate_rows | 261,831 |
| dim_geolocation | coordinates_outside_brazil | 33 |
| dim_geolocation | unique_observations_after_dedup | 738,332 |
| dim_geolocation | distinct_zip_prefixes | 19,015 |
| dim_geolocation | zips_with_all_coordinates_quarantined | 5 |
| dim_geolocation | zips_referenced_by_address_but_absent_from_source | 162 |
