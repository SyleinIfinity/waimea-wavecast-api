from datetime import date, datetime, timezone
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


T = TypeVar("T")


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Page(APIModel, Generic[T]):
    status: str = "ok"
    data: list[T]
    next_cursor: str | None = None
    has_more: bool = False


class Item(APIModel, Generic[T]):
    status: str
    data: T | None


class BatchResult(APIModel):
    inserted: int = 0
    updated: int = 0
    duplicate: int = 0
    skipped: int = 0


class DateRange(APIModel):
    from_hst: date
    to_hst: date

    @model_validator(mode="after")
    def ordered(self):
        if self.to_hst < self.from_hst:
            raise ValueError("to_hst must be on or after from_hst")
        return self


def require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-aware UTC timestamp required")
    return value.astimezone(timezone.utc)


class JobUpdate(APIModel):
    status: str = Field(pattern="^(running|success|partial|failed|skipped)$")
    output_record_count: int | None = Field(default=None, ge=0)
    error_message: str | None = None


class JobCreate(APIModel):
    job_type: str = Field(pattern="^(aggregation|analysis|seasonality|correlation|clustering|training|prediction|classification)$")
    input_period_start: date | None = None
    input_period_end: date | None = None
    algorithm_version: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
