"""
ShopScope - cleaning layer (Stage 2 of the pipeline).

Design rules
------------
* **No silent drops.** Every row removed from an entity is counted and carries a
  written reason; exclusions are returned in :class:`CleaningResult` so the run
  summary can reconcile input vs output row counts.
* **Missing means what it means.** A missing delivery date stays NULL (it is not
  "on time", it is not 0 days). A missing category becomes the explicit sentinel
  ``unknown`` so it survives group-by. A missing review comment stays NULL rather
  than becoming an empty string that would fake 100% comment coverage.
* **Flags over deletions.** Invalid-but-present rows (non-positive payments,
  unshippable dimensions) are kept and flagged so they can be excluded from the
  specific metric that would be corrupted by them.
* **Idempotent.** Functions are pure: ``f(raw) -> cleaned``. Running twice on the
  same input yields identical output, and the pipeline overwrites its own files.

Each cleaner returns a :class:`CleaningResult`` carrying row counts, the
exclusion reason (if any), and the issue/action counters used by the reports.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from . import config

logger = logging.getLogger(__name__)


@dataclass
class CleaningResult:
    """Row-count ledger plus issue counters for one entity."""

    name: str
    frame: pd.DataFrame
    input_rows: int
    output_rows: int
    excluded_rows: int = 0
    exclusion_reason: str | None = None
    # measured issue counts, e.g. {"missing_delivery_date": 2965}
    issues: dict[str, int] = field(default_factory=dict)
    # what we did about them, e.g. {"kept_null": 2965}
    actions: dict[str, int] = field(default_factory=dict)
    # rows split out of the main table (never dropped silently)
    quarantine: pd.DataFrame | None = None

    @property
    def net_change(self) -> int:
        return self.output_rows - self.input_rows


def _result(name: str, raw_len: int, frame: pd.DataFrame, **kw) -> CleaningResult:
    return CleaningResult(
        name=name,
        frame=frame,
        input_rows=raw_len,
        output_rows=len(frame),
        excluded_rows=kw.pop("excluded_rows", 0),
        exclusion_reason=kw.pop("exclusion_reason", None),
        quarantine=kw.pop("quarantine", None),
        **kw,
    )


# --------------------------------------------------------------------------
# customers
# --------------------------------------------------------------------------
def clean_customers(raw: pd.DataFrame) -> tuple[CleaningResult, CleaningResult]:
    """Dim on ``customer_id`` (order placement) + identity dim on ``customer_unique_id``."""
    df = raw.copy()
    for col in ("customer_city", "customer_state"):
        df[col] = df[col].str.strip()
    df["customer_zip_code_prefix"] = df["customer_zip_code_prefix"].astype("Int64")

    primary = _result(
        "dim_customer",
        len(raw),
        df,
        issues={"repeat_buyer_placements": int(df["customer_unique_id"].duplicated().sum())},
        actions={"kept_all_placements": len(df)},
    )

    # Identity grain: one row per real person. placement_count = how many
    # customer_ids (i.e. order placements) that person generated - this is the
    # only correct basis for repeat-purchase rate.
    identity = (
        df.groupby("customer_unique_id", as_index=False)
        .agg(
            placement_count=("customer_id", "size"),
            first_customer_id=("customer_id", "first"),
            states=("customer_state", lambda s: "|".join(sorted(set(s.dropna())))),
        )
        .sort_values("customer_unique_id")
        .reset_index(drop=True)
    )
    secondary = _result(
        "dim_customer_identity",
        len(df),
        identity,
        actions={"collapsed_duplicate_unique_id": len(df) - len(identity)},
    )
    return primary, secondary


# --------------------------------------------------------------------------
# orders
# --------------------------------------------------------------------------
def clean_orders(
    raw: pd.DataFrame,
    item_order_ids: set[str],
    payment_order_ids: set[str],
    review_order_ids: set[str],
) -> CleaningResult:
    """Typed order header + derived delivery metrics + coverage flags."""
    df = raw.copy()
    n = len(df)

    delivered = df["order_delivered_customer_date"]
    purchased = df["order_purchase_timestamp"]
    estimated = df["order_estimated_delivery_date"]

    # Delivery delay in days: NULL when the order was never delivered to the
    # customer - a NULL here is meaningful ("unknown"), 0 would mean "instant".
    delay = (delivered - purchased).dt.total_seconds() / 86_400
    df["delivery_delay_days"] = delay.round().astype("Int64")

    # Tri-state late flag: NA when we genuinely cannot know (no delivery date).
    df["is_late_vs_estimate"] = (
        (delivered > estimated).astype("boolean").where(delivered.notna() & estimated.notna())
    )

    df["purchase_date"] = df["order_purchase_timestamp"].dt.normalize()
    df["is_delivered_status"] = (df["order_status"] == "delivered").astype("boolean")
    df["is_revenue_status"] = df["order_status"].isin(config.REVENUE_STATUSES).astype("boolean")

    df["missing_approval_date"] = df["order_approved_at"].isna()
    df["missing_carrier_date"] = df["order_delivered_carrier_date"].isna()
    df["missing_delivery_date"] = delivered.isna()
    df["has_line_items"] = df["order_id"].isin(item_order_ids)
    df["has_payments"] = df["order_id"].isin(payment_order_ids)
    df["has_reviews"] = df["order_id"].isin(review_order_ids)

    # --- inconsistency detection (measured, not assumed) -------------------
    logical: dict[str, int] = {
        "delivered_before_purchase": int((delivered < purchased).sum()),
        "approved_before_purchase": int((df["order_approved_at"] < purchased).sum()),
        "carrier_before_approval": int(
            (df["order_delivered_carrier_date"] < df["order_approved_at"]).sum()
        ),
        "delivered_before_carrier": int((delivered < df["order_delivered_carrier_date"]).sum()),
        "delivered_after_estimate": int((delivered > estimated).sum()),
        "status_delivered_but_no_delivery_date": int(
            ((df["order_status"] == "delivered") & delivered.isna()).sum()
        ),
        "not_delivered_status_but_has_delivery_date": int(
            ((df["order_status"] != "delivered") & delivered.isna().eq(False)).sum()
        ),
        "missing_approval_date": int(df["order_approved_at"].isna().sum()),
        "missing_carrier_date": int(df["order_delivered_carrier_date"].isna().sum()),
        "missing_delivery_date": int(delivered.isna().sum()),
        "no_line_items": int((~df["has_line_items"]).sum()),
        "no_payments": int((~df["has_payments"]).sum()),
        "no_reviews": int((~df["has_reviews"]).sum()),
    }

    # Impossible timestamps (delivery before purchase, approval before purchase).
    # An order is the spine every child table hangs from, so it is NEVER dropped;
    # it is flagged and an audit copy is written to the quarantine file.
    impossible = (
        (delivered < purchased)
        | (df["order_approved_at"] < purchased)
        | (df["order_delivered_carrier_date"] < df["order_approved_at"])
        | (delivered < df["order_delivered_carrier_date"])
    ).fillna(False)
    df["has_impossible_date_sequence"] = impossible.astype("boolean")
    logical["impossible_date_sequence"] = int(impossible.sum())

    quarantine = df.loc[impossible, ["order_id", "order_status", "order_purchase_timestamp",
                                     "order_approved_at", "order_delivered_carrier_date",
                                     "order_delivered_customer_date"]].copy()

    return _result(
        "dim_order",
        n,
        df,
        excluded_rows=0,
        exclusion_reason=None,
        quarantine=quarantine,
        issues=logical,
        actions={
            "kept_null_delivery_date": logical["missing_delivery_date"],
            "flagged_tri_state_late": int(df["is_late_vs_estimate"].isna().sum()),
            "retained_and_flagged_impossible_dates": int(impossible.sum()),
            "orders_never_excluded": n,
        },
    )


# --------------------------------------------------------------------------
# products + categories
# --------------------------------------------------------------------------
def clean_categories(translation: pd.DataFrame, product_categories: set[str]) -> CleaningResult:
    """Conformed category lookup = translation table UNION categories actually used.

    Guarantees a 1:1 lookup for every category present in ``dim_product`` even
    when the translation CSV is incomplete (currently 2 categories) or when a
    product has no category at all (``unknown``).
    """
    trans = translation.copy()
    trans["product_category_name"] = trans["product_category_name"].str.strip()
    trans["product_category_name_english"] = trans["product_category_name_english"].str.strip()
    trans = trans.dropna(subset=["product_category_name"])

    used = pd.DataFrame({"product_category_name": sorted(product_categories)})
    merged = used.merge(trans, on="product_category_name", how="left")

    n_untranslated = int(merged["product_category_name_english"].isna().sum())
    # Sentinel row so products with no category still satisfy the FK.
    sentinel = pd.DataFrame(
        [{"product_category_name": config.UNKNOWN_CATEGORY, "product_category_name_english": None}]
    )
    merged = pd.concat([merged, sentinel], ignore_index=True)
    merged["has_english_translation"] = merged["product_category_name_english"].notna()

    return _result(
        "dim_product_category",
        len(trans),
        merged,
        issues={
            "categories_used_without_english_translation": n_untranslated,
            "translation_rows_total": len(trans),
        },
        actions={
            "added_unknown_sentinel_row": 1,
            "left_english_null_when_untranslated": n_untranslated,
        },
    )


def clean_products(
    raw: pd.DataFrame, category_lookup: pd.DataFrame
) -> CleaningResult:
    """Product dimension with explicit ``unknown`` category + shipping validity flag."""
    df = raw.copy()
    n = len(df)

    df["product_category_name"] = df["product_category_name"].str.strip()
    missing_cat = int(df["product_category_name"].isna().sum())
    df["product_category_name"] = df["product_category_name"].fillna(config.UNKNOWN_CATEGORY)
    df["category_was_missing"] = df["product_category_name"].eq(config.UNKNOWN_CATEGORY)

    df = df.merge(
        category_lookup[["product_category_name", "product_category_name_english"]],
        on="product_category_name",
        how="left",
    )
    # 'unknown' is an intentional sentinel, not a missing translation - it must
    # not inflate the untranslated-category count.
    untranslated = int(
        (
            df["product_category_name_english"].isna()
            & df["product_category_name"].ne(config.UNKNOWN_CATEGORY)
        ).sum()
    )

    # Tri-state shipping flag: True/False only when every input is present,
    # NA when weight or a dimension is missing ("unknown", not "invalid").
    weight = df["product_weight_g"]
    dims = df[["product_length_cm", "product_height_cm", "product_width_cm"]]
    complete = weight.notna() & dims.notna().all(axis=1)
    positive = (weight > 0) & (dims > 0).all(axis=1)
    df["is_shipping_valid"] = positive.astype("boolean").where(complete, other=pd.NA)

    issues = {
        "missing_category_name": missing_cat,
        "missing_name_length": int(df["product_name_lenght"].isna().sum()),
        "missing_description_length": int(df["product_description_lenght"].isna().sum()),
        "missing_photos_qty": int(df["product_photos_qty"].isna().sum()),
        "missing_weight": int(weight.isna().sum()),
        "non_positive_weight": int((weight <= 0).sum()),
        "missing_any_dimension": int(dims.isna().any(axis=1).sum()),
        "product_rows_with_untranslated_category": untranslated,
        "shipping_invalid": int((~df["is_shipping_valid"].fillna(False)).sum()),
    }
    actions = {
        "category_filled_with_unknown": missing_cat,
        "weight_left_null_not_zero": issues["missing_weight"],
        "dimension_left_null_not_zero": issues["missing_any_dimension"],
        "flagged_shipping_invalid": issues["shipping_invalid"],
    }
    return _result("dim_product", n, df, issues=issues, actions=actions)


# --------------------------------------------------------------------------
# sellers
# --------------------------------------------------------------------------
def clean_sellers(raw: pd.DataFrame) -> CleaningResult:
    df = raw.copy()
    for col in ("seller_city", "seller_state"):
        df[col] = df[col].str.strip()
    df["seller_zip_code_prefix"] = df["seller_zip_code_prefix"].astype("Int64")
    return _result(
        "dim_seller",
        len(raw),
        df,
        issues={"missing_city": int(df["seller_city"].isna().sum()),
                "missing_state": int(df["seller_state"].isna().sum())},
    )


# --------------------------------------------------------------------------
# fact: order items
# --------------------------------------------------------------------------
def clean_order_items(raw: pd.DataFrame) -> CleaningResult:
    df = raw.copy()
    price = df["price"]
    freight = df["freight_value"]

    invalid_price = (price.isna() | (price <= 0)).fillna(True)
    invalid_freight = (freight < 0).fillna(True)
    df["is_valid_price"] = (~invalid_price).astype("boolean")
    df["is_valid_freight"] = (~invalid_freight).astype("boolean")

    issues = {
        "missing_price": int(price.isna().sum()),
        "non_positive_price": int((price <= 0).sum()),
        "negative_freight": int((freight < 0).sum()),
        "missing_shipping_limit_date": int(df["shipping_limit_date"].isna().sum()),
        "repeated_product_within_order": int(df.duplicated(subset=["order_id", "product_id"]).sum()),
    }
    # Rows are retained (a repeated product on one order is a real quantity),
    # but they are flagged so a naive SUM over a joined frame can be caught.
    return _result(
        "fact_order_item",
        len(raw),
        df,
        issues=issues,
        actions={"retained_repeated_product_lines": issues["repeated_product_within_order"]},
    )


# --------------------------------------------------------------------------
# bridge: payments
# --------------------------------------------------------------------------
def clean_payments(raw: pd.DataFrame) -> CleaningResult:
    df = raw.copy()
    value = df["payment_value"]
    inst = df["payment_installments"]

    df["is_valid_payment_value"] = (value > 0).astype("boolean")
    df["is_valid_installments"] = (inst > 0).astype("boolean")

    issues = {
        "non_positive_payment_value": int((value <= 0).sum()),
        "missing_payment_value": int(value.isna().sum()),
        "non_positive_installments": int((inst <= 0).sum()),
        "zero_or_negative_installments_and_value": int(((inst <= 0) | (value <= 0)).sum()),
    }
    return _result(
        "bridge_payment",
        len(raw),
        df,
        issues=issues,
        actions={"kept_but_flagged": issues["zero_or_negative_installments_and_value"]},
    )


# --------------------------------------------------------------------------
# bridge: reviews
# --------------------------------------------------------------------------
def clean_reviews(raw: pd.DataFrame) -> CleaningResult:
    """Collapse to **one review per order** - the grain required by
    ``fact_order_summary.avg_review_score``.

    Measured facts driving this rule (recomputed every run, never assumed):
      * 789 ``review_id`` values are attached to two different ``order_id``\\ s
        while score/message/timestamp are identical - one review, two orders.
      * 547 ``order_id`` values carry more than one ``review_id`` - two reviews
        for one order, which would double-weight CSAT if left in.

    Keeping the **latest** answer per order is the documented tie-break.
    """
    df = raw.copy()
    n = len(df)

    dup_review_ids = int(df["review_id"].duplicated().sum())
    dup_order_ids = int(df["order_id"].duplicated().sum())

    # How many orders would be double-weighted with conflicting scores?
    score_nuniques = df.groupby("order_id")["review_score"].nunique(dropna=True)
    conflicting = int((score_nuniques > 1).sum())

    df = df.sort_values(["order_id", "review_answer_timestamp", "review_id"])
    before = len(df)
    deduped = df.drop_duplicates(subset=["order_id"], keep="last").copy()
    removed = before - len(deduped)

    # Provenance so the dropped duplicates stay auditable.
    provenance = (
        df.groupby("order_id")
        .agg(
            source_review_rows=("review_id", "size"),
            distinct_review_ids=("review_id", "nunique"),
        )
        .reset_index()
    )
    deduped = deduped.merge(provenance, on="order_id", how="left")

    # A single review_id still legitimately serves two orders after this pass.
    review_id_reused = int(deduped["review_id"].duplicated().sum())

    issues = {
        "source_rows": n,
        "duplicate_review_id_rows": dup_review_ids,
        "duplicate_order_id_rows": dup_order_ids,
        "orders_with_conflicting_duplicate_scores": conflicting,
        "missing_comment_title": int(deduped["review_comment_title"].isna().sum()),
        "missing_comment_message": int(deduped["review_comment_message"].isna().sum()),
        "missing_score": int(deduped["review_score"].isna().sum()),
        "score_out_of_range": int((~deduped["review_score"].between(1, 5)).sum()),
        "review_id_reused_across_orders_after_dedup": review_id_reused,
    }
    actions = {
        "collapsed_to_one_review_per_order": removed,
        "kept_latest_answer_timestamp": len(deduped),
        "kept_comments_null_not_empty_string": int(
            deduped["review_comment_message"].isna().sum()
        ),
    }
    return _result(
        "bridge_review",
        n,
        deduped,
        excluded_rows=removed,
        exclusion_reason="second-or-later review on the same order (would double-weight CSAT)",
        issues=issues,
        actions=actions,
    )


# --------------------------------------------------------------------------
# dimension: geolocation
# --------------------------------------------------------------------------
def clean_geolocation(raw: pd.DataFrame, referenced_zips: set[int] | None = None) -> CleaningResult:
    """Aggregate ~1M geolocation rows to **one row per ZIP-code prefix**.

    Documented strategy
    -------------------
    1. Remove exact duplicate rows (all five columns identical) - pure
       repetition of the same observation, no information lost.
    2. Quarantine coordinates outside the Brazil bounding box
       (rule C4). They are written to ``quarantine_geolocation_outliers.csv``,
       never deleted from disk.
    3. Group by ``geolocation_zip_code_prefix`` and aggregate:
         * lat / lng -> **median** of the valid coordinates. The median (not the
           mean) keeps a single bad coordinate from dragging a ZIP centroid
           off-shore; with thousands of points per ZIP the difference from the
           mean is negligible while robustness is much higher.
         * city / state -> **mode** (most frequent label), the modal
           administrative value.
         * ``valid_coordinate_count`` / ``source_row_count`` -> audit trail.
    4. A ZIP whose every coordinate was quarantined is **kept** in the dimension
       with NULL lat/lng and ``is_valid_coordinate = False``. Dropping it would
       create new foreign-key orphans on customer/seller addresses.
    5. Any ZIP prefix referenced by a customer or seller address that the source
       file simply does not cover is appended with ``source_row_count = 0`` and
       a NULL centroid. The dimension grain becomes *source ZIPs union ZIPs
       referenced by an address*, which keeps referential integrity intact
       without inventing coordinates; the gap stays visible as a warning.

    Parameters
    ----------
    referenced_zips
        ZIP prefixes used by customer and seller addresses (rule: an address
        must always be able to join to the ZIP master).
    """
    df = raw.copy()
    n = len(df)

    exact_dups = int(df.duplicated().sum())
    df = df.drop_duplicates()

    lat_ok = df["geolocation_lat"].between(config.BRAZIL_LAT_MIN, config.BRAZIL_LAT_MAX)
    lng_ok = df["geolocation_lng"].between(config.BRAZIL_LNG_MIN, config.BRAZIL_LNG_MAX)
    invalid = ~(lat_ok.fillna(False) & lng_ok.fillna(False))
    invalid_count = int(invalid.sum())

    quarantine = df.loc[invalid].copy()
    quarantine["rejection_reason"] = "coordinate outside Brazil bounding box"
    valid = df.loc[~invalid]

    def _mode(series: pd.Series):
        modes = series.mode(dropna=True)
        return modes.iat[0] if len(modes) else None

    dim = (
        valid.groupby("geolocation_zip_code_prefix", as_index=False)
        .agg(
            lat_median=("geolocation_lat", "median"),
            lng_median=("geolocation_lng", "median"),
            city_mode=("geolocation_city", _mode),
            state_mode=("geolocation_state", _mode),
            valid_coordinate_count=("geolocation_lat", "size"),
        )
        .rename(columns={"lat_median": "geolocation_lat", "lng_median": "geolocation_lng",
                         "city_mode": "geolocation_city", "state_mode": "geolocation_state"})
    )

    source_counts = df.groupby("geolocation_zip_code_prefix").size().rename("source_row_count")
    dim = dim.merge(source_counts, on="geolocation_zip_code_prefix", how="left")

    # --- step 4: ZIPs in the source that lost every coordinate to quarantine
    # --- step 5: ZIPs referenced by an address but absent from the source ----
    source_zips = set(df["geolocation_zip_code_prefix"].dropna())
    missing_zips = sorted(source_zips - set(dim["geolocation_zip_code_prefix"]))
    referenced = {z for z in (referenced_zips or set()) if pd.notna(z)}
    unseen_zips = sorted(referenced - source_zips)
    placeholders = missing_zips + unseen_zips

    if placeholders:
        orphan = pd.DataFrame({"geolocation_zip_code_prefix": placeholders})
        orphan = orphan.merge(source_counts, on="geolocation_zip_code_prefix", how="left")
        orphan["source_row_count"] = orphan["source_row_count"].fillna(0).astype("Int64")
        orphan["geolocation_lat"] = pd.NA
        orphan["geolocation_lng"] = pd.NA
        orphan["geolocation_city"] = None
        orphan["geolocation_state"] = None
        orphan["valid_coordinate_count"] = 0
        dim = pd.concat([dim, orphan], ignore_index=True)

    dim["valid_coordinate_count"] = dim["valid_coordinate_count"].fillna(0).astype("Int64")
    dim["is_valid_coordinate"] = dim["valid_coordinate_count"].gt(0).astype("boolean")
    dim = dim.sort_values("geolocation_zip_code_prefix").reset_index(drop=True)
    dim["geolocation_zip_code_prefix"] = dim["geolocation_zip_code_prefix"].astype("Int64")

    issues = {
        "source_rows": n,
        "exact_duplicate_rows": exact_dups,
        "coordinates_outside_brazil": invalid_count,
        "unique_observations_after_dedup": len(df),
        "distinct_zip_prefixes": int(df["geolocation_zip_code_prefix"].nunique()),
        "zips_with_all_coordinates_quarantined": len(missing_zips),
        "zips_referenced_by_address_but_absent_from_source": len(unseen_zips),
    }
    actions = {
        "removed_exact_duplicates": exact_dups,
        "quarantined_outlier_coordinates": invalid_count,
        "aggregated_to_one_row_per_zip": len(dim),
        "kept_null_centroid_for_unusable_zips": len(missing_zips),
        "appended_address_only_zip_placeholders": len(unseen_zips),
    }
    logger.info(
        "geolocation: %s rows -> %s rows (%s exact dups removed, %s outliers quarantined, "
        "%s address-only ZIPs appended)",
        f"{n:,}", f"{len(dim):,}", f"{exact_dups:,}", f"{invalid_count:,}", f"{len(unseen_zips):,}",
    )
    return _result(
        "dim_geolocation",
        n,
        dim,
        excluded_rows=exact_dups + invalid_count,
        exclusion_reason="exact duplicate rows + coordinates outside Brazil (quarantined, not deleted)",
        issues=issues,
        actions=actions,
        quarantine=quarantine,
    )
