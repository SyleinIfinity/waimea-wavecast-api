"""Run bounded retrospective fold audit against real daily NOAA data."""
from __future__ import annotations

import argparse
import json
from datetime import date

from packages.ml.gateway import HttpMLGateway
from packages.ml.worker import WorkerSettings
from training.tl08b_daily_horizons import samples_for_horizon
from training.tl08b_walkforward import evaluate_folds


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-hst", type=date.fromisoformat, required=True)
    parser.add_argument("--end-hst", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    settings = WorkerSettings()
    gateway = HttpMLGateway(settings.database_api_url, settings.internal_api_token)
    rows = gateway.get_daily_features(args.start_hst, args.end_hst)
    results = {f"{days}d": evaluate_folds(samples_for_horizon(rows, days), horizon_days=days)
               for days in (7, 30)}
    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
