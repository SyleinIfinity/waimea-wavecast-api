"""Verified GCS snapshots for the observation reset scope."""
from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timezone
from graphlib import CycleError, TopologicalSorter
from tempfile import TemporaryFile
from uuid import UUID, uuid4
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

from psycopg import sql
from psycopg.types.json import Jsonb

from packages.gcs.client import GCSClientProtocol

RESET_TABLES = (
    "gold.calisthenics_shadow_evaluations", "gold.calisthenics_shadow_forecasts",
    "gold.forward_evaluations", "gold.forward_forecasts",
    "gold.classifications", "gold.predictions", "gold.cluster_assignments",
    "gold.correlations", "gold.trend_seasonal_components", "gold.time_series_metrics",
    "gold.resolution_metrics", "gold.derived_imputation_candidates", "gold.pipeline_cycles",
    "silver.revision_payload_archives", "silver.wave_observation_revisions",
    "silver.data_quality_issues", "silver.observation_gaps", "silver.daily_wave_features",
    "silver.wave_observations", "bronze.raw_observations",
)
TABLE_SCOPE = sql.SQL(", ").join(sql.Identifier(*name.split(".")) for name in RESET_TABLES)
FORMAT_VERSION = 1


class BackupError(Exception):
    """A snapshot could not be saved or validated."""


def counts(conn) -> dict[str, int]:
    return {
        table: conn.execute(
            sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(*table.split(".")))
        ).fetchone()["n"]
        for table in RESET_TABLES
    }


def _layout(conn) -> dict:
    rows = conn.execute("""
        SELECT ns.nspname || '.' || c.relname AS name,
               jsonb_agg(jsonb_build_array(a.attname, format_type(a.atttypid, a.atttypmod))
                         ORDER BY a.attnum) AS cols
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace ns ON ns.oid = c.relnamespace
        WHERE ns.nspname || '.' || c.relname = ANY(%s)
          AND a.attnum > 0 AND NOT a.attisdropped AND a.attgenerated = ''
        GROUP BY 1
    """, (list(RESET_TABLES),)).fetchall()
    layout = {row["name"]: row["cols"] for row in rows}
    if layout.keys() != set(RESET_TABLES):
        raise BackupError("observation backup schema is incomplete")
    return layout


def _copy_statement(table: str, columns: list, direction: str) -> sql.Composed:
    return sql.SQL("COPY {} ({}) {} WITH (FORMAT csv, ENCODING 'UTF8')").format(
        sql.Identifier(*table.split(".")),
        sql.SQL(", ").join(sql.Identifier(column[0]) for column in columns),
        sql.SQL(direction),
    )


def _copy_settings(conn) -> None:
    conn.execute("SET LOCAL TIME ZONE 'UTC'")
    conn.execute("SET LOCAL DateStyle TO 'ISO, YMD'")
    conn.execute("SET LOCAL IntervalStyle TO 'postgres'")


def _export(conn, row_counts: dict) -> bytes:
    layout = _layout(conn)
    _copy_settings(conn)
    with TemporaryFile() as output:
        with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps({
                "version": FORMAT_VERSION, "columns": layout, "row_counts": row_counts,
            }))
            with conn.cursor() as cursor:
                for table in RESET_TABLES:
                    with archive.open(table + ".csv", "w", force_zip64=True) as entry:
                        with cursor.copy(_copy_statement(table, layout[table], "TO STDOUT")) as copy:
                            for block in copy:
                                entry.write(block)
        output.seek(0)
        return output.read()


def load_backup(gcs: GCSClientProtocol, record: dict) -> bytes:
    try:
        data = gcs.download(record["bucket"], record["object_path"], record["generation"])
    except Exception as exc:
        raise BackupError("could not read observation backup from GCS") from exc
    if hashlib.sha256(data).hexdigest() != record["sha256"]:
        raise BackupError("observation backup checksum mismatch")
    return data


def save_backup(conn, gcs: GCSClientProtocol, bucket: str, environment: str,
                row_counts: dict, purpose: str) -> dict:
    backup_id = uuid4()
    database_name = conn.info.dbname
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    object_path = f"backups/observation-reset/{environment}/{database_name}/{stamp}-{backup_id}.zip"
    # Fail before uploading if the database migration has not been applied.
    conn.execute("SELECT 1 FROM gold.observation_reset_backups LIMIT 0")
    data = _export(conn, row_counts)
    sha256 = hashlib.sha256(data).hexdigest()
    try:
        info = gcs.upload_if_absent(bucket, object_path, data, "application/zip")
        generation = int(info.generation)
    except Exception as exc:
        raise BackupError("could not save observation backup to GCS") from exc
    del data
    if generation <= 0:
        raise BackupError("observation backup generation is invalid")
    load_backup(gcs, {"bucket": bucket, "object_path": object_path,
                      "generation": generation, "sha256": sha256})
    return conn.execute("""
        INSERT INTO gold.observation_reset_backups
            (backup_id, environment, purpose, bucket, object_path, generation, sha256, row_counts)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
    """, (backup_id, environment, purpose, bucket, object_path, generation,
          sha256, Jsonb(row_counts))).fetchone()


def find_backup(conn, environment: str, backup_id: UUID | None) -> dict | None:
    if backup_id is not None:
        return conn.execute("""
            SELECT * FROM gold.observation_reset_backups
            WHERE environment = %s AND backup_id = %s
        """, (environment, backup_id)).fetchone()
    return conn.execute("""
        SELECT * FROM gold.observation_reset_backups
        WHERE environment = %s AND purpose = 'reset'
        ORDER BY created_at DESC, backup_id DESC LIMIT 1
    """, (environment,)).fetchone()


def _restore_order(conn) -> tuple[str, ...]:
    rows = conn.execute("""
        SELECT child_ns.nspname || '.' || child.relname AS child,
               parent_ns.nspname || '.' || parent.relname AS parent
        FROM pg_constraint c
        JOIN pg_class child ON child.oid = c.conrelid
        JOIN pg_class parent ON parent.oid = c.confrelid
        JOIN pg_namespace child_ns ON child_ns.oid = child.relnamespace
        JOIN pg_namespace parent_ns ON parent_ns.oid = parent.relnamespace
        WHERE c.contype = 'f' AND c.conrelid <> c.confrelid
          AND child_ns.nspname || '.' || child.relname = ANY(%s)
          AND parent_ns.nspname || '.' || parent.relname = ANY(%s)
    """, (list(RESET_TABLES), list(RESET_TABLES))).fetchall()
    order = TopologicalSorter({table: () for table in RESET_TABLES})
    for row in rows:
        order.add(row["child"], row["parent"])
    try:
        return tuple(order.static_order())
    except CycleError as exc:
        raise BackupError("observation backup has cyclic table dependencies") from exc


def restore_backup(conn, data: bytes, row_counts: dict) -> None:
    layout = _layout(conn)
    try:
        with ZipFile(io.BytesIO(data), "r") as archive:
            names = ["manifest.json", *(table + ".csv" for table in RESET_TABLES)]
            if sorted(archive.namelist()) != sorted(names):
                raise BackupError("observation backup file set does not match the reset scope")
            manifest = json.loads(archive.read("manifest.json"))
            if manifest != {"version": FORMAT_VERSION, "columns": layout, "row_counts": row_counts}:
                raise BackupError("observation backup manifest does not match this database")
            order = _restore_order(conn)
            _copy_settings(conn)
            conn.execute("SET LOCAL waimea.restoring_observations = 'on'")
            conn.execute(sql.SQL("TRUNCATE TABLE {}").format(TABLE_SCOPE))
            with conn.cursor() as cursor:
                for table in order:
                    with archive.open(table + ".csv") as entry:
                        with cursor.copy(_copy_statement(table, layout[table], "FROM STDIN")) as copy:
                            while block := entry.read(1024 * 1024):
                                copy.write(block)
    except (BadZipFile, UnicodeError, json.JSONDecodeError) as exc:
        raise BackupError("observation backup archive is invalid") from exc
    if counts(conn) != row_counts:
        raise BackupError("restored row counts do not match the observation backup")


def describe_backup(record: dict) -> dict:
    return {
        "backup_id": record["backup_id"],
        "created_at": record["created_at"],
        "gcs_uri": f"gs://{record['bucket']}/{record['object_path']}",
        "generation": str(record["generation"]),
        "sha256": record["sha256"],
        "row_count": sum(record["row_counts"].values()),
    }
