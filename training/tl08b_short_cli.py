"""Bounded one-off training for exact-observation short horizons."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

import httpx

from packages.gcs.client import RealGCSClient
from packages.ml.artifacts import ModelStore
from packages.ml.gateway import HttpMLGateway
from packages.ml.worker import WorkerSettings
from training.tl08b_short_horizons import SHORT_HORIZONS, train_short


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--horizon", choices=tuple(SHORT_HORIZONS))
    parser.add_argument("--start-utc", type=datetime.fromisoformat, required=True)
    parser.add_argument("--end-utc", type=datetime.fromisoformat, required=True)
    args = parser.parse_args()
    if args.start_utc.tzinfo is None or args.end_utc.tzinfo is None:
        parser.error("UTC offsets required")
    settings = WorkerSettings()
    if not settings.gcs_models_bucket:
        parser.error("GCS_MODELS_BUCKET required")
    gateway = HttpMLGateway(settings.database_api_url, settings.internal_api_token)
    rows = gateway.get_observations(args.start_utc.astimezone(timezone.utc), args.end_utc.astimezone(timezone.utc))
    coverage = gateway._request("GET", "/internal/v1/datalake/coverage")
    source_versions = [{key: item[key] for key in ("layer", "partition_key", "content_sha256", "gcs_uri", "gcs_generation")}
                       for item in coverage["active_exports"] if item["state"] == "VERIFIED" and item["row_count"] > 0]
    store = ModelStore(RealGCSClient(), settings.gcs_models_bucket)
    try:
        horizons = (args.horizon,) if args.horizon else tuple(SHORT_HORIZONS)
        print(json.dumps([train_short(rows, store, gateway, horizon=horizon, version=args.version,
                                      source_versions=source_versions) for horizon in horizons], indent=2))
    except httpx.HTTPStatusError as exc:
        print(exc.response.text[:3000], file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
