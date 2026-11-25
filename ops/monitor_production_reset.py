"""Read-only production reset observer. Never sends a delete request."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT = Path('/home/mcadmin/waimea-reset-monitor')
END = datetime(2026, 11, 19, 1, 0, tzinfo=timezone.utc)  # 08:00 Vietnam time
API = 'https://apiwaimea.liems.io.vn'
SQL = """SELECT
 (SELECT count(*) FROM bronze.raw_observations),
 (SELECT count(*) FROM silver.wave_observations),
 (SELECT count(*) FROM gold.calisthenics_shadow_forecasts),
 (SELECT count(*) FROM gold.classifications),
 (SELECT count(*) FROM silver.daily_wave_features),
 (SELECT count(*) FROM gold.resolution_metrics),
 (SELECT max(observed_at) FROM silver.wave_observations),
 (SELECT max(issued_at) FROM gold.calisthenics_shadow_forecasts)
"""


def database_environment() -> dict[str, str]:
    values = {}
    for line in (ROOT / '.env.production').read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, raw = line.split('=', 1)
            if key in {'DB_HOST', 'DB_PORT', 'DB_NAME', 'DB_USER', 'DB_PASSWORD', 'DB_SSLMODE'}:
                values[key] = shlex.split(raw)[0] if raw else ''
    env = os.environ.copy()
    for source, target in [('DB_HOST', 'PGHOST'), ('DB_PORT', 'PGPORT'), ('DB_NAME', 'PGDATABASE'),
                           ('DB_USER', 'PGUSER'), ('DB_PASSWORD', 'PGPASSWORD'), ('DB_SSLMODE', 'PGSSLMODE')]:
        env[target] = values[source]
    return env


def get_json(path: str) -> dict:
    try:
        with urllib.request.urlopen(API + path, timeout=15) as response:
            return {'http_status': response.status, 'body': json.load(response)}
    except urllib.error.HTTPError as exc:
        return {'http_status': exc.code, 'error': 'HTTP error'}
    except Exception as exc:
        return {'error': type(exc).__name__}


def database_snapshot() -> dict:
    try:
        result = subprocess.run(['psql', '-X', '-A', '-t', '-F', '|', '-c', SQL],
                                env=database_environment(), capture_output=True, text=True,
                                timeout=45, check=True)
        fields = result.stdout.strip().split('|')
        return dict(zip(('bronze_rows', 'silver_rows', 'shadow_forecast_rows',
                         'classification_rows', 'daily_feature_rows', 'gold_resolution_rows',
                         'latest_silver_utc', 'latest_shadow_issued_utc'),
                        [int(x) if i < 6 else x or None for i, x in enumerate(fields)]))
    except Exception as exc:
        return {'error': type(exc).__name__}


def container_snapshot() -> dict:
    try:
        result = subprocess.run(['sudo', '-n', 'docker', 'ps', '--format', '{{.Names}}|{{.Status}}'],
                                capture_output=True, text=True, timeout=10, check=True)
        return dict(line.split('|', 1) for line in result.stdout.splitlines()
                    if line.startswith('waimea-wavecast-backend-') and '|' in line)
    except Exception as exc:
        return {'error': type(exc).__name__}


def main() -> None:
    REPORT.mkdir(mode=0o700, parents=True, exist_ok=True)
    log = REPORT / 'monitor.jsonl'
    events = REPORT / 'events.jsonl'
    alerts = REPORT / 'alerts.jsonl'
    sample = 0
    previous_database: dict | None = None
    while datetime.now(timezone.utc) < END:
        now = datetime.now(timezone.utc)
        row = {'checked_at_utc': now.isoformat(), 'health_ready': get_json('/health/ready'),
               'latest_observation': get_json('/api/v1/observations/latest')}
        if sample % 6 == 0:
            row['database'] = database_snapshot()
            row['containers'] = container_snapshot()
        elif previous_database:
            row['database'] = previous_database
        database = row.get('database', {})
        if database and previous_database and 'error' not in database and 'error' not in previous_database:
            dropped = {key: {'before': previous_database[key], 'after': database[key]}
                       for key in ('bronze_rows', 'silver_rows', 'shadow_forecast_rows', 'classification_rows')
                       if database[key] < previous_database[key]}
            if dropped:
                with events.open('a') as stream:
                    stream.write(json.dumps({'event': 'row_count_decreased', 'at_utc': now.isoformat(),
                                             'tables': dropped}) + '\n')
        if database and 'error' not in database:
            previous_database = database
        problems = []
        if row['health_ready'].get('http_status') != 200:
            problems.append('health_ready_failed')
        if row['latest_observation'].get('http_status') != 200:
            problems.append('latest_observation_failed')
        if database.get('error'):
            problems.append('database_snapshot_failed')
        if row.get('containers', {}).get('error'):
            problems.append('container_snapshot_failed')
        for name, status in row.get('containers', {}).items():
            if name != 'error' and not status.startswith('Up'):
                problems.append('container_not_running:' + name)
        if problems:
            alert = {'at_utc': now.isoformat(), 'problems': problems}
            with alerts.open('a') as stream:
                stream.write(json.dumps(alert) + '\n')
            print(json.dumps(alert), flush=True)
        with log.open('a') as stream:
            stream.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')
        temporary = REPORT / 'latest.json.tmp'
        temporary.write_text(json.dumps(row, ensure_ascii=False, indent=2, default=str) + '\n')
        temporary.replace(REPORT / 'latest.json')
        sample += 1
        time.sleep(min(10, max(0, (END - datetime.now(timezone.utc)).total_seconds())))


if __name__ == '__main__':
    main()
