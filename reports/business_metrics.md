# ShopScope - Business Metrics (KPI catalogue)

All figures below are computed live from `data/shopscope.db`, which is the validated SQLite build of the cleaned CSVs. Nothing is hardcoded. **Merchandise sales** = `SUM(fact_order_item.price)` and always excludes freight; **freight** and **payment cash** are separate measures.

> The database is a snapshot of the Olist export cut on **2018-10-17**. See the trend caveats in `reports/eda_findings.md`.

## Summary

| # | KPI | Value | Unit | Population |
|---|-----|-------|------|------------|
| 1 | Total unique orders | 99,441 | count | all 99,441 orders in the export |
| 2 | Total merchandise sales | R$ 13,591,643.70 | currency | all 112,650 item units across 98,666 orders |
| 3 | Total freight charges | R$ 2,251,909.54 | currency | same 112,650 item units as merchandise sales |
| 4 | Average order value (AOV) | R$ 137.75 | currency | 98,666 of 99,441 orders |
| 5 | Delivered orders | 96,478 | count | all 99,441 orders |
| 6 | Delivery completion rate | 97.02% | percent | all 99,441 orders |
| 7 | Cancellation rate | 0.63% | percent | all 99,441 orders |
| 8 | Average review score | 4.09 | ratio | 98,673 reviewed orders of 99,441 |
| 9 | On-time delivery rate | 91.89% | percent | 96,476 of 99,441 orders |
| 10 | Late delivery rate | 8.11% | percent | 96,476 of 99,441 orders |
| 11 | Average delivery duration | 12.52 days | days | 96,476 of 99,441 orders |
| 12 | Repeat customer rate | 3.12% | percent | 96,096 distinct customers |
| 13 | Average items per order | 1.14 | ratio | 98,666 item-bearing orders |
| 14 | Payment method distribution | 78.34% | percent | 103,886 payment records |

## KPI definitions

### 1. Total unique orders

- **Value:** 99,441
- **Formula:** COUNT(DISTINCT order_id)
- **Numerator:** distinct orders = 99,441
- **Denominator:** not applicable (level count) = -
- **Filters:** none - every row of dim_order
- **Eligible population:** all 99,441 orders in the export
- **Limitations:** The export is a snapshot cut on 2018-10-17. Orders created after that date do not exist, and a small number of very recent orders are still in non-terminal states (shipped/processing).
- **Notes:** One order_id = one order; an order can contain several item lines.

### 2. Total merchandise sales

- **Value:** R$ 13,591,643.70
- **Formula:** SUM(fact_order_item.price)
- **Numerator:** merchandise sales (item price only) = R$ 13,591,643.70
- **Denominator:** not applicable (sum) = -
- **Filters:** every fact_order_item row; ALL order_status values included
- **Eligible population:** all 112,650 item units across 98,666 orders
- **Limitations:** Freight is excluded (it is reported separately). Non-delivered orders (canceled/unavailable) are still included as gross merchandise value, so this is list-price sales, not recognised revenue. Prices are BRL and not inflation-adjusted.
- **Notes:** Order-level merchandise sales always come from this sum; the ETL column fact_order_summary.item_revenue is GROSS (price + freight) and is never used as sales.

### 3. Total freight charges

- **Value:** R$ 2,251,909.54
- **Formula:** SUM(fact_order_item.freight_value)
- **Numerator:** freight charged to customers = R$ 2,251,909.54
- **Denominator:** not applicable (sum) = -
- **Filters:** every fact_order_item row
- **Eligible population:** same 112,650 item units as merchandise sales
- **Limitations:** Freight is what the customer was charged, not the carrier's true cost. It is reported per item line, so it cannot be split by seller without aggregating item lines.
- **Notes:** Freight is 16.57% of merchandise sales.

### 4. Average order value (AOV)

- **Value:** R$ 137.75
- **Formula:** merchandise sales / orders that contain at least one item
- **Numerator:** merchandise sales = R$ 13,591,643.70
- **Denominator:** orders with >= 1 item = R$ 98,666.00
- **Filters:** merchandise sales exclude freight; denominator excludes orders with no line items
- **Eligible population:** 98,666 of 99,441 orders
- **Limitations:** Orders with no line items (no product record) cannot have a basket value and are excluded. Using all orders as the denominator gives R$ 136.68, which understates a real basket because empty orders are treated as R$ 0. Freight is not part of AOV.
- **Notes:** Alternative denominator: all 99,441 orders -> R$ 136.68. Including freight per order would raise the figure by about R$ 22.82.

### 5. Delivered orders

- **Value:** 96,478
- **Formula:** COUNT(orders WHERE order_status = 'delivered')
- **Numerator:** orders with status delivered = 96,478
- **Denominator:** not applicable (count) = -
- **Filters:** order_status = 'delivered'
- **Eligible population:** all 99,441 orders
- **Limitations:** 'delivered' is the status at export time. A handful of delivered orders lack a customer-delivery timestamp in the source, so the delivered count is 2 higher than the orders with a usable delivery date.
- **Notes:** 2 delivered orders have no customer-delivery date and are excluded from duration/lateness KPIs.

### 6. Delivery completion rate

- **Value:** 97.02%
- **Formula:** delivered orders / all orders
- **Numerator:** delivered orders = 96,478.00%
- **Denominator:** all orders = 99,441.00%
- **Filters:** order_status = 'delivered' over all orders
- **Eligible population:** all 99,441 orders
- **Limitations:** This is a status share, not a true cohort conversion: orders created just before the export cut are still in transit and depress the rate slightly. It also counts orders that were delivered late.

### 7. Cancellation rate

- **Value:** 0.63%
- **Formula:** canceled orders / all orders
- **Numerator:** orders with status canceled = 625.00%
- **Denominator:** all orders = 99,441.00%
- **Filters:** order_status = 'canceled' over all orders
- **Eligible population:** all 99,441 orders
- **Limitations:** Only orders that reached the marketplace are visible; requests cancelled before an order_id was created are not in the data. 'unavailable' orders (failed fulfilment) are a separate status and are not counted as cancellations.

### 8. Average review score

- **Value:** 4.09
- **Formula:** AVG(bridge_review.review_score)
- **Numerator:** sum of review scores = 403,216.0000
- **Denominator:** reviews (one per order) = 98,673.0000
- **Filters:** every review row; the ETL keeps one review per order (latest answer)
- **Eligible population:** 98,673 reviewed orders of 99,441
- **Limitations:** Reviews are voluntary and left soon after delivery, so they over-represent early experience and under-represent silent dissatisfaction. Scores of 1-2 are conventionally treated as negative.
- **Notes:** Review coverage is 99.23% of orders.

### 9. On-time delivery rate

- **Value:** 91.89%
- **Formula:** orders delivered on/before estimate / orders with a delivery date
- **Numerator:** on-time orders = 88,649.00%
- **Denominator:** evaluable orders (delivery date present) = 96,476.00%
- **Filters:** order_delivered_customer_date IS NOT NULL; is_late_vs_estimate = 0 (delivered at or before the estimated date)
- **Eligible population:** 96,476 of 99,441 orders
- **Limitations:** Denominator excludes orders with no delivery timestamp (2,965), which includes some never-delivered orders; excluding them flatters the rate. The estimate itself is a commercial promise that can be padded, so a high rate is not the same as a fast delivery.

### 10. Late delivery rate

- **Value:** 8.11%
- **Formula:** orders delivered after estimate / orders with a delivery date
- **Numerator:** late orders = 7,827.00%
- **Denominator:** evaluable orders (delivery date present) = 96,476.00%
- **Filters:** order_delivered_customer_date IS NOT NULL; is_late_vs_estimate = 1
- **Eligible population:** 96,476 of 99,441 orders
- **Limitations:** Complement of the on-time rate; it is 'late vs the promise', not an absolute service level. Orders with no delivery record are neither on time nor late and are excluded from both.
- **Notes:** 2,965 orders (2.98%) have no delivery timestamp and sit outside this KPI.

### 11. Average delivery duration

- **Value:** 12.52 days
- **Formula:** AVG(days from purchase to customer delivery)
- **Numerator:** total purchase-to-delivery days = 1,207,643.00
- **Denominator:** orders with a delivery date = 96,476.00
- **Filters:** order_delivered_customer_date IS NOT NULL
- **Eligible population:** 96,476 of 99,441 orders
- **Limitations:** Calendar days, not business days. The mean is pulled up by a long tail (up to 210 days); the median/percentiles are more robust and are shown in the findings report. Non-delivered orders are excluded.

### 12. Repeat customer rate

- **Value:** 3.12%
- **Formula:** customers with >= 2 orders / distinct customers
- **Numerator:** repeat customers = 2,997.00%
- **Denominator:** distinct customers (customer_unique_id) = 96,096.00%
- **Filters:** grouped by customer_unique_id (never customer_id)
- **Eligible population:** 96,096 distinct customers
- **Limitations:** customer_id is regenerated per order, so customer_unique_id is required to link a person's orders. The export window is only ~2 years, so customers who would return after the cut are not observed and the rate is a lower bound. Guests are pooled only by the identity key the source provides.

### 13. Average items per order

- **Value:** 1.14
- **Formula:** total item units / orders with >= 1 item
- **Numerator:** item units = 112,650.0000
- **Denominator:** orders with >= 1 item = 98,666.0000
- **Filters:** sum of fact_order_summary.n_items over orders with items
- **Eligible population:** 98,666 item-bearing orders
- **Limitations:** 'Item' is an order line (order_item_id), not a distinct product: the same product twice in one order counts twice. Orders with no items are excluded.

### 14. Payment method distribution

- **Value:** 78.34%
- **Formula:** SUM(payment_value) by payment_type / total payment value
- **Numerator:** credit-card payment value = 12,542,084.19%
- **Denominator:** total payment value = 16,008,872.12%
- **Filters:** every bridge_payment row (an order can carry several payment records)
- **Eligible population:** 103,886 payment records
- **Limitations:** Grain is the payment attempt, not the order: a voucher + card order appears twice, so shares by row do not sum to order shares. 'not_defined' and a few non-positive voucher rows exist and are retained, not silently dropped.
- **Notes:** Primary value is the credit-card share of payment value; full mix is in `detail`.

Detail table:

```
payment_type  payments  orders_touched  payment_value  pct_of_value  avg_payment_value
 credit_card     76795           76505    12542084.19         78.34             163.32
      boleto     19784           19784     2869361.27         17.92             145.03
     voucher      5775            3866      379436.87          2.37              65.70
  debit_card      1529            1528      217989.79          1.36             142.57
 not_defined         3               3           0.00          0.00               0.00
```

## Eligible populations (live counts from `vw_kpi_population`)

```
                              kpi                                                      eligible_population  source_rows  rows_used
        monthly_merchandise_sales fact_order_item (1 row = 1 unit); joined to dim_order 1:1 from item side       112650     112650
             monthly_order_volume                                                       all dim_order rows        99441      99441
        order_status_distribution                                  all orders via fact_order_summary (1:1)        99441      99441
             category_performance                    units grouped to (category, order) before review join       112650      99470
state_customer_order_distribution                               customers joined to their own orders (1:1)        99441      99441
         repeat_customer_analysis                            distinct customer_unique_id (not customer_id)        99441      96096
      payment_method_distribution                                      individual payment rows, not orders       103886     103886
             installment_analysis                                                  individual payment rows       103886     103886
             delivery_performance                orders with a delivery timestamp (unknown dates excluded)        99441      96476
                    review_scores                                     one review per order after ETL dedup        99441      98673
               seller_performance                   units aggregated to (seller, order) before review join       112650     100010
                weight_vs_freight                         units with known weight and non-negative freight       112650     112650
```

## Cross-checks

- Merchandise sales + freight vs gross item value vs cash collected are reconciled in `tests/test_join_behavior.py` and `tests/test_sql_vs_python.py`.

- Every KPI above is recomputed independently in pandas in the test-suite; the SQL figure and the pandas figure must match.
