from datetime import datetime, timezone
from shared.utils.timezone import local_day

def test_hawaii_calendar_date_differs_from_utc():
    dt = datetime(2026, 11, 6, 5, 0, tzinfo=timezone.utc)
    assert str(local_day(dt)) == '2026-11-05'
