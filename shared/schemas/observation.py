from datetime import datetime
from pydantic import BaseModel

class Observation(BaseModel):
    station_id: str
    source: str
    observed_at: datetime
    wave_height_m: float | None
    dominant_period_s: float | None
    wave_direction_deg: float | None
    sea_temperature_c: float | None
    quality_flag: str
