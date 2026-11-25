"""Pin TL08-B evaluation reports in private GCS and verify every upload."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from packages.gcs.client import RealGCSClient
from packages.ml.worker import WorkerSettings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    if not args.version or "/" in args.version or ".." in args.version:
        parser.error("invalid version")
    bucket = WorkerSettings().gcs_models_bucket
    if not bucket:
        parser.error("GCS_MODELS_BUCKET required")
    client = RealGCSClient()
    result = []
    for path in args.files:
        if path.suffix != ".json" or not path.is_file():
            parser.error("JSON evidence file required")
        data = path.read_bytes()
        json.loads(data)
        sha = hashlib.sha256(data).hexdigest()
        object_path = f"training/reports/tl08b/{args.version}/{path.name}"
        info = client.upload_if_absent(bucket, object_path, data, "application/json")
        if info.content_sha256 != sha or client.download(bucket, object_path, info.generation) != data:
            raise RuntimeError(f"GCS report verification failed: {path.name}")
        result.append({"name": path.name, "gcs_uri": info.gcs_uri,
                       "generation": str(info.generation), "sha256": sha})
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
