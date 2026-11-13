"""Single-worker, resumable NOAA historical import and three-layer GCS export.

Run in the bounded historical-backfill Compose service. No archive URL is
constructed as proof of availability: each must be listed by NOAA's station
history page before it can be claimed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import threading
import time
from datetime import date, datetime, timedelta, timezone

import requests

from packages.db.http import HttpDatabaseGateway
from packages.gcs.client import RealGCSClient
from packages.ml.data_quality import infer_cadence
from packages.noaa.client import NOAAClient
from packages.noaa.urls import annual_url, monthly_url
from services.ingestion.app.services.datalake_export import _rows, export_month
from services.ingestion.app.services.ingest_pipeline import run_ingest
from services.ingestion.scripts.discover_history import discover
from services.preprocessing.app.jobs.gold import rebuild_gold
from services.preprocessing.app.jobs.quality import review_range
from services.preprocessing.app.jobs.rebuild_days import rebuild_daily_features


def _source(filename: str, url: str) -> dict:
    annual = re.fullmatch(r"51201h(20\d{2})\.txt\.gz", filename)
    monthly = re.fullmatch(r"51201([1-9abc])(20\d{2})\.txt\.gz", filename)
    if annual:
        year = int(annual[1])
        if url != annual_url(year):
            raise ValueError("annual URL mismatch")
        return {"feed_key": "historical_annual", "year": year, "month": None,
                "source_period": str(year), "source_url": url}
    if monthly:
        month = "123456789abc".index(monthly[1]) + 1
        year = int(monthly[2])
        if url != monthly_url(year, month):
            raise ValueError("monthly URL mismatch")
        return {"feed_key": "historical_monthly", "year": year, "month": month,
                "source_period": f"{year}-{month:02d}", "source_url": url}
    raise ValueError("unrecognized NOAA source")


def _available_memory() -> int:
    with open("/proc/meminfo", encoding="ascii") as stream:
        for line in stream:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    raise RuntimeError("memory accounting unavailable")


def _safe_resources() -> None:
    if _available_memory() < 3 * 1024**3 or shutil.disk_usage("/").free < 10 * 1024**3:
        raise RuntimeError("backfill paused: VM memory or disk below safety threshold")


def _metadata(session: requests.Session, url: str, job: dict | None = None) -> dict:
    try:
        headers = {}
        if job and job.get("source_etag"):
            headers["If-None-Match"] = job["source_etag"]
        elif job and job.get("source_last_modified"):
            headers["If-Modified-Since"] = job["source_last_modified"]
        response = session.head(url, timeout=(5, 20), allow_redirects=True, headers=headers)
        if response.status_code == 304 and job is not None:
            return {"not_modified": True, "source_etag": job.get("source_etag"),
                    "source_last_modified": job.get("source_last_modified"),
                    "source_content_length": job.get("source_content_length")}
        response.raise_for_status()
        return {"source_etag": response.headers.get("ETag"),
                "source_last_modified": response.headers.get("Last-Modified"),
                "source_content_length": int(response.headers["Content-Length"]) if response.headers.get("Content-Length", "").isdigit() else None}
    except requests.RequestException:
        return {}


def _unchanged(job: dict, metadata: dict, *, force_check: bool) -> bool:
    if job.get("status") != "VERIFIED":
        return False
    if force_check:
        # A manual audit must compare actual bytes, even when NOAA returns 304.
        return False
    checked = job.get("last_checked_at")
    check_hours = int(os.environ.get("HISTORY_METADATA_CHECK_HOURS", "24"))
    full_days = int(os.environ.get("HISTORY_FULL_CHECK_DAYS", "30"))
    if not 1 <= check_hours <= 168 or not 1 <= full_days <= 365:
        raise ValueError("invalid historical reconciliation frequency")
    if not force_check and checked and datetime.fromisoformat(checked.replace("Z", "+00:00")) > datetime.now(timezone.utc) - timedelta(hours=check_hours):
        return True
    verified = job.get("last_verified_at")
    if not verified or datetime.fromisoformat(verified.replace("Z", "+00:00")) < datetime.now(timezone.utc) - timedelta(days=full_days):
        return False
    if metadata.get("not_modified"):
        return True
    pairs = [("source_etag", metadata.get("source_etag"), job.get("source_etag")),
             ("source_last_modified", metadata.get("source_last_modified"), job.get("source_last_modified")),
             ("source_content_length", metadata.get("source_content_length"), job.get("source_content_length"))]
    # A changed validator wins over other matching validators. Servers can
    # update a file without changing its size or Last-Modified granularity.
    if any(current is not None and previous is not None and current != previous
           for _, current, previous in pairs):
        return False
    if metadata.get("source_etag") and metadata["source_etag"] == job.get("source_etag"):
        return True
    return (metadata.get("source_last_modified") is not None and
            metadata.get("source_content_length") is not None and
            metadata["source_last_modified"] == job.get("source_last_modified") and
            metadata["source_content_length"] == job.get("source_content_length"))


def _months_between(start: datetime, end: datetime) -> list[tuple[int, int]]:
    result = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        result.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return result


def _process(source: dict, job: dict | None, gateway, gcs, session, *, force_check: bool) -> dict:
    url = source["source_url"]
    if job and job.get("status") == "VERIFIED" and not force_check:
        checked = job.get("last_checked_at")
        check_hours = int(os.environ.get("HISTORY_METADATA_CHECK_HOURS", "24"))
        if not 1 <= check_hours <= 168:
            raise ValueError("invalid historical metadata check frequency")
        if checked and datetime.fromisoformat(checked.replace("Z", "+00:00")) > datetime.now(timezone.utc) - timedelta(hours=check_hours):
            return {"period": source["source_period"], "status": "UNCHANGED_RECENT", "job_id": job["job_id"]}
    metadata = _metadata(session, url, job)
    if job and _unchanged(job, metadata, force_check=force_check):
        gateway._request("POST", f"/internal/v1/datalake/jobs/{job['job_id']}/checked")
        return {"period": source["source_period"], "status": "UNCHANGED", "job_id": job["job_id"]}
    _safe_resources()
    client = NOAAClient()
    try:
        payload = client.download(url)
    except Exception:
        if job is None or job.get("status") != "VERIFIED":
            failed_claim = gateway._request("POST", "/internal/v1/datalake/jobs/claim", body=source)
            if failed_claim["claimed"]:
                gateway._request("POST", f"/internal/v1/datalake/jobs/{failed_claim['job']['job_id']}/checkpoint",
                                 body={"lease_token": failed_claim["lease_token"],
                                       "status": "FAILED", "error_stage": "DOWNLOAD"})
        raise
    sha = hashlib.sha256(payload).hexdigest()
    if job and job.get("status") == "VERIFIED" and job.get("source_sha256") == sha:
        gateway._request("POST", f"/internal/v1/datalake/jobs/{job['job_id']}/checked",
                         body={"full_checksum": True, **metadata})
        return {"period": source["source_period"], "status": "UNCHANGED_SHA256", "job_id": job["job_id"]}
    claim = gateway._request("POST", "/internal/v1/datalake/jobs/claim", body=source)
    if not claim["claimed"]:
        return {"period": source["source_period"], "status": "BUSY"}
    job_id, token = claim["job"]["job_id"], claim["lease_token"]
    stop_heartbeat = threading.Event()
    heartbeat_error: list[Exception] = []

    def heartbeat() -> None:
        with_gateway = HttpDatabaseGateway()
        try:
            while not stop_heartbeat.wait(45):
                try:
                    with_gateway._request("POST", f"/internal/v1/datalake/jobs/{job_id}/heartbeat",
                                          body={"lease_token": token})
                except Exception as exc:
                    heartbeat_error.append(exc)
                    return
        finally:
            with_gateway.close()

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()

    def checkpoint(values: dict) -> None:
        if heartbeat_error:
            raise RuntimeError("historical backfill heartbeat failed") from heartbeat_error[0]
        gateway._request("POST", f"/internal/v1/datalake/jobs/{job_id}/checkpoint",
                         body={"lease_token": token, **values})

    stage = "INGEST"
    try:
        result = run_ingest(source["feed_key"], source["year"], source["month"],
                            gcs_client=gcs, db_gateway=gateway, source_bytes=payload,
                            checkpoint=checkpoint,
                            resume_offset=(claim["job"].get("last_batch_offset") or 0)
                            if claim["job"].get("source_sha256") == sha else 0)
        if not result.success or result.parse_errors:
            raise RuntimeError(f"ingest failed: {result.error or 'parse errors'}")
        stage = "QUALITY"
        assert result.earliest_observed_at is not None and result.latest_observed_at is not None
        months = _months_between(result.earliest_observed_at, result.latest_observed_at)
        for year, month in months:
            start = datetime(year, month, 1, tzinfo=timezone.utc)
            end = datetime(year + 1, 1, 1, tzinfo=timezone.utc) if month == 12 else datetime(year, month + 1, 1, tzinfo=timezone.utc)
            review_range(gateway, start, end)
            month_rows = gateway.get_observations("51201", start, end)
            cadence = infer_cadence(month_rows, min_intervals=20, min_support_ratio=0.55)
            del month_rows
            expected_daily = 1440 // cadence.minutes if cadence and cadence.minutes in (30, 60) else None
            hst_start = (start - timedelta(hours=10)).date()
            hst_end = (end - timedelta(hours=10, microseconds=1)).date()
            # Daily aggregates use HST boundaries and observed source cadence.
            rebuild_daily_features(hst_start, hst_end, db_gateway=gateway,
                                   expected_sample_count=expected_daily)
            checkpoint({"status": "QC_APPLIED"})
        stage = "GOLD"
        local_first = result.earliest_observed_at - timedelta(hours=10)
        local_last = result.latest_observed_at - timedelta(hours=10)
        gold_months = _months_between(local_first, local_last)
        gold_years = sorted({year for year, _ in gold_months})
        for year in gold_years:
            rebuild_gold(gateway, year=year)
        checkpoint({"status": "GOLD_APPLIED"})
        stage = "EXPORT"
        exported = []
        for year, month in months:
            _safe_resources()
            for layer in ("bronze", "silver"):
                if not _rows(gateway, layer, year, month):
                    continue
                item = export_month(gateway, gcs, bucket="waimea-noaa", layer=layer, year=year, month=month,
                                    processing_run_id=str(job_id))
                exported.append(item)
                checkpoint({"status": "GOLD_APPLIED", "last_exported_partition": f"{layer}/{item['partition']}"})
        for year, month in gold_months:
            if not _rows(gateway, "gold", year, month):
                continue
            item = export_month(gateway, gcs, bucket="waimea-noaa", layer="gold", year=year, month=month,
                                processing_run_id=str(job_id))
            exported.append(item)
            checkpoint({"status": "GOLD_APPLIED", "last_exported_partition": f"gold/{item['partition']}"})
        checkpoint({"status": "VERIFIED", "source_sha256": sha, "source_row_count": result.source_row_count,
                    "gcs_uri": result.gcs_uri, "gcs_generation": result.gcs_generation, **metadata})
        return {"period": source["source_period"], "status": "VERIFIED", "job_id": job_id,
                "parsed_rows": result.source_row_count, "raw_inserted": result.raw_inserted,
                "silver_inserted": result.silver_inserted, "silver_revised": result.silver_revised,
                "exports": len(exported), "months": len(months)}
    except Exception:
        try:
            checkpoint({"status": "FAILED", "error_stage": stage, "source_sha256": sha})
        finally:
            raise
    finally:
        stop_heartbeat.set()
        thread.join(timeout=2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int)
    parser.add_argument("--month", type=int)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--max-files", type=int, default=int(os.environ.get("HISTORY_MAX_FILES_PER_RUN", "1")))
    parser.add_argument("--force-check", action="store_true")
    args = parser.parse_args()
    if args.month and not args.year or not 1 <= args.max_files <= 100:
        parser.error("month requires year and max-files must be 1..100")
    session = requests.Session()
    session.headers["User-Agent"] = "WaimeaWaveCast/1.0 (historical archive reconciliation)"
    sources = [_source(filename, url) for filename, url in discover(session, 100)]
    sources.sort(key=lambda item: (item["year"], item["month"] or 0), reverse=not args.all)
    sources = [item for item in sources if (args.year is None or item["year"] == args.year)
               and (args.month is None or item["month"] == args.month)]
    if not sources:
        raise RuntimeError("no matching archive listed by NOAA")
    gateway, gcs = HttpDatabaseGateway(), RealGCSClient()
    bucket = os.environ.get("GCS_BUCKET", "waimea-noaa")
    if bucket != "waimea-noaa":
        raise ValueError("TL08-A requires the configured private waimea-noaa bucket")
    try:
        existing = {(item["feed_key"], item["source_url"]): item for item in gateway._request("GET", "/internal/v1/datalake/jobs", params={"limit": 100})["data"]}
        processed = 0
        failures = []
        for source in sources:
            if processed >= (100 if args.all else args.max_files):
                break
            try:
                _safe_resources()
                report = _process(source, existing.get((source["feed_key"], source["source_url"])),
                                  gateway, gcs, session, force_check=args.force_check)
            except Exception as exc:
                failures.append(source["source_period"])
                report = {"period": source["source_period"], "status": "FAILED", "error_class": type(exc).__name__}
                print(json.dumps(report, sort_keys=True), flush=True)
                if "safety threshold" in str(exc) or getattr(getattr(exc, "response", None), "status_code", None) == 429:
                    break
                processed += 1
                time.sleep(0.25)
                continue
            print(json.dumps(report, sort_keys=True), flush=True)
            # A SHA-only result still downloaded an entire archive. Count it
            # against the scheduled per-run limit so daily runs stay bounded.
            if report["status"] not in ("UNCHANGED", "UNCHANGED_RECENT"):
                processed += 1
                time.sleep(1)
            else:
                time.sleep(0.25)
        if failures:
            raise SystemExit(f"historical sources failed: {','.join(failures)}")
    finally:
        gateway.close()
        session.close()


if __name__ == "__main__":
    main()
