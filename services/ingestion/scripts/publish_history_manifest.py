"""Verify every populated monthly partition and publish immutable yearly GCS manifests."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from calendar import monthrange
from datetime import date

from packages.common.time import hst_day_bounds_utc
from packages.db.http import HttpDatabaseGateway
from packages.gcs.client import RealGCSClient
from packages.ml.data_quality import infer_cadence
from services.ingestion.app.services.datalake_export import _rows, export_month, verify_month
from services.preprocessing.app.jobs.gold import rebuild_gold
from services.preprocessing.app.jobs.rebuild_days import rebuild_daily_features


def _rebuild_daily_for_year(gateway, year: int) -> dict:
    start = hst_day_bounds_utc(date(year, 1, 1))[0]
    end = hst_day_bounds_utc(date(year + 1, 1, 1))[0]
    observations = gateway.get_observations("51201", start, end)
    evidence = infer_cadence(observations, min_intervals=200, min_support_ratio=0.55)
    cadence = evidence.minutes if evidence and evidence.minutes in (30, 60) else 30
    del observations
    total = 0
    for month in range(1, 13):
        first = date(year, month, 1)
        last = date(year, month, monthrange(year, month)[1])
        total += rebuild_daily_features(first, last, db_gateway=gateway,
                                        expected_sample_count=1440 // cadence).total_upserted
    return {"cadence_minutes": cadence, "daily_features_upserted": total}


def _publish_monthly_artifacts(gateway, gcs, year: int, bucket: str, repair: bool) -> list[dict]:
    """Publish every Silver dependency before any Gold artifact in this year."""
    artifacts = []
    for month in range(1, 13):
        for layer in ("bronze", "silver"):
            if not _rows(gateway, layer, year, month):
                continue
            if repair:
                export_month(gateway, gcs, bucket=bucket, layer=layer, year=year, month=month,
                             processing_run_id=f"manifest-reconcile-{year}")
            verified = verify_month(gateway, gcs, bucket=bucket, layer=layer, year=year, month=month)
            if verified is None:
                raise RuntimeError(f"missing {layer} export for {year}-{month:02d}")
            artifacts.append(verified)
    # HST December reaches the first UTC hours of the next January. Make its
    # Silver source version stable before exporting the current year's Gold.
    if repair and year < 2100 and _rows(gateway, "silver", year + 1, 1):
        export_month(gateway, gcs, bucket=bucket, layer="silver", year=year + 1, month=1,
                     processing_run_id=f"manifest-reconcile-{year}")
    for month in range(1, 13):
        if not _rows(gateway, "gold", year, month):
            continue
        if repair:
            export_month(gateway, gcs, bucket=bucket, layer="gold", year=year, month=month,
                         processing_run_id=f"manifest-reconcile-{year}")
        verified = verify_month(gateway, gcs, bucket=bucket, layer="gold", year=year, month=month)
        if verified is None:
            raise RuntimeError(f"missing gold export for {year}-{month:02d}")
        artifacts.append(verified)
    return artifacts


def publish_year(gateway, gcs, year: int, *, repair: bool = False, rebuild: bool = False) -> dict:
    if not 2004 <= year <= 2100:
        raise ValueError("invalid historical year")
    bucket = os.environ.get("GCS_BUCKET", "waimea-noaa")
    if bucket != "waimea-noaa":
        raise ValueError("wrong GCS bucket")
    jobs = gateway._request("GET", "/internal/v1/datalake/jobs", params={"limit": 100})["data"]
    sources = [{key: item.get(key) for key in ("feed_key", "source_url", "source_period", "source_sha256",
                                                 "source_row_count", "gcs_uri", "gcs_generation", "status")}
               for item in jobs if item["source_period"].startswith(str(year))]
    if not sources or any(item["status"] != "VERIFIED" for item in sources):
        raise RuntimeError("year has no fully verified archive inventory")
    for source in sources:
        uri = source["gcs_uri"] or ""
        object_path = uri.removeprefix(f"gs://{bucket}/")
        if object_path == uri or not source["gcs_generation"] or not source["source_sha256"]:
            raise RuntimeError("source lacks pinned GCS provenance")
        original = gcs.download(bucket, object_path, int(source["gcs_generation"]))
        if hashlib.sha256(original).hexdigest() != source["source_sha256"]:
            raise RuntimeError("original NOAA source GCS checksum mismatch")
        registration = gateway._request("GET", "/internal/v1/source-files/by-gcs",
                                        params={"uri": uri, "generation": source["gcs_generation"]})
        if not registration["found"] or registration["source_row_count"] != source["source_row_count"] or registration["raw_count"] < 1:
            raise RuntimeError("original source PostgreSQL registration mismatch")
    daily_rebuild = _rebuild_daily_for_year(gateway, year) if rebuild else None
    gold_rebuild = rebuild_gold(gateway, year=year) if rebuild else None
    artifacts = _publish_monthly_artifacts(gateway, gcs, year, bucket, repair)
    document = {"schema_version": "tl08a-year-manifest-v1", "station_id": "51201", "year": year,
                "source_files": sorted(sources, key=lambda item: (item["source_period"], item["feed_key"])),
                "artifacts": sorted(artifacts, key=lambda item: (item["partition_key"], item["layer"]))}
    payload = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    sha = hashlib.sha256(payload).hexdigest()
    object_path = f"manifests/station=51201/historical/year={year}/{sha}.json"
    info = gcs.upload_if_absent(bucket, object_path, payload, content_type="application/json",
                                metadata={"content_sha256": sha, "schema_version": document["schema_version"]})
    if info.content_sha256 != sha or gcs.download(bucket, object_path, info.generation) != payload:
        raise RuntimeError("year manifest GCS round-trip failed")
    return {"year": year, "source_files": len(sources), "artifacts": len(artifacts),
            "manifest_sha256": sha, "gcs_uri": info.gcs_uri, "generation": info.generation,
            "verified": True, "daily_rebuild": daily_rebuild, "gold_rebuild": gold_rebuild}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("--rebuild-gold", action="store_true")
    args = parser.parse_args()
    if (args.year is None) == (not args.all):
        parser.error("choose exactly one of --year or --all")
    if args.rebuild_gold and not args.repair:
        parser.error("--rebuild-gold requires --repair to export updated Gold")
    gateway, gcs = HttpDatabaseGateway(), RealGCSClient()
    try:
        jobs = gateway._request("GET", "/internal/v1/datalake/jobs", params={"limit": 100})["data"]
        years = [args.year] if args.year is not None else sorted({int(item["source_period"][:4]) for item in jobs})
        for year in years:
            print(json.dumps(publish_year(gateway, gcs, year, repair=args.repair,
                                          rebuild=args.rebuild_gold), sort_keys=True), flush=True)
    finally:
        gateway.close()


if __name__ == "__main__":
    main()
