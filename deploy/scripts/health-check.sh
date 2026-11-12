#!/usr/bin/env bash
set -euo pipefail
for attempt in {1..30}; do
  if curl --fail --silent --show-error --max-time 5 http://127.0.0.1:8000/health/ready 2>/dev/null; then
    echo
    exit 0
  fi
  sleep 2
done
echo 'Database API /health/ready did not become ready' >&2
exit 1
