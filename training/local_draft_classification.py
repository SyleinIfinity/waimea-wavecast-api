"""Run a pinned DRAFT classification against a local Database API."""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse

from packages.gcs.client import RealGCSClient
from packages.ml.artifacts import ModelStore
from packages.ml.gateway import HttpMLGateway
from packages.ml.jobs import run_inference
from packages.ml.worker import WorkerSettings
from services.database_api.app.config import get_settings


class DownloadedModel:
    """Read an artifact and manifest already downloaded by generation from GCS."""

    def __init__(self, directory: Path):
        self.directory = directory

    def download(self, bucket: str, object_path: str, generation: int | None = None) -> bytes:
        return (self.directory / Path(object_path).name).read_bytes()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-id", type=int, required=True)
    parser.add_argument("--target-hst", type=date.fromisoformat, required=True)
    parser.add_argument("--artifact-dir", type=Path)
    args = parser.parse_args()

    settings = get_settings()
    worker = WorkerSettings()
    api = urlparse(worker.database_api_url)
    if (settings.app_env != "development" or not settings.local_draft_classification_enabled
        or api.scheme != "http" or api.hostname not in {"127.0.0.1", "localhost", "::1"}):
        raise SystemExit("A loopback Database API and local DRAFT opt-in are required")

    gateway = HttpMLGateway(worker.database_api_url, worker.internal_api_token)
    metadata = gateway._request("GET", f"/internal/v1/models/{args.model_id}")
    client = DownloadedModel(args.artifact_dir) if args.artifact_dir else RealGCSClient()
    result = run_inference(
        gateway, ModelStore(client, worker.gcs_models_bucket), "classifications",
        args.target_hst - timedelta(days=1), f"local-draft-classification:{args.model_id}:{args.target_hst}",
        local_draft_model=metadata,
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
