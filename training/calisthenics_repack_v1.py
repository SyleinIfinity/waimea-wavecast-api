"""One-time bounded repair of four draft Calisthenics v1.0 joblib artifacts.

The original one-off ran with `python -m` and pickled OnlineWaveModel under
`__main__`. Repack through the canonical module path and update only the four
known draft registry rows after GCS round-trip checks. Retain old GCS objects.
"""
from __future__ import annotations

import json
import sys

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from packages.gcs.client import RealGCSClient
from packages.ml.artifacts import ModelStore
from packages.ml.worker import WorkerSettings
from services.database_api.app.config import get_settings
from training.calisthenics_v1 import OnlineWaveModel


def main():
    settings = get_settings()
    worker = WorkerSettings()
    if worker.gcs_models_bucket != "waimea-noaa":
        raise RuntimeError("private model bucket required")
    store = ModelStore(RealGCSClient(),worker.gcs_models_bucket)
    # Load the legacy __main__ class only in this controlled repair process.
    # New artifacts use training.calisthenics_v1.OnlineWaveModel.
    sys.modules["__main__"].OnlineWaveModel = OnlineWaveModel
    results = []
    with psycopg.connect(host=settings.db_host,port=settings.db_port,dbname=settings.db_name,
                         user=settings.db_user,password=settings.db_password,
                         sslmode=settings.db_sslmode,row_factory=dict_row) as conn:
        for model_id in (18,19,20,21):
            row = conn.execute("SELECT * FROM gold.model_registry WHERE model_id=%s",(model_id,)).fetchone()
            if (row is None or row["status"] != "draft" or row["model_version"] != "v1.0"
                or row["model_name"] != f"calisthenics 1.0 / {row['hyperparameters']['horizon']}"):
                raise RuntimeError(f"unexpected registry row {model_id}")
            if "v1.0-repack-" in row["artifact_gcs_uri"]:
                results.append({"model_id":model_id,"status":"already_repacked"})
                continue
            old_sha = row["artifact_sha256"]
            model,manifest = store.load(row)
            if type(model) is not OnlineWaveModel:
                raise RuntimeError("unexpected legacy artifact class")
            horizon = row["hyperparameters"]["horizon"]
            artifact = store.save(model,task=f"calisthenics-1.0-{horizon}",
                version=f"v1.0-repack-{old_sha[:12]}",
                manifest={k:v for k,v in manifest.items() if k != "artifact_sha256"})
            params = {**row["hyperparameters"],
                      "manifest_sha256":artifact["manifest_sha256"],
                      "manifest_generation":artifact["manifest_generation"],
                      "serialization_repacked_from_sha256":old_sha}
            updated = conn.execute("UPDATE gold.model_registry SET artifact_gcs_uri=%s,"
                "artifact_generation=%s,artifact_sha256=%s,artifact_size_bytes=%s,"
                "hyperparameters=%s,updated_at=clock_timestamp() "
                "WHERE model_id=%s AND artifact_sha256=%s AND status='draft' RETURNING model_id",
                (artifact["artifact_gcs_uri"],artifact["artifact_generation"],
                 artifact["artifact_sha256"],artifact["artifact_size_bytes"],Jsonb(params),
                 model_id,old_sha)).fetchone()
            if updated is None:
                raise RuntimeError(f"concurrent registry change {model_id}")
            conn.commit()
            results.append({"model_id":model_id,"status":"repacked",
                            "artifact_sha256":artifact["artifact_sha256"]})
    print(json.dumps(results,sort_keys=True))


if __name__ == "__main__":
    main()
