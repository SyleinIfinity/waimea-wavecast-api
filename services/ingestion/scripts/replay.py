"""Replay one pinned NOAA raw object after a partial database failure.

Run only after reviewing the object URI and target database environment.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict

from packages.db.http import HttpDatabaseGateway
from packages.gcs.client import RealGCSClient
from services.ingestion.app.main import IngestionSettings
from services.ingestion.app.services.ingest_pipeline import replay_stored_object


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feed", required=True, choices=("realtime", "historical_monthly", "historical_annual"))
    parser.add_argument("--year", required=True, type=int)
    parser.add_argument("--month", type=int)
    parser.add_argument("--object-path", required=True)
    parser.add_argument("--generation", required=True, type=int)
    args = parser.parse_args()
    settings = IngestionSettings()
    if settings.ingestion_adapter_mode != "real":
        parser.error("INGESTION_ADAPTER_MODE=real is required")
    if args.feed == "historical_monthly" and not args.month:
        parser.error("--month is required for historical_monthly")
    gateway = HttpDatabaseGateway(settings.database_api_url, settings.internal_api_token)
    try:
        result = replay_stored_object(args.feed, args.year, month=args.month,
                                      object_path=args.object_path, generation=args.generation,
                                      gcs_client=RealGCSClient(), db_gateway=gateway,
                                      bucket=settings.gcs_raw_bucket)
    finally:
        gateway.close()
    print({key: value for key, value in asdict(result).items() if key != "error"})
    if not result.success:
        raise SystemExit(f"replay failed: {result.error}")


if __name__ == "__main__":
    main()
