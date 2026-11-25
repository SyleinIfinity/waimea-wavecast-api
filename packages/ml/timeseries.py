"""Read-only, multi-resolution descriptive statistics for observed NOAA data."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from math import cos, pi, sin
from statistics import mean, median, pvariance, pstdev
from typing import Any
from zoneinfo import ZoneInfo

from packages.ml.data_quality import _get, _utc

HST = ZoneInfo("Pacific/Honolulu")
RESOLUTIONS = ("30min", "daily", "weekly", "monthly", "seasonal", "yearly")


def cyclical_features(observed_at: datetime) -> dict[str, float | str]:
    local = _utc(observed_at).astimezone(HST)
    hour = local.hour + local.minute / 60
    doy = local.timetuple().tm_yday
    month = local.month - 1
    return {"hour_sin": sin(2*pi*hour/24), "hour_cos": cos(2*pi*hour/24),
            "day_of_year_sin": sin(2*pi*doy/365.25),
            "day_of_year_cos": cos(2*pi*doy/365.25),
            "month_sin": sin(2*pi*month/12), "month_cos": cos(2*pi*month/12),
            "calendar_season": ("DJF", "MAM", "JJA", "SON")[(local.month % 12)//3]}


def _bounds(local: datetime, resolution: str) -> tuple[datetime, datetime]:
    midnight = datetime.combine(local.date(), time.min, HST)
    if resolution == "30min":
        start = local.replace(minute=0 if local.minute < 30 else 30, second=0, microsecond=0)
        return start, start + timedelta(minutes=30)
    if resolution == "daily": return midnight, midnight + timedelta(days=1)
    if resolution == "weekly":
        start = midnight - timedelta(days=midnight.weekday())
        return start, start + timedelta(days=7)
    if resolution == "monthly":
        start = midnight.replace(day=1)
        month = start.month + 1
        return start, start.replace(year=start.year + (month > 12), month=1 if month > 12 else month)
    if resolution == "seasonal":
        start = midnight.replace(month=((midnight.month-1)//3)*3+1, day=1)
        month = start.month+3
        return start, start.replace(year=start.year+(month>12), month=month if month<=12 else month-12)
    if resolution == "yearly":
        start = midnight.replace(month=1, day=1)
        return start, start.replace(year=start.year+1)
    raise ValueError("unknown resolution")


def aggregate_resolution(rows: list[Any], resolution: str, *, cadence_minutes: int,
                         minimum_coverage: float) -> list[dict]:
    """Full-period denominator prevents a few readings from implying coverage.

    Output remains descriptive even when a period fails the gate; consumers
    must inspect quality_status before using it for model training/inference.
    """
    if resolution not in RESOLUTIONS or cadence_minutes <= 0 or not 0 <= minimum_coverage <= 1:
        raise ValueError("invalid aggregation parameters")
    groups: dict[datetime, list[Any]] = defaultdict(list)
    ends = {}
    for row in rows:
        local = _utc(_get(row, "observed_at")).astimezone(HST)
        start, end = _bounds(local, resolution)
        groups[start].append(row)
        ends[start] = end
    output = []
    for start, group in sorted(groups.items()):
        valid = [float(_get(row, "wave_height_m")) for row in group
                 if _get(row, "wave_height_m") is not None and
                 _get(row, "quality_status", "unverified") == "valid"]
        expected = round((ends[start]-start).total_seconds() / (cadence_minutes*60))
        count = len(valid)
        coverage = min(1.0, count/expected) if expected else 0.0
        output.append({"resolution": resolution, "period_start_hst": start.isoformat(),
                       "period_end_hst": ends[start].isoformat(),
                       "sample_count": len(group), "valid_sample_count": count,
                       "expected_sample_count": expected, "completeness": coverage,
                       "quality_status": "OBSERVED" if coverage >= minimum_coverage else "INSUFFICIENT_DATA",
                       "mean": mean(valid) if valid else None,
                       "median": median(valid) if valid else None,
                       "min": min(valid) if valid else None,
                       "max": max(valid) if valid else None,
                       "stddev": pstdev(valid) if len(valid) > 1 else None,
                       "variance": pvariance(valid) if len(valid) > 1 else None})
    return output


def trailing_statistics(periods: list[dict], *, window: int) -> list[dict]:
    """Causal rolling mean, trend and residual; incomplete periods are excluded."""
    if window < 2:
        raise ValueError("window must be at least 2")
    rows = sorted(periods, key=lambda item: item["period_start_hst"])
    output = []
    for index, item in enumerate(rows):
        past = rows[max(0,index-window+1):index+1]
        values = [row["median"] for row in past
                  if row["quality_status"] == "OBSERVED" and row["median"] is not None]
        rolling = mean(values) if len(values) == len(past) and len(past) == window else None
        trend = (values[-1]-values[0])/(window-1) if rolling is not None else None
        output.append({**item, "rolling_mean": rolling, "trend_per_period": trend,
                       "residual": item["median"]-rolling if rolling is not None and item["median"] is not None else None})
    return output


def autocorrelation(values: list[float | None], lag: int) -> float | None:
    """Return undefined for short/constant sequences, never a fabricated zero."""
    if lag < 1 or len(values) < 3*lag:
        return None
    pairs = [(float(values[i]), float(values[i-lag])) for i in range(lag,len(values))
             if values[i] is not None and values[i-lag] is not None]
    if len(pairs) < 3:
        return None
    xs, ys = zip(*pairs)
    xm, ym = mean(xs), mean(ys)
    denominator = sum((x-xm)**2 for x in xs) * sum((y-ym)**2 for y in ys)
    if denominator <= 0:
        return None
    return sum((x-xm)*(y-ym) for x,y in pairs) / denominator**.5


def seasonal_anomalies(monthly_periods: list[dict], *, minimum_prior_years: int) -> list[dict]:
    """Compare each month with prior years only; no future climatology leakage."""
    if minimum_prior_years < 1:
        raise ValueError("positive prior-year requirement")
    history: dict[int, list[float]] = defaultdict(list)
    output = []
    for row in sorted(monthly_periods, key=lambda item: item["period_start_hst"]):
        month = datetime.fromisoformat(row["period_start_hst"]).month
        value = row.get("median")
        prior = history[month]
        baseline = mean(prior) if len(prior) >= minimum_prior_years else None
        output.append({**row, "climatology_mean": baseline,
                       "seasonal_anomaly": value-baseline if baseline is not None and value is not None else None})
        if value is not None and row.get("quality_status") == "OBSERVED":
            prior.append(value)
    return output
