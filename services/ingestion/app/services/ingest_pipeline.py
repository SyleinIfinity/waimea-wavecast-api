"""
services/ingestion/app/services/ingest_pipeline.py

Core ingestion pipeline logic (SAGA pattern):

DISCOVERED -> DOWNLOADED -> GCS_UPLOADED -> SOURCE_FILE_REGISTERED
  -> PARSED -> RAW_UPSERTED -> SILVER_APPLIED

Each step is idempotent — retrying from any step is safe.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Literal

from packages.db.gateway import (
    DatabaseGateway,
    RawObservationDTO,
    SilverObservationDTO,
    SourceFileDTO,
)
from packages.gcs.client import GCSClientProtocol
from packages.gcs.paths import annual_object_path, monthly_object_path, realtime_object_path
from packages.noaa.client import NOAAClient
from packages.noaa.hash import file_sha256, measurement_sha256, row_sha256
from packages.noaa.normalize import normalize_row
from packages.noaa.parser import ParsedRow, parse_stdmet
from packages.noaa.urls import annual_url, monthly_url, realtime_url

logger = logging.getLogger(__name__)

FeedKey = Literal["historical_annual", "historical_monthly", "realtime"]

STATION_ID = "51201"
PARSER_VERSION = "v1.0"


@dataclass
class IngestResult:
    """Summary of one ingest pipeline run."""
    feed_key: FeedKey
    period_label: str         # e.g. "2023", "2023-05", "realtime"
    file_id: int | None = None
    gcs_uri: str | None = None
    gcs_generation: int | None = None
    raw_inserted: int = 0
    raw_duplicate: int = 0
    silver_inserted: int = 0
    silver_revised: int = 0
    silver_duplicate: int = 0
    silver_skipped: int = 0
    parse_errors: int = 0
    success: bool = False
    error: str | None = None
    latest_observed_at: datetime | None = None
    changed_from: datetime | None = None
    changed_to: datetime | None = None
    retry_after_seconds: int | None = None


def _make_object_path(feed_key: FeedKey, year: int, month: int | None, sha256: str) -> str:
    if feed_key == "historical_annual":
        return annual_object_path(STATION_ID, year, sha256)
    elif feed_key == "historical_monthly":
        assert month is not None
        return monthly_object_path(STATION_ID, year, month, sha256)
    else:  # realtime
        return realtime_object_path(STATION_ID, date.today(), sha256)


def _make_url(feed_key: FeedKey, year: int, month: int | None) -> str:
    if feed_key == "historical_annual":
        return annual_url(year)
    elif feed_key == "historical_monthly":
        assert month is not None
        return monthly_url(year, month)
    else:
        return realtime_url()


def _build_silver_dto(
    row: ParsedRow,
    station_id: str,
    feed_key: FeedKey,
    file_id: int,
    raw_id: int | None,
    source_dataset: str,
) -> SilverObservationDTO:
    """Convert a parsed + normalized row into a SilverObservationDTO."""
    normalized = normalize_row(row.raw_values)
    msha = measurement_sha256(station_id, row.observed_at, normalized)

    missing = [field for field, val in normalized.items() if val is None]

    return SilverObservationDTO(
        station_id=station_id,
        observed_at=row.observed_at,
        source_kind=feed_key,
        source_dataset=source_dataset,
        source_file_id=file_id,
        raw_id=raw_id,
        measurement_sha256=msha,
        quality_status="unverified",
        missing_fields=missing,
        wind_direction_deg=normalized.get("WDIR"),
        wind_speed_ms=normalized.get("WSPD"),
        wind_gust_ms=normalized.get("GST"),
        wave_height_m=normalized.get("WVHT"),
        dominant_period_s=normalized.get("DPD"),
        average_period_s=normalized.get("APD"),
        mean_wave_direction_deg=normalized.get("MWD"),
        sea_level_pressure_hpa=normalized.get("PRES"),
        air_temperature_c=normalized.get("ATMP"),
        sea_surface_temp_c=normalized.get("WTMP"),
        dew_point_c=normalized.get("DEWP"),
        visibility_nmi=normalized.get("VIS"),
        pressure_tendency_hpa=normalized.get("PTDY"),
        tide_ft=normalized.get("TIDE"),
    )


def run_ingest(
    feed_key: FeedKey,
    year: int,
    month: int | None = None,
    *,
    noaa_client: NOAAClient | None = None,
    gcs_client: GCSClientProtocol | None = None,
    db_gateway: DatabaseGateway | None = None,
    bucket: str = "waimea-noaa",
    replay_object_path: str | None = None,
    replay_generation: int | None = None,
) -> IngestResult:
    """
    Execute the full ingestion SAGA for one (feed_key, year[, month]) period.

    Args:
        feed_key:    One of 'historical_annual', 'historical_monthly', 'realtime'.
        year:        Calendar year (integer).
        month:       Month 1-12 (required for monthly feed).
        noaa_client: HTTP client; defaults to NOAAClient().
        gcs_client:  Explicit GCS adapter.
        db_gateway:  Explicit Database API adapter.
        bucket:      GCS bucket name.

    Returns:
        IngestResult with counters.
    """
    if noaa_client is None:
        noaa_client = NOAAClient()
    if gcs_client is None or db_gateway is None:
        raise ValueError("GCS and database gateways must be supplied explicitly")

    period_label = str(year) if month is None else f"{year}-{month:02d}"
    result = IngestResult(feed_key=feed_key, period_label=period_label)

    url = _make_url(feed_key, year, month)
    logger.info("Ingesting %s / %s from %s", feed_key, period_label, url)

    # --- Step 1: Download from NOAA, or replay the original immutable GCS object. ---
    obj_info = None
    if replay_object_path is not None:
        allowed_replay_path = replay_object_path.startswith("noaa/raw/") or (
            replay_object_path.startswith("test/tl06/") and "/noaa/raw/" in replay_object_path
        )
        if not allowed_replay_path or replay_generation is None:
            raise ValueError("replay requires a NOAA raw object path and pinned generation")
        try:
            obj_info = gcs_client.object_info(bucket, replay_object_path)
            if obj_info is None or obj_info.generation != replay_generation:
                raise ValueError("replay object generation not found")
            raw_bytes = gcs_client.download(bucket, replay_object_path, replay_generation)
        except Exception as exc:
            result.error = f"GCS replay download failed: {type(exc).__name__}"
            return result
    else:
        try:
            raw_bytes = noaa_client.download(url)
        except Exception as exc:
            logger.warning("Download failed for %s %s: %s", feed_key, period_label, exc)
            result.error = str(exc)
            response = getattr(exc, "response", None)
            if response is not None and response.status_code == 429:
                retry_after = response.headers.get("Retry-After", "60")
                try:
                    seconds = float(retry_after)
                except ValueError:
                    try:
                        seconds = (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds()
                    except (TypeError, ValueError):
                        seconds = 60
                result.retry_after_seconds = min(3600, max(60, int(seconds)))
            return result

    if not raw_bytes:
        result.error = "NOAA returned an empty payload"
        return result

    # --- Step 2: Parse before writing; malformed/empty datasets cannot look successful. ---
    try:
        parse_result = parse_stdmet(raw_bytes, filename=url)
    except Exception as exc:
        logger.warning("NOAA parse failed for %s %s: %s", feed_key, period_label, type(exc).__name__)
        result.error = f"NOAA payload invalid: {type(exc).__name__}"
        return result
    result.parse_errors = parse_result.parse_error_count
    if not parse_result.rows:
        result.error = "NOAA payload contained no valid observations"
        return result
    result.latest_observed_at = max(row.observed_at for row in parse_result.rows)

    fhash = file_sha256(raw_bytes)
    if obj_info is not None and obj_info.content_sha256 and obj_info.content_sha256 != fhash:
        result.error = "GCS replay object checksum mismatch"
        return result
    compression = "gz" if raw_bytes[:2] == b"\x1f\x8b" else "none"

    # --- Step 3: Upload to GCS (if_generation_match=0) ---
    obj_path = replay_object_path or _make_object_path(feed_key, year, month, fhash)
    if obj_info is None:
        try:
            obj_info = gcs_client.upload_if_absent(
                bucket=bucket,
                object_path=obj_path,
                data=raw_bytes,
                content_type="application/x-gzip" if compression == "gz" else "text/plain",
                metadata={"content_sha256": fhash, "station_id": STATION_ID, "feed_key": feed_key, "source_url": url},
            )
        except Exception as exc:
            logger.error("GCS upload failed for %s %s: %s", feed_key, period_label, exc)
            result.error = f"GCS upload failed: {exc}"
            return result
    result.gcs_uri = obj_info.gcs_uri
    result.gcs_generation = obj_info.generation

    # --- Step 4: Register source file in DB ---
    if parse_result.parse_error_count > 0:
        logger.warning(
            "%d parse errors in %s %s: %s",
            parse_result.parse_error_count, feed_key, period_label,
            parse_result.errors[:3],
        )

    # Determine source period from parsed rows
    dates = [r.observed_at.date() for r in parse_result.rows]
    period_start = min(dates) if dates else None
    period_end = max(dates) if dates else None

    source_file_dto = SourceFileDTO(
        station_id=STATION_ID,
        feed_id=None,       # Database API resolves the feed ID from feed_key.
        feed_key=feed_key,
        original_filename=obj_path.split("/")[-1],
        source_url=url,
        storage_format="ndbc_stdmet_txt",
        gcs_uri=obj_info.gcs_uri,
        gcs_generation=obj_info.generation,
        content_sha256=fhash,
        size_bytes=len(raw_bytes),
        compression_type=compression,
        source_period_start=period_start,
        source_period_end=period_end,
        parse_status="parsed" if parse_result.parse_error_count == 0 else "partial",
        columns_in_source=parse_result.columns_in_source,
        gcs_bucket=bucket,
        gcs_object_name=obj_info.object_path,
        gcs_upload_status="verified",
        parser_version=PARSER_VERSION,
        source_row_count=len(parse_result.rows),
        parse_error_count=parse_result.parse_error_count,
        parsed_at=datetime.now(timezone.utc),
    )

    try:
        file_id = db_gateway.register_source_file(source_file_dto)
        result.file_id = file_id
    except Exception as exc:
        logger.error("DB register_source_file failed: %s", exc)
        result.error = f"DB source file registration failed: {exc}"
        return result

    # --- Steps 5 & 6: Upsert raw + silver in batches ---
    batch_size = 500
    rows = parse_result.rows
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]

        # Raw batch
        raw_dtos = [
            RawObservationDTO(
                file_id=file_id,
                station_id=STATION_ID,
                source_line_number=r.line_number,
                observed_at=r.observed_at,
                raw_line=r.source_line,
                raw_values=r.raw_values,
                row_sha256=row_sha256(r.source_line),
            )
            for r in batch
        ]
        try:
            raw_counts = db_gateway.upsert_raw_batch(raw_dtos)
            result.raw_inserted += raw_counts["inserted"]
            result.raw_duplicate += raw_counts["duplicate"]
            raw_ids = raw_counts["raw_ids"]
            if any(r.row_sha256 not in raw_ids for r in raw_dtos):
                raise ValueError("raw upsert omitted lineage IDs")
        except Exception as exc:
            logger.error("DB upsert_raw_batch failed at offset %d: %s", i, exc)
            result.error = f"Raw upsert failed: {exc}"
            return result

        # Silver batch
        silver_dtos = [
            _build_silver_dto(
                row=r,
                station_id=STATION_ID,
                feed_key=feed_key,
                file_id=file_id,
                raw_id=raw_ids[row_sha256(r.source_line)],
                source_dataset=f"noaa_ndbc_{feed_key}_{period_label}",
            )
            for r in batch
        ]
        try:
            silver_counts = db_gateway.upsert_silver_batch(silver_dtos)
            result.silver_inserted += silver_counts["inserted"]
            result.silver_revised += silver_counts.get("revised", silver_counts.get("updated", 0))
            result.silver_duplicate += silver_counts["duplicate"]
            result.silver_skipped += silver_counts.get("skipped", 0)
            if silver_counts.get("changed_from"):
                changed_from = datetime.fromisoformat(silver_counts["changed_from"])
                changed_to = datetime.fromisoformat(silver_counts["changed_to"])
                result.changed_from = min(result.changed_from, changed_from) if result.changed_from else changed_from
                result.changed_to = max(result.changed_to, changed_to) if result.changed_to else changed_to
        except Exception as exc:
            logger.error("DB upsert_silver_batch failed at offset %d: %s", i, exc)
            result.error = f"Silver upsert failed: {exc}"
            return result

    result.success = True
    logger.info(
        "Ingest complete: %s %s | raw(ins=%d dup=%d) silver(ins=%d rev=%d dup=%d) errors=%d",
        feed_key, period_label,
        result.raw_inserted, result.raw_duplicate,
        result.silver_inserted, result.silver_revised, result.silver_duplicate,
        result.parse_errors,
    )
    return result


def replay_stored_object(feed_key: FeedKey, year: int, *, object_path: str,
                         generation: int, month: int | None = None,
                         gcs_client: GCSClientProtocol, db_gateway: DatabaseGateway,
                         bucket: str = "waimea-noaa") -> IngestResult:
    """Resume an orphaned GCS object without re-fetching a changed NOAA feed."""
    return run_ingest(feed_key, year, month, gcs_client=gcs_client, db_gateway=db_gateway,
                      bucket=bucket, replay_object_path=object_path,
                      replay_generation=generation)
