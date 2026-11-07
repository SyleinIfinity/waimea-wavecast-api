from datetime import datetime
from zoneinfo import ZoneInfo

HAWAII = ZoneInfo("Pacific/Honolulu")

def local_day(dt: datetime):
    if dt.tzinfo is None:
        raise ValueError("Provide an offset-aware datetime")
    return dt.astimezone(HAWAII).date()
