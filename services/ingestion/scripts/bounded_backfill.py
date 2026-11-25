"""One verified NOAA annual file per invocation; plan by default.

Usage: python -m services.ingestion.scripts.bounded_backfill --year 2025
       python -m services.ingestion.scripts.bounded_backfill --year 2025 --execute

Reads a checked-in coverage manifest. `--execute` is deliberate and never
scheduled by TL07-B. Idempotent source/Raw/Silver keys support rerun; a failed
database step prints its pinned GCS URI/generation for recovery.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from packages.db.http import HttpDatabaseGateway
from packages.gcs.client import RealGCSClient
from packages.noaa.urls import annual_url
from services.ingestion.app.services.ingest_pipeline import run_ingest

MANIFEST = Path(__file__).resolve().parents[3] / "docs/contributions/TL07-B_HISTORY_COVERAGE.json"


def selected(year: int, *, max_rows: int, manifest: Path = MANIFEST) -> dict:
    if not 2004 <= year <= 2025 or not 1 <= max_rows <= 20000:
        raise ValueError("invalid year or row budget")
    data = json.loads(manifest.read_text())
    filename = f"51201h{year}.txt.gz"
    candidates = [item for item in data["files"] if item["filename"] == filename]
    if len(candidates) != 1:
        raise ValueError("annual file not verified in NOAA history manifest")
    item = candidates[0]
    if item.get("url") != annual_url(year) or item.get("parse_errors") != 0:
        raise ValueError("archive URL or parser evidence is not verified")
    if not item.get("rows") or item["rows"] > max_rows or item["nonmissing_by_variable"].get("WVHT", 0) == 0:
        raise ValueError("file exceeds row budget or has no usable wave height")
    return item


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--max-rows", type=int, default=18000)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    item = selected(args.year, max_rows=args.max_rows)
    if not args.execute:
        print(json.dumps({"mode": "PLAN_ONLY", "year": args.year, "rows": item["rows"],
                          "wave_rows": item["nonmissing_by_variable"]["WVHT"],
                          "source_url": item["url"], "max_rows": args.max_rows}))
        return
    result = run_ingest("historical_annual", args.year,
                        gcs_client=RealGCSClient(), db_gateway=HttpDatabaseGateway(),
                        bucket="waimea-noaa")
    print(json.dumps({"mode": "EXECUTED", "year": args.year, "success": result.success,
                      "raw_inserted": result.raw_inserted,
                      "silver_inserted": result.silver_inserted,
                      "silver_revised": result.silver_revised,
                      "gcs_uri": result.gcs_uri, "gcs_generation": result.gcs_generation,
                      "error_class": result.error.split(":", 1)[0] if result.error else None}))
    if not result.success:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
