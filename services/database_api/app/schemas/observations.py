from datetime import date, datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from services.database_api.app.schemas.common import APIModel, require_utc


Hash = str
SourceKind = Literal["historical_annual", "historical_monthly", "realtime"]
Quality = Literal["unverified", "valid", "partial", "suspect", "rejected"]


class SourceFileIn(APIModel):
    station_id: Literal["51201"] = "51201"
    feed_key: SourceKind
    ingestion_run_id: int | None = None
    original_filename: str
    source_url: str
    storage_format: str
    gcs_uri: str | None = None
    local_path: str | None = None
    content_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    size_bytes: int | None = Field(default=None, ge=0)
    compression_type: str = "none"
    source_period_start: date | None = None
    source_period_end: date | None = None
    gcs_generation: str | None = Field(default=None, pattern=r"^[0-9]+$")
    gcs_crc32c: str | None = None
    gcs_upload_status: Literal["pending", "uploaded", "verified", "failed", "not_applicable"] | None = None
    gcs_bucket: str | None = None
    gcs_object_name: str | None = None
    source_etag: str | None = None

    @model_validator(mode="after")
    def storage_valid(self):
        if not self.gcs_uri and not self.local_path:
            raise ValueError("a storage location is required")
        if self.gcs_uri and not self.gcs_uri.startswith("gs://"):
            raise ValueError("GCS URI must start with gs://")
        if self.gcs_generation and not self.gcs_uri:
            raise ValueError("GCS generation requires URI")
        if self.gcs_upload_status == "verified" and not (self.gcs_uri and self.gcs_generation):
            raise ValueError("verified GCS files require URI and generation")
        return self


class RawRow(APIModel):
    station_id: Literal["51201"] = "51201"
    source_line_number: int | None = Field(default=None, gt=0)
    observed_at: datetime | None = None
    raw_line: str | None = None
    raw_values: dict[str, Any] = Field(default_factory=dict)
    row_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    parse_ok: bool = True
    parse_error: str | None = None

    @model_validator(mode="after")
    def has_payload(self):
        if self.raw_line is None and not self.raw_values:
            raise ValueError("raw line or values required")
        return self

    @field_validator("observed_at")
    @classmethod
    def utc(cls, value):
        return require_utc(value) if value is not None else value


class RawBatch(APIModel):
    file_id: int = Field(gt=0)
    rows: list[RawRow] = Field(min_length=1, max_length=500)


class SilverRow(APIModel):
    station_id: Literal["51201"] = "51201"
    observed_at: datetime
    source_kind: SourceKind
    source_dataset: str
    source_file_id: int = Field(gt=0)
    raw_id: int | None = None
    measurement_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    quality_status: Quality = "unverified"
    quality_flags: dict[str, Any] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    wind_direction_deg: float | None = Field(default=None, ge=0, le=360)
    wind_speed_ms: float | None = Field(default=None, ge=0)
    wind_gust_ms: float | None = Field(default=None, ge=0)
    wave_height_m: float | None = Field(default=None, ge=0)
    dominant_period_s: float | None = Field(default=None, gt=0)
    average_period_s: float | None = Field(default=None, gt=0)
    mean_wave_direction_deg: float | None = Field(default=None, ge=0, le=360)
    sea_level_pressure_hpa: float | None = Field(default=None, gt=0)
    air_temperature_c: float | None = None
    sea_surface_temp_c: float | None = None
    dew_point_c: float | None = None
    visibility_nmi: float | None = Field(default=None, ge=0)
    pressure_tendency_hpa: float | None = None
    tide_ft: float | None = None

    @field_validator("observed_at")
    @classmethod
    def utc(cls, value):
        return require_utc(value)


class SilverBatch(APIModel):
    rows: list[SilverRow] = Field(min_length=1, max_length=500)


class DailyFeatures(APIModel):
    station_id: Literal["51201"] = "51201"
    date_hst: date
    sample_count: int = Field(ge=0)
    valid_wave_count: int = Field(ge=0)
    expected_sample_count: int | None = Field(default=None, ge=0)
    coverage_ratio: float | None = Field(default=None, ge=0, le=1)
    mean_wave_height_m: float | None = None
    median_wave_height_m: float | None = None
    max_wave_height_m: float | None = None
    min_wave_height_m: float | None = None
    p95_wave_height_m: float | None = None
    stddev_wave_height_m: float | None = None
    mean_dominant_period_s: float | None = None
    circular_mean_direction_deg: float | None = Field(default=None, ge=0, le=360)
    mean_sea_surface_temp_c: float | None = None
    max_wind_speed_ms: float | None = None
    good_quality_count: int = Field(ge=0)
    calculation_version: str = "v1"
    mean_average_period_s: float | None = None
    max_dominant_period_s: float | None = None
    mean_wind_speed_ms: float | None = None
    mean_air_temperature_c: float | None = None
    mean_sea_level_pressure_hpa: float | None = None
    valid_dominant_period_count: int | None = Field(default=None, ge=0)
    source_data_max_updated_at: datetime | None = None

    @model_validator(mode="after")
    def counts_valid(self):
        if self.valid_wave_count > self.sample_count or self.good_quality_count > self.sample_count:
            raise ValueError("valid counts cannot exceed sample count")
        return self


class IngestionRunIn(APIModel):
    station_id: Literal["51201"] = "51201"
    feed_key: SourceKind
    scheduled_at: datetime | None = None
    request_url: str | None = None


class IngestionRunUpdate(APIModel):
    status: Literal["running", "success", "partial", "failed", "skipped"]
    received_rows: int | None = Field(default=None, ge=0)
    inserted_rows: int | None = Field(default=None, ge=0)
    revised_rows: int | None = Field(default=None, ge=0)
    duplicate_rows: int | None = Field(default=None, ge=0)
    rejected_rows: int | None = Field(default=None, ge=0)
    error_type: str | None = None
    error_message: str | None = None


class QualityIssueIn(APIModel):
    station_id: Literal["51201"] = "51201"
    observed_at: datetime | None = None
    raw_id: int | None = None
    file_id: int | None = None
    field_code: str | None = None
    issue_type: str
    severity: Literal["info", "warning", "error"] = "warning"
    original_value: str | None = None
    explanation: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class GapIn(APIModel):
    station_id: Literal["51201"] = "51201"
    gap_start_at: datetime
    gap_end_at: datetime
    assumed_interval_min: int | None = Field(default=None, gt=0)
    estimated_missing_slots: int | None = Field(default=None, ge=0)
    notes: str | None = None

    @model_validator(mode="after")
    def ordered_utc(self):
        self.gap_start_at = require_utc(self.gap_start_at)
        self.gap_end_at = require_utc(self.gap_end_at)
        if self.gap_end_at <= self.gap_start_at:
            raise ValueError("gap end must follow start")
        return self
