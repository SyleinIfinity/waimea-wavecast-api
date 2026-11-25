# Calisthenics 1.5 — 2026 continuation and 20-pair updates

Status on 2026-11-13 UTC: four real Cloud SQL/GCS bootstrap runs completed.
All four models are DRAFT/NOT_READY. Production-issued forecast model IDs
remain 17 (30min), 8 (1d), and 11 (7d). Calisthenics 1.5 has **not** been
promoted or issued as the production forecast model.

## Method

`training/calisthenics_15.py` loads the pinned Calisthenics 1.0 artifact for
each horizon and continues it on QC-valid 2026 observations. The retrospective
replay scores each simulated prediction before learning its target. Target
values join training only after their simulated target time closes; updates
occur in batches of 20 matured labels. Historical monthly input publication
time is simulated. Realtime 30-minute inputs must have been first seen and
last updated before the simulated issue time. Logs and model artifacts are
written to the private `waimea-noaa` bucket, downloaded by generation, and
checked against SHA-256. Cloud SQL stores an immutable dataset split, model,
training run, and backtest evaluation for each version.

The 2026 Silver source includes 8,252 `historical_monthly` rows from January
through August and 2,216 `realtime` rows from late September through November
13 at the bootstrap cutoff. September 1–27 has a source gap. Realtime rows in
the bootstrap are still **retrospective** examples; their predictions were not
issued by Calisthenics 1.5 before the target occurred.

| Horizon | Model ID | 2026 scored outcomes | Model MAE (m) | Persistence MAE (m) | Status |
|---|---:|---:|---:|---:|---|
| 30min | 25 | 8,230 | 0.09967 | 0.10315 | DRAFT |
| 1d | 22 | 195 | 0.52834 | 0.29997 | DRAFT |
| 7d | 23 | 176 | 0.64604 | 0.50730 | DRAFT |
| 30d | 24 | 115 | 0.49123 | 0.34170 | DRAFT |

Within the 30-minute replay, 8,184 historical-monthly outcomes had model MAE
0.09952 m versus persistence 0.10294 m. The 46 realtime-source outcomes had
model MAE 0.12625 m versus persistence 0.14130 m. This subset is small and
retrospective. Daily horizons underperform persistence in this continuation;
none of these numbers establish independent forward skill.

## Forward-issued shadow forecasts

Migration `13_calisthenics_shadow.sql` adds a separate, append-only forecast
and evaluation ledger. `training/calisthenics_shadow.py` issues predictions
from the latest DRAFT Calisthenics 1.5 model **before the target**, pins the
input feature snapshot and model version, uploads immutable JSON to private
GCS, downloads it by generation and verifies its SHA-256. A five-minute
systemd timer retries unfinalized reservations and matches QC Silver/Gold
truth after the target. It never writes to approved Gold predictions or
`gold.forward_training_examples`.

The 30-minute target is the exact WVHT observation 90 minutes after the
anchor, with a 15–45 minute issued lead. `1d` and `7d` target the median of
the respective HST day. `30d` targets the **mean of at least 27 QC-valid
daily medians in the next 30 HST days**, matching the training label; it is
not the median of day 30. A missing or unready day remains missing. Daily
inputs require seven complete QC Gold-ready days and publication timestamps
before issue. All four horizons are experimental.

The first production container run at 2026-11-13 15:16 UTC issued and
round-trip verified shadow forecast IDs 1 (`30min`, target 15:56 UTC),
2 (`7d`, target 2026-11-19 HST), and 3 (`30d`, target 2026-12-12 HST).
`1d` was correctly skipped because its target day was already too far
underway. A second run and the first timer run returned `issued=0` for
those same model/anchor pairs, proving idempotence. The read-only public
`/api/v1/forecasts/shadow/latest` and `/metrics` endpoints returned 200;
at issuance all `actual_value` fields were null. Forecast 1 was then matched
automatically to QC truth 2.4 m at 2026-11-13 16:30:48 UTC; its frozen
prediction was 2.53936 m, absolute error 0.13936 m. The existing
experimental forecast API continued returning 200.
At 2026-11-14 00:41 UTC, the public shadow metrics endpoint reported 15
evaluated 30-minute forecasts from model 25: MAE 0.11110 m, RMSE 0.15370 m,
and persistence MAE 0.12000 m. Fifteen outcomes are far too few for
promotion; 1d/7d/30d forward scores remain pending their target periods.

## Forward 20-pair watcher

`waimea-calisthenics-15-live.timer` checks every ten minutes. It reads only
`gold.calisthenics_shadow_forecasts` joined to the latest append-only shadow
evaluation. It uses `evaluation_id` as its cursor so delayed truth for a
lower forecast ID is not skipped. It waits until **20 new** Calisthenics
30-minute pairs exist, records their immutable **issued predictions** against
truth, updates the learner, then creates a new DRAFT artifact and registry
entry. A correction to an already consumed target stops the update for a
controlled rebuild; a correction before consumption can be scored once from
the latest evaluation. The score belongs to the model version that issued
the forecast, not automatically to the newly trained child model. The
watcher never changes active production forecast IDs.

After the switch to this independent ledger, the real watcher returned
`new_pairs=0`, `required=20` initially. By 2026-11-14 00:33 UTC it had
15/20 QC-matched shadow pairs; no 20-pair update had occurred yet.
The threshold and frozen-prediction handling passed isolated tests. Prior
forward pairs from model 17 are no longer counted for Calisthenics 1.5.

## Reproduce and operate

Build the bounded Compose worker from the current checkout, then run a
bootstrap only when a new immutable version is intended:

```bash
sudo docker compose --env-file .env.production -f compose.yaml -f deploy/compose.prod.yaml \
  --profile calisthenics build calisthenics-replay
sudo docker compose --env-file .env.production -f compose.yaml -f deploy/compose.prod.yaml \
  --profile calisthenics run --rm --no-deps calisthenics-replay \
  python -m training.calisthenics_15 bootstrap --horizon 30min
```

The bootstrap versions are content-addressed; rerunning after source changes
must create a distinct version or fail on an immutable version conflict.
The watcher is run by systemd and survives reboot:

```bash
sudo cp deploy/systemd/waimea-calisthenics-15-live.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now waimea-calisthenics-15-live.timer
sudo cp deploy/systemd/waimea-calisthenics-15-shadow.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now waimea-calisthenics-15-shadow.timer
systemctl list-timers 'waimea-calisthenics-15-live.timer'
journalctl -u waimea-calisthenics-15-shadow.service -n 30 --no-pager
journalctl -u waimea-calisthenics-15-live.service -n 30 --no-pager
```

The legacy 1.5 dataset-split train counts were repaired to seed-only values
after a private, mode-600 backup at
`/home/mcadmin/.local/share/waimea/backups/calisthenics-15-splits-pre-metadata-fix-20261113.json`.
No measurement schema, GCS IAM policy, or production forecast pointer changed.
The shadow ledger was added after a verified PostgreSQL 17 custom-format
backup at
`/home/mcadmin/.local/share/waimea/backups/pre-calisthenics-shadow-20261113.dump`
(mode 600). Remaining work is natural forward truth accumulation, direct
quality comparison against persistence, the first 20-pair update, and a
separate decision on promotion. Realtime data gaps remain observed gaps.

The 104 MiB custom-format backup was restored with `pg_restore` into an
isolated PostgreSQL 17 container (`--network none`, 2 GiB memory and 0.5 CPU
limits). The restored database returned 314,922 Silver observations,
323,710 Gold resolution metrics, 33 application tables and 304 constraints;
the pre-migration forward ledger existed and the new shadow ledger was
correctly absent. The isolated container was stopped and removed. This
validates this dump's readability and a basic restore, not a full VM/Cloud
SQL disaster-recovery exercise.

## Missing-data and operational checks

The read-only `training.gap_holdout` run masked known QC-valid realtime
observations from 2026-09-28 through 2026-11-13 15:24 UTC (2,220 valid
rows). Every 41st eligible contiguous window was tested. The numbers below
are **research only**; delayed linear interpolation uses an observation
after the gap and cannot be used as a realtime input.

| Masked gap | Points scored | Causal persistence MAE (m) | Delayed interpolation MAE (m) |
|---|---:|---:|---:|
| 1 slot | 55 | 0.0891 | 0.0773 |
| 2 slots | 110 | 0.0945 | 0.0845 |
| 3 slots | 162 | 0.1025 | 0.0914 |
| 6 slots | 312 | 0.1083 | 0.0848 |

No Silver row was modified and auto-imputation remains disabled. The sample
selection and absence of a separate time holdout are insufficient to approve
automatic filling; missing source observations must retain their gap status.

The VM watchdog checks systemd timers, core service and recent job results,
Database API health, shadow API, and NOAA observation freshness every ten
minutes. At 2026-11-13 15:19 UTC its first production run returned
`status=ok`, no failures and source age 53.1 minutes. A failure returns a
nonzero exit and is visible in journald/systemd. Google Cloud Ops Agent is
active, but no external notification channel or Cloud Monitoring alert
policy was configured in this task. The CI workflow now includes `developer`
and compiles `packages` and `training`; the example production deploy
workflow remains inactive. A full container test suite passed with 198
tests and 2 skips after the shadow and gap-validation changes.
