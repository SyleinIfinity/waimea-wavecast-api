from services.database_api.app.repositories.common import insert_row
from services.database_api.app.schemas.observations import IngestionRunIn, IngestionRunUpdate, RawBatch, SourceFileIn


def register_source_file(conn, item: SourceFileIn) -> dict:
    feed = conn.execute(
        "SELECT feed_id FROM bronze.source_feeds WHERE station_id=%s AND feed_key=%s",
        (item.station_id, item.feed_key),
    ).fetchone()
    if feed is None:
        raise ValueError("unknown NOAA feed")
    values = item.model_dump(exclude={"feed_key"}, exclude_none=True)
    values["feed_id"] = feed["feed_id"]
    result = insert_row(
        conn, "bronze", "source_files", values,
        conflict="ON CONFLICT (station_id, feed_id, content_sha256) DO UPDATE SET "
                 "gcs_uri=CASE WHEN EXCLUDED.gcs_generation IS NOT NULL THEN EXCLUDED.gcs_uri ELSE source_files.gcs_uri END, "
                 "gcs_bucket=CASE WHEN EXCLUDED.gcs_generation IS NOT NULL THEN EXCLUDED.gcs_bucket ELSE source_files.gcs_bucket END, "
                 "gcs_object_name=CASE WHEN EXCLUDED.gcs_generation IS NOT NULL THEN EXCLUDED.gcs_object_name ELSE source_files.gcs_object_name END, "
                 "gcs_generation=COALESCE(EXCLUDED.gcs_generation, source_files.gcs_generation), "
                 "gcs_crc32c=CASE WHEN EXCLUDED.gcs_generation IS NOT NULL THEN EXCLUDED.gcs_crc32c ELSE source_files.gcs_crc32c END, "
                 "gcs_upload_status=CASE WHEN EXCLUDED.gcs_generation IS NOT NULL AND EXCLUDED.gcs_upload_status IN ('uploaded','verified') "
                 "THEN EXCLUDED.gcs_upload_status ELSE source_files.gcs_upload_status END",
        returning="file_id",
    ).fetchone()
    return {"file_id": result["file_id"]}


def upsert_raw_batch(conn, batch: RawBatch) -> dict:
    inserted = duplicate = 0
    raw_ids = {}
    source = conn.execute(
        "SELECT feed.feed_key FROM bronze.source_files f "
        "JOIN bronze.source_feeds feed ON feed.feed_id=f.feed_id WHERE f.file_id=%s",
        (batch.file_id,),
    ).fetchone() if any(row.observed_at is not None for row in batch.rows) else None
    if source is None and any(row.observed_at is not None for row in batch.rows):
        raise ValueError("unknown source file")
    for row in batch.rows:
        result = None
        # Realtime NOAA files are rolling snapshots. The complete source bytes
        # remain pinned in GCS; one PostgreSQL Raw row per identical reading is
        # sufficient for Silver lineage. Serialize concurrent workers by key.
        if source is not None and source["feed_key"] == "realtime" and row.observed_at is not None:
            key = f"{row.station_id}:{row.observed_at.isoformat()}:{row.row_sha256}"
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (key,))
            result = conn.execute(
                "SELECT raw_id FROM bronze.raw_observations "
                "WHERE station_id=%s AND observed_at=%s AND row_sha256=%s "
                "ORDER BY raw_id LIMIT 1",
                (row.station_id, row.observed_at, row.row_sha256),
            ).fetchone()
            if result is not None:
                duplicate += 1
                raw_ids[row.row_sha256] = result["raw_id"]
                continue
        values = row.model_dump(exclude_none=True)
        values["file_id"] = batch.file_id
        result = insert_row(
            conn, "bronze", "raw_observations", values,
            conflict="ON CONFLICT (file_id, row_sha256) DO NOTHING",
            returning="raw_id",
        ).fetchone()
        inserted += result is not None
        duplicate += result is None
        if result is None:
            result = conn.execute(
                "SELECT raw_id FROM bronze.raw_observations WHERE file_id=%s AND row_sha256=%s",
                (batch.file_id, row.row_sha256),
            ).fetchone()
        raw_ids[row.row_sha256] = result["raw_id"]
    return {"inserted": inserted, "duplicate": duplicate, "raw_ids": raw_ids}


def create_ingestion_run(conn, item: IngestionRunIn, idempotency_key: str):
    feed = conn.execute("SELECT feed_id FROM bronze.source_feeds WHERE station_id=%s AND feed_key=%s", (item.station_id, item.feed_key)).fetchone()
    if feed is None:
        raise ValueError("unknown NOAA feed")
    values = item.model_dump(exclude={"station_id", "feed_key"}, exclude_none=True)
    values["feed_id"] = feed["feed_id"]
    values["details"] = {"idempotency_key": idempotency_key}
    return insert_row(conn, "bronze", "ingestion_runs", values, returning="run_id").fetchone()


def update_ingestion_run(conn, run_id: int, item: IngestionRunUpdate):
    fields = item.model_dump(exclude_none=True)
    from psycopg import sql
    columns = list(fields)
    statement = sql.SQL("UPDATE bronze.ingestion_runs SET {}, finished_at=CASE WHEN %s='running' THEN NULL ELSE now() END WHERE run_id=%s RETURNING run_id,status").format(
        sql.SQL(", ").join(sql.SQL("{}=%s").format(sql.Identifier(c)) for c in columns)
    )
    return conn.execute(statement, [fields[c] for c in columns] + [item.status, run_id]).fetchone()
