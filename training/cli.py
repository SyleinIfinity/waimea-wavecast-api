"""Explicit one-off training; fixture mode never writes to real services."""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from packages.gcs.client import RealGCSClient
from packages.gcs.models.local import LocalGCSClient
from packages.ml.artifacts import ModelStore
from packages.ml.gateway import FakeMLGateway, HttpMLGateway
from packages.ml.worker import WorkerSettings
from training.run import train_suite


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--fixture-json", type=Path)
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--start-hst", type=date.fromisoformat)
    parser.add_argument("--end-hst", type=date.fromisoformat)
    args = parser.parse_args()
    if args.fixture_json:
        if not args.artifact_dir:
            parser.error("--artifact-dir is required with --fixture-json")
        rows = json.loads(args.fixture_json.read_text(encoding="utf-8"))
        gateway = FakeMLGateway(daily=rows)
        store = ModelStore(LocalGCSClient(args.artifact_dir), "fixture-models")
        synthetic = True
    else:
        if not args.start_hst or not args.end_hst:
            parser.error("--start-hst and --end-hst are required for live training")
        settings = WorkerSettings()
        if not settings.gcs_models_bucket:
            parser.error("GCS_MODELS_BUCKET is required")
        gateway = HttpMLGateway(settings.database_api_url, settings.internal_api_token)
        rows = gateway.get_daily_features(args.start_hst, args.end_hst)
        store = ModelStore(RealGCSClient(), settings.gcs_models_bucket)
        synthetic = False
    result = train_suite(rows, store, gateway, version=args.version, synthetic_fixture=synthetic)
    print(json.dumps({"dataset_split_ids": result["dataset_split_ids"], "synthetic_fixture": synthetic,
                      "models": {task: {"model_id": value["model_id"], "metrics": value["metrics"]}
                                 for task, value in result["models"].items()}}, indent=2))


if __name__ == "__main__":
    main()
