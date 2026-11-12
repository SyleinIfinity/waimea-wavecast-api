"""Pure Gold analytics; input observations stay separate from persistence."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from math import cos, radians, sin, sqrt
from statistics import mean, median, pstdev
from typing import Any
from zoneinfo import ZoneInfo

HST = ZoneInfo("Pacific/Honolulu")
METRICS = {"WVHT": "wave_height_m", "DPD": "dominant_period_s", "APD": "average_period_s",
           "WTMP": "sea_surface_temp_c", "WSPD": "wind_speed_ms", "MWD": "mean_wave_direction_deg"}


def get(row: Any, field: str) -> Any:
    return row.get(field) if isinstance(row, dict) else getattr(row, field)


def period_start(day: date, period: str) -> date:
    if period == "day":
        return day
    if period == "week":
        return date.fromordinal(day.toordinal() - day.weekday())
    if period == "month":
        return day.replace(day=1)
    raise ValueError("period must be day, week or month")


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = int(position)
    return ordered[lower] + (ordered[min(lower + 1, len(ordered) - 1)] - ordered[lower]) * (position - lower)


def aggregate(observations: list[Any], period: str, metric: str = "WVHT") -> list[dict]:
    field = METRICS[metric]
    groups: dict[date, list[Any]] = defaultdict(list)
    for row in observations:
        timestamp = get(row, "observed_at")
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        groups[period_start(timestamp.astimezone(HST).date(), period)].append(row)
    result = []
    for day, group in sorted(groups.items()):
        values = [float(value) for row in group
                  if (row.get("quality_status", "unverified") if isinstance(row, dict)
                      else getattr(row, "quality_status", "unverified")) == "valid"
                  and (value := get(row, field)) is not None]
        if metric == "MWD" and values:
            x = mean(cos(radians(v)) for v in values)
            y = mean(sin(radians(v)) for v in values)
            from math import atan2, degrees
            average = degrees(atan2(y, x)) % 360 if x * x + y * y > 1e-12 else None
            method = "circular-v1"
        else:
            average = mean(values) if values else None
            method = "observations-v1"
        result.append({"station_id": "51201", "period_type": period, "period_start_hst": day.isoformat(),
                       "metric_code": metric, "sample_count": len(group), "valid_sample_count": len(values),
                       "mean_value": average, "median_value": None if not values or metric == "MWD" else median(values),
                       "min_value": None if not values or metric == "MWD" else min(values),
                       "max_value": None if not values or metric == "MWD" else max(values),
                       "stddev_value": None if not values or metric == "MWD" else pstdev(values),
                       "p95_value": None if not values or metric == "MWD" else _quantile(values, .95),
                       "method": method})
    return result


def decompose(monthly: list[dict], *, seasonal_periods: int = 12) -> list[dict]:
    """Trailing monthly trend; no invented components for short/gapped series."""
    rows = sorted((row for row in monthly if row["median_value"] is not None), key=lambda r: r["period_start_hst"])
    if len(rows) < 2 * seasonal_periods:
        return []
    dates = [date.fromisoformat(row["period_start_hst"]) for row in rows]
    if any((a.year * 12 + a.month + 1) != (b.year * 12 + b.month) for a, b in zip(dates, dates[1:])):
        return []
    values = [row["median_value"] for row in rows]
    trend = [None] * len(rows)
    for i in range(seasonal_periods - 1, len(rows)):
        trend[i] = mean(values[i - seasonal_periods + 1:i + 1])
    output = []
    prior_residuals: dict[int, list[float]] = defaultdict(list)
    for i in range(len(rows)):
        # The seasonal estimate for month i uses *earlier* years only.
        seasonal = mean(prior_residuals[dates[i].month]) if prior_residuals[dates[i].month] else None
        output.append({"station_id": "51201", "metric_code": rows[i]["metric_code"], "period_type": "month",
                       "period_start_hst": dates[i].isoformat(), "observed_value": values[i], "trend_value": trend[i],
                       "seasonal_value": seasonal,
                       "residual_value": values[i] - trend[i] - seasonal if trend[i] is not None and seasonal is not None else None,
                       "decomposition_method": "trailing-moving-average", "seasonal_periods": seasonal_periods,
                       "analysis_version": "v2-causal"})
        if trend[i] is not None:
            prior_residuals[dates[i].month].append(values[i] - trend[i])
    return output


def _rank(values: list[float]) -> list[float]:
    ordered = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    i = 0
    while i < len(values):
        j = i + 1
        while j < len(values) and values[ordered[j]] == values[ordered[i]]:
            j += 1
        for k in range(i, j):
            ranks[ordered[k]] = (i + j + 1) / 2
        i = j
    return ranks


def correlation(pairs: list[tuple[float | None, float | None]], method: str = "pearson") -> tuple[int, float | None]:
    clean = [(float(x), float(y)) for x, y in pairs if x is not None and y is not None]
    if len(clean) < 3:
        return len(clean), None
    xs, ys = map(list, zip(*clean))
    if method == "spearman":
        xs, ys = _rank(xs), _rank(ys)
    elif method != "pearson":
        raise ValueError("unknown correlation method")
    xm, ym = mean(xs), mean(ys)
    xx = sum((x - xm) ** 2 for x in xs)
    yy = sum((y - ym) ** 2 for y in ys)
    if xx == 0 or yy == 0:
        return len(clean), None
    coefficient = sum((x - xm) * (y - ym) for x, y in zip(xs, ys)) / sqrt(xx * yy)
    return len(clean), max(-1.0, min(1.0, coefficient))


def daily_correlations(observations: list[Any], start: date, end: date) -> list[dict]:
    days: dict[date, list[Any]] = defaultdict(list)
    for row in observations:
        ts = get(row, "observed_at")
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        day = ts.astimezone(HST).date()
        if start <= day <= end:
            days[day].append(row)
    result = []
    for other in ("DPD", "WTMP", "WSPD"):
        pairs = []
        for rows in days.values():
            paired = [(get(row, "wave_height_m"), get(row, METRICS[other])) for row in rows
                      if get(row, "wave_height_m") is not None and get(row, METRICS[other]) is not None]
            x = [item[0] for item in paired]
            y = [item[1] for item in paired]
            pairs.append((median(x) if x else None, median(y) if y else None))
        for method in ("pearson", "spearman"):
            count, coefficient = correlation(pairs, method)
            if count < 3:
                continue
            metric_x, metric_y = sorted(("WVHT", other))
            result.append({"station_id": "51201", "metric_x": metric_x, "metric_y": metric_y,
                           "method": method, "date_start_hst": start.isoformat(), "date_end_hst": end.isoformat(),
                           "paired_sample_count": count, "coefficient": coefficient})
    return result


def histogram(observations: list[Any], metric: str = "WVHT", bins: int = 20) -> list[dict]:
    """Bounded histogram computed from Silver; no histogram Gold table exists."""
    if not 2 <= bins <= 100:
        raise ValueError("bins out of range")
    values = [float(value) for row in observations[:100_000]
              if (value := get(row, METRICS[metric])) is not None]
    if not values:
        return []
    low, high = min(values), max(values)
    if low == high:
        return [{"min": low, "max": high, "count": len(values)}]
    width = (high - low) / bins
    counts = [0] * bins
    for value in values:
        counts[min(bins - 1, int((value - low) / width))] += 1
    return [{"min": low + i * width, "max": low + (i + 1) * width, "count": count}
            for i, count in enumerate(counts)]


def scatter(observations: list[Any], x_metric: str = "WVHT", y_metric: str = "DPD", limit: int = 1000) -> list[dict]:
    if not 1 <= limit <= 1000:
        raise ValueError("limit out of range")
    result = []
    for row in observations:
        x, y = get(row, METRICS[x_metric]), get(row, METRICS[y_metric])
        if x is not None and y is not None:
            result.append({"x": float(x), "y": float(y), "observed_at": str(get(row, "observed_at"))})
        if len(result) == limit:
            break
    return result
