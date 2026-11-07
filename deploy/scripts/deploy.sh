#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
if [[ ! -f .env.production ]]; then echo 'Missing .env.production' >&2; exit 1; fi
# Compose interpolation reads .env.production, services also receive env_file via overlay.
docker compose --env-file .env.production -f compose.yaml -f deploy/compose.prod.yaml up -d --build
docker compose --env-file .env.production -f compose.yaml -f deploy/compose.prod.yaml ps
