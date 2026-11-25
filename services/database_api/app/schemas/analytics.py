from datetime import date
from typing import Any, Literal

from pydantic import Field

from services.database_api.app.schemas.common import APIModel


GoldTable = Literal["time_series_metrics", "resolution_metrics", "trend_seasonal_components", "correlations", "cluster_assignments", "predictions", "classifications"]


class GoldBatch(APIModel):
    table: GoldTable
    rows: list[dict[str, Any]] = Field(min_length=1, max_length=500)


class DemoScope(APIModel):
    station_id: Literal["51201"] = "51201"
    from_hst: date
    to_hst: date
    pattern_types: list[GoldTable] = Field(min_length=1)


class DeleteConfirmation(APIModel):
    dry_run_id: str
    confirmation: str
