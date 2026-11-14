"""Move old revision snapshot JSON to verified, immutable monthly GCS objects.

Revision identifiers and all searchable metadata remain in PostgreSQL. Each
snapshot becomes a small archive pointer only after an ADC GCS round trip and
a locked second read of exactly the rows being archived.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
from datetime import datetime, timezone

from packages.gcs.client import RealGCSClient
from services.database_api.app.config import Settings
from services.database_api.app.db.session import Database
from services.database_api.app.repositories.common import json_safe

STATION = "51201"
BUCKET = "waimea-noaa"


def _bounds(year: int, month: int):
    if not 2000 <= year <= 2100 or not 1 <= month <= 12:
        raise ValueError("invalid UTC month")
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    end = datetime(year + 1, 1, 1, tzinfo=timezone.utc) if month == 12 else datetime(year, month + 1, 1, tzinfo=timezone.utc)
    return start, end


def _rows(conn, start, end, *, lock=False):
    return conn.execute(
        "SELECT revision_id,station_id,observed_at,revision_no,source_kind,"
        "source_file_id,raw_id,measurement_sha256,change_type,snapshot,recorded_at "
        "FROM silver.wave_observation_revisions "
        "WHERE station_id=%s AND observed_at >= %s AND observed_at < %s "
        "AND NOT (snapshot ? 'archive_id') ORDER BY revision_id" + (" FOR UPDATE" if lock else ""),
        (STATION, start, end),
    ).fetchall()


def _payload(rows, year, month):
    header = {"type": "header", "schema_version": "revision-snapshot-v1",
              "station_id": STATION, "year_utc": year, "month_utc": month,
              "row_count": len(rows)}
    lines = [json.dumps(header, sort_keys=True, separators=(",", ":"))]
    lines.extend(json.dumps({"type": "record", "data": json_safe(row)},
                            sort_keys=True, separators=(",", ":"), allow_nan=False)
                 for row in rows)
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0, filename="") as stream:
        stream.write(("\n".join(lines) + "\n").encode())
    return buf.getvalue()


def archive_month(db, gcs, year: int, month: int) -> dict:
    start, end = _bounds(year, month)
    with db.connection() as conn:
        rows = _rows(conn, start, end)
    if not rows:
        return {"year": year, "month": month, "archived_rows": 0}

    payload = _payload(rows, year, month)
    sha = hashlib.sha256(payload).hexdigest()
    path = f"datasets/revisions/station={STATION}/year={year}/month={month:02d}/{sha}.jsonl.gz"
    info = gcs.upload_if_absent(BUCKET, path, payload, content_type="application/gzip",
                                metadata={"schema_version": "revision-snapshot-v1", "row_count": str(len(rows))})
    if info.content_sha256 != sha or info.generation < 1:
        raise RuntimeError("revision archive GCS upload checksum mismatch")
    downloaded = gcs.download(BUCKET, path, info.generation)
    if downloaded != payload or hashlib.sha256(downloaded).hexdigest() != sha:
        raise RuntimeError("revision archive GCS round-trip mismatch")

    with db.connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                     (f"revision-archive:{STATION}:{year}:{month}",))
        current = _rows(conn, start, end, lock=True)
        if _payload(current, year, month) != payload:
            raise RuntimeError("revision month changed during GCS archive; retry")
        archive = conn.execute(
            "INSERT INTO silver.revision_payload_archives "
            "(station_id,year_utc,month_utc,row_count,content_sha256,gcs_uri,gcs_generation) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (station_id,year_utc,month_utc,content_sha256) DO UPDATE "
            "SET gcs_generation=EXCLUDED.gcs_generation RETURNING archive_id",
            (STATION, year, month, len(rows), sha, info.gcs_uri, info.generation),
        ).fetchone()
        ids = [row["revision_id"] for row in current]
        updated = conn.execute(
            "UPDATE silver.wave_observation_revisions "
            "SET snapshot=jsonb_build_object('archive_id',%s) "
            "WHERE revision_id=ANY(%s) AND NOT (snapshot ? 'archive_id')",
            (archive["archive_id"], ids),
        ).rowcount
        if updated != len(ids):
            raise RuntimeError("revision archive row count changed; rolling back")
    return {"year": year, "month": month, "archived_rows": len(rows),
            "archive_id": archive["archive_id"], "sha256": sha,
            "gcs_generation": info.generation, "gcs_uri": info.gcs_uri}


def _eligible_months(db, before: datetime, limit: int):
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT EXTRACT(YEAR FROM observed_at AT TIME ZONE 'UTC')::int AS year_utc,"
            "EXTRACT(MONTH FROM observed_at AT TIME ZONE 'UTC')::int AS month_utc "
            "FROM silver.wave_observation_revisions WHERE station_id=%s "
            "AND observed_at < %s AND NOT (snapshot ? 'archive_id') "
            "GROUP BY 1,2 ORDER BY 1,2 LIMIT %s", (STATION, before, limit),
        ).fetchall()
    return [(row["year_utc"], row["month_utc"]) for row in rows]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retention-months", type=int,
                        default=int(os.environ.get("REVISION_ARCHIVE_RETENTION_MONTHS", "24")))
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
    try:
        months = [(args.year, args.month)] if args.year is not None else _eligible_months(db, before, args.max_months)
        if any(_bounds(year, month)[1] > before for year, month in months):
            parser.error("only complete months older than retention cutoff may be archived")
        gcs = RealGCSClient()
        for year, month in months:
            print(json.dumps(archive_month(db, gcs, year, month), sort_keys=True))
    finally:
        db.close()


if __name__ == "__main__":
    main()
