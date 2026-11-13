#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
if [[ ! -f .env.production ]]; then echo 'Missing .env.production' >&2; exit 1; fi
# Compose interpolation reads .env.production, services also receive env_file via overlay.
docker compose --env-file .env.production -f compose.yaml -f deploy/compose.prod.yaml config --quiet
docker compose --env-file .env.production -f compose.yaml -f deploy/compose.prod.yaml --profile historical build database-api ingestion preprocessing pipeline-scheduler historical-backfill
docker compose --env-file .env.production -f compose.yaml -f deploy/compose.prod.yaml up -d --no-deps --no-build database-api ingestion preprocessing pipeline-scheduler
docker compose --env-file .env.production -f compose.yaml -f deploy/compose.prod.yaml ps
bash deploy/scripts/health-check.sh
