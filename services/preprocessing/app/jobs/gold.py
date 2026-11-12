"""Descriptive, quality-controlled multi-resolution WVHT Gold recomputation."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from math import sqrt
from zoneinfo import ZoneInfo

from packages.common.time import hst_day_bounds_utc
from packages.ml.timeseries import (RESOLUTIONS, _bounds, aggregate_resolution,
                                    autocorrelation, cyclical_features, trailing_statistics)


def _wave_period_correlation(members) -> float | None:
    pairs = [(row.wave_height_m, row.dominant_period_s) for row in members
             if row.quality_status == "valid" and row.wave_height_m is not None
             and row.dominant_period_s is not None]
    if len(pairs) < 3:
        return None
    xs, ys = zip(*pairs)
    mx, my = sum(xs)/len(xs), sum(ys)/len(ys)
    numerator = sum((x-mx)*(y-my) for x, y in pairs)
    denominator = sqrt(sum((x-mx)**2 for x in xs)*sum((y-my)**2 for y in ys))
    return max(-1.0, min(1.0, numerator/denominator)) if denominator else None


def rebuild_gold(gateway, *, year: int, now: datetime | None = None,
                 minimum_coverage: float = 0.9, changed_from: datetime | None = None,
                 changed_to: datetime | None = None) -> dict:
    """Recompute one calendar year's periods; no partial-window aggregates are published."""
    if year < 2000 or year > 2100:
        raise ValueError("invalid year")
    now = now or datetime.now(timezone.utc)
    start, _ = hst_day_bounds_utc(date(year, 1, 1))
    end, _ = hst_day_bounds_utc(date(year + 1, 1, 1))
    observations = gateway.get_observations("51201", start, end)
    if not observations:
        return {"observations": 0, "saved": 0}
    grouped = {resolution: defaultdict(list) for resolution in RESOLUTIONS}
    for row in observations:
        local = row.observed_at.astimezone(ZoneInfo("Pacific/Honolulu"))
        for resolution in RESOLUTIONS:
            period_start, _ = _bounds(local, resolution)
            grouped[resolution][period_start.isoformat()].append(row)
    total = 0
    status_counts = defaultdict(int)
    for resolution in RESOLUTIONS:
        periods = aggregate_resolution(observations, resolution, cadence_minutes=30,
                                       minimum_coverage=minimum_coverage)
        periods = trailing_statistics(periods, window=3)
        for index, item in enumerate(periods):
            previous = periods[index-1] if index else None
            contiguous = previous is not None and previous["period_end_hst"] == item["period_start_hst"]
            item["lag1_median_value"] = previous["median"] if contiguous and previous["quality_status"] == "OBSERVED" else None
            recent = [period["median"] if period["quality_status"] == "OBSERVED" else None
                      for period in periods[max(0,index-47):index+1]]
            item["autocorrelation_lag1"] = autocorrelation(recent, 1)
        if changed_from is not None and changed_to is not None:
            impacted = set()
            for index, item in enumerate(periods):
                period_start = datetime.fromisoformat(item["period_start_hst"]).astimezone(timezone.utc)
                period_end = datetime.fromisoformat(item["period_end_hst"]).astimezone(timezone.utc)
                if period_start <= changed_to and period_end > changed_from:
                    impacted.update(range(index, min(len(periods), index + 3)))
            periods = [item for index, item in enumerate(periods) if index in impacted]
        output = []
        for item in periods:
            members = grouped[resolution][item["period_start_hst"]]
            end_at = datetime.fromisoformat(item["period_end_hst"]).astimezone(timezone.utc)
            if end_at > now:
                readiness = "WAITING_FOR_SOURCE"
            elif item["quality_status"] == "OBSERVED":
                readiness = "GOLD_READY"
            else:
                readiness = "INSUFFICIENT_DATA"
            status_counts[readiness] += 1
            signals = cyclical_features(datetime.fromisoformat(item["period_start_hst"]))
            output.append({
                "station_id": "51201", "resolution": resolution,
                "period_start_at": datetime.fromisoformat(item["period_start_hst"]).astimezone(timezone.utc),
                "period_end_at": end_at, "metric_code": "WVHT",
                "sample_count": item["sample_count"], "valid_sample_count": item["valid_sample_count"],
                "expected_sample_count": item["expected_sample_count"],
                "missing_count": max(0, item["expected_sample_count"] - item["sample_count"]),
                "suspect_count": sum(row.quality_status == "suspect" for row in members),
                "rejected_count": sum(row.quality_status == "rejected" for row in members),
                "unverified_count": sum(row.quality_status == "unverified" for row in members),
                "completeness": item["completeness"], "readiness": readiness,
                "mean_value": item["mean"], "median_value": item["median"],
                "min_value": item["min"], "max_value": item["max"],
                "variance_value": item["variance"], "stddev_value": item["stddev"],
                "rolling_mean": item["rolling_mean"], "trend_per_period": item["trend_per_period"],
                "residual_value": item["residual"], "calculation_version": "tl07b-v1",
                "lag1_median_value": item["lag1_median_value"],
                "autocorrelation_lag1": item["autocorrelation_lag1"],
                "correlation_wvht_dpd": _wave_period_correlation(members),
                **signals,
            })
        total += gateway.save_resolution_metrics(output)["saved"]
    return {"observations": len(observations), "saved": total, "readiness": dict(status_counts)}
