"""Single-replica scheduler loop. Use external singleton deployment only."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from services.scheduler.app.main import DispatchRequest, dispatch, due_phases, settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
HST = ZoneInfo("Pacific/Honolulu")


def run_once(now: datetime) -> list[dict]:
    local = now.astimezone(HST)
    results = []
    for phase in due_phases(now):
        day = local.date()
        target = day - timedelta(days=1) if phase == "analysis" else day
        start = target.replace(day=1) if phase == "analysis" else target
        key = f"schedule:{phase}:{local.strftime('%Y%m%d%H%M')}"
        try:
            results.append(dispatch(DispatchRequest(phase=phase, from_hst=start, to_hst=target),
                                    x_internal_token=settings.internal_api_token, idempotency_key=key))
        except Exception:
            logger.exception("scheduled phase failed: %s", phase)
    return results


def main():
    if not settings.internal_api_token:
        raise RuntimeError("INTERNAL_API_TOKEN is required")
    while True:
        now = datetime.now(timezone.utc)
        local = now.astimezone(HST)
        next_local = local.replace(minute=30 if local.minute < 30 else 0, second=0, microsecond=0)
        if local.minute >= 30:
            next_local += timedelta(hours=1)
        time.sleep(max(0, (next_local - local).total_seconds()))
        try:
            run_once(datetime.now(timezone.utc).replace(second=0, microsecond=0))
        except Exception:
            logger.exception("scheduled TL05 dispatch failed")


if __name__ == "__main__":
    main()
