"""One-time cleanup of redundant realtime Raw rows, with pinned GCS checks.

Only duplicate readings with no Silver, revision or QC foreign-key references
are removed. Source-file metadata and original NOAA objects remain intact.
"""
from __future__ import annotations

import argparse
import hashlib
import json

from packages.gcs.client import RealGCSClient
from services.database_api.app.config import Settings
from services.database_api.app.db.session import Database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--batch-size", type=int, default=2000)
    args = parser.parse_args()
    if not 100 <= args.batch_size <= 5000:
        parser.error("batch-size must be 100-5000")
    db = Database(Settings())
    try:
        with db.connection() as conn:
            files = conn.execute(
                "SELECT f.file_id,f.gcs_uri,f.gcs_generation,f.content_sha256,"
                "f.source_row_count,f.extra_metadata->>'ingest_complete' AS complete,"
                "(SELECT count(*) FROM bronze.raw_observations r WHERE r.file_id=f.file_id) AS raw_count "
                "FROM bronze.source_files f JOIN bronze.source_feeds feed ON feed.feed_id=f.feed_id "
                "WHERE feed.feed_key='realtime' AND f.gcs_upload_status='verified' "
                "AND f.gcs_uri LIKE 'gs://waimea-noaa/noaa/raw/realtime/%' ORDER BY f.file_id"
            ).fetchall()
        eligible = [item for item in files if item["complete"] == "true" or
                    (item["source_row_count"] is not None and item["raw_count"] >= item["source_row_count"])]
        ids = [item["file_id"] for item in eligible]
        if not args.apply:
            print(json.dumps({"mode": "preview", "verified_source_files": len(files),
                              "eligible_complete_files": len(ids),
                              "skipped_incomplete_files": len(files) - len(ids)}))
            return
        gcs = RealGCSClient()
        for item in eligible:
            path = item["gcs_uri"].removeprefix("gs://waimea-noaa/")
            if path == item["gcs_uri"] or not item["gcs_generation"]:
                raise RuntimeError("source file lacks pinned GCS provenance")
            original = gcs.download("waimea-noaa", path, int(item["gcs_generation"]))
            if hashlib.sha256(original).hexdigest() != item["content_sha256"]:
                raise RuntimeError("original NOAA source GCS checksum mismatch")
        if not ids:
            print(json.dumps({"deleted": 0, "verified_source_files": 0}))
            return
        with db.connection() as conn:
            conn.execute(
                "UPDATE bronze.source_files SET extra_metadata="
                "jsonb_set(extra_metadata,'{ingest_complete}','true'::jsonb) "
                "WHERE file_id=ANY(%s) AND gcs_upload_status='verified'", (ids,),
            )
        deleted = 0
        months = set()
        while True:
            with db.connection() as conn:
                candidates = conn.execute(
                    "SELECT r.raw_id FROM bronze.raw_observations r "
                    "WHERE r.file_id=ANY(%s) AND r.observed_at IS NOT NULL "
                    "AND EXISTS (SELECT 1 FROM bronze.raw_observations x "
                    "WHERE x.station_id=r.station_id AND x.observed_at=r.observed_at "
                    "AND x.row_sha256=r.row_sha256 AND x.raw_id<r.raw_id) "
                    "AND NOT EXISTS (SELECT 1 FROM silver.wave_observations s WHERE s.raw_id=r.raw_id) "
                    "AND NOT EXISTS (SELECT 1 FROM silver.wave_observation_revisions v WHERE v.raw_id=r.raw_id) "
                    "AND NOT EXISTS (SELECT 1 FROM silver.data_quality_issues q WHERE q.raw_id=r.raw_id) "
                    "ORDER BY r.raw_id LIMIT %s FOR UPDATE SKIP LOCKED",
                    (ids, args.batch_size),
                ).fetchall()
                if not candidates:
                    break
                removed = conn.execute(
                    "DELETE FROM bronze.raw_observations WHERE raw_id=ANY(%s) "
                    "RETURNING observed_at", ([item["raw_id"] for item in candidates],),
                ).fetchall()
                if len(removed) != len(candidates):
                    raise RuntimeError("Raw duplicate cleanup count mismatch")
                deleted += len(removed)
                months.update(item["observed_at"].strftime("%Y-%m") for item in removed)
        print(json.dumps({"deleted": deleted, "verified_source_files": len(eligible),
                          "affected_utc_months": sorted(months)}))
    finally:
        db.close()


if __name__ == "__main__":
    main()
