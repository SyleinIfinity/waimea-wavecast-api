#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
compose=(docker compose --env-file .env.production -f compose.yaml -f deploy/compose.prod.yaml)
for attempt in {1..30}; do
  if curl --fail --silent --show-error --max-time 5 http://127.0.0.1:8000/health/ready >/dev/null 2>&1; then
    break
  fi
  if [[ "$attempt" -eq 30 ]]; then
    echo 'Database API /health/ready did not become ready' >&2
    exit 1
  fi
  sleep 2
done
for attempt in {1..30}; do
  if "${compose[@]}" exec -T ingestion python -c \
      'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/health/ready", timeout=5)' \
      >/dev/null 2>&1; then
    echo 'Database API and Ingestion ready'
    exit 0
  fi
  sleep 2
done
echo 'Ingestion /health/ready did not become ready' >&2
exit 1
