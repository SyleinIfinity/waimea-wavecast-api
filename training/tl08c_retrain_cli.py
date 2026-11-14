"""Weekly bounded retraining from verified forward evaluation examples."""
from __future__ import annotations

import json

from packages.gcs.client import RealGCSClient
from packages.ml.artifacts import ModelStore
from packages.ml.gateway import HttpMLGateway
from packages.ml.worker import WorkerSettings
from training.tl08c_retrain import train_forward


def main():
    settings = WorkerSettings()
    if settings.gcs_models_bucket != "waimea-noaa":
        raise RuntimeError("private model bucket required")
    gateway = HttpMLGateway(settings.database_api_url,settings.internal_api_token)
    store = ModelStore(RealGCSClient(),settings.gcs_models_bucket)
    outcomes = []
    for horizon in ("30min","1d","7d"):
        rows = []
        cursor = 0
        while True:
            result = gateway._request("GET","/internal/v1/forward/training-examples",params={
                "horizon":horizon,"after_id":cursor,"limit":1000})
            page = result["data"]
            rows.extend(page)
            if len(page)<1000:
                break
            cursor = result["next_after_id"]
        outcomes.append(train_forward(rows,horizon,gateway,store))
    print(json.dumps(outcomes,sort_keys=True))


if __name__ == "__main__":
    main()
