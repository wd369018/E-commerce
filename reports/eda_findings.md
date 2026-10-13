# ShopScope - Exploratory Data Analysis and Business Findings

This report is generated from the validated SQLite database (`data/shopscope.db`) on the cleaned Olist export. Every figure below is computed live; nothing is hardcoded. Merchandise sales always exclude freight, and payment cash is reported as a separate measure.

## Scope and method

- **Source window:** 2016-09-04 to 2018-10-17.
- **Complete analysis window:** 2017-01 to 2018-08 (20 months).
- **Excluded from trends:** 2016-09, 2016-10, 2016-12, 2018-09, 2018-10 (349 orders). Missing calendar months in the export: 2016-11.
- **Sales measure:** `SUM(fact_order_item.price)` (freight excluded).
- **Identity rule:** customers are linked with `customer_unique_id`, never `customer_id`.
- **Join discipline:** many-per-order tables (payments, reviews) are aggregated to order level before being combined; verified in `tests/test_join_behavior.py`.

## Executive summary - verified findings

The list below is a short, high-confidence digest. Each item links to the detailed finding that follows.

- **Growth:** Merchandise sales rose 176.1% year over year in H1 (H1 2017 vs H1 2018), with orders up 181.9%.
- **Seasonality:** November 2017 was the peak month at R$ 1,010,271.37; Q4 2017 was 42.6% above Q3 2017.
- **Category concentration:** The top 5 of 74 categories generate 39.74% of merchandise sales; 'health_beauty' leads with 9.26%.
- **Geography:** Sao Paulo alone is 41.98% of orders and 66.61% comes from just three states (SP, RJ, MG).
- **Retention:** Only 3.12% of customers placed a second order, yet they generated 5.73% of merchandise sales.
- **Payments:** Total payment value R$ 16,008,872.12 across 103,886 payment records.
- **Installments:** 49.42% of payment records use more than one installment.
- **Delivery and satisfaction:** On-time orders average 4.294/5 over 88,168 reviews; late orders average 2.565/5 over 7,662 reviews (difference 1.729 points).
- **Seller concentration:** The top 1% of sellers generate 26.07% of sales and the top 10% generate 67.56%.
- **Seller lateness:** Among 634 sellers with >= 30 orders, the mean late rate is 7.8% but the 90th percentile reaches 14.6%.
- **Freight economics:** Freight is 16.57% of merchandise sales; freight per kg falls from R$ 112.70 to R$ 4.03 from the lightest to the heaviest bucket.
- **Data quality:** The largest gap is delivery dates: 2,965 orders lack one and sit outside every delivery KPI; 775 orders carry no line items.


## Sales trends

#### Like-for-like growth: H1 2018 versus H1 2017

- **Business question:** Is the marketplace growing once the incomplete start and end of the export are removed?
- **Calculation method:** Sum merchandise sales (SUM of item price, freight excluded) and order counts for the two fully-observed half-years Jan-Jun 2017 and Jan-Jun 2018, then compute the year-over-year percentage change.
- **Actual result:** H1 2017 = R$ 2,040,997.16 across 14,611 orders; H1 2018 = R$ 5,635,712.25 across 41,187 orders. YoY merchandise-sales growth = 176.1%; YoY order growth = 181.9%.
- **Period and population:** Jan-Jun 2017 and Jan-Jun 2018; all 99,441 orders in the export, item-bearing lines only for sales
- **Interpretation:** The two half-years are calendar-matched and fully covered, so this is a fair like-for-like comparison. Sales grew 176.1% while orders grew 181.9%: order volume grew faster than sales, so the average basket compressed modestly. The two components point to different levers, so both are reported.
- **Recommendation:** Treat H1 2018 as the run-rate for planning rather than the partial tail of 2018. Track the sales-per-order and orders-per-customer components separately to see which is driving growth.
- **Limitations:** Two half-years are a short series and the platform's customer mix and pricing changed between them. No inflation adjustment is applied to BRL figures.
- **Correlation vs causation:** Growth is a descriptive time comparison, not a causal model: no intervention is isolated here.
- **Figures:** `figures/monthly_sales_trend.png`, `figures/quarterly_sales.png`

#### Strongest trading month and Q4-2017 acceleration

- **Business question:** When does demand peak, and how strong is the Q4 seasonal lift?
- **Calculation method:** Rank complete months by merchandise sales; compare Q4 2017 (Oct-Dec) with Q3 2017 (Jul-Sep). Only months inside the full-volume window are ranked.
- **Actual result:** Peak month = 2017-11 with R$ 1,010,271.37 and 7,544 orders (lowest complete month: 2017-01). Q4 2017 sales = R$ 2,418,404.97 vs Q3 2017 = R$ 1,696,404.85, a quarter-on-quarter change of 42.6%.
- **Period and population:** complete months 2017-01..2018-08; quarterly figures for 2017Q3-Q4
- **Interpretation:** The November spike is consistent with Black-Friday promotional demand; the Q4 lift being concentrated in a single month suggests event-driven rather than broad secular demand.
- **Recommendation:** Plan logistics and inventory capacity around the Q4 event peak. Because the lift is event-driven, provision for the peak months rather than an even seasonal ramp.
- **Limitations:** One observed Q4 (2017) cannot separate a recurring seasonal effect from a one-off campaign; November 2017 may also reflect platform maturity rather than a seasonal pattern.
- **Correlation vs causation:** The association with Black Friday is plausible context, not a controlled measurement: the dataset does not include marketing spend or campaign flags.
- **Figures:** `figures/monthly_sales_trend.png`, `figures/quarterly_sales.png`

#### Incomplete periods are excluded from trends but always reported

- **Business question:** Which parts of the timeline cannot support trend conclusions?
- **Calculation method:** Flag the first and last calendar months as partial, and treat months before the full-volume window (2017-01) and after it (2018-08) as ramp-up / truncated; count them explicitly.
- **Actual result:** Data spans 2016-09-04 to 2018-10-17. 20 complete months are used; 5 months are excluded (2016-09, 2016-10, 2016-12, 2018-09, 2018-10) holding 349 orders and R$ 49,930.92 of sales. Calendar months with no orders at all: 2016-11. 
- **Period and population:** every month in the export
- **Interpretation:** The 2016 months are platform ramp-up with negligible volume, and September-October 2018 are cut off by the export date. Including them would distort trend lines and averages.
- **Recommendation:** Report trend metrics on the complete window and label the ramp-up/tail months as such in the dashboard; never chart the partial tail as if it were a collapse in demand.
- **Limitations:** The completeness window is a judgement anchored to observed volume, not an external calendar of the business; revisit it if a longer export becomes available.
- **Figures:** `figures/monthly_sales_trend.png`


## Product categories

#### Sales are concentrated in a few categories

- **Business question:** Is category revenue diversified or dependent on a handful of categories?
- **Calculation method:** Rank categories by merchandise sales, compute the share of the top 5 and the leading category.
- **Actual result:** 74 categories. Largest = 'health_beauty' with R$ 1,258,681.34 (9.26% of sales); smallest non-zero = 'security_and_services' with R$ 283.29 (0.002% of sales). Top 5 categories = 39.74% of all merchandise sales.
- **Period and population:** one row per (category, order) pair - 99,470 pairs across all item-bearing orders
- **Interpretation:** Revenue depends on a narrow set of categories, so supplier or logistics disruption in those categories has an outsized effect on the platform.
- **Recommendation:** Protect continuity for the top categories (inventory, seller relationships) while treating long-tail categories as optional growth experiments rather than core revenue.
- **Limitations:** Sales are list prices and include non-delivered orders; category names come from the product catalogue and a few products have no category mapping.
- **Correlation vs causation:** Concentration is a structural fact, not a causal finding.
- **Figures:** `figures/category_performance.png`

#### Category satisfaction and punctuality vary widely

- **Business question:** Which product categories satisfy customers, and which run late?
- **Calculation method:** Restrict to categories with at least 100 orders; compare average review score and the category late-delivery rate (share of delivered orders past the estimated date, computed once per order).
- **Actual result:** Best-reviewed: 'books_general_interest' 4.463/5; worst-reviewed: 'office_furniture' 3.618/5. Highest late rate: 'audio' 12.9%; lowest: 'air_conditioning' 4.1%. Spearman correlation between a category's sales and its review score = -0.022.
- **Period and population:** 52 categories with >= 100 orders
- **Interpretation:** Some low-volume or logistically awkward categories consistently under-perform on satisfaction and punctuality, while high-volume categories are not automatically the worst-rated.
- **Recommendation:** Investigate the worst-reviewed and latest categories for handling/packaging or seller concentration before spending on demand; review-score gains compound through repeat purchase and word of mouth.
- **Limitations:** Category review averages can hide seller-level problems, and the 100-order threshold is a stability rule, not a significance test.
- **Correlation vs causation:** The category sales-vs-score correlation (-0.022) is an association across categories; it does not show that selling more causes lower scores.
- **Figures:** `figures/category_performance.png`


## Geography

#### Demand is concentrated in the South-East

- **Business question:** How geographically concentrated is the customer base?
- **Calculation method:** Count orders and distinct customers per customer_state; compute the top-state and top-3-state shares.
- **Actual result:** Sao Paulo (SP) = 41.98% of orders and R$ 5,202,955.05 of sales. Top 3 states = 66.61% of orders (SP, RJ, MG).
- **Period and population:** all 99,441 orders across 27 states
- **Interpretation:** The marketplace is South-East-centric, which shapes delivery economics: most orders ship within a dense region, so national averages under-represent the cost of long-distance delivery.
- **Recommendation:** Prioritise fulfilment and carrier coverage in SP/RJ/MG where volume and basket justify investment; test long-haul lanes separately because their freight and lateness behaviour differs.
- **Limitations:** State is taken from the customer address at order time; a handful of customers moved between states (their orders are attributed to the address on each order).
- **Correlation vs causation:** Geography is an observed distribution, not a causal driver by itself.
- **Figures:** `figures/state_distribution.png`

#### Average basket differs by state, but volume and basket are not aligned

- **Business question:** Do high-volume states also have high-value baskets?
- **Calculation method:** For each state compute average merchandise sales per item-bearing order (SUM of item price divided by distinct orders), then compare the most populous state with the highest-basket state.
- **Actual result:** Largest state SP: average basket R$ 125.75 over 41,375 orders. Highest-basket state (with meaningful volume): PB at R$ 216.67.
- **Period and population:** 27 states, item-bearing orders
- **Interpretation:** Basket size and order volume are separate dimensions; high-volume states are not necessarily high-basket states, so a single national AOV hides real mix differences.
- **Recommendation:** Segment retention and assortment decisions by state cluster rather than nationally; use basket size, not just volume, to decide where to invest in logistics.
- **Limitations:** Freight is excluded from the basket, and small states can show volatile average baskets that should not be over-read.
- **Correlation vs causation:** This is a descriptive comparison, not evidence that geography changes willingness to pay.
- **Figures:** `figures/state_distribution.png`


## Customer retention

#### Most customers buy only once

- **Business question:** What share of customers return for a second order?
- **Calculation method:** Group all orders by customer_unique_id, count customers with >= 2 orders, and divide by all distinct customers. customer_unique_id is used because customer_id is regenerated per order.
- **Actual result:** 2,997 of 96,096 customers placed 2+ orders = a repeat rate of 3.12%. Repeat customers placed 6,342 of 99,441 orders and generated R$ 778,821.97 (5.73% of merchandise sales).
- **Period and population:** 96,096 distinct customers over the full export window
- **Interpretation:** The business is acquisition-led: the overwhelming majority of customers transact once. Repeat customers are few but, per customer, are much more valuable.
- **Recommendation:** Test a post-delivery retention programme (service follow-up, next-purchase incentive) targeted at first-time buyers; even a small lift in the repeat rate has a leveraged effect on sales.
- **Limitations:** A ~2-year export window truncates the tail: customers who would return after the cut are counted as one-time, so the true repeat rate is a lower bound. Guest checkout may also split a person across identities.
- **Correlation vs causation:** Repeat status and value are jointly observed; the data cannot show that repeat buying causes higher spend per se.
- **Figures:** `figures/repeat_segments.png`

#### Repeat customers spend more and rate similarly

- **Business question:** Are returning customers worth more or happier than one-time buyers?
- **Calculation method:** Compare average review score for one-time versus repeat customers, and report the median days between a repeat customer's first and last order.
- **Actual result:** Average review score: one-time = 4.084, repeat = 4.110. Median first-to-last span among repeat customers = 33 days.
- **Period and population:** 2,997 repeat and 93,099 one-time customers
- **Interpretation:** Repeat customers contribute disproportionate value through cumulative spend. Similar satisfaction scores suggest retention is driven by need and assortment rather than by a service gap between the groups.
- **Recommendation:** Focus messaging on the second purchase while the median return window is still open; measure whether the return interval shortens after the campaign.
- **Limitations:** Per-customer spend is cumulative across orders, so comparing totals against one-time buyers is expected to favour repeat customers. Scores are simple averages and not weighted by order value.
- **Correlation vs causation:** Higher cumulative spend for repeat customers is mechanical (more orders); it is not evidence of a causal retention premium.
- **Figures:** `figures/repeat_segments.png`


## Payments

#### Card dominates, boleto is a material minority, vouchers fund part-payments

- **Business question:** Which payment methods carry the transaction value?
- **Calculation method:** Aggregate bridge_payment by payment_type and compute each method's share of total payment value.
- **Actual result:** Total payment value R$ 16,008,872.12 across 103,886 payment records. credit_card = 78.34% of value, boleto = 17.92%, voucher = 2.37%, debit = 1.36%.
- **Period and population:** all payment records (an order may have more than one)
- **Interpretation:** Card is the backbone of collections, but a meaningful slice pays by bank slip (boleto), which has different settlement timing and failure modes and should not be ignored in checkout analytics.
- **Recommendation:** Keep boleto and voucher paths well-instrumented; monitor decline/conversion by method rather than reporting a single 'payment success' number.
- **Limitations:** Grain is the payment attempt, not the order: voucher + card orders appear twice, so row counts and value shares are by payment, not by basket. A few 'not_defined' rows exist.
- **Correlation vs causation:** Payment mix is a distribution, not a causal relationship.
- **Figures:** `figures/payment_methods.png`

#### Installments are widely used and track order size

- **Business question:** How often do customers split payments, and does basket size drive it?
- **Calculation method:** Compute the share of payment records with more than one installment and compare average payment value for multi-installment versus single-payment records.
- **Actual result:** 49.42% of payment records use more than one installment. Average value: multi-installment R$ 196.76 vs single R$ 112.42 (75% higher).
- **Period and population:** all payment records with a known installment count
- **Interpretation:** Installment plans are close to a default behaviour rather than an exception, and they are used more on larger tickets, which is what one would expect if affordability, not method choice, drives the split.
- **Recommendation:** Keep installment options visible at checkout for mid/high-ticket baskets; monitor default/chargeback rates by installment band, since longer plans change credit risk.
- **Limitations:** The dataset records installment count, not interest paid or default outcome, so the cost and risk of installment selling cannot be measured here.
- **Correlation vs causation:** The positive association between basket value and installment count is consistent with affordability pressure, but it is a correlation: product mix and promotions also move both.
- **Figures:** `figures/installments.png`


## Delivery and satisfaction

#### Late delivery is strongly associated with low review scores

- **Business question:** How much lower are review scores when a delivery misses its estimated date?
- **Calculation method:** Join reviews to the late/on-time flag (delivered orders with an estimate), compare average score and the share of 1-2 star reviews, and compute point-biserial (Pearson) and Spearman correlations.
- **Actual result:** On-time orders average 4.294/5 over 88,168 reviews; late orders average 2.565/5 over 7,662 reviews (difference 1.729 points). Negative (1-2 star) review share: on-time 9.2% vs late 54.1%. Pearson r = -0.365, Spearman rho = -0.294.
- **Period and population:** 95,830 reviewed, delivered orders with a late/on-time flag
- **Interpretation:** The gap is large and consistent: late deliveries are the single clearest correlate of poor satisfaction in this dataset. The effect is visible both in average score and in the concentration of 1-2 star reviews.
- **Recommendation:** Prioritise lateness reduction as a satisfaction lever: proactive delay notifications and expedited recovery on at-risk orders are more likely to protect scores than generic service messaging.
- **Limitations:** Reviews are voluntary, so only reviewed orders enter this KPI, and the late/on-time split excludes orders with no delivery timestamp. Review scores are ordinal and coarse (1-5).
- **Correlation vs causation:** This is a strong association, not proof of causation. Lateness may co-occur with long-distance routes, problem sellers, or peak-season congestion that independently depress scores; a controlled comparison holding those factors constant is needed before claiming that lateness alone causes dissatisfaction.
- **Figures:** `figures/late_vs_review.png`, `figures/review_distribution.png`

#### Month-level on-time rate moves with the month-level review score

- **Business question:** Does satisfaction track punctuality over time as well as across orders?
- **Calculation method:** Join the monthly on-time delivery rate to the monthly average review score over complete months and compute their Pearson correlation (an ecological correlation).
- **Actual result:** Across 20 complete months, correlation between monthly on-time % and monthly average review score = 0.776. On-time ranged 78.6%-98.6%; review score ranged 3.75-4.28.
- **Period and population:** 20 complete months (2017-01..2018-08)
- **Interpretation:** The order-level relationship also appears at the month level, which makes the pattern harder to dismiss as an artifact of a single bad week.
- **Recommendation:** Use punctuality as a leading operational indicator for satisfaction in the dashboard and investigate months where on-time falls before review scores follow.
- **Limitations:** An ecological correlation across months can differ from the individual-level relationship and is sensitive to a few months; it is supporting evidence, not a substitute for the order-level analysis.
- **Correlation vs causation:** Ecological correlations do not establish individual-level causation and may be confounded by seasonality or platform growth.
- **Figures:** `figures/delivery_trend.png`, `figures/review_distribution.png`


## Seller operations

#### Seller revenue is highly concentrated

- **Business question:** How dependent is the marketplace on its largest sellers?
- **Calculation method:** Rank sellers by merchandise sales and compute the share held by the top 1%, 5% and 10% of sellers.
- **Actual result:** 3,095 sellers. Top 1% of sellers = 26.07% of merchandise sales; top 5% = 53.30%; top 10% = 67.56%.
- **Period and population:** 3,095 sellers across all item-bearing orders
- **Interpretation:** A minority of sellers supplies most of the value, so seller churn or quality problems at the top have platform-level consequences.
- **Recommendation:** Run a key-account programme for the top decile (service levels, inventory visibility) and keep a bench of qualified backup sellers for the top categories.
- **Limitations:** Seller sales are gross merchandise value before returns/cancellations and exclude freight; sellers with no items are not represented.
- **Correlation vs causation:** Concentration is a structural measurement, not a causal relationship.
- **Figures:** `figures/seller_late_distribution.png`

#### Lateness is not uniform across sellers

- **Business question:** Is late delivery a platform-wide problem or a seller-specific one?
- **Calculation method:** For sellers with at least 30 evaluable orders, describe the distribution of the late-delivery rate and correlate it with average review score (Spearman).
- **Actual result:** Among 634 sellers with >= 30 evaluable orders, mean late rate = 7.8%, 90th percentile = 14.6%. Highest-late seller: ede0c03645598cdfc63ca8237acbe73d (late 34.9%, review 3.52). Spearman correlation between seller late rate and review score = -0.451.
- **Period and population:** 634 sellers with >= 30 evaluable orders
- **Interpretation:** The spread is wide, which means a substantial part of the lateness problem is addressable at the seller level rather than being an unavoidable platform-wide condition.
- **Recommendation:** Use a late-rate threshold to trigger seller coaching or reduced placement, and track the worst decile monthly; target the tail rather than the average.
- **Limitations:** Small sellers have noisy rates, which is why a 30-order floor is applied; seller lateness also reflects carrier and route, which the data attributes to the seller.
- **Correlation vs causation:** The negative late-rate/review association is cross-sectional and confounded by product, price and geography; it is a correlation that supports prioritisation, not a causal claim that lateness alone drives the score.
- **Figures:** `figures/seller_late_distribution.png`, `figures/late_vs_review.png`


## Weight and freight

#### Freight rises with weight, but per-kg cost falls sharply

- **Business question:** How does freight scale with product weight, and where is the freight burden concentrated?
- **Calculation method:** Bucket item units by product weight and compute average freight and freight-per-kg per bucket; correlate unit weight with freight across all known-weight units (Pearson and Spearman).
- **Actual result:** Freight is 16.57% of merchandise sales overall. Freight per kg falls from R$ 112.70 in the 1. 0-200 g bucket to R$ 4.03 in the 6. 5001+ g bucket, while absolute average freight rises from R$ 14.74 to R$ 39.81. Weight-vs-freight correlation: Pearson 0.610, Spearman 0.447.
- **Period and population:** 112,624 item units with usable weight and non-negative freight
- **Interpretation:** Freight has a large fixed component (handling/leg cost) that dominates light items, so the marginal cost of extra weight is low. Heavy items pay more in absolute terms but less per kilogram.
- **Recommendation:** Bundle light items to amortise the fixed freight component, and price or promote heavy items with freight-aware thresholds so the per-kg economics are visible.
- **Limitations:** Freight is the customer charge, not carrier cost. Weight comes from the product catalogue and a few products have missing/invalid weight (kept in an 'unknown' bucket).
- **Correlation vs causation:** The weight/freight correlation is mechanical and expected, not a behavioural causal link; size and distance are additional unmodelled drivers.
- **Figures:** `figures/weight_freight.png`


## Data quality

#### Known data-quality gaps and how they affect the KPIs

- **Business question:** Which data defects could change a business conclusion if ignored?
- **Calculation method:** Count the rows affected by each documented defect directly in the database and map each to the KPIs it touches.
- **Actual result:** orders with no line items: 775; orders with no payment record: 1; orders with no review: 768; orders with no delivery date (excluded from delivery KPIs): 2,965; payment rows with a non-positive value: 9; products with no category: 0; category names with no English mapping: 3; orders whose duplicate reviews were collapsed to one: 547; customer_unique_ids spanning two states: 39; customer ZIPs with no usable geolocation centroid: 158 (167 geolocation rows in total).
- **Period and population:** 99,441 orders, 98,673 reviews, and associated dimension rows
- **Interpretation:** None of these defects invalidate the headline KPIs - each is either immaterial in size or explicitly excluded and documented - but the delivery KPIs carry the largest population gap (orders with no delivery date), and any distance-based geography work is limited because some customer ZIPs have no usable centroid.
- **Recommendation:** Carry these counts as a data-health panel in the dashboard, and define the eligible population on every KPI card. Treat the no-delivery-date gap as the first target for upstream data capture.
- **Limitations:** These are defects detectable within the export; systematic issues in the source process (e.g. events never logged) cannot be counted here.
- **Correlation vs causation:** Data-quality counts are descriptive and do not claim causal impact.

## Headline KPIs (see `reports/business_metrics.md` for full documentation)

| KPI | Value | Eligible population |
|-----|-------|---------------------|
| Total unique orders | 99,441 | all 99,441 orders in the export |
| Total merchandise sales | R$ 13,591,643.70 | all 112,650 item units across 98,666 orders |
| Total freight charges | R$ 2,251,909.54 | same 112,650 item units as merchandise sales |
| Average order value (AOV) | R$ 137.75 | 98,666 of 99,441 orders |
| Delivered orders | 96,478 | all 99,441 orders |
| Delivery completion rate | 97.02% | all 99,441 orders |
| Cancellation rate | 0.63% | all 99,441 orders |
| Average review score | 4.09 | 98,673 reviewed orders of 99,441 |
| On-time delivery rate | 91.89% | 96,476 of 99,441 orders |
| Late delivery rate | 8.11% | 96,476 of 99,441 orders |
| Average delivery duration | 12.52 days | 96,476 of 99,441 orders |
| Repeat customer rate | 3.12% | 96,096 distinct customers |
| Average items per order | 1.14 | 98,666 item-bearing orders |
| Payment method distribution | 78.34% | 103,886 payment records |

## Correlation versus causation - how to read this report

This is observational, transaction-level data: no experiment and no counterfactual. Findings therefore distinguish three strengths of claim:
- **Descriptive** (counts, shares, averages): the number is simply what the data says.
- **Association / correlation** (late delivery vs low scores; weight vs freight; late rate vs seller score): two quantities move together. The report states the direction and strength but does not claim one causes the other.
- **Causal**: not claimed. Where a recommendation implies cause and effect, it is framed as a hypothesis to test (e.g. an A/B test of delay notifications).

## Data quality caveats affecting conclusions

- 2,965 orders have no delivery date and are outside every delivery KPI (they are neither on time nor late).
- 775 orders have no line items and cannot carry a basket value.
- 768 orders have no review; review-based KPIs cover the rest only.
- 9 payment rows are non-positive and are retained, not dropped.
- 0 products have no category and 3 category labels lack an English mapping.
- 547 orders had duplicate review rows collapsed to the latest.
- 39 customer_unique_ids appear in two states; per-state customer counts can exceed the global distinct count.
- Every one of the 158 customer ZIPs that has no usable geolocation centroid blocks distance-based analysis; 167 geolocation rows in total have no valid coordinates.

## Figure index

- **monthly_sales_trend** - `monthly_sales_trend.png`
- **quarterly_sales** - `quarterly_sales.png`
- **category_performance** - `category_performance.png`
- **state_distribution** - `state_distribution.png`
- **repeat_segments** - `repeat_segments.png`
- **payment_methods** - `payment_methods.png`
- **installments** - `installments.png`
- **late_vs_review** - `late_vs_review.png`
- **review_distribution** - `review_distribution.png`
- **seller_late_distribution** - `seller_late_distribution.png`
- **weight_freight** - `weight_freight.png`
- **delivery_trend** - `delivery_trend.png`

## How to present this in the web application

When the Streamlit pages are built, keep the analysis layer as the single source of truth and follow these presentation rules:
- **KPI cards:** show value + unit and the eligible population in a caption, exactly as in `reports/business_metrics.md`. Never show a KPI without its population.
- **Trend pages:** default to the complete window and render the ramp-up/tail months in grey with a tooltip explaining why they are excluded; expose a toggle to reveal them.
- **Drill-downs:** category, state and seller tables should expose sales *and* the operational measures (late rate, review average) together, so a user never reads sales without quality context.
- **Correlation messaging:** any chart pairing two operational measures must carry a 'correlation, not causation' note and avoid causal verbs in titles.
- **Data health:** a persistent badge showing the counts in the Data quality section, so users can see the population excluded behind each metric.
