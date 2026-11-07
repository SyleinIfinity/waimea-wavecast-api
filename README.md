# Waimea WaveCast — Backend

**FastAPI microservices on Compute Engine**, managed **Cloud SQL for PostgreSQL**, **Google Cloud Storage** for raw source payloads and ML artifacts. Public API: `https://apiwaimea.liems.io.vn`.

## Local dev
```bash
cp .env.example .env
# edit credentials and URL in .env
docker compose --profile devdb up -d --build
# initial SQL migration must be applied to the local DB before querying observations
```

## Production VM
```bash
cp .env.production.example .env.production
# fill secrets and values; chmod 600 .env.production
bash deploy/scripts/deploy.sh
```

**Infrastructure prerequisites:** Cloud SQL Private IP connectivity from VM, service account with Cloud SQL Client + limited GCS IAM, private storage buckets, Nginx reverse proxy with TLS, firewall TCP 80/443 and limited SSH access. `cloud-sql-proxy` uses private IP.

**Status:** This is a directory and configuration scaffold; only Database API `GET /health` and `GET /api/v1/observations/latest` are implemented. Other microservice processes expose health endpoints only and do not yet ingest, preprocess or train. `docs/implementation-roadmap.md` describes implementation order. CI/CD deployment examples are inactive until secured.

## Important
- `NOAA 51201` and `CDIP 106` represent the same buoy.
- Open-Meteo is model output, not observed buoy data.
- Use UTC internally and Hawaii local dates for daily features.
- Never expose DB port 5432 publicly or commit `.env.production`.
