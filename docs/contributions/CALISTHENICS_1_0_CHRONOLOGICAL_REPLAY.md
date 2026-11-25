# Calisthenics 1.0 — chronological NOAA replay

Status: experimental retrospective model family, DRAFT/NOT_READY. This is
separate from production-issued forward forecasts and never writes to
`gold.forward_forecasts` or changes the active model IDs.

## Method

The family has distinct artifacts and registry entries for exact WVHT at an
observation anchor +90 minutes (a simulated 30-minute issued lead), next HST
day median, the HST day seven dates ahead, and the mean of at least 27 valid
daily medians in the next 30 HST days. `calisthenics 1.0 / {horizon}` is the
registry name and `v1.0` is the model version. Each artifact is stored and
round-trip verified in the private `waimea-noaa` bucket.

For daily horizons, 2005–2006 provides the initial training seed. Replay
issues forecasts from 2007-01-01 through 2025-12-31. For exact 30-minute
samples, 2005–2007 is hourly and cannot supply 30-minute lag windows; 2008
provides the seed, and replay begins in 2009. Missing or QC-invalid inputs
and targets do not become examples. Daily source availability is assumed at
02:00 HST after the anchor day, and 30-minute observations are assumed
available 60 minutes after their timestamp. Original historical publication
times are unavailable, so these are simulation assumptions, not measured
NOAA latency.

Each replay forecast is evaluated against its recorded QC NOAA target
immediately in wall-clock execution. The learner receives the target only
after its simulated target period has closed. It updates after each batch of
ten newly matured labels. The initial imputer and scaler are fitted only on
the seed period. The final artifact additionally learns the remaining labels
whose target period closed by the end of 2025. The GCS replay log records
simulated issue time, target time, prediction, observed value, error and the
number of examples learned before issue. The dataset split and evaluation
registry entries record this as `walk_forward` / `backtest`, not `live`.

The algorithm is an online `SGDRegressor` with fixed seed-only preprocessing.
It is a new experimental model family. Retrospective MAE versus persistence
is necessary evidence, but historical availability assumptions and no
independent live validation prevent automatic promotion. Calisthenics 1.5
uses the immutable 1.0 artifact as its seed and continues on 2026 data in a
separate job. See `CALISTHENICS_1_5_2026_CONTINUATION.md`.

## Verified production runs

| Horizon | Model ID | Seed | Replay outcomes | Model MAE (m) | Persistence MAE (m) | Decision |
|---|---:|---:|---:|---:|---:|---|
| 30min | 21 | 11,624 | 245,034 | 0.122 | 0.107 | DRAFT; worse than baseline |
| 1d | 18 | 437 | 4,285 | 0.517 | 0.322 | DRAFT; worse than baseline |
| 7d | 19 | 425 | 4,165 | 0.550 | 0.557 | DRAFT; marginal improvement |
| 30d | 20 | 320 | 3,554 | 0.474 | 0.403 | DRAFT; worse than baseline |

The 30-minute run made 24,414 incremental update calls and ended with 256,658
learned examples. Its replay log is
`gs://waimea-noaa/training/calisthenics/1.0/horizon=30min/replay-a2827105cb69e4a260b343ffc78980fc7fe99d0e55bc32802cb92b9c94aefe2a.jsonl.gz`
(generation `1794574291319333`, SHA-256
`a2827105cb69e4a260b343ffc78980fc7fe99d0e55bc32802cb92b9c94aefe2a`).
The above counts and metrics are retrospective
and must not be presented as production-issued forecast performance.

All four model artifacts and replay objects were downloaded independently by
pinned GCS generation and SHA-256. The first artifact serialization stored
`OnlineWaveModel` as `__main__`, which prevented fresh-process loading. The
four DRAFT registry rows were backed up to private file
`/home/mcadmin/.local/share/waimea/backups/calisthenics-v1-registry-pre-repair-20261113.json`
(mode 600), then repacked under the canonical
`training.calisthenics_v1.OnlineWaveModel` module and their pinned artifact
metadata updated. A fresh Compose container successfully loaded all four
models with `ModelStore.load`. Old GCS artifacts remain for audit. The CLI now
imports its canonical module before serialization. Repeated runs reject
changed artifact bytes under the same registry version; a new version is
required for changed source data or model code.

The first 30-minute run exposed an O(n²) R² aggregation: `regression()`
recomputed the actual-value mean inside its denominator loop. It was changed
to compute the mean once, then the 30-minute replay completed in about 80
seconds on the bounded 0.75-CPU Compose job. The full test suite passed with
189 tests and 2 skips after the serialization regression test was added.
No production forecast model ID was
changed.

Run one bounded Compose job per horizon:

```bash
sudo docker compose --env-file .env.production -f compose.yaml -f deploy/compose.prod.yaml \
  --profile calisthenics run --rm --no-deps calisthenics-replay \
  python -m training.calisthenics_v1 --horizon 1d
```

Change `1d` to `30min`, `7d`, or `30d`. Repeated runs use immutable GCS paths
and idempotent registry keys; if a changed replay/model has a different SHA,
the `v1.0` registry entry remains unchanged and the job fails explicitly.
The job is manual and does not run on VM reboot.
