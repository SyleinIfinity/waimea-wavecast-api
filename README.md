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

**Status:** TL01 Database API is implemented under `services/database_api/` with PostgreSQL v2 migrations, public/internal/admin routes and tests. Worker integration remains separate; consult `docs/contributions/TL01_STATUS.md`. `docs/implementation-roadmap.md` describes implementation order. CI/CD deployment examples are inactive until secured.

## Important
- `NOAA 51201` and `CDIP 106` represent the same buoy.
- Open-Meteo is model output, not observed buoy data.
- Use UTC internally and Hawaii local dates for daily features.
- Never expose DB port 5432 publicly or commit `.env.production`.

## TL01 Database API

Run from the repository root:

```bash
python -m uvicorn services.database_api.app.main:app --reload
```

Swagger UI is at `http://127.0.0.1:8000/docs`. The root `.env` is loaded
automatically. See `services/database_api/README.md` and `database/README.md`.

## TL05 analytics and ML

The analysis, clustering, prediction and classification workers expose authenticated
`/internal/v1/*` jobs and keep model training in the one-off CLI. Start a worker
from the repository root, for example:

```powershell
.\.venv\Scripts\python.exe -m uvicorn services.analysis.app.main:app --port 8001
.\.venv\Scripts\python.exe -m pytest -q tests/tl05
```

Workers read the root `.env`; set `INTERNAL_API_TOKEN`, `DATABASE_API_URL`, and
`GCS_MODELS_BUCKET` before using real storage. Train from existing daily Silver
features with `python -m training.cli --version VERSION --start-hst YYYY-MM-DD
--end-hst YYYY-MM-DD`. Training creates draft models only; TL01 promotion requires
reviewed evaluation and a verified artifact. `python -m services.scheduler.app.runner`
runs the single-replica scheduler. In Compose it is opt-in: `docker compose --profile tl05-scheduler up -d scheduler`. Fixtures and local artifacts are for tests,
never NOAA benchmark results.
