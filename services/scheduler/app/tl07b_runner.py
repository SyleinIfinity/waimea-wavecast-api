"""Single-station durable TL07-B poll/retry/recovery loop.

PostgreSQL claims fence concurrent replicas. Each tick is bounded to one feed
request and one downstream recomputation. All times are UTC.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)
HST = ZoneInfo("Pacific/Honolulu")


def _parse(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value


def _request(client, method, url, token, body=None):
    response = client.request(method, url, json=body, headers={"X-Internal-Token": token})
    response.raise_for_status()
    return response.json()


def _downstream(client, base, derived_base, token, changed_from, changed_to):
    start = changed_from.astimezone(HST).date()
    end = changed_to.astimezone(HST).date()
    cursor = changed_from
    while cursor <= changed_to:
        chunk_end = min(changed_to + timedelta(microseconds=1), cursor + timedelta(days=29))
        _request(client, "POST", base + "/quality/review", token,
                 {"start": cursor.isoformat(), "end": chunk_end.isoformat()})
        cursor = chunk_end
    cursor = changed_from
    while cursor <= changed_to:
        chunk_end = min(changed_to + timedelta(microseconds=1), cursor + timedelta(days=7))
        _request(client, "POST", derived_base + "/reconcile", token,
                 {"start": cursor.isoformat(), "end": chunk_end.isoformat()})
        cursor = chunk_end
    day = start
    while day <= end:
        last = min(end, day + timedelta(days=29))
        _request(client, "POST", base + "/daily/rebuild", token,
                 {"start_hst": day.isoformat(), "end_hst": last.isoformat(), "expected_sample_count": 48})
        day = last + timedelta(days=1)
    for year in range(start.year, end.year + 1):
        _request(client, "POST", base + "/gold/rebuild", token,
                 {"year": year, "changed_from": changed_from.isoformat(), "changed_to": changed_to.isoformat()})


def run_once(now: datetime | None = None, client=None) -> dict:
    now = now or datetime.now(timezone.utc)
    token = os.environ["INTERNAL_API_TOKEN"]
    db = os.environ.get("DATABASE_API_URL", "http://database-api:8000").rstrip("/") + "/internal/v1/pipeline"
    ingestion = os.environ.get("INGESTION_WORKER_URL", "http://ingestion:8000").rstrip("/")
    preprocessing = os.environ.get("PREPROCESSING_WORKER_URL", "http://preprocessing:8000").rstrip("/") + "/internal/v1/preprocessing"
    owned = client is None
    client = client or httpx.Client(timeout=180)
    try:
        due = _request(client, "GET", db + "/cycles/due", token)["slots"]
        slot = _parse(due[0]) if due else now.replace(minute=0 if now.minute < 30 else 30, second=0, microsecond=0)
        claim = _request(client, "POST", db + "/cycles/claim", token, {"slot_at": slot.isoformat()})
        if not claim.get("claimed"):
            return claim
        finish = {"slot_at": slot.isoformat(), "lease_token": claim["lease_token"], "success": False,
                  "failure_stage": "INGESTION"}
        try:
            if claim.get("failure_stage") in ("INGESTION", "PREPROCESSING") and claim.get("gcs_uri"):
                uri = claim["gcs_uri"]
                if not uri.startswith("gs://waimea-noaa/"):
                    raise ValueError("unexpected replay bucket")
                result = _request(client, "POST", ingestion + "/internal/v1/ingestion/replay", token,
                                  {"object_path": uri.split("/", 3)[3],
                                   "generation": claim["gcs_generation"]})
            else:
                result = _request(client, "POST", ingestion + "/internal/v1/ingestion/poll", token, {})
            finish.update({key: result.get(key) for key in ("silver_inserted", "silver_revised",
                           "latest_observed_at", "gcs_uri", "gcs_generation", "changed_from", "changed_to")})
            if not result["success"]:
                if result.get("retry_after_seconds") is not None:
                    finish["retry_after_seconds"] = result["retry_after_seconds"]
                    finish["error_class"] = "HTTP429"
                raise RuntimeError("ingestion reported failure")
            previous = claim.get("failure_stage") in ("INGESTION", "PREPROCESSING")
            changed_from = _parse(result.get("changed_from") or (claim.get("changed_from") if previous else None))
            changed_to = _parse(result.get("changed_to") or (claim.get("changed_to") if previous else None))
            if changed_from is not None and changed_to is not None:
                finish["failure_stage"] = "PREPROCESSING"
                _downstream(client, preprocessing, db.replace("/pipeline", "/derived"),
                            token, changed_from, changed_to)
                cursor = changed_from - timedelta(days=1)
                gap_end = min(datetime.now(timezone.utc), changed_to + timedelta(days=1))
                while cursor < gap_end:
                    window_end = min(gap_end, cursor + timedelta(days=7))
                    _request(client, "POST", preprocessing + "/gaps/reconcile", token,
                             {"start": cursor.isoformat(), "end": window_end.isoformat()})
                    if window_end == gap_end:
                        break
                    cursor = window_end - timedelta(minutes=30)
                _request(client, "POST", preprocessing + "/gaps/reconcile", token, {})
            finish["success"] = True
            finish["failure_stage"] = None
        except Exception as exc:
            finish.setdefault("error_class", type(exc).__name__)
            logger.exception("TL07-B cycle failed slot=%s stage=%s", slot, finish["failure_stage"])
        return _request(client, "POST", db + "/cycles/finish", token, finish)
    finally:
        if owned:
            client.close()


def main():
    logging.basicConfig(level=logging.INFO)
    if not os.environ.get("INTERNAL_API_TOKEN"):
        raise RuntimeError("INTERNAL_API_TOKEN required")
    while True:
        started = time.monotonic()
        try:
            logger.info("TL07-B tick result: %s", run_once())
        except Exception:
            logger.exception("TL07-B scheduler tick failed")
        time.sleep(max(1, 60 - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
