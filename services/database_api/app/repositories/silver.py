from services.database_api.app.repositories.common import insert_row, upsert_row
from services.database_api.app.schemas.observations import DailyFeatures, GapIn, QualityIssueIn, SilverBatch


PRIORITY = {"realtime": 1, "historical_monthly": 2, "historical_annual": 3}


def upsert_silver_batch(conn, batch: SilverBatch) -> dict:
    counts = {"inserted": 0, "updated": 0, "duplicate": 0, "skipped": 0}
    for row in batch.rows:
        values = row.model_dump()
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"silver:{row.station_id}:{row.observed_at.isoformat()}",))
        current = conn.execute(
            "SELECT source_kind, measurement_sha256, source_file_id, quality_status, quality_flags, missing_fields "
            "FROM silver.wave_observations WHERE station_id=%s AND observed_at=%s FOR UPDATE",
            (row.station_id, row.observed_at),
        ).fetchone()
        if current is None:
            insert_row(conn, "silver", "wave_observations", values)
            counts["inserted"] += 1
            continue
        if PRIORITY[row.source_kind] < PRIORITY[current["source_kind"]]:
            counts["skipped"] += 1
            continue
        if all(current[key] == values[key] for key in (
            "source_kind", "measurement_sha256", "source_file_id", "quality_status", "quality_flags", "missing_fields"
        )):
            counts["duplicate"] += 1
            continue
        # The SQL trigger increments revision_no and writes the audit snapshot.
        from psycopg import sql
        from services.database_api.app.repositories.common import adapted
        columns = [k for k in values if k not in ("station_id", "observed_at")]
        query = sql.SQL("UPDATE silver.wave_observations SET {} WHERE station_id=%s AND observed_at=%s").format(
            sql.SQL(", ").join(sql.SQL("{}=%s").format(sql.Identifier(k)) for k in columns)
        )
        conn.execute(query, [adapted(values[k], k) for k in columns] + [row.station_id, row.observed_at])
        counts["updated"] += 1
    return counts


def upsert_daily_features(conn, item: DailyFeatures) -> dict:
    values = item.model_dump(exclude_none=True)
    upsert_row(conn, "silver", "daily_wave_features", values, ("station_id", "date_hst"))
    return {"station_id": item.station_id, "date_hst": item.date_hst.isoformat()}


def save_quality_issue(conn, item: QualityIssueIn, idempotency_key: str):
    previous = conn.execute("SELECT issue_id FROM silver.data_quality_issues WHERE metadata->>'idempotency_key'=%s ORDER BY issue_id DESC LIMIT 1", (idempotency_key,)).fetchone()
    if previous:
        return previous
    values = item.model_dump(exclude_none=True)
    values["metadata"] = {**item.metadata, "idempotency_key": idempotency_key}
    return insert_row(conn, "silver", "data_quality_issues", values, returning="issue_id").fetchone()


def save_gap(conn, item: GapIn):
    return insert_row(conn, "silver", "observation_gaps", item.model_dump(exclude_none=True), conflict="ON CONFLICT (station_id, gap_start_at, gap_end_at) DO NOTHING", returning="gap_id").fetchone()
