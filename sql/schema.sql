-- ==========================================================================
-- ShopScope - SQLite relational schema
-- ==========================================================================
-- Loads the 11 cleaned tables produced by `python -m src.etl_pipeline`.
--
-- Design notes
-- ------------
-- * Grain is declared for every table; one table = one grain.
-- * `bridge_payment` and `bridge_review` are deliberately NOT joined to each
--   other or to `fact_order_item` in analytical SQL: both are one-to-many with
--   `dim_order` and combining them multiplies rows. Use `fact_order_summary`
--   (exactly one row per order) whenever an order-level measure is needed.
-- * Booleans are stored as INTEGER 0/1 (nullable where the source is tri-state,
--   e.g. is_late_vs_estimate which is NULL when there is no delivery date).
-- * Timestamps are ISO-8601 TEXT, so lexicographic ordering == chronological.
-- * `dim_product.product_category_name_english` from the CSV is intentionally
--   NOT stored: the translation is normalised into dim_product_category.
-- * SQLite has no separate DDL transaction, so the whole script runs inside
--   BEGIN/COMMIT with foreign-key enforcement suspended for the DROPs.
--
-- Rerunning this file drops and recreates every object: creation is idempotent.
-- ==========================================================================

PRAGMA foreign_keys = OFF;

BEGIN;

-- ---- drops (children first) ---------------------------------------------
DROP TABLE IF EXISTS fact_order_summary;
DROP TABLE IF EXISTS bridge_review;
DROP TABLE IF EXISTS bridge_payment;
DROP TABLE IF EXISTS fact_order_item;
DROP TABLE IF EXISTS dim_order;
DROP TABLE IF EXISTS dim_geolocation;
DROP TABLE IF EXISTS dim_seller;
DROP TABLE IF EXISTS dim_product;
DROP TABLE IF EXISTS dim_product_category;
DROP TABLE IF EXISTS dim_customer_identity;
DROP TABLE IF EXISTS dim_customer;

-- ---- dimensions ---------------------------------------------------------

-- Grain: one row per customer_id (the order-placement identity).
CREATE TABLE dim_customer (
    customer_id             TEXT    NOT NULL PRIMARY KEY,
    customer_unique_id      TEXT    NOT NULL,
    customer_zip_code_prefix INTEGER,
    customer_city           TEXT,
    customer_state          TEXT
);
CREATE INDEX idx_dim_customer_unique_id ON dim_customer (customer_unique_id);
CREATE INDEX idx_dim_customer_state      ON dim_customer (customer_state);

-- Grain: one row per customer_unique_id - the ONLY correct grain for
-- repeat-purchase / retention analysis (customer_id is per-placement).
CREATE TABLE dim_customer_identity (
    customer_unique_id TEXT    NOT NULL PRIMARY KEY,
    placement_count    INTEGER NOT NULL CHECK (placement_count >= 1),
    first_customer_id  TEXT,
    states             TEXT
);
CREATE INDEX idx_dim_customer_identity_placements ON dim_customer_identity (placement_count);

-- Grain: one row per Portuguese category name, including the 'unknown'
-- sentinel used for the 610 products that ship without a category.
CREATE TABLE dim_product_category (
    product_category_name        TEXT    NOT NULL PRIMARY KEY,
    product_category_name_english TEXT,
    has_english_translation      INTEGER NOT NULL CHECK (has_english_translation IN (0, 1))
);
CREATE INDEX idx_dim_category_english ON dim_product_category (product_category_name_english);

-- Grain: one row per product_id.
CREATE TABLE dim_product (
    product_id                TEXT    NOT NULL PRIMARY KEY,
    product_category_name     TEXT    NOT NULL,
    product_name_lenght       INTEGER,
    product_description_lenght INTEGER,
    product_photos_qty        INTEGER,
    product_weight_g          REAL,
    product_length_cm         REAL,
    product_height_cm         REAL,
    product_width_cm          REAL,
    category_was_missing      INTEGER NOT NULL CHECK (category_was_missing IN (0, 1)),
    is_shipping_valid         INTEGER CHECK (is_shipping_valid IN (0, 1) OR is_shipping_valid IS NULL),
    CONSTRAINT fk_product_category
        FOREIGN KEY (product_category_name) REFERENCES dim_product_category (product_category_name)
);
CREATE INDEX idx_dim_product_category ON dim_product (product_category_name);
CREATE INDEX idx_dim_product_weight   ON dim_product (product_weight_g);

-- Grain: one row per seller_id.
CREATE TABLE dim_seller (
    seller_id               TEXT    NOT NULL PRIMARY KEY,
    seller_zip_code_prefix  INTEGER,
    seller_city             TEXT,
    seller_state            TEXT
);
CREATE INDEX idx_dim_seller_state ON dim_seller (seller_state);

-- Grain: one row per ZIP-code prefix = source ZIPs UNION ZIPs referenced by a
-- customer/seller address. Rows with source_row_count = 0 are placeholders
-- (the source geolocation file does not cover them): centroid is NULL.
CREATE TABLE dim_geolocation (
    geolocation_zip_code_prefix INTEGER NOT NULL PRIMARY KEY,
    geolocation_lat             REAL,
    geolocation_lng             REAL,
    geolocation_city            TEXT,
    geolocation_state           TEXT,
    valid_coordinate_count      INTEGER NOT NULL DEFAULT 0,
    source_row_count            INTEGER NOT NULL DEFAULT 0,
    is_valid_coordinate         INTEGER NOT NULL CHECK (is_valid_coordinate IN (0, 1))
);
CREATE INDEX idx_dim_geo_state ON dim_geolocation (geolocation_state);
CREATE INDEX idx_dim_geo_valid ON dim_geolocation (is_valid_coordinate);

-- Grain: one row per order_id (order header / degenerate dimension).
CREATE TABLE dim_order (
    order_id                      TEXT NOT NULL PRIMARY KEY,
    customer_id                   TEXT NOT NULL,
    order_status                  TEXT NOT NULL,
    order_purchase_timestamp      TEXT NOT NULL,
    order_approved_at             TEXT,
    order_delivered_carrier_date  TEXT,
    order_delivered_customer_date TEXT,
    order_estimated_delivery_date TEXT,
    delivery_delay_days           INTEGER,
    is_late_vs_estimate           INTEGER CHECK (is_late_vs_estimate IN (0, 1) OR is_late_vs_estimate IS NULL),
    purchase_date                 TEXT NOT NULL,
    is_delivered_status           INTEGER NOT NULL CHECK (is_delivered_status IN (0, 1)),
    is_revenue_status             INTEGER NOT NULL CHECK (is_revenue_status IN (0, 1)),
    missing_approval_date         INTEGER NOT NULL CHECK (missing_approval_date IN (0, 1)),
    missing_carrier_date          INTEGER NOT NULL CHECK (missing_carrier_date IN (0, 1)),
    missing_delivery_date         INTEGER NOT NULL CHECK (missing_delivery_date IN (0, 1)),
    has_line_items                INTEGER NOT NULL CHECK (has_line_items IN (0, 1)),
    has_payments                  INTEGER NOT NULL CHECK (has_payments IN (0, 1)),
    has_reviews                   INTEGER NOT NULL CHECK (has_reviews IN (0, 1)),
    has_impossible_date_sequence  INTEGER NOT NULL CHECK (has_impossible_date_sequence IN (0, 1)),
    CONSTRAINT fk_order_customer FOREIGN KEY (customer_id) REFERENCES dim_customer (customer_id)
);
CREATE INDEX idx_dim_order_customer      ON dim_order (customer_id);
CREATE INDEX idx_dim_order_purchase_ts   ON dim_order (order_purchase_timestamp);
CREATE INDEX idx_dim_order_purchase_date ON dim_order (purchase_date);
CREATE INDEX idx_dim_order_status        ON dim_order (order_status);
CREATE INDEX idx_dim_order_delivery      ON dim_order (order_delivered_customer_date);

-- ---- facts / bridges ----------------------------------------------------

-- Grain: one row per (order_id, order_item_id) = ONE UNIT of merchandise.
-- Merchandise sales are always SUM(price); freight is a separate measure.
CREATE TABLE fact_order_item (
    order_id           TEXT    NOT NULL,
    order_item_id      INTEGER NOT NULL CHECK (order_item_id >= 1),
    product_id         TEXT    NOT NULL,
    seller_id          TEXT    NOT NULL,
    shipping_limit_date TEXT,
    price              REAL    NOT NULL CHECK (price > 0),
    freight_value      REAL    NOT NULL CHECK (freight_value >= 0),
    is_valid_price     INTEGER NOT NULL CHECK (is_valid_price IN (0, 1)),
    is_valid_freight   INTEGER NOT NULL CHECK (is_valid_freight IN (0, 1)),
    PRIMARY KEY (order_id, order_item_id),
    CONSTRAINT fk_item_order   FOREIGN KEY (order_id)   REFERENCES dim_order (order_id),
    CONSTRAINT fk_item_product FOREIGN KEY (product_id) REFERENCES dim_product (product_id),
    CONSTRAINT fk_item_seller  FOREIGN KEY (seller_id)  REFERENCES dim_seller (seller_id)
);
CREATE INDEX idx_fact_item_order   ON fact_order_item (order_id);
CREATE INDEX idx_fact_item_product ON fact_order_item (product_id);
CREATE INDEX idx_fact_item_seller  ON fact_order_item (seller_id);

-- Grain: one row per (order_id, payment_sequential).
-- MANY-to-one with dim_order: aggregate to order level BEFORE combining with
-- fact_order_item, or use fact_order_summary.
CREATE TABLE bridge_payment (
    order_id             TEXT    NOT NULL,
    payment_sequential   INTEGER NOT NULL CHECK (payment_sequential >= 1),
    payment_type         TEXT,
    payment_installments INTEGER,
    payment_value        REAL,
    is_valid_payment_value INTEGER NOT NULL CHECK (is_valid_payment_value IN (0, 1)),
    is_valid_installments  INTEGER NOT NULL CHECK (is_valid_installments IN (0, 1)),
    PRIMARY KEY (order_id, payment_sequential),
    CONSTRAINT fk_payment_order FOREIGN KEY (order_id) REFERENCES dim_order (order_id)
);
CREATE INDEX idx_bridge_payment_order ON bridge_payment (order_id);
CREATE INDEX idx_bridge_payment_type  ON bridge_payment (payment_type);
CREATE INDEX idx_bridge_payment_inst  ON bridge_payment (payment_installments);

-- Grain: one row per order_id - the ETL already collapsed the 551 source rows
-- that were a second review on the same order (latest answer wins).
CREATE TABLE bridge_review (
    order_id                TEXT    NOT NULL PRIMARY KEY,
    review_id               TEXT    NOT NULL,
    review_score            INTEGER NOT NULL CHECK (review_score BETWEEN 1 AND 5),
    review_comment_title    TEXT,
    review_comment_message  TEXT,
    review_creation_date    TEXT,
    review_answer_timestamp TEXT,
    source_review_rows      INTEGER NOT NULL,
    distinct_review_ids     INTEGER NOT NULL,
    CONSTRAINT fk_review_order FOREIGN KEY (order_id) REFERENCES dim_order (order_id)
);
CREATE INDEX idx_bridge_review_order ON bridge_review (order_id);
CREATE INDEX idx_bridge_review_score ON bridge_review (review_score);

-- Grain: exactly one row per order_id. The sanctioned way to combine
-- order-level measures without join fan-out.
CREATE TABLE fact_order_summary (
    order_id                  TEXT    NOT NULL PRIMARY KEY,
    n_items                   INTEGER NOT NULL CHECK (n_items >= 0),
    n_distinct_products       INTEGER NOT NULL CHECK (n_distinct_products >= 0),
    item_revenue              REAL,
    n_payments                INTEGER NOT NULL CHECK (n_payments >= 0),
    payment_collected         REAL,
    n_reviews                 INTEGER NOT NULL CHECK (n_reviews >= 0),
    avg_review_score          REAL,
    payment_minus_item_revenue REAL,
    order_status              TEXT    NOT NULL,
    is_revenue_status         INTEGER NOT NULL CHECK (is_revenue_status IN (0, 1)),
    purchase_date             TEXT    NOT NULL,
    delivery_delay_days       INTEGER,
    is_late_vs_estimate       INTEGER CHECK (is_late_vs_estimate IN (0, 1) OR is_late_vs_estimate IS NULL),
    CONSTRAINT fk_summary_order FOREIGN KEY (order_id) REFERENCES dim_order (order_id)
);
CREATE INDEX idx_fact_summary_status    ON fact_order_summary (order_status);
CREATE INDEX idx_fact_summary_purchase  ON fact_order_summary (purchase_date);
CREATE INDEX idx_fact_summary_revenue   ON fact_order_summary (is_revenue_status);
CREATE INDEX idx_fact_summary_late      ON fact_order_summary (is_late_vs_estimate);

COMMIT;

PRAGMA foreign_keys = ON;
