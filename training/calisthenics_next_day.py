"""Day-ahead features built from six closed Hawaii days and today's morning."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, fields
from datetime import date, datetime, timedelta
from itertools import groupby

from packages.db.gateway import SilverObservationDTO
from packages.ml.data_quality import infer_cadence
from packages.ml.features import build_samples
from services.database_api.app.api.forward import HST, day_bounds
from services.preprocessing.app.features.daily import compute_daily_features
from training.calisthenics_v1 import Event
from training.tl08c_forward_runner import ready_daily_row, utc, valid_observation

INPUT_CUTOFF_HOUR_HST = 12
PUBLICATION_WAIT = timedelta(hours=1)
_OBSERVATION_FIELDS = {field.name for field in fields(SilverObservationDTO)}


def input_cutoff(day: date) -> datetime:
    return day_bounds(day)[0] + timedelta(hours=INPUT_CUTOFF_HOUR_HST)


def _summary(day: date, observations: list[dict], expected: int) -> dict | None:
    inputs = []
    for row in observations:
        values = {key: value for key, value in row.items() if key in _OBSERVATION_FIELDS}
        values["observed_at"] = utc(values["observed_at"])
        inputs.append(SilverObservationDTO(**values))
    result = compute_daily_features(day, inputs, expected_sample_count=expected)
    if ((result.coverage_ratio or 0) < .9 or result.suspect_count or result.rejected_count
            or result.unverified_count):
        return None
    return asdict(result)


def _features(day: date, history: list[dict], morning: dict) -> tuple | None:
    # Availability is checked by the caller against the actual issue time.
    rows = [{key: value for key, value in row.items() if key != "source_data_max_updated_at"}
            for row in [*history, morning]]
    return next((sample.features for sample in build_samples(rows, require_target=False)
                 if sample.anchor_date_hst == day), None)


def live_features(day: date, daily: list[dict], observations: list[dict], now: datetime):
    start, end = day_bounds(day)
    cutoff = input_cutoff(day)
    if not cutoff + PUBLICATION_WAIT <= now < end:
        return None
    by_day = {date.fromisoformat(str(row["date_hst"])): row for row in daily}
    history = [by_day.get(day - timedelta(days=lag)) for lag in range(6, 0, -1)]
    if not all(ready_daily_row(row, now) for row in history):
        return None
    morning_rows = sorted((row for row in observations if start <= utc(row["observed_at"]) < cutoff),
                          key=lambda row: utc(row["observed_at"]))
    if not all(valid_observation(row, now) for row in morning_rows):
        return None
    morning = _summary(day, morning_rows, expected=INPUT_CUTOFF_HOUR_HST * 2)
    if morning is None:
        return None
    features = _features(day, history, morning)
    if features is None:
        return None
    source = {"daily": history, "morning": [(row["observed_at"], row["measurement_sha256"])
                                          for row in morning_rows]}
    digest = hashlib.sha256(json.dumps(source, sort_keys=True, default=str).encode()).hexdigest()
    return features, digest


def historical_events(conn, end: datetime) -> list[Event]:
    """Rebuild the same morning cutoff from QC Silver, with full-day future labels."""
    full: dict[date, dict] = {}
    mornings: dict[date, dict] = {}
    with conn.cursor(name="calisthenics_next_day_observations") as cursor:
        cursor.execute("""
            SELECT station_id, observed_at, source_kind, source_dataset, source_file_id,
                   raw_id, measurement_sha256, quality_status, wave_height_m, dominant_period_s,
                   mean_wave_direction_deg
            FROM silver.wave_observations
            WHERE station_id='51201' AND observed_at >= %s AND observed_at < %s
            ORDER BY observed_at
        """, (day_bounds(date(2005, 1, 1))[0], end))
        def hst_day(row: dict) -> date:
            return row["observed_at"].astimezone(HST).date()

        for _, month_rows in groupby(cursor, key=lambda row: hst_day(row).replace(day=1)):
            month = list(month_rows)
            cadence = infer_cadence(month, min_intervals=20, min_support_ratio=.55)
            if cadence is None or cadence.minutes not in (30, 60):
                continue
            expected_day = 1440 // cadence.minutes
            expected_morning = INPUT_CUTOFF_HOUR_HST * 60 // cadence.minutes
            for day, day_rows in groupby(month, key=hst_day):
                rows = list(day_rows)
                if day_bounds(day)[1] > end:
                    continue
                summary = _summary(day, rows, expected_day)
                if summary is not None:
                    full[day] = summary
                cutoff = input_cutoff(day)
                summary = _summary(day, [row for row in rows if row["observed_at"] < cutoff], expected_morning)
                if summary is not None:
                    mornings[day] = summary

    events = []
    for day, morning in sorted(mornings.items()):
        target_day = day + timedelta(days=1)
        truth = full.get(target_day)
        if truth is None:
            continue
        prior_days = [day - timedelta(days=lag) for lag in range(6, 0, -1)]
        features = _features(day, [full[d] for d in prior_days if d in full], morning)
        if features is not None:
            cutoff = input_cutoff(day)
            events.append(Event(cutoff, cutoff + PUBLICATION_WAIT, day_bounds(target_day)[1],
                                features, float(truth["median_wave_height_m"])))
    return events
