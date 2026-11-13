# TL08-C — Automated forecasting and continuous evaluation

Status: **BLOCKED BY MODEL READINESS** (2026-11-13 UTC). TL08-C requires at least one TL08-B model to pass its explicit quality gate. All models trained so far remain DRAFT/NOT_READY. Therefore no ML inference service was enabled, no forecast was published, and no live ground truth evaluation is claimed. The existing NOAA ingestion → Bronze → QC → Silver → Gold scheduler continues independently.

## Existing backend contracts

The existing TL05 worker has a guarded `run_inference` function. It requests only a READY model, loads a generation-pinned private GCS artifact with SHA-256 validation, checks feature order and sufficient seven-day Daily Features, then writes a next-day Gold prediction/classification/cluster assignment through Database API. It has no production schedule and does not yet write versioned GCS prediction artifacts. The public API already exposes `/api/v1/predictions/latest`, `/api/v1/predictions/history`, `/api/v1/classifications/latest`, `/api/v1/clusters`, `/api/v1/clusters/profiles`, `/api/v1/models`, and model evaluation endpoints. Their response shape is not yet a verified multi-horizon/live evaluation contract.

## Required implementation before enablement

1. Validate forward feature availability and define per-horizon readiness policy; complete QC and seasonal/period breakdowns, calibrated intervals, and short-horizon walk-forward evaluation. Promote only a model with documented READY evidence.
2. Add a separate bounded inference schedule after the Gold quality gate; issue forecasts with explicit horizon, target UTC period, feature cutoff, model/dataset versions and processing run ID. Refuse stale or incomplete features.
3. Write immutable, generation-pinned prediction objects to private GCS and idempotent Gold rows. Reconcile partial PostgreSQL/GCS writes without replacing an issued forecast.
4. Add an independent pending-ground-truth reconciler that uses only QC-valid NOAA observations, distinguishes backtests from forward evaluations, handles late/corrected data, and records per-horizon metrics, coverage and drift.
5. Test retry, crash recovery, model rollback, artifact mismatch, corrected ground truth and API consistency in isolation, then observe at least one complete real forecast → NOAA target → evaluation cycle in production. Until that observation: **NOT YET LIVE VERIFIED**.

No ML worker/container was enabled on the VM, and the historical reconciliation timer and Minecraft service were not changed for TL08-C. Frontend handoff: model registry and existing public endpoints are readable, but clients must show an empty/NOT_READY forecast state until a qualified production model exists. No frontend repository was modified.
