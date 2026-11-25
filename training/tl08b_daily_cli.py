"""One-off bounded training of longer descriptive forecast horizons."""
from __future__ import annotations

import argparse
import json
from datetime import date

from packages.gcs.client import RealGCSClient
from packages.ml.artifacts import ModelStore
from packages.ml.gateway import HttpMLGateway
from packages.ml.worker import WorkerSettings
from training.tl08b_daily_horizons import train_horizon


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--start-hst", type=date.fromisoformat, required=True)
    parser.add_argument("--end-hst", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    settings = WorkerSettings()
    if not settings.gcs_models_bucket:
        parser.error("GCS_MODELS_BUCKET required")
    gateway = HttpMLGateway(settings.database_api_url, settings.internal_api_token)
    rows = gateway.get_daily_features(args.start_hst, args.end_hst)
    coverage = gateway._request("GET", "/internal/v1/datalake/coverage")
    source_versions = [{key: item[key] for key in ("layer", "partition_key", "content_sha256", "gcs_uri", "gcs_generation")}
                       for item in coverage["active_exports"] if item["state"] == "VERIFIED" and item["row_count"] > 0]
    if not source_versions:
        raise RuntimeError("no verified source dataset versions")
    store = ModelStore(RealGCSClient(), settings.gcs_models_bucket)
    print(json.dumps([train_horizon(rows, store, gateway, days=days, version=args.version,
                                    source_versions=source_versions) for days in (7, 30)], sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
