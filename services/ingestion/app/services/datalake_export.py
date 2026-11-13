"""Deterministic month exports, verified against live Database API and pinned GCS bytes."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
from calendar import monthrange
from datetime import date, datetime, timezone

from packages.common.time import hst_day_bounds_utc

SCHEMA_VERSION = "tl08a-jsonl-v1"
LAYERS = ("bronze", "silver", "gold")


def _window(year: int, month: int, layer: str) -> tuple[datetime, datetime]:
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    if layer == "gold":
        return hst_day_bounds_utc(date(year, month, 1))[0], hst_day_bounds_utc(date(next_year, next_month, 1))[0]
    return datetime(year, month, 1, tzinfo=timezone.utc), datetime(next_year, next_month, 1, tzinfo=timezone.utc)


def _rows(gateway, layer: str, year: int, month: int) -> list[dict]:
    start, end = _window(year, month, layer)
    rows: list[dict] = []
    if layer == "bronze":
        after = 0
        while True:
            page = gateway._request("GET", "/internal/v1/datalake/bronze/raw",
                                    params={"start": start.isoformat(), "end": end.isoformat(), "after_raw_id": after, "limit": 500})
            rows.extend(page["data"])
            if page["next_after"] is None:
                break
            after = page["next_after"]
    elif layer == "silver":
        after = None
        while True:
            params = {"start": start.isoformat(), "end": end.isoformat(), "limit": 500}
            if after:
                params["after"] = after
            page = gateway._request("GET", "/internal/v1/datalake/silver/rows", params=params)
            rows.extend(page["data"])
            if page["next_after"] is None:
                break
            after = page["next_after"]
    elif layer == "gold":
        after = None
        resolution = ""
        while True:
            params = {"start": start.isoformat(), "end": end.isoformat(), "limit": 500,
                      "after_resolution": resolution}
            if after:
                params["after_start"] = after
            page = gateway._request("GET", "/internal/v1/datalake/gold/rows", params=params)
            rows.extend({"record_type": "resolution_metric", **row} for row in page["data"])
            if page["next_after"] is None:
                break
            after = page["next_after"]["start"]
            resolution = page["next_after"]["resolution"]
        last_day = monthrange(year, month)[1]
        daily = gateway._request("GET", "/internal/v1/silver/daily-features",
                                 params={"start_hst": date(year, month, 1).isoformat(),
                                         "end_hst": date(year, month, last_day).isoformat(), "limit": 1000})["data"]
        rows.extend({"record_type": "daily_feature", **row} for row in daily)
    else:
        raise ValueError("unknown dataset layer")
    return rows


def _canonical(rows: list[dict], layer: str, year: int, month: int,
               source_datasets: list[dict] | None = None) -> bytes:
    header = {"schema_version": SCHEMA_VERSION, "dataset_layer": layer, "station_id": "51201",
              "partition_key": f"station=51201/year={year}/month={month:02d}",
              "timezone": "Pacific/Honolulu" if layer == "gold" else "UTC",
              "record_count": len(rows), "source_datasets": source_datasets or []}
    lines = [json.dumps({"type": "header", **header}, sort_keys=True, separators=(",", ":"), allow_nan=False)]
    lines.extend(json.dumps({"type": "record", "data": row}, sort_keys=True, separators=(",", ":"), allow_nan=False)
                 for row in rows)
    return ("\n".join(lines) + "\n").encode()


def _gzip(data: bytes) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0, filename="") as stream:
        stream.write(data)
    return buf.getvalue()


def _gold_sources(gateway, year: int, month: int) -> list[dict]:
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    refs = []
    for part_year, part_month in ((year, month), (next_year, next_month)):
        partition = f"station=51201/year={part_year}/month={part_month:02d}"
        row = gateway._request("GET", "/internal/v1/datalake/exports/active",
                               params={"layer": "silver", "partition_key": partition})
        if row.get("state") == "VERIFIED":
            refs.append({"partition_key": partition, "content_sha256": row["content_sha256"],
                         "gcs_generation": row["gcs_generation"]})
    return refs


def export_month(gateway, gcs, *, bucket: str, layer: str, year: int, month: int,
                 processing_run_id: str | None = None) -> dict:
    """Export a bounded partition; activate only after DB reread and GCS round trip."""
    if bucket != "waimea-noaa" or layer not in LAYERS or not 2000 <= year <= 2100 or not 1 <= month <= 12:
        raise ValueError("invalid export target")
    rows = _rows(gateway, layer, year, month)
    sources = _gold_sources(gateway, year, month) if layer == "gold" else []
    raw = _canonical(rows, layer, year, month, sources)
    payload = _gzip(raw)
    sha = hashlib.sha256(payload).hexdigest()
    partition = f"station=51201/year={year}/month={month:02d}"
    object_path = f"datasets/{layer}/{partition}/{sha}.jsonl.gz"
    uri = f"gs://{bucket}/{object_path}"
    timestamps = [row.get("observed_at") or row.get("period_start_at") for row in rows]
    timestamps = [value for value in timestamps if value]
    body = {"layer": layer, "partition_key": partition, "schema_version": SCHEMA_VERSION,
            "content_sha256": sha, "row_count": len(rows), "gcs_uri": uri,
            "min_observed_at": min(timestamps) if timestamps else None,
            "max_observed_at": max(timestamps) if timestamps else None,
            "source_watermark": max(timestamps) if timestamps else None,
            "processing_run_id": processing_run_id}
    manifest = gateway._request("POST", "/internal/v1/datalake/exports/prepare", body=body)
    try:
        info = gcs.upload_if_absent(bucket, object_path, payload, content_type="application/gzip",
                                    metadata={"content_sha256": sha, "schema_version": SCHEMA_VERSION,
                                              "dataset_layer": layer, "dataset_version": sha,
                                              "row_count": str(len(rows))})
        if info.content_sha256 != sha or info.generation < 1:
            raise RuntimeError("GCS checksum or generation mismatch")
        stored = gcs.download(bucket, object_path, info.generation)
        if hashlib.sha256(stored).hexdigest() != sha or gzip.decompress(stored) != raw:
            raise RuntimeError("GCS round-trip verification failed")
        reread = _rows(gateway, layer, year, month)
        current_sources = _gold_sources(gateway, year, month) if layer == "gold" else []
        if _canonical(reread, layer, year, month, current_sources) != raw:
            raise RuntimeError("PostgreSQL partition changed during export")
        gateway._request("POST", "/internal/v1/datalake/exports/complete",
                         body={"export_id": manifest["export_id"], "gcs_generation": info.generation})
    except Exception as exc:
        try:
            gateway._request("POST", "/internal/v1/datalake/exports/fail",
                             body={"export_id": manifest["export_id"], "error_class": type(exc).__name__})
        except Exception:
            pass  # The prepared row remains durable if the API is unavailable.
        raise
    return {"layer": layer, "partition": partition, "rows": len(rows), "sha256": sha,
            "gcs_uri": uri, "generation": info.generation, "verified": True}


def verify_month(gateway, gcs, *, bucket: str, layer: str, year: int, month: int) -> dict | None:
    """Reconcile an active manifest with canonical PostgreSQL and pinned GCS bytes."""
    if bucket != "waimea-noaa" or layer not in LAYERS or not 2000 <= year <= 2100 or not 1 <= month <= 12:
        raise ValueError("invalid verification target")
    partition = f"station=51201/year={year}/month={month:02d}"
    row = gateway._request("GET", "/internal/v1/datalake/exports/active",
                           params={"layer": layer, "partition_key": partition})
    if row.get("found") is False:
        return None
    if row["state"] != "VERIFIED" or row["gcs_generation"] is None:
        raise RuntimeError("active export lacks verified generation")
    object_path = row["gcs_uri"].removeprefix(f"gs://{bucket}/")
    if object_path == row["gcs_uri"]:
        raise RuntimeError("export bucket mismatch")
    stored = gcs.download(bucket, object_path, int(row["gcs_generation"]))
    if hashlib.sha256(stored).hexdigest() != row["content_sha256"]:
        raise RuntimeError("active export GCS checksum mismatch")
    records = _rows(gateway, layer, year, month)
    sources = _gold_sources(gateway, year, month) if layer == "gold" else []
    if _gzip(_canonical(records, layer, year, month, sources)) != stored or len(records) != row["row_count"]:
        raise RuntimeError("active export differs from PostgreSQL partition")
    return {"layer": layer, "partition_key": partition, "row_count": len(records),
            "content_sha256": row["content_sha256"], "gcs_uri": row["gcs_uri"],
            "gcs_generation": row["gcs_generation"], "schema_version": row["schema_version"],
            "min_observed_at": row["min_observed_at"], "max_observed_at": row["max_observed_at"]}
