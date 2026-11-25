"""Emit a machine-readable production coverage snapshot; no SQL or GCS writes."""
from __future__ import annotations

import calendar
import gzip
import hashlib
import json
import time
from collections import Counter
from datetime import datetime, timezone

import requests

from packages.db.http import HttpDatabaseGateway
from packages.gcs.client import RealGCSClient
from packages.ml.data_quality import infer_cadence
from packages.noaa.normalize import normalize_row
from packages.noaa.parser import CANONICAL_FIELDS, detect_header, parse_stdmet
from services.ingestion.app.services.datalake_export import _rows


def build_report(gateway, gcs) -> dict:
    coverage = gateway._request("GET", "/internal/v1/datalake/coverage")
    jobs = gateway._request("GET", "/internal/v1/datalake/jobs", params={"limit": 100})["data"]
    raw = {item["month_utc"][:7]: item for item in coverage["raw_months"]}
    silver = {item["month_utc"][:7]: item for item in coverage["silver_months"]}
    corrections = {item["month_utc"][:7]: item["corrected_revisions"] for item in coverage["corrections"]}
    gold = {}
    for item in coverage["gold_months"]:
        gold.setdefault(item["month_hst"][:7], {})[item["resolution"]] = item
    exports = {}
    for item in coverage["active_exports"]:
        period = item["partition_key"].split("year=")[1][:4] + "-" + item["partition_key"].split("month=")[1][:2]
        exports.setdefault(period, {})[item["layer"]] = item
    periods = sorted(set(raw) | set(silver) | set(gold) | set(exports))
    files_by_id = {item["file_id"]: item for item in coverage["source_files"]}
    months = []
    for period in periods:
        year, month = map(int, period.split("-"))
        r, s, g, e = raw.get(period, {}), silver.get(period, {}), gold.get(period, {}), exports.get(period, {})
        obs = s.get("observations", 0)
        silver_rows = _rows(gateway, "silver", year, month) if obs else []
        cadence = infer_cadence(silver_rows, min_intervals=20, min_support_ratio=0.55) if silver_rows else None
        nominal_expected = (calendar.monthrange(year, month)[1] * 1440 // cadence.minutes) if cadence else None
        months.append({"period": period, "timezone_silver": "UTC", "timezone_gold": "Pacific/Honolulu",
                       "source_file_count": r.get("source_files", 0), "raw_rows": r.get("raw_rows", 0),
                       "source_files": [{key: files_by_id[file_id].get(key) for key in
                                         ("file_id", "feed_key", "source_url", "content_sha256", "source_row_count",
                                          "parse_error_count", "gcs_uri", "gcs_generation")}
                                        for file_id in r.get("source_file_ids", []) if file_id in files_by_id],
                       "silver_observations": obs, "qc_valid": s.get("valid", 0),
                       "qc_suspect": s.get("suspect", 0), "qc_rejected": s.get("rejected", 0),
                       "qc_partial": s.get("partial", 0),
                       "qc_unverified": s.get("unverified", 0),
                       "wave_height_available": s.get("wave_height_available", 0),
                       "dominant_period_available": s.get("dominant_period_available", 0),
                       "wind_speed_available": s.get("wind_speed_available", 0),
                       "tide_available": s.get("tide_available", 0),
                       "correction_revisions": corrections.get(period, 0),
                       "inferred_cadence_minutes": cadence.minutes if cadence else None,
                       "cadence_support_ratio": round(cadence.support_ratio, 6) if cadence else None,
                       "nominal_expected_slots_at_inferred_cadence": nominal_expected,
                       "nominal_missing_slots": max(0, nominal_expected - obs) if nominal_expected is not None else None,
                       "nominal_30min_coverage_ratio": round(obs / (48 * calendar.monthrange(year, month)[1]), 6),
                       "earliest": s.get("earliest"), "latest": s.get("latest"),
                       "gold_resolutions": g,
                       "exports": {layer: e.get(layer) for layer in ("bronze", "silver", "gold")}})
    years = []
    for year in sorted({int(item["period"][:4]) for item in months}):
        group = [item for item in months if item["period"].startswith(str(year))]
        years.append({"year": year, "months_with_observations": sum(item["silver_observations"] > 0 for item in group),
                      "raw_rows": sum(item["raw_rows"] for item in group),
                      "silver_observations": sum(item["silver_observations"] for item in group),
                      "qc_valid": sum(item["qc_valid"] for item in group),
                      "qc_suspect": sum(item["qc_suspect"] for item in group),
                      "qc_rejected": sum(item["qc_rejected"] for item in group),
                      "qc_partial": sum(item["qc_partial"] for item in group),
                      "correction_revisions": sum(item["correction_revisions"] for item in group)})
    safe_jobs = [{key: item.get(key) for key in ("feed_key", "source_url", "source_period", "status",
                                                 "source_sha256", "source_row_count", "gcs_uri", "gcs_generation",
                                                 "last_batch_offset", "error_stage", "last_exported_partition",
                                                 "last_checked_at", "last_verified_at", "source_etag",
                                                 "source_last_modified", "source_content_length")}
                 for item in jobs]
    source_audits = []
    for job in safe_jobs:
        uri = job["gcs_uri"] or ""
        object_path = uri.removeprefix("gs://waimea-noaa/")
        if object_path == uri or not job["gcs_generation"]:
            raise RuntimeError("historical source lacks pinned GCS object")
        payload = gcs.download("waimea-noaa", object_path, int(job["gcs_generation"]))
        sha = hashlib.sha256(payload).hexdigest()
        if sha != job["source_sha256"]:
            raise RuntimeError("historical source checksum mismatch")
        parsed = parse_stdmet(payload, filename=job["source_url"])
        if len(parsed.rows) != job["source_row_count"]:
            raise RuntimeError("historical source row count mismatch")
        fields = Counter()
        for row in parsed.rows:
            fields.update(key for key, value in normalize_row(row.raw_values).items() if value is not None)
        cadence = infer_cadence(parsed.rows, min_intervals=20, min_support_ratio=0.55)
        source_text = (gzip.decompress(payload) if payload[:2] == b"\x1f\x8b" else payload).decode("ascii", errors="replace")
        source_lines = source_text.splitlines()
        _, header_index = detect_header(source_lines)
        units_header = (source_lines[header_index + 1].strip() if header_index + 1 < len(source_lines)
                        and source_lines[header_index + 1].strip().startswith("#") else None)
        first = min((row.observed_at for row in parsed.rows), default=None)
        last = max((row.observed_at for row in parsed.rows), default=None)
        expected_between = (int((last-first).total_seconds() // (cadence.minutes * 60)) + 1
                            if first and last and cadence else None)
        try:
            response = requests.head(job["source_url"], timeout=(5, 20), allow_redirects=True)
            http_status = response.status_code
            http_content_type = response.headers.get("Content-Type")
            response.close()
        except requests.RequestException:
            http_status, http_content_type = None, None
        time.sleep(0.25)
        source_audits.append({"source_period": job["source_period"], "feed_key": job["feed_key"],
                              "source_url": job["source_url"], "gcs_uri": uri,
                              "gcs_generation": job["gcs_generation"], "content_sha256": sha,
                              "size_bytes": len(payload), "compression": "gzip" if payload[:2] == b"\x1f\x8b" else "none",
                              "content_type_inferred": "application/gzip" if payload[:2] == b"\x1f\x8b" else "text/plain",
                              "source_http_status_at_audit": http_status,
                              "source_http_content_type_at_audit": http_content_type,
                              "columns_in_source": parsed.columns_in_source,
                              "units_header": units_header,
                              "parse_errors": parsed.parse_error_count, "parsed_rows": len(parsed.rows),
                              "first_observed_at": first.isoformat() if first else None,
                              "last_observed_at": last.isoformat() if last else None,
                              "inferred_cadence_minutes": cadence.minutes if cadence else None,
                              "cadence_support_ratio": round(cadence.support_ratio, 6) if cadence else None,
                              "expected_slots_between_first_and_last": expected_between,
                              "estimated_missing_slots_between_first_and_last": max(0, expected_between-len(parsed.rows)) if expected_between is not None else None,
                              "nonmissing_by_variable": {key: fields.get(key, 0) for key in CANONICAL_FIELDS}})
    return {"schema_version": "tl08a-coverage-v1", "generated_at": datetime.now(timezone.utc).isoformat(),
            "station_id": "51201", "yearly": years, "monthly": months,
            "historical_jobs": safe_jobs, "historical_source_audits": source_audits,
            "source_files": coverage["source_files"],
            "notes": ["Nominal 30-minute ratio is a reference, not proof of continuous coverage or historical cadence.",
                      "Nominal expected/missing slots use the month's inferred cadence and full calendar month; publication changes and partial source periods can lower the ratio.",
                      "Bronze can exceed canonical Silver because multiple source feeds retain raw provenance.",
                      "Gold month boundaries use Pacific/Honolulu; Bronze and Silver use UTC."]}


def main() -> None:
    gateway, gcs = HttpDatabaseGateway(), RealGCSClient()
    try:
        print(json.dumps(build_report(gateway, gcs), sort_keys=True, indent=2))
    finally:
        gateway.close()


if __name__ == "__main__":
    main()
