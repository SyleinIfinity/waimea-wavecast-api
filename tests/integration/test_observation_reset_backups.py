"""Exercise reset and restore against a disposable, seeded rehearsal database."""
import hashlib
import os
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from google.cloud import storage
from psycopg import sql
from psycopg.types.json import Jsonb

from packages.gcs.client import RealGCSClient
from services.database_api.app.config import Settings
from services.database_api.app.db.observation_backups import RESET_TABLES, counts
from services.database_api.app.db.session import Database
from services.database_api.app.main import create_app

LEGACY_PATH = "/api/v1/admin/demo/observations"
PRIVATE_PATH = "/private/admin/observations"
RESET_CONFIRMATION = "RESET LOCAL OBSERVATIONS"
RESTORE_CONFIRMATION = "RESTORE LATEST OBSERVATIONS"


def _fingerprint(db):
    with db.connection() as conn:
        return {
            table: conn.execute(sql.SQL("""
                SELECT count(*) AS n,
                       md5(string_agg(md5(to_jsonb(t)::text), '' ORDER BY md5(to_jsonb(t)::text))) AS hash
                FROM {} t
            """).format(sql.Identifier(*table.split(".")))).fetchone()
            for table in (*RESET_TABLES, "gold.model_registry", "gold.observation_arrival_outbox")
        }


def test_private_swagger_lists_the_backup_and_restore_actions():
    settings = Settings(_env_file=None, app_env="production")
    with TestClient(create_app(settings)) as api:
        docs = api.get("/private/docs")
        assert docs.status_code == 200 and "/private/openapi.json" in docs.text
        paths = api.get("/private/openapi.json").json()["paths"]
        for route in ("preview-reset", "backup-and-reset", "restore-latest"):
            params = paths[PRIVATE_PATH + "/" + route]["post"]["parameters"]
            assert any(p["in"] == "header" and p["name"] == "x-admin-token" for p in params)
        assert LEGACY_PATH + "/reset" in paths
        assert LEGACY_PATH + "/restore-latest" not in paths


def test_verified_gcs_backup_then_reset_and_restore(monkeypatch):
    password = os.environ.get("OBSERVATION_RESET_TEST_DB_PASSWORD")
    emulator = os.environ.get("STORAGE_EMULATOR_HOST")
    if not password or not emulator:
        pytest.skip("requires a disposable rehearsal database and a local GCS emulator")
    assert urlsplit(emulator).hostname in {"gcs", "localhost", "127.0.0.1"}
    bucket_name = f"waimea-reset-test-{uuid4().hex[:8]}"
    client = storage.Client(project="waimea-local-reset")
    bucket = client.create_bucket(bucket_name)
    settings = Settings(
        _env_file=None, app_env="staging", db_host="waimea-rehearsal-postgres-local-1",
        db_name="waimea_rehearsal", db_user="postgres", db_password=password, db_sslmode="disable",
        admin_api_token=uuid4().hex, demo_delete_enabled=True, demo_backup_confirmed=True,
        gcs_backup_bucket=bucket_name,
    )
    db = Database(settings)
    admin = {"X-Admin-Token": settings.admin_api_token}
    initial = _fingerprint(db)
    assert initial["silver.wave_observations"]["n"] > 0
    assert initial["silver.wave_observation_revisions"]["n"] > 0
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM gold.observation_reset_backups").fetchone()["n"] == 0
        raw_row = conn.execute("SELECT to_jsonb(t) AS row FROM bronze.raw_observations t LIMIT 1").fetchone()["row"]

    with TestClient(create_app(settings, db)) as api:
        def reset():
            preview = api.post(PRIVATE_PATH + "/preview-reset", headers=admin)
            assert preview.status_code == 200, preview.text
            return api.post(PRIVATE_PATH + "/backup-and-reset", headers=admin, json={
                "dry_run_id": preview.json()["dry_run_id"], "confirmation": RESET_CONFIRMATION,
            })

        def restore(**fields):
            return api.post(PRIVATE_PATH + "/restore-latest", headers=admin,
                            json={"confirmation": RESTORE_CONFIRMATION, **fields})

        assert api.post(PRIVATE_PATH + "/restore-latest",
                        json={"confirmation": RESTORE_CONFIRMATION}).status_code == 401
        assert restore().status_code == 404

        for preview_path, reset_path in [
            (LEGACY_PATH, PRIVATE_PATH + "/backup-and-reset"),
            (PRIVATE_PATH, LEGACY_PATH + "/reset"),
        ]:
            preview = api.post(preview_path + "/preview-reset", headers=admin)
            assert preview.status_code == 200, preview.text
            response = api.post(reset_path, headers=admin, json={
                "dry_run_id": preview.json()["dry_run_id"], "confirmation": RESET_CONFIRMATION,
            })
            assert response.status_code == 409, response.text
        assert _fingerprint(db) == initial

        settings.demo_backup_confirmed = False
        assert api.post(LEGACY_PATH + "/preview-reset", headers=admin).status_code == 403

        settings.gcs_backup_bucket = bucket_name + "-missing"
        failure = reset()
        settings.gcs_backup_bucket = bucket_name
        assert failure.status_code == 503, failure.text
        assert _fingerprint(db) == initial

        download = RealGCSClient.download
        with monkeypatch.context() as patch:
            def corrupt_readback(self, bucket, object_path, generation=None):
                return download(self, bucket, object_path, generation) + b"corrupt"

            patch.setattr(RealGCSClient, "download", corrupt_readback)
            failure = reset()
            assert failure.status_code == 503, failure.text
            assert "checksum" in failure.json()["error"]["message"]
        assert _fingerprint(db) == initial
        assert restore().status_code == 404

        result = reset()
        assert result.status_code == 200, result.text
        backup = result.json()["backup"]
        assert result.json()["row_count"] == sum(initial[table]["n"] for table in RESET_TABLES)
        path = backup["gcs_uri"].removeprefix(f"gs://{bucket_name}/")
        blob = bucket.blob(path, generation=int(backup["generation"]))
        assert hashlib.sha256(blob.download_as_bytes()).hexdigest() == backup["sha256"]
        repeated = reset()
        assert repeated.status_code == 200 and repeated.json()["status"] == "already_empty"
        assert repeated.json()["backup"] is None
        with db.connection() as conn:
            assert all(n == 0 for n in counts(conn).values())
            conn.execute("""
                INSERT INTO bronze.raw_observations
                SELECT * FROM jsonb_populate_record(NULL::bronze.raw_observations, %s)
            """, (Jsonb(raw_row),))
        after_reset = _fingerprint(db)

        with TestClient(create_app(settings)) as restarted_api:
            result = restarted_api.post(PRIVATE_PATH + "/restore-latest", headers=admin,
                                        json={"confirmation": RESTORE_CONFIRMATION})
        assert result.status_code == 200, result.text
        assert result.json()["backup"]["backup_id"] == backup["backup_id"]
        safety_backup = result.json()["safety_backup"]
        assert safety_backup["row_count"] == 1
        assert _fingerprint(db) == initial

        result = restore(backup_id=safety_backup["backup_id"])
        assert result.status_code == 200, result.text
        assert _fingerprint(db) == after_reset

        result = reset()
        assert result.status_code == 200, result.text
        latest = result.json()["backup"]
        result = restore()
        assert result.status_code == 200, result.text
        assert result.json()["backup"]["backup_id"] == latest["backup_id"]
        assert _fingerprint(db) == after_reset

        latest_path = latest["gcs_uri"].removeprefix(f"gs://{bucket_name}/")
        bucket.blob(latest_path, generation=int(latest["generation"])).delete()
        failure = restore()
        assert failure.status_code == 503, failure.text
        assert _fingerprint(db) == after_reset

        # The existing dashboard submits this request and displays row_count.
        settings.demo_backup_confirmed = True
        settings.gcs_backup_bucket = bucket_name + "-missing"
        preview = api.post(LEGACY_PATH + "/preview-reset", headers=admin)
        assert preview.status_code == 200, preview.text
        legacy = api.post(LEGACY_PATH + "/reset", headers=admin, json={
            "dry_run_id": preview.json()["dry_run_id"], "confirmation": RESET_CONFIRMATION,
        })
        assert legacy.status_code == 200, legacy.text
        assert legacy.json()["status"] == "reset" and legacy.json()["row_count"] == 1
        assert "backup" not in legacy.json()

        print(f"Verified {backup['row_count']} rows across {len(RESET_TABLES)} reset tables, "
              f"snapshot SHA-256, latest selection, safety restore, storage failures "
              f"and dashboard compatibility")
