"""Reconcile recent candidate slots without declaring permanent NOAA failure."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from packages.ml.data_quality import infer_cadence


def reconcile_window(gateway, start: datetime, end: datetime) -> dict:
    if end <= start or end-start > timedelta(days=8):
        raise ValueError("gap window must be at most 8 days")
    rows = gateway.get_observations("51201", start, end)
    cadence = infer_cadence(rows, min_intervals=48, min_support_ratio=0.9)
    if cadence is None or len(rows) < 2:
        return {"status": "INSUFFICIENT_CADENCE_EVIDENCE", "open": None}
    times = sorted({row.observed_at for row in rows})
    interval = timedelta(minutes=cadence.minutes)
    slots = []
    for left, right in zip(times, times[1:]):
        span = (right-left).total_seconds() / interval.total_seconds()
        count = round(span)
        if 1 < count <= 49 and abs(span-count) <= .05:
            slots.extend(left + interval * offset for offset in range(1, count))
    if len(slots) > 500:
        return {"status": "GAP_COUNT_EXCEEDS_BUDGET", "open": len(slots)}
    result = gateway.reconcile_gaps(window_start=times[0], window_end=times[-1],
                                    cadence_minutes=cadence.minutes, missing_slots=slots)
    return {"status": "RECONCILED", "cadence_minutes": cadence.minutes, **result}


def reconcile_recent(gateway, *, as_of: datetime | None = None) -> dict:
    as_of = as_of or datetime.now(timezone.utc)
    return reconcile_window(gateway, as_of - timedelta(days=7), as_of)
