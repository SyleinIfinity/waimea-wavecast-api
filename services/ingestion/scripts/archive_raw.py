"""Replace old Raw payloads with pointers to verified monthly Bronze GCS exports.

Raw IDs and source-file foreign keys stay in PostgreSQL. The export reader
rehydrates payloads from pinned GCS generations, including after a later
historical correction creates a new active monthly export.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from datetime import datetime, timezone

from packages.db.http import HttpDatabaseGateway
from packages.gcs.client import RealGCSClient
from services.database_api.app.config import Settings
from services.database_api.app.db.session import Database
from services.ingestion.app.services.datalake_export import export_month, verify_month

STATION = "51201"
BUCKET = "waimea-noaa"


def _bounds(year, month):
    if not 2000 <= year <= 2100 or not 1 <= month <= 12:
        raise ValueError("invalid UTC month")
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    end = datetime(year + 1, 1, 1, tzinfo=timezone.utc) if month == 12 else datetime(year, month + 1, 1, tzinfo=timezone.utc)
    return start, end


def _eligible_months(db, before, limit):
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT EXTRACT(YEAR FROM observed_at AT TIME ZONE 'UTC')::int AS year_utc,"
            "EXTRACT(MONTH FROM observed_at AT TIME ZONE 'UTC')::int AS month_utc "
            "FROM bronze.raw_observations WHERE station_id=%s AND observed_at < %s "
            "AND NOT (raw_values ? 'archive_export_id') GROUP BY 1,2 ORDER BY 1,2 LIMIT %s",
            (STATION, before, limit),
        ).fetchall()
    return [(row["year_utc"], row["month_utc"]) for row in rows]


def archive_month(db, gateway, gcs, year, month):
    start, end = _bounds(year, month)
    partition = f"station={STATION}/year={year}/month={month:02d}"
    verified = verify_month(gateway, gcs, bucket=BUCKET, layer="bronze", year=year, month=month)
    if verified is None:
        export_month(gateway, gcs, bucket=BUCKET, layer="bronze", year=year, month=month,
                     processing_run_id="raw-retention")
        verified = verify_month(gateway, gcs, bucket=BUCKET, layer="bronze", year=year, month=month)
    if verified is None:
        raise RuntimeError("Bronze month has no verified GCS export")
    export = gateway._request("GET", "/internal/v1/datalake/exports/active",
                              params={"layer": "bronze", "partition_key": partition})
    if export.get("state") != "VERIFIED" or export.get("gcs_generation") is None:
        raise RuntimeError("Bronze export is not verified")
    path = export["gcs_uri"].removeprefix(f"gs://{BUCKET}/")
    if path == export["gcs_uri"]:
        raise RuntimeError("Bronze export bucket mismatch")
    blob = gcs.download(BUCKET, path, int(export["gcs_generation"]))
    if hashlib.sha256(blob).hexdigest() != export["content_sha256"]:
        raise RuntimeError("Bronze export checksum mismatch")
    decoded = gzip.decompress(blob).splitlines()
    header = json.loads(decoded[0])
    original = {record["data"]["raw_id"]: record["data"]
                for record in map(json.loads, decoded[1:])}
    if header["record_count"] != len(original) or len(original) != export["row_count"]:
        raise RuntimeError("Bronze export count mismatch")

    with db.connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                     (f"export:bronze:{partition}",))
        current = conn.execute(
            "SELECT raw_id,row_sha256,raw_line,raw_values FROM bronze.raw_observations "
            "WHERE station_id=%s AND observed_at >= %s AND observed_at < %s "
            "ORDER BY raw_id FOR UPDATE", (STATION, start, end),
        ).fetchall()
        if len(current) != len(original):
            raise RuntimeError("Bronze month changed during archive; retry")
        ids = []
        for row in current:
            saved = original.get(row["raw_id"])
            if saved is None or saved["row_sha256"] != row["row_sha256"]:
                raise RuntimeError("Bronze GCS row mismatch")
            if "archive_export_id" in row["raw_values"]:
                continue
            if row["raw_line"] != saved["raw_line"] or row["raw_values"] != saved["raw_values"]:
                raise RuntimeError("Bronze payload changed during archive")
            ids.append(row["raw_id"])
        if ids:
            updated = conn.execute(
                "UPDATE bronze.raw_observations SET raw_line=NULL,"
                "raw_values=jsonb_build_object('archive_export_id',%s) "
                "WHERE raw_id=ANY(%s) AND NOT (raw_values ? 'archive_export_id')",
                (export["export_id"], ids),
            ).rowcount
            if updated != len(ids):
                raise RuntimeError("Raw archive row count changed; rolling back")
    return {"year": year, "month": month, "archived_rows": len(ids),
            "export_id": export["export_id"], "gcs_generation": export["gcs_generation"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retention-months", type=int,
                        default=int(os.environ.get("RAW_ARCHIVE_RETENTION_MONTHS", "24")))
    parser.add_argument("--max-months", type=int, default=1)
    parser.add_argument("--year", type=int)
    parser.add_argument("--month", type=int)
    args = parser.parse_args()
    if not 12 <= args.retention_months <= 120 or not 1 <= args.max_months <= 300:
        parser.error("retention must be 12-120 months and batch must be 1-300 months")
    if (args.year is None) != (args.month is None):
        parser.error("--year and --month must be supplied together")
    now = datetime.now(timezone.utc)
    total_months = now.year * 12 + now.month - 1 - args.retention_months
    before = datetime(total_months // 12, total_months % 12 + 1, 1, tzinfo=timezone.utc)
    db = Database(Settings())
    gateway = HttpDatabaseGateway()
    try:
        months = [(args.year, args.month)] if args.year is not None else _eligible_months(db, before, args.max_months)
        if any(_bounds(year, month)[1] > before for year, month in months):
            parser.error("only complete months older than retention cutoff may be archived")
        gcs = RealGCSClient()
        for year, month in months:
            print(json.dumps(archive_month(db, gateway, gcs, year, month), sort_keys=True))
    finally:
        gateway.close()
        db.close()


if __name__ == "__main__":
    main()
