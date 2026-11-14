#!/usr/bin/env python3
"""Read-only VM watchdog; nonzero exit makes a failed systemd unit visible."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from urllib.request import urlopen

BASE="http://127.0.0.1:8000"
TIMERS=("waimea-history-reconcile.timer","waimea-forward-forecast.timer",
        "waimea-forward-retrain.timer","waimea-calisthenics-15-live.timer",
        "waimea-calisthenics-15-shadow.timer")
SERVICES=("waimea-wavecast-api.service","waimea-forward-forecast.service",
          "waimea-history-reconcile.service","waimea-calisthenics-15-live.service",
          "waimea-calisthenics-15-shadow.service")
MAX_SOURCE_AGE_MINUTES=120


def age_minutes(stamp: str, now: datetime) -> float:
    parsed=datetime.fromisoformat(stamp.replace("Z","+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("observation timestamp lacks timezone")
    return (now-parsed.astimezone(timezone.utc)).total_seconds()/60


def _systemctl(*args: str) -> str:
    result=subprocess.run(["systemctl",*args],capture_output=True,text=True,timeout=8,check=False)
    return result.stdout.strip()


def _get(path: str) -> dict:
    with urlopen(BASE+path,timeout=8) as response:
        if response.status != 200:
            raise RuntimeError(f"{path} HTTP {response.status}")
        return json.load(response)


def check(now:datetime|None=None) -> dict:
    now=now or datetime.now(timezone.utc)
    failures=[]
    for unit in TIMERS:
        if _systemctl("is-active",unit)!="active" or _systemctl("is-enabled",unit)!="enabled":
            failures.append(f"timer_inactive:{unit}")
    if _systemctl("is-active",SERVICES[0])!="active":
        failures.append(f"service_inactive:{SERVICES[0]}")
    for unit in SERVICES[1:]:
        if _systemctl("show",unit,"--property=Result","--value") not in ("success", ""):
            failures.append(f"service_failed:{unit}")
    for path in ("/health/live","/health/ready","/api/v1/forecasts/shadow/latest?limit=1"):
        try:
            _get(path)
        except Exception as exc:
            failures.append(f"http_error:{path}:{type(exc).__name__}")
    source_age=None
    try:
        latest=_get("/api/v1/observations/latest")
        source_age=age_minutes(latest["data"]["observed_at"],now)
        if source_age < -10 or source_age > MAX_SOURCE_AGE_MINUTES:
            failures.append(f"source_age_minutes:{source_age:.1f}")
    except Exception as exc:
        failures.append(f"source_error:{type(exc).__name__}")
    return {"status":"ok" if not failures else "failed",
            "checked_at":now.isoformat(),"source_age_minutes":source_age,
            "failures":failures}


if __name__=="__main__":
    result=check()
    print(json.dumps(result,sort_keys=True))
    sys.exit(0 if result["status"]=="ok" else 1)
