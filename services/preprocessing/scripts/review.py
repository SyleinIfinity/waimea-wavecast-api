"""Read-only TL07-A NOAA quality and horizon review through Database API.

Usage: python -m services.preprocessing.scripts.review --start 2026-09-28 \
       --end 2026-11-13 --publication-wait-hours 6

The publication wait is a research scenario, not an approved operational SLA.
This command never writes observations, features, models or artifacts.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone

from packages.ml.data_quality import detect_gaps, timeline_statuses
from packages.ml.forecast import evaluate_persistence, evaluate_regime_persistence
from packages.ml.gateway import HttpMLGateway
from packages.ml.timeseries import aggregate_resolution, autocorrelation


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--publication-wait-hours", required=True, type=float)
    args = parser.parse_args()
    if args.end < args.start or (args.end-args.start).days > 90 or args.publication_wait_hours <= 0:
        parser.error("require a positive wait and a date range of at most 90 days")
    gateway = HttpMLGateway()
    start = datetime.combine(args.start,time.min,timezone.utc)
    end = datetime.combine(args.end,time.min,timezone.utc)
    rows = gateway.get_observations(start,end)
    if not rows:
        print(json.dumps({"status":"INSUFFICIENT_DATA","observation_count":0}))
        return
    as_of = datetime.now(timezone.utc)
    gaps = detect_gaps(rows,as_of=as_of,
                       publication_wait=timedelta(hours=args.publication_wait_hours),
                       min_intervals=48,min_support_ratio=.9,max_trailing_slots=0)
    cadence = gaps.cadence
    if cadence is None:
        print(json.dumps({"status":"INSUFFICIENT_DATA","observation_count":len(rows),
                          "reason":"cadence not established"}))
        return
    timeline = timeline_statuses(rows,gaps)
    resolutions = {}
    for resolution in ("30min","daily","weekly","monthly","seasonal","yearly"):
        periods = aggregate_resolution(rows,resolution,cadence_minutes=cadence.minutes,
                                       minimum_coverage=.9)
        resolutions[resolution] = {"period_count":len(periods),
                                   "coverage_pass":sum(item["quality_status"]=="OBSERVED" for item in periods)}
    horizons = {horizon:asdict(evaluate_persistence(rows,horizon,split_fraction=.75,
                min_train_pairs=200,min_test_pairs=50))
                for horizon in ("30min","1h","6h","24h","7d","30d","season","cycle","year")}
    classification = evaluate_regime_persistence(rows,"1h",split_fraction=.75,
                                                  min_train_pairs=200,min_test_pairs=50)
    heights = [row.get("wave_height_m") for row in rows]
    autocorrelations = {str(lag):autocorrelation(heights,lag) for lag in (1,2,12,48,336)}
    print(json.dumps({"status":"RESEARCH_ONLY","observation_count":len(rows),
        "cadence_minutes":cadence.minutes,"cadence_support_ratio":cadence.support_ratio,
        "missing_slots":timeline.count("MISSING"),
        "pending_slots":timeline.count("PENDING_PUBLICATION"),
        "unverified_observations":timeline.count("UNVERIFIED"),
        "duplicate_timestamps":gaps.duplicate_timestamps,
        "corrected_observations":gaps.corrected_observations,
        "resolutions":resolutions,"horizons":horizons,
        "classification_baseline_1h":classification,
        "autocorrelation_lags":autocorrelations},default=str,sort_keys=True))


if __name__ == "__main__":
    main()
