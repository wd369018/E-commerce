# ShopScope — Planning Phase Deliverables

Companion to `reports/data_profiling_report.md` (generated from actual file contents by
`scripts/profile_datasets.py`). No source CSV was modified.

---

## 4. Recommended Cleaning Rules & Business Impact

| # | Rule | Affected rows (measured) | Business impact if ignored |
|---|------|--------------------------|-----------------------------|
| C1 | **Never join `order_items` × `order_payments` × `order_reviews` in one query.** Build them as separate marts keyed on `order_id`. | Naive 3-way join = **119,143 rows vs 99,441 real orders (1.20× inflation; worst order = 63 rows)** | GMV, AOV and revenue KPIs inflated ~20%. Every dashboard number becomes wrong. |
| C2 | **Treat `order_id` as the grain of the order fact; `order_item_id` as the grain of the line-item fact.** Aggregate payments to order level *before* joining to items. | 9,803 orders have >1 item line; 2,961 orders have >1 payment; 547 orders have >1 review | Line-item revenue summed twice; payment counts double as order counts. |
| C3 | **Deduplicate `olist_geolocation_dataset` to one row per `geolocation_zip_code_prefix` (centroid) before joining.** | 1,000,163 rows → **19,015 distinct zips**; 261,831 exact duplicate rows (26.2%); 17,972 zips have multiple rows; worst zip has **1,146** rows | Joining geo to customers/sellers would multiply the customer table by up to ~1000×. Region-level reporting becomes unusable. |
| C4 | **Quarantine out-of-Brazil coordinates; do not use them for distance calculations.** | 31 rows lat outside (-33.75, 5.27); 37 rows lng outside (-73.99, -34.79); observed lat -36.61→45.07, lng -101.47→121.11 | Shipping-distance and delivery-ETA models get garbage inputs; maps break. |
| C5 | **Filter revenue metrics to `order_status = 'delivered'` (or 'shipped') and exclude cancellations.** Keep all statuses in a `order_status` dimension. | delivered 96,478 / shipped 1,107 / canceled 625 / unavailable 609 / invoiced 314 / processing 301 / created 5 / approved 2 | Cancelled and "unavailable" orders would be counted as sales; delivered-only analysis silently drops 2,963 orders. |
| C6 | **Delivery-performance metrics must use `order_delivered_customer_date`; rows where it is NULL are excluded from on-time-rate but counted in an explicit "no delivery date" bucket.** | 2,965 orders (2.98%) missing; **8 orders are `delivered` but have no delivery date** (inconsistent) | On-time delivery % computed on 96,476 orders, not 99,441 — the denominator must be stated or the KPI is misleading. |
| C7 | **Late-vs-estimated flag: `order_delivered_customer_date > order_estimated_delivery_date`.** Keep NULL, not FALSE, when delivery date is missing. | 7,827 orders delivered after the estimated date | Silently treating NULL as "on time" would understate lateness by ~3 percentage points. |
| C8 | **Fill `product_category_name` NULLs with `'unknown'`, then LEFT JOIN translation; add the 2 untranslated categories manually.** | 610 products (1.85%) have no category; **2 categories lack an English translation**: `pc_gamer`, `portateis_cozinha_e_preparadores_de_alimentos`; **13 product rows** are affected by the gap | Category revenue analysis loses 610 products; an INNER JOIN would drop them entirely and understate category totals. |
| C9 | **Deduplicate reviews: prefer the latest row per `order_id`; keep `review_id` duplicates as a documented data defect.** | 814 duplicate `review_id`; 551 duplicate `order_id` (max 3 reviews/order) | Average rating is double-weighted for those 551 orders → CSAT trend is biased. |
| C10 | **Review text columns: keep NULL as NULL (do not fill with empty string). Use `comment_message` only for NLP subsets.** | `review_comment_title` missing 87,656 (88.34%); `review_comment_message` missing 58,247 (58.70%) | Filling with '' makes "comment sentiment coverage" look like 100%. |
| C11 | **Product dimension: reject rows where `product_weight_g <= 0` or any dimension is NULL/≤0 from shipping analytics.** | weight NULL = 2; weight ≤ 0 = **4**; length/height/width NULL = 2 each; name/description/photos/category NULL = 610 each | Freight-per-kg and dimensional-weight calculations divide by zero or understate cost. |
| C12 | **Payments: exclude `payment_value <= 0` and `payment_installments <= 0` from payment-mix metrics; flag 1 order with no payment record.** | payment_value ≤ 0 = **9**; installments ≤ 0 = **2**; 1 order has zero payment rows | Instalment and payment-type mix percentages get distorted at small denominators. |
| C13 | **Never treat `customer_id` as the customer. Use `customer_unique_id` for customer-level metrics.** | 3,345 duplicate `customer_unique_id` = repeat buyers | Retention/repeat-purchase rate would be reported as 0%. |
| C14 | **Orders with no line items are real and must not be inner-joined away.** | **775 orders have 0 `order_items` rows**; 768 have 0 reviews; 1 has 0 payments | An INNER JOIN orders→items silently deletes 775 orders (0.78%) and inflates "orders" averages. |
| C15 | **Revenue source of truth: `order_items.price + freight_value` for GMV; `payments.payment_value` for cash collected.** Document the delta; do not mix them. | items total **15,843,553.24** vs payments total **16,008,872.12** → delta **165,318.88 (1.04%)** | Two dashboards reporting "revenue" with different numbers destroys trust in the platform. |
| C16 | **Parse all dates to timezone-naive UTC timestamps; validate monotonic order (purchase ≤ approved ≤ carrier ≤ delivered).** | 0 unparseable timestamps; 0 delivered-before-purchase; but 160 missing `order_approved_at`, 1,783 missing `order_delivered_carrier_date` | Funnel stage durations (approval lag, carrier lag) silently skip rows. |
| C17 | **Zip codes are `INT` zero-padded to 5 digits — never read as categorical strings that drop leading zeros; keep separate `state` from address, not from zip.** | 278 customer zips and 7 seller zips are absent from the geolocation table | LEFT JOIN must be used, or those 285 parties vanish from geographic reports. |
| C18 | **Out-of-range numeric validation: `review_score` ∈ [1,5], `price > 0`, `freight_value ≥ 0`.** | score out of range: 0; price ≤ 0: 0; freight < 0: 0 — these columns are clean, assert them in tests | Regression protection; a bad future data load would otherwise go unnoticed. |

---

## 5. Proposed Relational Database Schema (PostgreSQL)

Star-schema: 4 dimensions + 1 fact + 2 bridge/auxiliary tables. Grain is stated for every table.

```
staging (exact copies, typed but untransformed)  ->  marts (typed, deduped, conformed)
```

### Dimensions

```sql
-- Grain: one row per customer_id (order-placement identity)
CREATE TABLE dim_customer (
    customer_id          TEXT PRIMARY KEY,          -- natural key from source
    customer_unique_id   TEXT NOT NULL,             -- TRUE customer for retention metrics
    customer_zip_prefix  INTEGER,                   -- 5-digit prefix
    customer_city        TEXT,
    customer_state       CHAR(2)
);

-- Grain: one row per customer_unique_id (conformed customer)
CREATE TABLE dim_customer_identity (
    customer_unique_id   TEXT PRIMARY KEY,
    first_customer_id    TEXT,
    order_count          INTEGER
);

-- Grain: one row per product_id
CREATE TABLE dim_product (
    product_id                TEXT PRIMARY KEY,
    product_category_pt       TEXT,                 -- NULL -> 'unknown' (C8)
    product_category_en       TEXT,
    product_name_length       SMALLINT,
    product_description_length INTEGER,
    product_photos_qty        SMALLINT,
    product_weight_g          INTEGER,
    product_length_cm         SMALLINT,
    product_height_cm         SMALLINT,
    product_width_cm          SMALLINT,
    is_shipping_valid         BOOLEAN               -- C11
);

-- Grain: one row per seller_id
CREATE TABLE dim_seller (
    seller_id          TEXT PRIMARY KEY,
    seller_zip_prefix  INTEGER,
    seller_city        TEXT,
    seller_state       CHAR(2)
);

-- Grain: one row per order (header / degenerate dimension)
CREATE TABLE dim_order (
    order_id                     TEXT PRIMARY KEY,
    customer_id                  TEXT NOT NULL REFERENCES dim_customer(customer_id),
    order_status                 TEXT NOT NULL,
    order_purchase_timestamp     TIMESTAMP NOT NULL,
    order_approved_at            TIMESTAMP,
    order_delivered_carrier_date TIMESTAMP,
    order_delivered_customer_date TIMESTAMP,
    order_estimated_delivery_date TIMESTAMP,
    is_delivered                 BOOLEAN GENERATED ALWAYS AS (order_status='delivered') STORED,
    delivery_delay_days          INTEGER,            -- NULL when no delivery date (C7)
    is_late_vs_estimate          BOOLEAN,            -- tri-state, NULL allowed
    has_line_items               BOOLEAN             -- C14 (775 FALSE)
);

-- Grain: one row per geolocation_zip_code_prefix (deduped centroid, C3)
CREATE TABLE dim_geolocation (
    zip_code_prefix  INTEGER PRIMARY KEY,
    lat              NUMERIC(9,6),
    lng              NUMERIC(9,6),
    city             TEXT,
    state            CHAR(2),
    source_row_count INTEGER                       -- how many raw rows were averaged
);

-- Small conformed lookup
CREATE TABLE dim_product_category (
    product_category_pt TEXT PRIMARY KEY,
    product_category_en TEXT
);
```

### Fact

```sql
-- Grain: one row per (order_id, order_item_id)  -- 112,650 rows
CREATE TABLE fact_order_item (
    order_id          TEXT NOT NULL REFERENCES dim_order(order_id),
    order_item_id     INTEGER NOT NULL,
    product_id        TEXT REFERENCES dim_product(product_id),
    seller_id         TEXT REFERENCES dim_seller(seller_id),
    shipping_limit_date   TIMESTAMP,
    price             NUMERIC(10,2) NOT NULL CHECK (price > 0),
    freight_value     NUMERIC(10,2) NOT NULL CHECK (freight_value >= 0),
    item_total        NUMERIC(10,2) GENERATED ALWAYS AS (price + freight_value) STORED,
    order_purchase_date DATE,
    PRIMARY KEY (order_id, order_item_id)
);
```

### Bridge / auxiliary (never joined directly to each other)

```sql
-- Grain: (order_id, payment_sequential) -- 103,886 rows; aggregate to order BEFORE joining items
CREATE TABLE bridge_payment (
    order_id              TEXT NOT NULL REFERENCES dim_order(order_id),
    payment_sequential    SMALLINT NOT NULL,
    payment_type          TEXT,
    payment_installments  SMALLINT,
    payment_value         NUMERIC(10,2),
    PRIMARY KEY (order_id, payment_sequential)
);

-- Grain: one row per review (dedup per rule C9) -- 99,224 raw / 98,673 distinct orders
CREATE TABLE bridge_review (
    review_id             TEXT NOT NULL,
    order_id              TEXT NOT NULL REFERENCES dim_order(order_id),
    review_score          SMALLINT CHECK (review_score BETWEEN 1 AND 5),
    review_creation_date  DATE,
    review_answer_timestamp TIMESTAMP,
    review_comment_title  TEXT,
    review_comment_message TEXT,
    PRIMARY KEY (review_id, order_id)
);

-- Order-level rollup so dashboards never re-join the bridges
CREATE TABLE fact_order_summary (
    order_id            TEXT PRIMARY KEY REFERENCES dim_order(order_id),
    n_items             INTEGER,
    n_payments          INTEGER,
    n_reviews           INTEGER,
    item_revenue        NUMERIC(12,2),   -- sum(price+freight)
    payment_collected   NUMERIC(12,2),   -- sum(payment_value)
    avg_review_score    NUMERIC(3,2)
);
```

### Indexes

```sql
CREATE INDEX ix_order_purchase ON dim_order (order_purchase_timestamp);
CREATE INDEX ix_order_status   ON dim_order (order_status);
CREATE INDEX ix_fact_item_prod ON fact_order_item (product_id);
CREATE INDEX ix_fact_item_sell ON fact_order_item (seller_id);
CREATE INDEX ix_fact_item_date ON fact_order_item (order_purchase_date);
CREATE INDEX ix_dim_cust_uniq  ON dim_customer (customer_unique_id);
CREATE INDEX ix_dim_prod_cat   ON dim_product (product_category_pt);
```

### Anti-double-counting contract

1. `dim_order` ↔ any one of `bridge_payment` / `bridge_review` is safe (fan-out ≤ 29/3).
2. `fact_order_item` ↔ `bridge_payment` is **unsafe**: pre-aggregate payments to `fact_order_summary` first.
3. `dim_geolocation` is 1:1 with a zip only **after** C3 dedup — never join the raw 1,000,163-row file.

---

## 6. Proposed Project Folder Structure

```
shopscope/
├── README.md
├── pyproject.toml                  # deps: pandas, sqlalchemy, psycopg, pydantic, pytest, ruff
├── .env.example                    # DATABASE_URL=
├── data/
│   ├── raw/                        # the 9 original CSVs, READ-ONLY (already in workspace root)
│   ├── staging/                    # typed-but-untransformed parquet (checkpoint)
│   └── processed/                  # cleaned parquet / CSV for the warehouse loader
├── config/
│   ├── settings.yaml               # paths, DB url, constants (BRAZIL_LAT/LNG, statuses)
│   ├── file_schemas.yaml           # expected columns + dtypes per CSV (contract tests)
│   └── cleaning_rules.yaml         # C1..C18 as declarative, versioned rules
├── db/
│   ├── ddl/                        # 001_dimensions.sql, 002_fact.sql, 003_indexes.sql
│   └── migrations/
├── src/shopscope/
│   ├── __init__.py
│   ├── extract/
│   │   ├── reader.py               # read_csv wrapper: dtype contract, chunking for geo (58 MB)
│   │   └── inventory.py            # file presence + row-count assertions
│   ├── profile/                    # <-- Stage 1 (this phase)
│   │   └── profile_datasets.py
│   ├── transform/
│   │   ├── orders.py
│   │   ├── customers.py
│   │   ├── items.py
│   │   ├── payments.py
│   │   ├── reviews.py
│   │   ├── products.py
│   │   ├── sellers.py
│   │   ├── geolocation.py          # C3/C4 dedup + outlier quarantine
│   │   └── categories.py           # C8 translation join
│   ├── validate/
│   │   ├── schema_checks.py        # columns / dtypes / nullability
│   │   ├── referential_checks.py   # orphan FK counts == 0
│   │   ├── grain_checks.py         # PK uniqueness, row-count reconciliation
│   │   └── business_rules.py       # C5..C17 assertions
│   ├── load/
│   │   ├── warehouse.py            # truncate-and-load or upsert
│   │   └── marts.py                # fact_order_summary + aggregate views
│   ├── analytics/
│   │   ├── metrics.py              # GMV, AOV, on-time rate, CSAT, retention
│   │   └── queries/
│   └── cli.py                      # `shopscope profile|etl|validate|report`
├── notebooks/
│   └── 01_exploration.ipynb
├── dashboards/                     # Streamlit / Metabase definitions
├── reports/
│   ├── data_profiling_report.md    # <-- generated this phase
│   └── quality_gate.md             # Stage-2 validation output
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/                   # tiny CSV slices for fast tests
├── scripts/
│   └── profile_datasets.py         # <-- created this phase
└── docs/
    └── project_plan.md             # this file
```

Design notes:
- `extract → transform → validate → load` are separate packages so each stage is independently testable and re-runnable.
- Every transform module owns exactly one CSV → keeps functions small and makes failures attributable.
- `validate` runs **after** transform and **before** load; a failed check halts the pipeline.
- Raw CSVs are never written to; all outputs land in `data/` or `reports/`.

---

## 7. Recommended Implementation Order

| Step | Work | Why first | Exit criterion |
|---|---|---|---|
| **0** | Repo scaffolding: `pyproject.toml`, package skeleton, `config/file_schemas.yaml`, `.env.example` | Everything else hangs off it | `pip install -e .` works; `shopscope --help` runs |
| **1** | Move (not copy) the 9 CSVs into `data/raw/` **or** leave in place and point config at them — *decision needed from you* | Keeps raw immutable before any write happens | Config resolves all 9 paths |
| **2** | `extract/reader.py` + contract tests (columns & dtypes match `file_schemas.yaml`) | Catches schema drift before cleaning | 9/9 files load; contract test green |
| **3** | `db/ddl` — create the schema above | Transform targets need to exist | Tables created, PK/FK constraints live |
| **4** | `transform/geolocation.py` (C3, C4) | Highest fan-out risk (1M rows → 19k); blocks every geo join | `dim_geolocation` = 19,015 rows, 1 PK |
| **5** | `transform/orders.py` + `customers.py` (C5, C6, C7, C13, C16) | Order is the central grain | 99,441 order rows, 0 PK dups, delay columns populated |
| **6** | `transform/products.py`, `sellers.py`, `categories.py` (C8, C11) | Small, independent dims | 32,951 products; 71 + 2 categories mapped; 13 orphan rows resolved |
| **7** | `transform/items.py` (C2) | First fact table; measures GMV | 112,650 rows; sum(price+freight) = 15,843,553.24 |
| **8** | `transform/payments.py` (C12) then aggregate to `fact_order_summary` | Enforces the anti-join rule C1 | 103,886 rows; total = 16,008,872.12 |
| **9** | `transform/reviews.py` (C9, C10) | Last bridge; has dup keys to resolve | 99,224 rows; 0 duplicate `review_id` |
| **10** | `validate/*` — full gate: PK uniqueness, orphans = 0, row-count reconciliation vs source, revenue totals | This is the quality gate | `shopscope validate` exits 0 |
| **11** | `load/warehouse.py` — idempotent load, then `marts.py` → `fact_order_summary` | Data now queryable | Reload twice → identical row counts |
| **12** | `analytics/metrics.py`: GMV, AOV, on-time rate, CSAT, repeat-rate, category revenue | First business value | Metrics match the profiling totals above |
| **13** | Report/dashboard layer | Presentation last, once numbers are trusted | One dashboard sourced only from `fact_order_summary` |
| **14** | CI: `pytest` + `ruff` + the validate gate | Prevents regressions | Red build on any rule violation |

**Gate between phases:** stop after Step 3 (schema created) and confirm the DDL with you before loading data.

---

## Open Decisions (need your input before implementation)

1. **Raw file location** — move the 9 CSVs from the workspace root into `data/raw/`, or leave them where they are and configure paths? *(Nothing will be moved without your approval.)*
2. **Target database** — PostgreSQL, SQLite, or DuckDB?
3. **Review de-duplication** — keep latest row per `order_id`, or keep all 99,224 and weight by order?
