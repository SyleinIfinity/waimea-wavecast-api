"""Bounded daily source check followed by durable year/coverage publication."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys

from packages.db.http import HttpDatabaseGateway
from packages.gcs.client import RealGCSClient
from services.ingestion.scripts.export_coverage_report import build_report
from services.ingestion.scripts.publish_history_manifest import publish_year


def publish_pending(gateway, gcs) -> list[dict]:
    pending = gateway._request("GET", "/internal/v1/datalake/publications/pending",
                               params={"limit": 2})["data"]
    if not pending:
        return []
    manifests = []
    for row in pending:
        manifest = publish_year(gateway, gcs, int(row["year"]), repair=True)
        manifests.append((row, manifest))
    report = build_report(gateway, gcs)
    payload = (json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n").encode()
    sha = hashlib.sha256(payload).hexdigest()
    path = f"manifests/station=51201/coverage/{sha}.json"
    info = gcs.upload_if_absent("waimea-noaa", path, payload,
                                content_type="application/json",
                                metadata={"content_sha256": sha, "schema_version": "tl08a-coverage-v1"})
    if info.content_sha256 != sha or gcs.download("waimea-noaa", path, info.generation) != payload:
        raise RuntimeError("coverage GCS round-trip failed")
    results = []
    for row, manifest in manifests:
        body = {"pending_updated_at": row["updated_at"],
                "manifest_sha256": manifest["manifest_sha256"],
                "manifest_gcs_uri": manifest["gcs_uri"],
                "manifest_generation": manifest["generation"],
                "coverage_sha256": sha,
                "coverage_gcs_uri": info.gcs_uri,
                "coverage_generation": info.generation}
        gateway._request("POST", f"/internal/v1/datalake/publications/{row['year']}/complete", body=body)
        results.append({"year": row["year"], "status": "PUBLISHED",
                        "manifest_sha256": manifest["manifest_sha256"],
                        "coverage_sha256": sha})
    return results


def main() -> None:
    # Keep the one-source resource limit and all existing CLI behavior.
    subprocess.run([sys.executable, "-m", "services.ingestion.scripts.historical_backfill"], check=True)
    gateway, gcs = HttpDatabaseGateway(), RealGCSClient()
    try:
        for result in publish_pending(gateway, gcs):
            print(json.dumps(result, sort_keys=True), flush=True)
    finally:
        gateway.close()


if __name__ == "__main__":
    main()
