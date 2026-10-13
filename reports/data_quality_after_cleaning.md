# ShopScope - Data Quality Report (AFTER cleaning)

- Generated: `2026-10-11T18:21:30+00:00`
- Stage: **cleaning + validation complete**

## 1. Record count ledger (input -> output)

Every difference is explained. Nothing was dropped silently.

| Table | Input rows | Output rows | Excluded | Reason / transformation |
|---|---|---|---|---|
| `dim_customer` | 99,441 | 99,441 | - | no exclusions (row count preserved) |
| `dim_customer_identity` | 99,441 | 96,096 | - | 1 row per real person (repeat buyers collapsed) |
| `dim_order` | 99,441 | 99,441 | - | orders are never excluded; bad dates are flagged and quarantined for audit |
| `dim_product_category` | 71 | 74 | - | lookup = translation UNION categories actually used UNION 'unknown' |
| `dim_product` | 32,951 | 32,951 | - | no exclusions (row count preserved) |
| `dim_seller` | 3,095 | 3,095 | - | no exclusions (row count preserved) |
| `fact_order_item` | 112,650 | 112,650 | - | no exclusions (row count preserved) |
| `bridge_payment` | 103,886 | 103,886 | - | no exclusions (row count preserved) |
| `bridge_review` | 99,224 | 98,673 | 551 | second-or-later review on the same order (would double-weight CSAT) |
| `dim_geolocation` | 1,000,163 | 19,177 | 261,864 | exact duplicate rows + coordinates outside Brazil (quarantined, not deleted) |
| `fact_order_summary` | 99,441 | 99,441 | - | rollup: one row per order (pre-aggregates items/payouts/reviews) |

## 2. Cleaning actions and measured issues

| Table | Type | Detail | Count |
|---|---|---|---|
| `dim_customer` | issue | repeat_buyer_placements | 3,345 |
| `dim_customer` | action | kept_all_placements | 99,441 |
| `dim_customer_identity` | action | collapsed_duplicate_unique_id | 3,345 |
| `dim_order` | issue | delivered_before_purchase | 0 |
| `dim_order` | issue | approved_before_purchase | 0 |
| `dim_order` | issue | carrier_before_approval | 1,359 |
| `dim_order` | issue | delivered_before_carrier | 23 |
| `dim_order` | issue | delivered_after_estimate | 7,827 |
| `dim_order` | issue | status_delivered_but_no_delivery_date | 8 |
| `dim_order` | issue | not_delivered_status_but_has_delivery_date | 6 |
| `dim_order` | issue | missing_approval_date | 160 |
| `dim_order` | issue | missing_carrier_date | 1,783 |
| `dim_order` | issue | missing_delivery_date | 2,965 |
| `dim_order` | issue | no_line_items | 775 |
| `dim_order` | issue | no_payments | 1 |
| `dim_order` | issue | no_reviews | 768 |
| `dim_order` | issue | impossible_date_sequence | 1,382 |
| `dim_order` | action | kept_null_delivery_date | 2,965 |
| `dim_order` | action | flagged_tri_state_late | 2,965 |
| `dim_order` | action | retained_and_flagged_impossible_dates | 1,382 |
| `dim_order` | action | orders_never_excluded | 99,441 |
| `dim_product_category` | issue | categories_used_without_english_translation | 2 |
| `dim_product_category` | issue | translation_rows_total | 71 |
| `dim_product_category` | action | added_unknown_sentinel_row | 1 |
| `dim_product_category` | action | left_english_null_when_untranslated | 2 |
| `dim_product` | issue | missing_category_name | 610 |
| `dim_product` | issue | missing_name_length | 610 |
| `dim_product` | issue | missing_description_length | 610 |
| `dim_product` | issue | missing_photos_qty | 610 |
| `dim_product` | issue | missing_weight | 2 |
| `dim_product` | issue | non_positive_weight | 4 |
| `dim_product` | issue | missing_any_dimension | 2 |
| `dim_product` | issue | product_rows_with_untranslated_category | 13 |
| `dim_product` | issue | shipping_invalid | 6 |
| `dim_product` | action | category_filled_with_unknown | 610 |
| `dim_product` | action | weight_left_null_not_zero | 2 |
| `dim_product` | action | dimension_left_null_not_zero | 2 |
| `dim_product` | action | flagged_shipping_invalid | 6 |
| `dim_seller` | issue | missing_city | 0 |
| `dim_seller` | issue | missing_state | 0 |
| `fact_order_item` | issue | missing_price | 0 |
| `fact_order_item` | issue | non_positive_price | 0 |
| `fact_order_item` | issue | negative_freight | 0 |
| `fact_order_item` | issue | missing_shipping_limit_date | 0 |
| `fact_order_item` | issue | repeated_product_within_order | 10,225 |
| `fact_order_item` | action | retained_repeated_product_lines | 10,225 |
| `bridge_payment` | issue | non_positive_payment_value | 9 |
| `bridge_payment` | issue | missing_payment_value | 0 |
| `bridge_payment` | issue | non_positive_installments | 2 |
| `bridge_payment` | issue | zero_or_negative_installments_and_value | 11 |
| `bridge_payment` | action | kept_but_flagged | 11 |
| `bridge_review` | issue | source_rows | 99,224 |
| `bridge_review` | issue | duplicate_review_id_rows | 814 |
| `bridge_review` | issue | duplicate_order_id_rows | 551 |
| `bridge_review` | issue | orders_with_conflicting_duplicate_scores | 202 |
| `bridge_review` | issue | missing_comment_title | 87,123 |
| `bridge_review` | issue | missing_comment_message | 57,898 |
| `bridge_review` | issue | missing_score | 0 |
| `bridge_review` | issue | score_out_of_range | 0 |
| `bridge_review` | issue | review_id_reused_across_orders_after_dedup | 578 |
| `bridge_review` | action | collapsed_to_one_review_per_order | 551 |
| `bridge_review` | action | kept_latest_answer_timestamp | 98,673 |
| `bridge_review` | action | kept_comments_null_not_empty_string | 57,898 |
| `dim_geolocation` | issue | source_rows | 1,000,163 |
| `dim_geolocation` | issue | exact_duplicate_rows | 261,831 |
| `dim_geolocation` | issue | coordinates_outside_brazil | 33 |
| `dim_geolocation` | issue | unique_observations_after_dedup | 738,332 |
| `dim_geolocation` | issue | distinct_zip_prefixes | 19,015 |
| `dim_geolocation` | issue | zips_with_all_coordinates_quarantined | 5 |
| `dim_geolocation` | issue | zips_referenced_by_address_but_absent_from_source | 162 |
| `dim_geolocation` | action | removed_exact_duplicates | 261,831 |
| `dim_geolocation` | action | quarantined_outlier_coordinates | 33 |
| `dim_geolocation` | action | aggregated_to_one_row_per_zip | 19,177 |
| `dim_geolocation` | action | kept_null_centroid_for_unusable_zips | 5 |
| `dim_geolocation` | action | appended_address_only_zip_placeholders | 162 |
| `fact_order_summary` | action | prevents_join_fanout | 99,441 |

## 3. Structural profile after cleaning

| Source file | Rows | Cols | Exact dup rows | Cols w/ missing | Missing cells |
|---|---|---|---|---|---|
| dim_customer | 99,441 | 5 | 0 | 0 | 0 (0.00%) |
| dim_customer_identity | 96,096 | 4 | 0 | 0 | 0 (0.00%) |
| dim_order | 99,441 | 20 | 0 | 5 | 10,838 (0.54%) |
| dim_product_category | 74 | 3 | 0 | 1 | 3 (1.35%) |
| dim_product | 32,951 | 12 | 0 | 9 | 2,463 (0.62%) |
| dim_seller | 3,095 | 4 | 0 | 0 | 0 (0.00%) |
| dim_geolocation | 19,177 | 8 | 0 | 4 | 668 (0.44%) |
| fact_order_item | 112,650 | 9 | 0 | 0 | 0 (0.00%) |
| bridge_payment | 103,886 | 7 | 0 | 0 | 0 (0.00%) |
| bridge_review | 98,673 | 9 | 0 | 2 | 145,021 (16.33%) |
| fact_order_summary | 99,441 | 14 | 0 | 6 | 8,250 (0.59%) |

## 4. Missing values after cleaning (NULL = unknown, never 0)

| File | Column | Inferred dtype | Missing | Missing % | Unique |
|---|---|---|---|---|---|
| dim_order | `order_approved_at` | datetime64[us] | 160 | 0.16% | 90,733 |
| dim_order | `order_delivered_carrier_date` | datetime64[us] | 1,783 | 1.79% | 81,018 |
| dim_order | `order_delivered_customer_date` | datetime64[us] | 2,965 | 2.98% | 95,664 |
| dim_order | `delivery_delay_days` | Int64 | 2,965 | 2.98% | 144 |
| dim_order | `is_late_vs_estimate` | boolean | 2,965 | 2.98% | 2 |
| dim_product_category | `product_category_name_english` | object | 3 | 4.05% | 71 |
| dim_product | `product_name_lenght` | Int64 | 610 | 1.85% | 66 |
| dim_product | `product_description_lenght` | Int64 | 610 | 1.85% | 2,960 |
| dim_product | `product_photos_qty` | Int64 | 610 | 1.85% | 19 |
| dim_product | `product_weight_g` | Float64 | 2 | 0.01% | 2,204 |
| dim_product | `product_length_cm` | Float64 | 2 | 0.01% | 99 |
| dim_product | `product_height_cm` | Float64 | 2 | 0.01% | 102 |
| dim_product | `product_width_cm` | Float64 | 2 | 0.01% | 95 |
| dim_product | `product_category_name_english` | object | 623 | 1.89% | 71 |
| dim_product | `is_shipping_valid` | boolean | 2 | 0.01% | 2 |
| dim_geolocation | `geolocation_lat` | object | 167 | 0.87% | 18,993 |
| dim_geolocation | `geolocation_lng` | object | 167 | 0.87% | 18,995 |
| dim_geolocation | `geolocation_city` | object | 167 | 0.87% | 5,828 |
| dim_geolocation | `geolocation_state` | object | 167 | 0.87% | 27 |
| bridge_review | `review_comment_title` | str | 87,123 | 88.29% | 4,525 |
| bridge_review | `review_comment_message` | str | 57,898 | 58.68% | 36,060 |
| fact_order_summary | `item_revenue` | Float64 | 775 | 0.78% | 31,074 |
| fact_order_summary | `payment_collected` | Float64 | 1 | 0.00% | 27,979 |
| fact_order_summary | `avg_review_score` | Float64 | 768 | 0.77% | 5 |
| fact_order_summary | `payment_minus_item_revenue` | Float64 | 776 | 0.78% | 309 |
| fact_order_summary | `delivery_delay_days` | Int64 | 2,965 | 2.98% | 144 |
| fact_order_summary | `is_late_vs_estimate` | boolean | 2,965 | 2.98% | 2 |

## 5. Validation results

| Check | Description | Severity | Result | Observed | Expected |
|---|---|---|---|---|---|
| dim_customer.pk_not_null | primary key ['customer_id'] has no NULL | error | PASS | 0 rows with NULL key | - |
| dim_customer.pk_unique | primary key ['customer_id'] is unique | error | PASS | 0 duplicate key rows out of 99,441 | 0 |
| dim_customer_identity.pk_not_null | primary key ['customer_unique_id'] has no NULL | error | PASS | 0 rows with NULL key | - |
| dim_customer_identity.pk_unique | primary key ['customer_unique_id'] is unique | error | PASS | 0 duplicate key rows out of 96,096 | 0 |
| dim_product.pk_not_null | primary key ['product_id'] has no NULL | error | PASS | 0 rows with NULL key | - |
| dim_product.pk_unique | primary key ['product_id'] is unique | error | PASS | 0 duplicate key rows out of 32,951 | 0 |
| dim_product_category.pk_not_null | primary key ['product_category_name'] has no NULL | error | PASS | 0 rows with NULL key | - |
| dim_product_category.pk_unique | primary key ['product_category_name'] is unique | error | PASS | 0 duplicate key rows out of 74 | 0 |
| dim_seller.pk_not_null | primary key ['seller_id'] has no NULL | error | PASS | 0 rows with NULL key | - |
| dim_seller.pk_unique | primary key ['seller_id'] is unique | error | PASS | 0 duplicate key rows out of 3,095 | 0 |
| dim_order.pk_not_null | primary key ['order_id'] has no NULL | error | PASS | 0 rows with NULL key | - |
| dim_order.pk_unique | primary key ['order_id'] is unique | error | PASS | 0 duplicate key rows out of 99,441 | 0 |
| dim_geolocation.pk_not_null | primary key ['geolocation_zip_code_prefix'] has no NULL | error | PASS | 0 rows with NULL key | - |
| dim_geolocation.pk_unique | primary key ['geolocation_zip_code_prefix'] is unique | error | PASS | 0 duplicate key rows out of 19,177 | 0 |
| fact_order_item.pk_not_null | primary key ['order_id', 'order_item_id'] has no NULL | error | PASS | 0 rows with NULL key | - |
| fact_order_item.pk_unique | primary key ['order_id', 'order_item_id'] is unique | error | PASS | 0 duplicate key rows out of 112,650 | 0 |
| bridge_payment.pk_not_null | primary key ['order_id', 'payment_sequential'] has no NULL | error | PASS | 0 rows with NULL key | - |
| bridge_payment.pk_unique | primary key ['order_id', 'payment_sequential'] is unique | error | PASS | 0 duplicate key rows out of 103,886 | 0 |
| bridge_review.pk_not_null | primary key ['order_id'] has no NULL | error | PASS | 0 rows with NULL key | - |
| bridge_review.pk_unique | primary key ['order_id'] is unique | error | PASS | 0 duplicate key rows out of 98,673 | 0 |
| fact_order_summary.pk_not_null | primary key ['order_id'] has no NULL | error | PASS | 0 rows with NULL key | - |
| fact_order_summary.pk_unique | primary key ['order_id'] is unique | error | PASS | 0 duplicate key rows out of 99,441 | 0 |
| order.customer.no_orphans | FK customer_id -> customer_id | error | PASS | 0 orphan child rows of 99,441 (customer_id) | 0 |
| item.order.no_orphans | FK order_id -> order_id | error | PASS | 0 orphan child rows of 112,650 (order_id) | 0 |
| item.product.no_orphans | FK product_id -> product_id | error | PASS | 0 orphan child rows of 112,650 (product_id) | 0 |
| item.seller.no_orphans | FK seller_id -> seller_id | error | PASS | 0 orphan child rows of 112,650 (seller_id) | 0 |
| payment.order.no_orphans | FK order_id -> order_id | error | PASS | 0 orphan child rows of 103,886 (order_id) | 0 |
| review.order.no_orphans | FK order_id -> order_id | error | PASS | 0 orphan child rows of 98,673 (order_id) | 0 |
| summary.order.no_orphans | FK order_id -> order_id | error | PASS | 0 orphan child rows of 99,441 (order_id) | 0 |
| product.category.no_orphans | FK product_category_name -> product_category_name | error | PASS | 0 orphan child rows of 32,951 (product_category_name) | 0 |
| customer.geolocation.no_orphans | FK customer_zip_code_prefix -> geolocation_zip_code_prefix | error | PASS | 0 orphan child rows of 99,441 (customer_zip_code_prefix) | 0 |
| seller.geolocation.no_orphans | FK seller_zip_code_prefix -> geolocation_zip_code_prefix | error | PASS | 0 orphan child rows of 3,095 (seller_zip_code_prefix) | 0 |
| dim_order.order_id.not_null | order_id is never NULL | error | PASS | 0 NULL of 99,441 | - |
| dim_order.customer_id.not_null | customer_id is never NULL | error | PASS | 0 NULL of 99,441 | - |
| dim_order.order_status.not_null | order_status is never NULL | error | PASS | 0 NULL of 99,441 | - |
| dim_order.order_purchase_timestamp.not_null | order_purchase_timestamp is never NULL | error | PASS | 0 NULL of 99,441 | - |
| fact_order_item.order_id.not_null | order_id is never NULL | error | PASS | 0 NULL of 112,650 | - |
| fact_order_item.order_item_id.not_null | order_item_id is never NULL | error | PASS | 0 NULL of 112,650 | - |
| fact_order_item.product_id.not_null | product_id is never NULL | error | PASS | 0 NULL of 112,650 | - |
| fact_order_item.seller_id.not_null | seller_id is never NULL | error | PASS | 0 NULL of 112,650 | - |
| fact_order_item.price.not_null | price is never NULL | error | PASS | 0 NULL of 112,650 | - |
| fact_order_item.price.range | price within [0.0001, 1000000000000.0] | error | PASS | 0 out-of-range values of 112,650 | 0 |
| fact_order_item.freight_value.range | freight_value within [0.0, 1000000000000.0] | error | PASS | 0 out-of-range values of 112,650 | 0 |
| bridge_review.review_score.range | review_score within [1, 5] | error | PASS | 0 out-of-range values of 98,673 | 0 |
| bridge_payment.payment_value.range | payment_value within [0.0001, 1000000000000.0] | warning | WARN | 9 out-of-range values of 103,886 | 0 |
| dim_order.row_count | row count dim_order - orders are never dropped | error | PASS | 99,441 | 99,441 |
| dim_customer.row_count | row count dim_customer | error | PASS | 99,441 | 99,441 |
| dim_product.row_count | row count dim_product | error | PASS | 32,951 | 32,951 |
| dim_seller.row_count | row count dim_seller | error | PASS | 3,095 | 3,095 |
| fact_order_item.row_count | row count fact_order_item | error | PASS | 112,650 | 112,650 |
| bridge_payment.row_count | row count bridge_payment | error | PASS | 103,886 | 103,886 |
| bridge_review.row_count | row count bridge_review - deliberate collapse to one review per order | warning | PASS | 98,673 | 98,673 |
| dim_geolocation.row_count | row count dim_geolocation - grain = source ZIPs union ZIPs referenced by an address | error | PASS | 19,177 | 19,177 |
| recon.item_revenue | sum(price + freight) equals source total | error | PASS | 15,843,553.24 | 15,843,553.24 |
| recon.payment_total | sum(payment_value) equals source total | error | PASS | 16,008,872.120000001 | 16,008,872.120000001 |
| recon.summary_item_revenue | fact_order_summary item_revenue equals fact_order_item total | error | PASS | 15,843,553.240000015 | 15,843,553.24 |
| recon.summary_payment | fact_order_summary payment_collected equals bridge_payment total | error | PASS | 16,008,872.120000001 | 16,008,872.120000001 |
| recon.summary_count | fact_order_summary has one row per order | error | PASS | 99,441 | 99,441 |
| recon.orders_without_items | orders flagged without line items equals source count | error | PASS | 775 | 775 |
| recon.orders_without_payments | orders flagged without payments equals source count | error | PASS | 1 | 1 |
| recon.unknown_categories | products with no category were tagged 'unknown', not dropped | error | PASS | 610 | 610 |
| quality.product_untranslated_categories | every real product category has an English translation | warning | WARN | 13 product row(s) whose category has no English label | 0 (categories missing from the translation file) |
| quality.geolocation_usable | every ZIP has a usable centroid | warning | WARN | 167 ZIP(s) with NULL centroid (all coordinates quarantined or absent) | 0 |
| quality.geolocation_address_coverage | every address ZIP has geolocation source data | warning | WARN | 162 ZIP prefix(es) referenced by customers/sellers but absent from the geolocation source (placeholder row, NULL centroid) | 0 (gap present in the source data, not created by this pipeline) |

## 6. Unresolved issues

1. payment_value within [0.0001, 1000000000000.0] -> 9 out-of-range values of 103,886 (expected 0)
2. every real product category has an English translation -> 13 product row(s) whose category has no English label (expected 0 (categories missing from the translation file))
3. every ZIP has a usable centroid -> 167 ZIP(s) with NULL centroid (all coordinates quarantined or absent) (expected 0)
4. every address ZIP has geolocation source data -> 162 ZIP prefix(es) referenced by customers/sellers but absent from the geolocation source (placeholder row, NULL centroid) (expected 0 (gap present in the source data, not created by this pipeline))
5. 551 source rows were a second review on the same order and were collapsed to one (latest kept); 202 orders had conflicting scores.
6. 33 geolocation coordinates were outside Brazil and were moved to data/quarantine/ (ZIP kept, centroid from valid rows).
7. 775 orders have no line items: item_revenue is NULL for them, so GMV built on fact_order_item omits them by construction.
8. 8 orders are marked 'delivered' with no delivery timestamp - excluded from delivery-delay metrics (NULL kept).
9. 1,382 orders have a non-monotonic date sequence (approval / carrier / delivery out of order). They are kept and flagged with has_impossible_date_sequence; an audit copy is in data/quarantine/quarantine_invalid_dates.csv.
10. 11 payment rows have a non-positive value or instalment count; kept and flagged, excluded from payment-mix rates.
11. 13 product rows belong to a category with no English translation (English label left NULL, not guessed).
12. 610 products had no category and were tagged 'unknown' so they survive group-by and joins.
