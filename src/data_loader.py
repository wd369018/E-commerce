"""
ShopScope - ingestion layer (Stage 1 of the pipeline).

Responsibilities
----------------
* Locate each required raw CSV using project-root-relative paths.
* Verify the file contract (required columns present, no duplicate headers).
* Load the file **read-only** and return an untouched copy of the source rows.
* Record a SHA-256 digest of every raw file so the run can later *prove*
  the sources were not modified.
* Cast date and numeric columns to proper dtypes, counting every value that
  fails to convert instead of dropping it.

This module never writes to the raw CSVs. All writes happen in
``etl_pipeline.write_outputs``.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import config

logger = logging.getLogger(__name__)


class DataLoadError(RuntimeError):
    """Raised when a raw file is missing or violates its declared contract."""


# --------------------------------------------------------------------------
# Raw file location + integrity
# --------------------------------------------------------------------------
def find_raw_file(filename: str) -> Path:
    """Resolve ``filename`` against the configured search directories."""
    for directory in config.RAW_SEARCH_DIRS:
        candidate = directory / filename
        if candidate.is_file():
            return candidate
    searched = "\n  ".join(str(d / filename) for d in config.RAW_SEARCH_DIRS)
    raise DataLoadError(
        f"Required raw file '{filename}' not found. Searched:\n  {searched}"
    )


def sha256_of(path: Path, chunk_size: int = 1 << 20) -> str:
    """Streamed SHA-256 so large files (geolocation, 58 MB) stay cheap."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_raw_files() -> dict[str, str]:
    """Digest every raw CSV before the pipeline touches anything."""
    return {name: sha256_of(find_raw_file(name)) for name in config.REQUIRED_COLUMNS}


def verify_raw_unchanged(before: dict[str, str]) -> list[str]:
    """Return a list of files whose digest changed during the run (should be empty)."""
    changed: list[str] = []
    for name, digest in before.items():
        if sha256_of(find_raw_file(name)) != digest:
            changed.append(name)
    return changed


# --------------------------------------------------------------------------
# Loading + contract validation
# --------------------------------------------------------------------------
@dataclass
class LoadResult:
    """One raw file, its source path, digest and basic load statistics."""

    name: str
    path: Path
    sha256: str
    frame: pd.DataFrame
    source_rows: int
    source_columns: int
    file_bytes: int
    parse_failures: dict[str, int] = field(default_factory=dict)


def validate_contract(name: str, frame: pd.DataFrame) -> None:
    """Fail fast if the file does not contain the columns the pipeline needs."""
    required = config.REQUIRED_COLUMNS[name]
    missing = [c for c in required if c not in frame.columns]
    duplicated = [c for c in frame.columns if list(frame.columns).count(c) > 1]
    if missing:
        raise DataLoadError(
            f"{name}: missing required column(s) {missing}. "
            f"Found columns: {list(frame.columns)}"
        )
    if duplicated:
        raise DataLoadError(f"{name}: duplicate column header(s) {sorted(set(duplicated))}")
    extra = [c for c in frame.columns if c not in required]
    if extra:
        # Informational only - the Olist files are stable, but tolerate additions.
        logger.warning("%s: unexpected column(s) present and ignored: %s", name, extra)


def apply_dtypes(name: str, frame: pd.DataFrame) -> dict[str, int]:
    """Cast declared date/numeric columns; count (never hide) conversion failures."""
    failures: dict[str, int] = {}

    for col in config.DATE_COLUMNS.get(name, []):
        parsed = pd.to_datetime(frame[col], errors="coerce")
        bad = int(frame[col].notna().sum() - parsed.notna().sum())
        if bad:
            failures[f"{col} (unparseable date)"] = bad
        frame[col] = parsed

    for col, dtype in config.NUMERIC_COLUMNS.get(name, {}).items():
        parsed = pd.to_numeric(frame[col], errors="coerce")
        bad = int(frame[col].notna().sum() - parsed.notna().sum())
        if bad:
            failures[f"{col} (unparseable number)"] = bad
        frame[col] = parsed.astype(dtype)

    return failures


def load_all() -> dict[str, LoadResult]:
    """Load and type- every raw CSV. Raises :class:`DataLoadError` on any contract breach."""
    results: dict[str, LoadResult] = {}

    for name in config.REQUIRED_COLUMNS:
        path = find_raw_file(name)
        digest = sha256_of(path)
        logger.info("Loading %s (%.2f MB) from %s", name, path.stat().st_size / 1e6, path.name)

        # dtype=str keeps the source exactly as written; casting happens after
        # the contract check so a schema change is reported, not mis-typed.
        try:
            frame = pd.read_csv(path, dtype=str, keep_default_na=True)
        except Exception as exc:  # noqa: BLE001 - surfaced as a domain error
            raise DataLoadError(f"{name}: could not be parsed as CSV - {exc}") from exc

        validate_contract(name, frame)
        failures = apply_dtypes(name, frame)

        if failures:
            logger.warning("%s: type conversion failures: %s", name, failures)

        results[name] = LoadResult(
            name=name,
            path=path,
            sha256=digest,
            frame=frame,
            source_rows=len(frame),
            source_columns=frame.shape[1],
            file_bytes=path.stat().st_size,
            parse_failures=failures,
        )
        logger.info(
            "  -> %s rows=%s cols=%s cast_failures=%s",
            name,
            f"{len(frame):,}",
            frame.shape[1],
            sum(failures.values()),
        )

    return results
