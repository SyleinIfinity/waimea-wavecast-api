"""Bounded recovery of recent GCS raw snapshots missing DB registration/Raw rows."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from services.ingestion.app.services.ingest_pipeline import replay_stored_object


def reconcile_orphans(*, gcs_client, db_gateway, bucket: str,
                      now: datetime | None = None, max_objects: int = 100,
                      max_replays: int = 3) -> dict:
    now = now or datetime.now(timezone.utc)
    if bucket != "waimea-noaa" or not 1 <= max_objects <= 100 or not 1 <= max_replays <= 3:
        raise ValueError("invalid orphan scan budget")
    objects = []
    for day in (now.date()-timedelta(days=1), now.date()):
        prefix = f"noaa/raw/realtime/{day:%Y/%m/%d}/"
        objects.extend(gcs_client.list_raw_objects(bucket, prefix, max_results=max_objects))
    objects = sorted(objects, key=lambda item: item.object_path, reverse=True)[:max_objects]
    repaired = []
    scanned = 0
    for item in objects:
        scanned += 1
        state = db_gateway.source_file_by_gcs(item.gcs_uri, item.generation)
        incomplete = not state["found"] or (state["source_row_count"] is not None and
                                            state["raw_count"] < state["source_row_count"])
        if not incomplete:
            continue
        result = replay_stored_object(
            "realtime", int(item.object_path.split("/")[3]),
            object_path=item.object_path, generation=item.generation,
            gcs_client=gcs_client, db_gateway=db_gateway, bucket=bucket,
        )
        repaired.append({"success": result.success, "gcs_uri": item.gcs_uri,
                         "gcs_generation": item.generation,
                         "silver_inserted": result.silver_inserted,
                         "silver_revised": result.silver_revised,
                         "changed_from": result.changed_from.isoformat() if result.changed_from else None,
                         "changed_to": result.changed_to.isoformat() if result.changed_to else None})
        if len(repaired) >= max_replays:
            break
    return {"scanned": scanned, "listed": len(objects), "repaired": repaired,
            "truncated": len(objects) >= max_objects}
