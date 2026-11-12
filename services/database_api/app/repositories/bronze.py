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
    for row in batch.rows:
        values = row.model_dump(exclude_none=True)
        values["file_id"] = batch.file_id
        result = insert_row(
            conn, "bronze", "raw_observations", values,
            conflict="ON CONFLICT (file_id, row_sha256) DO NOTHING",
            returning="raw_id",
        ).fetchone()
        inserted += result is not None
        duplicate += result is None
    return {"inserted": inserted, "duplicate": duplicate}


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
