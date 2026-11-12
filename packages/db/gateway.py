"""
packages/db/gateway.py

DatabaseGateway Protocol — the ONLY interface that TL04/TL05 workers use
to communicate with the database layer (owned by TL01).

TL04 never calls SQL directly; it calls these methods.
In tests, FakeDatabaseGateway (in-memory) is used.
In production, the HTTP implementation (calling TL01'"'"'s Database API) is used.

Contract from 00_HOP_DONG_CHUNG.md:
  register_source_file, upsert_raw_batch, upsert_silver_batch,
  upsert_daily_features, get_observations, get_daily_features,
  save_gold_results, save_model_metadata, get_model_metadata,
  save_job_status, get_job_status
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Shared DTOs (must match TL01'"'"'s OpenAPI contract)
# ---------------------------------------------------------------------------

@dataclass
class SourceFileDTO:
    station_id: str
    feed_id: int | None
    feed_key: str                 # 'historical_annual' | 'historical_monthly' | 'realtime'
    original_filename: str
    source_url: str
    storage_format: str
    gcs_uri: str
    gcs_generation: int
    content_sha256: str
    size_bytes: int
    compression_type: str
    source_period_start: date | None = None
    source_period_end: date | None = None
    parse_status: str = "pending"  # pending | parsed | partial | failed
    columns_in_source: list[str] = field(default_factory=list)
    gcs_bucket: str | None = None
    gcs_object_name: str | None = None
    gcs_upload_status: str = "verified"
    parser_version: str | None = None
    source_row_count: int | None = None
    parse_error_count: int = 0
    parsed_at: datetime | None = None


@dataclass
class RawObservationDTO:
    file_id: int
    station_id: str
    source_line_number: int
    observed_at: datetime
    raw_line: str
    raw_values: dict[str, Any]
    row_sha256: str
    parse_ok: bool = True
    parse_error: str | None = None


@dataclass
class SilverObservationDTO:
    station_id: str
    observed_at: datetime
    source_kind: str              # 'historical_annual' | 'historical_monthly' | 'realtime'
    source_dataset: str
    source_file_id: int
    raw_id: int | None
    measurement_sha256: str
    quality_status: str = "unverified"
    missing_fields: list[str] = field(default_factory=list)
    # 14 measurement fields — all nullable
    wind_direction_deg: float | None = None
    wind_speed_ms: float | None = None
    wind_gust_ms: float | None = None
    wave_height_m: float | None = None
    dominant_period_s: float | None = None
    average_period_s: float | None = None
    mean_wave_direction_deg: float | None = None
    sea_level_pressure_hpa: float | None = None
    air_temperature_c: float | None = None
    sea_surface_temp_c: float | None = None
    dew_point_c: float | None = None
    visibility_nmi: float | None = None
    pressure_tendency_hpa: float | None = None
    tide_ft: float | None = None


@dataclass
class DailyFeaturesDTO:
    station_id: str
    date_hst: date
    sample_count: int
    valid_wave_count: int
    expected_sample_count: int | None
    coverage_ratio: float | None
    mean_wave_height_m: float | None
    median_wave_height_m: float | None
    max_wave_height_m: float | None
    min_wave_height_m: float | None
    p95_wave_height_m: float | None
    stddev_wave_height_m: float | None
    mean_dominant_period_s: float | None
    circular_mean_direction_deg: float | None
    mean_sea_surface_temp_c: float | None
    max_wind_speed_ms: float | None
    good_quality_count: int = 0
    calculation_version: str = "v1"
    missing_count: int = 0
    suspect_count: int = 0
    rejected_count: int = 0
    unverified_count: int = 0


@dataclass
class JobStatusDTO:
    job_id: str
    job_type: str
    status: str           # running | success | partial | failed | skipped
    started_at: datetime
    finished_at: datetime | None = None
    error_message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class DatabaseGateway(Protocol):
    """
    Abstract interface for all database interactions by TL04 workers.
    TL01 provides the HTTP implementation; tests use FakeDatabaseGateway.
    """

    def register_source_file(self, dto: SourceFileDTO) -> int:
        """Register a downloaded source file. Returns file_id."""
        ...

    def upsert_raw_batch(self, rows: list[RawObservationDTO]) -> dict[str, int]:
        """Bulk-insert raw observation rows. Returns {inserted, duplicate}."""
        ...

    def upsert_silver_batch(self, rows: list[SilverObservationDTO]) -> dict[str, int]:
        """Upsert Silver canonical observations. Returns {inserted, revised, duplicate}."""
        ...

    def upsert_daily_features(self, rows: list[DailyFeaturesDTO]) -> dict[str, int]:
        """Upsert daily wave features (HST). Returns {upserted}."""
        ...

    def get_observations(
        self,
        station_id: str,
        start_at: datetime,
        end_at: datetime,
    ) -> list[SilverObservationDTO]:
        """Return Silver observations in [start_at, end_at) UTC range."""
        ...

    def get_daily_features(
        self,
        station_id: str,
        start_hst: date,
        end_hst: date,
    ) -> list[DailyFeaturesDTO]:
        """Return daily features for [start_hst, end_hst] inclusive."""
        ...

    def save_job_status(self, dto: JobStatusDTO) -> None:
        """Create or update a job status record."""
        ...

    def get_job_status(self, job_id: str) -> JobStatusDTO | None:
        """Retrieve job status by job_id; None if not found."""
        ...
