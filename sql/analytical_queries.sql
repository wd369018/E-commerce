-- ==========================================================================
-- ShopScope - analytical views and query library
-- ==========================================================================
-- TWO SECTIONS:
--   1. Everything above the marker  -> CREATE VIEW statements (applied by
--      src.database.build_database(), which executes this file after schema.sql)
--   2. Everything below the marker  -> named queries extracted by
--      src.queries. Each block is `-- @query: name` ... `-- @end`.
--
-- JOIN DISCIPLINE (why the SQL looks the way it does)
-- ---------------------------------------------------
-- * fact_order_item is 1 row per UNIT; dim_order / dim_product / dim_seller are
--   their parents (many:1) -> joining them never multiplies rows.
-- * bridge_payment and bridge_review are MANY rows per order. They are
--   aggregated to order level FIRST, or joined only in queries whose grain is
--   already order-level, and NEVER combined with each other.
-- * fact_order_summary is the sanctioned one-row-per-order surface: any measure
--   that needs money + status + review together goes through it.
-- * Verified in tests/test_join_behavior.py.
--
-- MEASURE DEFINITIONS
-- -------------------
--   merchandise sales = SUM(fact_order_item.price)   - freight excluded
--   freight charges   = SUM(fact_order_item.freight_value) - kept separate
--   payment value     = SUM(bridge_payment.payment_value)  - cash, not sales
--
--   WARNING: fact_order_summary.item_revenue is the GROSS item value
--   (price + freight) produced by the ETL. It must NOT be used as merchandise
--   sales. Order-level sales are always derived from fact_order_item.price via
--   vw_order_item_rollup.
--
-- Each view documents its ELIGIBLE POPULATION (the rows a KPI is computed on
-- and, where relevant, which rows are excluded). vw_kpi_population reports the
-- live row counts of those populations.
-- ==========================================================================

DROP VIEW IF EXISTS vw_kpi_population;
DROP VIEW IF EXISTS vw_order_item_rollup;
DROP VIEW IF EXISTS vw_weight_vs_freight;
DROP VIEW IF EXISTS vw_seller_performance;
DROP VIEW IF EXISTS vw_review_monthly;
DROP VIEW IF EXISTS vw_review_score_distribution;
DROP VIEW IF EXISTS vw_delivery_summary;
DROP VIEW IF EXISTS vw_delivery_performance_monthly;
DROP VIEW IF EXISTS vw_installment_analysis;
DROP VIEW IF EXISTS vw_payment_method_distribution;
DROP VIEW IF EXISTS vw_repeat_customer_summary;
DROP VIEW IF EXISTS vw_customer_repeat_purchase;
DROP VIEW IF EXISTS vw_state_customer_order_distribution;
DROP VIEW IF EXISTS vw_category_performance;
DROP VIEW IF EXISTS vw_order_status_distribution;
DROP VIEW IF EXISTS vw_monthly_order_volume;
DROP VIEW IF EXISTS vw_monthly_sales;

-- --------------------------------------------------------------------------
-- 0. Per-order item rollup (the money building block)
--    Population: every order that has at least one fact_order_item row
--    (98,666 of 99,441). Orders with no items are simply absent, so order-level
--    views that need them must use a LEFT JOIN and treat the gap explicitly.
--
--    NOTE on naming: fact_order_summary.item_revenue is the GROSS value of the
--    items (price + freight). Merchandise sales in this analytics layer are
--    always the sum of fact_order_item.price ALONE, so this view is the single
--    place order-level merchandise sales / freight are derived.
-- --------------------------------------------------------------------------
CREATE VIEW vw_order_item_rollup AS
SELECT
    order_id,
    COUNT(*)                        AS item_lines,
    COUNT(DISTINCT product_id)      AS distinct_products,
    ROUND(SUM(price), 2)            AS merchandise_sales,
    ROUND(SUM(freight_value), 2)    AS freight_charges,
    ROUND(SUM(price + freight_value), 2) AS gross_item_value
FROM fact_order_item
GROUP BY order_id;

-- --------------------------------------------------------------------------
-- 1. Monthly merchandise sales
--    Population: ALL 112,650 fact_order_item rows (1 row = 1 unit). No order
--    is excluded, so merchandise sales always reconcile to the source total.
--    `*_revenue_status` variants restrict to delivered/shipped orders for
--    sales reporting that must ignore cancelled/unavailable orders.
--    Join: fact_order_item -> dim_order is many:1, no fan-out.
-- --------------------------------------------------------------------------
CREATE VIEW vw_monthly_sales AS
SELECT
    substr(o.order_purchase_timestamp, 1, 7)              AS sales_month,
    COUNT(DISTINCT i.order_id)                            AS orders,
    COUNT(*)                                              AS item_lines,
    ROUND(SUM(i.price), 2)                                AS merchandise_sales,
    ROUND(SUM(i.freight_value), 2)                        AS freight_charges,
    ROUND(SUM(i.price + i.freight_value), 2)              AS gross_item_value,
    ROUND(AVG(i.price), 2)                                AS avg_unit_price,
    SUM(CASE WHEN o.is_revenue_status = 1 THEN 1 ELSE 0 END) AS item_lines_revenue_status,
    ROUND(SUM(CASE WHEN o.is_revenue_status = 1 THEN i.price ELSE 0 END), 2)
                                                          AS merchandise_sales_revenue_status
FROM fact_order_item i
JOIN dim_order o ON o.order_id = i.order_id
GROUP BY substr(o.order_purchase_timestamp, 1, 7);

-- --------------------------------------------------------------------------
-- 2. Monthly order volume by status
--    Population: ALL 99,441 dim_order rows.
-- --------------------------------------------------------------------------
CREATE VIEW vw_monthly_order_volume AS
SELECT
    substr(order_purchase_timestamp, 1, 7) AS sales_month,
    order_status,
    COUNT(*)                               AS orders,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY substr(order_purchase_timestamp, 1, 7)), 2)
                                           AS pct_of_month
FROM dim_order
GROUP BY substr(order_purchase_timestamp, 1, 7), order_status;

-- --------------------------------------------------------------------------
-- 3. Order status distribution
--    Population: ALL orders, joined 1:1 to fact_order_summary so a single
--    order can never be counted twice.
-- --------------------------------------------------------------------------
CREATE VIEW vw_order_status_distribution AS
SELECT
    o.order_status,
    COUNT(*)                                   AS orders,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct_of_orders,
    ROUND(COALESCE(SUM(ro.merchandise_sales), 0), 2) AS merchandise_sales,
    ROUND(COALESCE(SUM(ro.freight_charges), 0), 2)   AS freight_charges,
    ROUND(SUM(fs.payment_collected), 2)        AS payment_collected,
    ROUND(AVG(fs.n_items), 2)                  AS avg_items_per_order
FROM dim_order o
JOIN fact_order_summary fs ON fs.order_id = o.order_id
LEFT JOIN vw_order_item_rollup ro ON ro.order_id = o.order_id
GROUP BY o.order_status;

-- --------------------------------------------------------------------------
-- 4. Product category performance
--    Population: units from fact_order_item joined to dim_product, then
--    aggregated to (category, order) BEFORE the review is attached - so an
--    order containing 5 units is weighted once per category for the review
--    average, not 5 times. Reviews are 1 row per order: no fan-out.
-- --------------------------------------------------------------------------
CREATE VIEW vw_category_performance AS
WITH category_order AS (
    SELECT
        p.product_category_name AS category_name,
        i.order_id,
        COUNT(*)                 AS units,
        SUM(i.price)             AS merchandise_sales,
        SUM(i.freight_value)     AS freight_charges
    FROM fact_order_item i
    JOIN dim_product p ON p.product_id = i.product_id
    GROUP BY p.product_category_name, i.order_id
),
category_order_review AS (
    SELECT
        co.category_name,
        co.order_id,
        co.units,
        co.merchandise_sales,
        co.freight_charges,
        r.review_score
    FROM category_order co
    LEFT JOIN bridge_review r ON r.order_id = co.order_id
)
SELECT
    c.product_category_name,
    c.product_category_name_english,
    SUM(cr.units)                          AS units,
    COUNT(DISTINCT cr.order_id)            AS orders,
    ROUND(SUM(cr.merchandise_sales), 2)    AS merchandise_sales,
    ROUND(SUM(cr.freight_charges), 2)      AS freight_charges,
    ROUND(AVG(cr.merchandise_sales), 2)    AS avg_sales_per_order,
    ROUND(AVG(cr.review_score), 3)         AS avg_review_score,
    COUNT(cr.review_score)                 AS reviewed_orders
FROM category_order_review cr
JOIN dim_product_category c ON c.product_category_name = cr.category_name
GROUP BY c.product_category_name, c.product_category_name_english;

-- --------------------------------------------------------------------------
-- 5. State-wise customer and order distribution
--    Population: customers joined to their orders 1:1, then to
--    fact_order_summary 1:1 - three tables, still exactly one row per order.
-- --------------------------------------------------------------------------
CREATE VIEW vw_state_customer_order_distribution AS
SELECT
    c.customer_state,
    COUNT(DISTINCT c.customer_unique_id)       AS customers,
    COUNT(DISTINCT c.customer_id)              AS order_placements,
    COUNT(DISTINCT o.order_id)                 AS orders,
    ROUND(100.0 * COUNT(DISTINCT o.order_id) / SUM(COUNT(DISTINCT o.order_id)) OVER (), 2)
                                               AS pct_of_orders,
    ROUND(COALESCE(SUM(ro.merchandise_sales), 0), 2) AS merchandise_sales,
    ROUND(COALESCE(SUM(ro.freight_charges), 0), 2)   AS freight_charges,
    ROUND(SUM(fs.payment_collected), 2)        AS payment_collected
FROM dim_customer c
JOIN dim_order o ON o.customer_id = c.customer_id
JOIN fact_order_summary fs ON fs.order_id = o.order_id
LEFT JOIN vw_order_item_rollup ro ON ro.order_id = o.order_id
GROUP BY c.customer_state;

-- --------------------------------------------------------------------------
-- 6. Customer repeat purchases (customer grain)
--    Population: ALL 99,441 placements grouped by customer_unique_id ->
--    96,096 real customers. customer_unique_id is used, never customer_id.
-- --------------------------------------------------------------------------
CREATE VIEW vw_customer_repeat_purchase AS
SELECT
    c.customer_unique_id,
    MAX(c.customer_state)                       AS customer_state,
    COUNT(DISTINCT o.order_id)                  AS orders,
    COUNT(DISTINCT o.customer_id)               AS placement_ids,
    MIN(o.order_purchase_timestamp)             AS first_order_at,
    MAX(o.order_purchase_timestamp)             AS last_order_at,
    ROUND(SUM(ro.merchandise_sales), 2)         AS merchandise_spend,
    ROUND(SUM(fs.payment_collected), 2)         AS cash_collected,
    COUNT(fs.avg_review_score)                  AS reviews,
    ROUND(AVG(fs.avg_review_score), 3)          AS avg_review_score
FROM dim_customer c
JOIN dim_order o ON o.customer_id = c.customer_id
LEFT JOIN vw_order_item_rollup ro ON ro.order_id = o.order_id
LEFT JOIN fact_order_summary fs ON fs.order_id = o.order_id
GROUP BY c.customer_unique_id;

-- --------------------------------------------------------------------------
-- 7. Repeat-purchase segments (derived from view 6)
-- --------------------------------------------------------------------------
CREATE VIEW vw_repeat_customer_summary AS
SELECT
    CASE
        WHEN orders = 1 THEN '1 order'
        WHEN orders = 2 THEN '2 orders'
        WHEN orders = 3 THEN '3 orders'
        ELSE '4+ orders'
    END                          AS segment,
    COUNT(*)                     AS customers,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct_customers,
    SUM(orders)                  AS total_orders,
    ROUND(AVG(orders), 3)        AS avg_orders,
    ROUND(SUM(COALESCE(merchandise_spend, 0)), 2) AS merchandise_spend
FROM vw_customer_repeat_purchase
GROUP BY CASE
        WHEN orders = 1 THEN '1 order'
        WHEN orders = 2 THEN '2 orders'
        WHEN orders = 3 THEN '3 orders'
        ELSE '4+ orders'
    END;

-- --------------------------------------------------------------------------
-- 8. Payment method distribution
--    Population: ALL 103,886 bridge_payment rows (individual payment attempts,
--    NOT orders). An order paid with a voucher + credit card appears twice, so
--    `orders` per row does not sum to the order total - documented as such.
--    Never joined to fact_order_item here.
-- --------------------------------------------------------------------------
CREATE VIEW vw_payment_method_distribution AS
SELECT
    payment_type,
    COUNT(*)                                        AS payments,
    COUNT(DISTINCT order_id)                        AS orders_touched,
    ROUND(SUM(payment_value), 2)                    AS payment_value,
    ROUND(100.0 * SUM(payment_value) / SUM(SUM(payment_value)) OVER (), 2)
                                                    AS pct_of_value,
    ROUND(AVG(payment_value), 2)                    AS avg_payment_value,
    SUM(CASE WHEN is_valid_payment_value = 0 THEN 1 ELSE 0 END) AS non_positive_values
FROM bridge_payment
GROUP BY payment_type;

-- --------------------------------------------------------------------------
-- 9. Installment analysis
--    Population: the same 103,886 payment rows. Bucketed installments; the
--    row count of `orders_touched` double-counts orders that used more than
--    one instalment plan (they are separate payment records by definition).
-- --------------------------------------------------------------------------
CREATE VIEW vw_installment_analysis AS
SELECT
    CASE
        WHEN payment_installments IS NULL      THEN 'unknown'
        WHEN payment_installments <= 1         THEN '1 (full payment)'
        WHEN payment_installments <= 3         THEN '2-3'
        WHEN payment_installments <= 6         THEN '4-6'
        WHEN payment_installments <= 12        THEN '7-12'
        ELSE '13+'
    END AS installment_bucket,
    MIN(CASE
        WHEN payment_installments IS NULL      THEN 6
        WHEN payment_installments <= 1         THEN 1
        WHEN payment_installments <= 3         THEN 2
        WHEN payment_installments <= 6         THEN 3
        WHEN payment_installments <= 12        THEN 4
        ELSE 5
    END) AS bucket_order,
    COUNT(*)                        AS payments,
    COUNT(DISTINCT order_id)        AS orders_touched,
    ROUND(AVG(payment_value), 2)    AS avg_payment_value,
    ROUND(SUM(payment_value), 2)    AS total_payment_value,
    ROUND(AVG(payment_installments), 2) AS avg_installments
FROM bridge_payment
GROUP BY CASE
        WHEN payment_installments IS NULL      THEN 'unknown'
        WHEN payment_installments <= 1         THEN '1 (full payment)'
        WHEN payment_installments <= 3         THEN '2-3'
        WHEN payment_installments <= 6         THEN '4-6'
        WHEN payment_installments <= 12        THEN '7-12'
        ELSE '13+'
    END;

-- --------------------------------------------------------------------------
-- 10. Delivery performance (monthly)
--     Population: the 96,476 orders with a non-null delivery timestamp.
--     The 2,965 orders with no delivery date are EXCLUDED from every rate
--     here (they are not "on time", they are unknown) - see vw_delivery_summary
--     for how many were left out.
-- --------------------------------------------------------------------------
CREATE VIEW vw_delivery_performance_monthly AS
SELECT
    substr(order_purchase_timestamp, 1, 7) AS sales_month,
    COUNT(*)                               AS delivered_orders,
    ROUND(AVG(delivery_delay_days), 2)     AS avg_days_purchase_to_delivery,
    ROUND(AVG(julianday(order_delivered_customer_date)
              - julianday(order_estimated_delivery_date)), 2) AS avg_days_vs_estimate,
    SUM(CASE WHEN is_late_vs_estimate = 0 THEN 1 ELSE 0 END) AS on_time_orders,
    SUM(CASE WHEN is_late_vs_estimate = 1 THEN 1 ELSE 0 END) AS late_orders,
    ROUND(100.0 * SUM(CASE WHEN is_late_vs_estimate = 0 THEN 1 ELSE 0 END) / COUNT(*), 2)
                                                             AS on_time_pct,
    ROUND(MAX(delivery_delay_days), 0)                       AS max_days_purchase_to_delivery
FROM dim_order
WHERE order_delivered_customer_date IS NOT NULL
GROUP BY substr(order_purchase_timestamp, 1, 7);

-- --------------------------------------------------------------------------
-- 10b. Delivery summary: shows the eligible population explicitly.
-- --------------------------------------------------------------------------
CREATE VIEW vw_delivery_summary AS
SELECT
    order_status,
    COUNT(*)                                                        AS orders,
    SUM(missing_delivery_date)                                      AS excluded_no_delivery_date,
    SUM(CASE WHEN order_delivered_customer_date IS NOT NULL THEN 1 ELSE 0 END)
                                                                    AS eligible_delivered,
    ROUND(AVG(CASE WHEN order_delivered_customer_date IS NOT NULL
                   THEN delivery_delay_days END), 2)                AS avg_delivery_days,
    SUM(CASE WHEN is_late_vs_estimate = 1 THEN 1 ELSE 0 END)        AS late_orders,
    SUM(CASE WHEN is_late_vs_estimate = 0 THEN 1 ELSE 0 END)        AS on_time_orders,
    ROUND(100.0 * SUM(CASE WHEN is_late_vs_estimate = 0 THEN 1 ELSE 0 END)
          / NULLIF(SUM(CASE WHEN is_late_vs_estimate IS NOT NULL THEN 1 ELSE 0 END), 0), 2)
                                                                    AS on_time_pct,
    ROUND(AVG(CASE WHEN delivered_after_estimate_days IS NOT NULL
                   THEN delivered_after_estimate_days END), 2)      AS avg_days_vs_estimate
FROM (
    SELECT
        order_status,
        missing_delivery_date,
        order_delivered_customer_date,
        delivery_delay_days,
        is_late_vs_estimate,
        CASE WHEN order_delivered_customer_date IS NOT NULL
                  AND order_estimated_delivery_date IS NOT NULL
             THEN julianday(order_delivered_customer_date)
                  - julianday(order_estimated_delivery_date)
        END AS delivered_after_estimate_days
    FROM dim_order
)
GROUP BY order_status;

-- --------------------------------------------------------------------------
-- 11a. Review score distribution
--      Population: the 98,673 bridge_review rows = exactly ONE review per
--      order (the ETL collapsed 551 duplicate-order rows), so no order can be
--      double-weighted in the average.
-- --------------------------------------------------------------------------
CREATE VIEW vw_review_score_distribution AS
SELECT
    review_score,
    COUNT(*)                                  AS orders,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct_of_orders,
    ROUND(AVG(source_review_rows), 3)         AS avg_source_rows_per_order
FROM bridge_review
GROUP BY review_score;

-- --------------------------------------------------------------------------
-- 11b. Monthly review trend
-- --------------------------------------------------------------------------
CREATE VIEW vw_review_monthly AS
SELECT
    substr(o.order_purchase_timestamp, 1, 7) AS sales_month,
    COUNT(r.review_score)                    AS reviews,
    ROUND(AVG(r.review_score), 3)            AS avg_review_score,
    SUM(CASE WHEN r.review_score <= 2 THEN 1 ELSE 0 END) AS negative_reviews,
    ROUND(100.0 * SUM(CASE WHEN r.review_score <= 2 THEN 1 ELSE 0 END) / COUNT(*), 2)
                                             AS negative_pct
FROM bridge_review r
JOIN dim_order o ON o.order_id = r.order_id
GROUP BY substr(o.order_purchase_timestamp, 1, 7);

-- --------------------------------------------------------------------------
-- 12. Seller performance
--      Population: fact_order_item aggregated to (seller, order) FIRST.
--      Joining the review after that aggregation keeps exactly one row per
--      (seller, order), so a seller is scored once per order - an order with
--      6 units from one seller contributes 1 review, not 6.
-- --------------------------------------------------------------------------
CREATE VIEW vw_seller_performance AS
WITH seller_order AS (
    SELECT
        i.seller_id,
        i.order_id,
        COUNT(*)                    AS units,
        SUM(i.price)                AS merchandise_sales,
        SUM(i.freight_value)        AS freight_charges
    FROM fact_order_item i
    GROUP BY i.seller_id, i.order_id
),
seller_products AS (
    -- Must be counted across ALL of a seller's items, not summed from the
    -- per-order distinct counts (2 products + 3 products is not 2).
    SELECT
        seller_id,
        COUNT(DISTINCT product_id) AS distinct_products
    FROM fact_order_item
    GROUP BY seller_id
),
seller_order_scored AS (
    SELECT
        so.*,
        o.is_late_vs_estimate,
        r.review_score
    FROM seller_order so
    JOIN dim_order o ON o.order_id = so.order_id
    LEFT JOIN bridge_review r ON r.order_id = so.order_id
)
SELECT
    s.seller_id,
    s.seller_state,
    s.seller_city,
    COUNT(DISTINCT so.order_id)                     AS orders,
    SUM(so.units)                                   AS units,
    sp.distinct_products,
    ROUND(SUM(so.merchandise_sales), 2)             AS merchandise_sales,
    ROUND(SUM(so.freight_charges), 2)               AS freight_charges,
    ROUND(AVG(so.merchandise_sales), 2)             AS avg_sales_per_order,
    COUNT(so.review_score)                          AS reviewed_orders,
    ROUND(AVG(so.review_score), 3)                  AS avg_review_score,
    ROUND(100.0 * SUM(CASE WHEN so.is_late_vs_estimate = 1 THEN 1 ELSE 0 END)
          / NULLIF(SUM(CASE WHEN so.is_late_vs_estimate IS NOT NULL THEN 1 ELSE 0 END), 0), 2)
                                                    AS late_pct
FROM seller_order_scored so
JOIN dim_seller s ON s.seller_id = so.seller_id
JOIN seller_products sp ON sp.seller_id = so.seller_id
GROUP BY s.seller_id, s.seller_state, s.seller_city, sp.distinct_products;

-- --------------------------------------------------------------------------
-- 13. Product weight versus freight cost
--      Population: units whose product has a usable weight (weight > 0) and a
--      non-negative freight value. The `unknown/invalid` bucket keeps the
--      excluded units visible instead of silently dropping them.
--      Freight per kg is only computed when the weight is known.
-- --------------------------------------------------------------------------
CREATE VIEW vw_weight_vs_freight AS
WITH sample AS (
    SELECT
        CASE
            WHEN p.product_weight_g IS NULL OR p.product_weight_g <= 0 THEN 'unknown/invalid'
            WHEN p.product_weight_g <= 200  THEN '1. 0-200 g'
            WHEN p.product_weight_g <= 500  THEN '2. 201-500 g'
            WHEN p.product_weight_g <= 1000 THEN '3. 501-1000 g'
            WHEN p.product_weight_g <= 2000 THEN '4. 1001-2000 g'
            WHEN p.product_weight_g <= 5000 THEN '5. 2001-5000 g'
            ELSE '6. 5001+ g'
        END              AS weight_bucket,
        i.product_id,
        i.price,
        i.freight_value,
        p.product_weight_g
    FROM fact_order_item i
    JOIN dim_product p ON p.product_id = i.product_id
    WHERE i.freight_value >= 0
)
SELECT
    weight_bucket,
    COUNT(*)                                     AS units,
    COUNT(DISTINCT product_id)                   AS products,
    ROUND(AVG(product_weight_g), 1)              AS avg_weight_g,
    ROUND(AVG(price), 2)                         AS avg_unit_price,
    ROUND(AVG(freight_value), 2)                 AS avg_freight,
    ROUND(AVG(CASE WHEN product_weight_g > 0
                   THEN freight_value / (product_weight_g / 1000.0) END), 4)
                                                 AS avg_freight_per_kg,
    ROUND(100.0 * SUM(freight_value) / SUM(SUM(freight_value)) OVER (), 2)
                                                 AS pct_of_freight
FROM sample
GROUP BY weight_bucket;

-- --------------------------------------------------------------------------
-- 14. Eligible population of every KPI (live counts, nothing hardcoded)
-- --------------------------------------------------------------------------
CREATE VIEW vw_kpi_population AS
SELECT 'monthly_merchandise_sales' AS kpi,
       'fact_order_item (1 row = 1 unit); joined to dim_order 1:1 from item side' AS eligible_population,
       (SELECT COUNT(*) FROM fact_order_item) AS source_rows,
       (SELECT COUNT(*) FROM fact_order_item i JOIN dim_order o ON o.order_id = i.order_id) AS rows_used
UNION ALL
SELECT 'monthly_order_volume', 'all dim_order rows',
       (SELECT COUNT(*) FROM dim_order), (SELECT COUNT(*) FROM dim_order)
UNION ALL
SELECT 'order_status_distribution', 'all orders via fact_order_summary (1:1)',
       (SELECT COUNT(*) FROM dim_order), (SELECT COUNT(*) FROM fact_order_summary)
UNION ALL
SELECT 'category_performance', 'units grouped to (category, order) before review join',
       (SELECT COUNT(*) FROM fact_order_item),
       (SELECT COUNT(DISTINCT p.product_category_name || '|' || i.order_id)
          FROM fact_order_item i JOIN dim_product p ON p.product_id = i.product_id)
UNION ALL
SELECT 'state_customer_order_distribution', 'customers joined to their own orders (1:1)',
       (SELECT COUNT(*) FROM dim_customer),
       (SELECT COUNT(*) FROM dim_order)
UNION ALL
SELECT 'repeat_customer_analysis', 'distinct customer_unique_id (not customer_id)',
       (SELECT COUNT(*) FROM dim_customer),
       (SELECT COUNT(*) FROM dim_customer_identity)
UNION ALL
SELECT 'payment_method_distribution', 'individual payment rows, not orders',
       (SELECT COUNT(*) FROM bridge_payment), (SELECT COUNT(*) FROM bridge_payment)
UNION ALL
SELECT 'installment_analysis', 'individual payment rows',
       (SELECT COUNT(*) FROM bridge_payment), (SELECT COUNT(*) FROM bridge_payment)
UNION ALL
SELECT 'delivery_performance', 'orders with a delivery timestamp (unknown dates excluded)',
       (SELECT COUNT(*) FROM dim_order),
       (SELECT COUNT(*) FROM dim_order WHERE order_delivered_customer_date IS NOT NULL)
UNION ALL
SELECT 'review_scores', 'one review per order after ETL dedup',
       (SELECT COUNT(*) FROM dim_order), (SELECT COUNT(*) FROM bridge_review)
UNION ALL
SELECT 'seller_performance', 'units aggregated to (seller, order) before review join',
       (SELECT COUNT(*) FROM fact_order_item),
       (SELECT COUNT(DISTINCT seller_id || '|' || order_id) FROM fact_order_item)
UNION ALL
SELECT 'weight_vs_freight', 'units with known weight and non-negative freight',
       (SELECT COUNT(*) FROM fact_order_item),
       (SELECT COUNT(*) FROM fact_order_item i JOIN dim_product p ON p.product_id = i.product_id
         WHERE i.freight_value >= 0);

-- ==== QUERY LIBRARY ========================================================

-- @query: monthly_merchandise_sales
-- Monthly merchandise sales (SUM of item price), freight kept separate,
-- with month-over-month growth.
-- @end
SELECT
    sales_month,
    orders,
    item_lines,
    merchandise_sales,
    freight_charges,
    gross_item_value,
    avg_unit_price,
    merchandise_sales_revenue_status,
    ROUND(100.0 * (merchandise_sales - LAG(merchandise_sales) OVER (ORDER BY sales_month))
          / NULLIF(LAG(merchandise_sales) OVER (ORDER BY sales_month), 0), 2) AS mom_growth_pct
FROM vw_monthly_sales
ORDER BY sales_month;

-- @query: monthly_order_volume
-- Order volume per month, split by status, with each month's status mix.
-- @end
SELECT sales_month, order_status, orders, pct_of_month
FROM vw_monthly_order_volume
ORDER BY sales_month, orders DESC;

-- @query: order_status_distribution
-- Share of orders per status together with the merchandise sales and cash
-- collected attributable to each status.
-- @end
SELECT order_status, orders, pct_of_orders, merchandise_sales, freight_charges,
       payment_collected, avg_items_per_order
FROM vw_order_status_distribution
ORDER BY orders DESC;

-- @query: category_performance
-- Category league table by merchandise sales.
-- @end
SELECT product_category_name, product_category_name_english, units, orders,
       merchandise_sales, freight_charges, avg_sales_per_order, avg_review_score, reviewed_orders
FROM vw_category_performance
ORDER BY merchandise_sales DESC;

-- @query: state_customer_order_distribution
-- Customers, orders and sales per Brazilian state.
-- @end
SELECT customer_state, customers, order_placements, orders, pct_of_orders,
       merchandise_sales, freight_charges, payment_collected
FROM vw_state_customer_order_distribution
ORDER BY orders DESC;

-- @query: repeat_customer_summary
-- How many customers bought once, twice, three times, four or more times.
-- @end
SELECT segment, customers, pct_customers, total_orders, avg_orders, merchandise_spend
FROM vw_repeat_customer_summary
ORDER BY customers DESC;

-- @query: repeat_customer_detail
-- Per-customer detail (customer_unique_id grain) - top 20 by spend.
-- @end
SELECT customer_unique_id, customer_state, orders, first_order_at, last_order_at,
       merchandise_spend, cash_collected, reviews, avg_review_score
FROM vw_customer_repeat_purchase
ORDER BY merchandise_spend DESC
LIMIT 20;

-- @query: payment_method_distribution
-- Payment type mix by value and by payment-record count.
-- @end
SELECT payment_type, payments, orders_touched, payment_value, pct_of_value,
       avg_payment_value, non_positive_values
FROM vw_payment_method_distribution
ORDER BY payment_value DESC;

-- @query: installment_analysis
-- Instalment buckets: average instalment count and ticket per bucket.
-- @end
SELECT installment_bucket, bucket_order, payments, orders_touched,
       avg_installments, avg_payment_value, total_payment_value
FROM vw_installment_analysis
ORDER BY bucket_order;

-- @query: delivery_performance_monthly
-- On-time rate per month over the orders that actually have a delivery date.
-- @end
SELECT sales_month, delivered_orders, avg_days_purchase_to_delivery, avg_days_vs_estimate,
       on_time_orders, late_orders, on_time_pct, max_days_purchase_to_delivery
FROM vw_delivery_performance_monthly
ORDER BY sales_month;

-- @query: delivery_summary
-- Delivery performance per status, including how many orders were excluded
-- for having no delivery timestamp.
-- @end
SELECT order_status, orders, eligible_delivered, excluded_no_delivery_date,
       avg_delivery_days, on_time_orders, late_orders, on_time_pct, avg_days_vs_estimate
FROM vw_delivery_summary
ORDER BY orders DESC;

-- @query: review_score_distribution
-- Star rating distribution (one row per order).
-- @end
SELECT review_score, orders, pct_of_orders, avg_source_rows_per_order
FROM vw_review_score_distribution
ORDER BY review_score;

-- @query: review_monthly_trend
-- Average score and negative-review rate by purchase month.
-- @end
SELECT sales_month, reviews, avg_review_score, negative_reviews, negative_pct
FROM vw_review_monthly
ORDER BY sales_month;

-- @query: seller_performance
-- Seller league table by merchandise sales, with order-weighted review and
-- late-delivery rate.
-- @end
SELECT seller_id, seller_state, orders, units, distinct_products, merchandise_sales,
       freight_charges, avg_sales_per_order, reviewed_orders, avg_review_score, late_pct
FROM vw_seller_performance
ORDER BY merchandise_sales DESC
LIMIT 50;

-- @query: weight_vs_freight
-- Average freight cost by product weight bucket, including cost per kg.
-- @end
SELECT weight_bucket, units, products, avg_weight_g, avg_unit_price,
       avg_freight, avg_freight_per_kg, pct_of_freight
FROM vw_weight_vs_freight
ORDER BY weight_bucket;

-- @query: kpi_population
-- Live eligible-population counts behind every KPI above.
-- @end
SELECT kpi, eligible_population, source_rows, rows_used
FROM vw_kpi_population
ORDER BY kpi;
