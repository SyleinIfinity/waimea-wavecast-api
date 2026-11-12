# TL07-B — Automated Production Data Pipeline

**Checked:** 2026-11-12 UTC. **Branch:** `feature/tl06-Ingestion` (the checkout was not on `developer`); the pre-existing TL07-A and CORS changes were preserved. During validation, an external commit `be6248c` (`tl07 - pA + pB`, authored by SyleinIfinity at 12:55:59 UTC) appeared in the shared checkout. This assistant did not create that commit or push. Subsequent report/API/backfill/test edits remain uncommitted. This report separates tested production behavior from unobserved or unfinished behavior.

## 1. Executive Summary

**VERIFIED_IN_PRODUCTION:** Real NOAA realtime data reached private GCS, Bronze, QC-approved Silver, HST Daily Features and six-resolution descriptive Gold through the Database API. A controlled poll added seven observations; a pinned GCS replay added zero duplicates. **ENABLED_IN_PRODUCTION:** The PostgreSQL-leased TL07-B scheduler and four required containers are running; the unit is enabled for reboot. Natural 00/30 primary cycles and a bounded recovery retry have now been observed. ML readiness, auto-imputation and bulk historical backfill remain **NOT READY**.

## 2. Initial Architecture Audit

Database API and Ingestion used Cloud SQL TLS, the private `waimea-noaa` bucket and VM ADC. The persistent Preprocessing entrypoint used `FakeDatabaseGateway`; the old TL05 scheduler would invoke prediction workers and was not running. The internal Silver list stopped at 1,000 rows. All initial 2,160 Silver rows were `unverified`; Daily and Gold had no production rows. TL07-A modules were present as uncommitted work. The checkout is `feature/tl06-Ingestion`, and the working tree remains uncommitted.

## 3. Changes Implemented

- Production Preprocessing now uses `HttpDatabaseGateway`, authenticated internal routes and readiness checks. QC checks numerical/domain validity, core wave data, timestamps and lineage, then updates status/issue flags only when the measurement hash still matches.
- Silver API supports keyset pagination. Daily and multi-resolution Gold include valid-only numerical aggregates and distinct excluded/missing counts.
- A durable cycle API and minute-loop scheduler coordinate primary polls, bounded retry, GCS replay, QC, gap reconciliation, Daily and incremental Gold. Ingestion serializes poll/replay calls, reports changed timestamps and stores the exact GCS generation for recovery.
- NOAA fetching handles conditional ETag/Last-Modified responses and `Retry-After`. Historical parser accepts early archive headers without `#` and numeric sentinel spellings such as `99.00`.

## 4. Database/API Contract Changes

Additive migrations: `04_tl07b_quality_gold.sql` adds Daily QC counts and `gold.resolution_metrics`; `05_tl07b_cycles.sql` adds durable leases, attempts, status, source watermark and replay metadata; `06_tl07b_latest_valid.sql` restricts the existing latest-observation view to `valid` rows while retaining its columns; `07_tl07b_derived_candidates.sql` adds a separate, research-only derived candidate table; `08_tl07b_gold_signals.sql` adds causal lag, correlation, autocorrelation and cyclical Gold columns. Migrations 04–07 were applied to production after isolated PostgreSQL 17 tests. Migration 08 was applied after a second private backup (`/home/mcadmin/backups/waimea/tl07b-before-cyclical-20261112.dump`, 1,653,617 bytes, `pg_restore --list` succeeded) and an isolated rerun test. New internal APIs cover Silver QC, gap reconciliation, derived candidate storage/reconciliation, Gold pagination, pipeline cycle claim/finish/status and a fail-closed TL08 readiness handoff. The readiness route was queried in production for a seven-day daily/24h interval and returned HTTP 200 with `ml_ready=false`, `INSUFFICIENT_DATA` and 332 valid Silver rows. `GET /internal/v1/silver/observations` now uses `after` for complete keyset pagination. Internal endpoints remain off public Nginx routing.

## 5. Production QC Results

Before QC: 2,160 rows, all `unverified`. A read-only preview found 2,160 physically valid rows with Raw and Source File links. A private `pg_dump` backup was created at `/home/mcadmin/backups/waimea/tl07b-before-qc-20261112-v3.dump` (548,975 bytes, mode 0600); `pg_restore --list` succeeded. The controlled QC pass updated 1,428 + 732 rows to `valid`; zero suspect/rejected, zero stale-hash/missing conflicts. Repeating QC returned 2,160 duplicates and zero updates. The row count remained 2,160 and SQL joins found zero missing Raw/Source File references. After the next real poll, 2,167/2,167 rows were `valid`. This is a rule-based QC outcome, not proof of perfect NOAA measurements.

## 6. Daily Features and Gold Results

Initial QC produced 46 HST Daily rows and 2,220 Gold rows. The first real poll increased these to 47 Daily and 2,228 Gold rows: 2,167 half-hour, 47 daily, 8 weekly, 3 monthly, 2 seasonal and 1 yearly. By 15:03 UTC the totals were 47 Daily and 2,233 Gold; the latter had 2,224 `GOLD_READY`, 4 `INSUFFICIENT_DATA`, 5 `WAITING_FOR_SOURCE`. Migration 08 backfilled all 2,233 Gold rows with Hawaii-local cyclical features; 2,206 have causal lag-one medians, 2,216 have rolling lag-one autocorrelation, and 61 have a computable within-period WVHT/DPD correlation. Undefined signals remain NULL. The 90% full-period threshold applies to descriptive Gold only. The 47 Daily rows contain 2,172 valid heights and 84 expected-but-not-observed slots, including partial boundary days. No model or TL08 forecast was marked ready.

## 7. Primary Polling Schedule

The new `pipeline-scheduler` computes UTC minute `00`/`30` slots, one primary cycle per slot. A PostgreSQL unique key and lease prevent duplicate active cycles. **VERIFIED_IN_PRODUCTION:** After the controlled 12:30 UTC run, natural primary cycles at 13:00, 13:30, 14:00, 14:30 and 15:00 UTC were persisted. The 15:00 cycle succeeded on its second attempt after receiving the 14:26 observation.

## 8. Fast/Fallback Retry Behavior

The first live scheduler observation exposed an over-eager expected-point calculation: it waited for the `:56` point at the immediately following `:00` slot, while ongoing arrivals showed detection delays of roughly 24–35 minutes. This caused 9–10 attempts in the 13:00–14:30 UTC cycles. The calculation was corrected to expect the previous half-hour point (`slot − 34 minutes`), so a slot only retries when the point that should already be available is absent. If that point is absent, attempts 2–6 are scheduled at roughly one-minute spacing; attempts 7–10 at slot+10/15/20/25 minutes. A completed cycle does not retry. The NOAA client makes at most one HTTP attempt per scheduler call with `NOAA_MAX_RETRIES=0`; 429 `Retry-After` can postpone a cycle across slots and blocks another primary while source backoff is active. A PostgreSQL 17 isolated test confirmed a 429 cycle persisted `RETRY_SCHEDULED` and a later slot returned `source_backoff`. The corrected 15:00 UTC production cycle succeeded in two attempts, compared with 9–10 under the old expectation. The 24-hour metrics include 42 poll attempts across six cycles, of which 36 were retries from the earlier over-eager policy; continue monitoring request volume.

## 9. Gap Detection and Watermarks

The cycle stores latest observed timestamp, last poll, first new-data detection and changed timestamp bounds. Seven-day cadence reconciliation uses the inferred 30-minute interval and persists candidate slots without asserting permanent source failure. A controlled historical pass stored 17 open candidate slots, matching the earlier TL07-A cadence review; two candidates were inside the latest seven days. Resolution on later source arrival is implemented through the gap API but has not been observed live. Publication delay is not inferred from the original 45-day TL06 bulk import.

## 10. Missing-Data/Imputation Status

`AUTO_IMPUTATION_ENABLED=false` in production and examples. TL07-A `DerivedPoint` and GCS writer remain separate from observed Bronze/Silver. The imputation and reconciliation helpers now require `quality_status=valid` observations. A separate `gold.derived_imputation_candidates` contract stores model version, uncertainty, quality score, provenance and pinned GCS generation; later valid Silver observations reconcile candidate state. Its authenticated API was tested in isolation, including idempotent insert; production has no candidate rows. No estimate was generated or mixed into Gold. A production-approved waiting period, held-out model validation and uncertainty limits are **NOT READY**.

## 11. Historical Data Coverage

A bounded read-only scan followed links actually listed on the [NDBC 51201 station history](https://www.ndbc.noaa.gov/station_history.php?station=51201): 22 annual files (2004–2025) and six 2026 monthly files (January, February, May–August). All 28 parsed after the early-header fix, with zero parse errors. They contain 312,702 rows and 312,686 nonmissing WVHT and DPD values; APD 284,587, MWD 284,598, WTMP 308,182. WSPD and TIDE have zero usable values under the current normalization. The [per-file coverage JSON](./TL07-B_HISTORY_COVERAGE.json) records variables, row counts, observed bounds and source URLs. Annual counts vary sharply (2004: 2,436; 2018: 6,037; 2025: 17,219); listed files do not establish continuous seasonal coverage. `bounded_backfill.py` plans one verified annual file per invocation and enforces an 18,000-row default cap. A plan-only 2025 run reported 17,219 rows and no database/GCS writes. Execution requires explicit `--execute`; no historical rows were backfilled into production.

## 12. Durable Jobs and Recovery

`gold.pipeline_cycles` persists slot, attempts, next retry, 10-minute lease token, latest observation, changed bounds, failure stage and pinned GCS URI/generation. Isolated PostgreSQL tests demonstrated exclusive claim, delayed retry, successful reclaim and 429 cross-slot backoff. A scheduler-only production container restart retained the 12:30 `SUCCESS` cycle. A production replay of the exact GCS generation returned 2,167 Raw duplicates, 2,167 Silver duplicates, zero inserts/revisions. A bounded startup/daily orphan scan now compares recent GCS snapshots with Source File registration and Raw row counts, replaying up to three incomplete objects and propagating new rows to QC/Gold. Its production startup run scanned seven GCS objects, all registered and Raw-complete; zero replays were necessary. The isolated test verified replay of a missing Source File by pinned generation. Automatic replay on a real downstream failure and long-outage recovery remain **NOT VERIFIED IN LIVE PRODUCTION**.

## 13. Incremental Reprocessing

Silver upsert reports changed timestamp bounds. The scheduler reviews their QC window, recomputes affected HST days and writes only intersecting Gold periods plus two later rolling dependencies. The annual source is read in memory to establish full-period denominators; all historical Gold rows are not rewritten on each change. The first live poll added seven half-hour Gold rows and one Daily row. An isolated test verifies selection of the affected half-hour and two rolling successors. Correction of an older production observation was not naturally observed.

## 14. Production Deployment Details

Compose now runs Database API, Ingestion, Preprocessing and `pipeline-scheduler`; no TL05 inference/training worker was enabled. Preprocessing is capped at 768 MiB/0.75 CPU, scheduler at 256 MiB/0.25 CPU. The installed `/etc/systemd/system/waimea-wavecast-api.service` matches the tracked unit, is enabled and was reloaded; it will start the same four services on reboot. The service was not restarted through systemd and the VM was not rebooted. No Minecraft service was touched. Existing Cloud SQL TLS and VM ADC configuration were retained.

## 15. Real PostgreSQL/GCS Evidence

The controlled production poll inserted seven new Silver rows (from 2,160 to 2,167) and recorded latest NOAA time `2026-11-12T11:56:00Z`. The latest row had `quality_status=valid`, a Raw ID, Source File ID, and `gcs_upload_status=verified`. `RealGCSClient.object_info` confirmed its private `gs://waimea-noaa/noaa/raw/realtime/...` object, checksum and pinned generation `1794486702697921`. The subsequent pinned replay proved duplicate behavior without another NOAA request. No GCS object or production observation was deleted.

## 16. Automated Tests

Full repository suite: **156 passed, 2 skipped**, one Starlette deprecation warning, after the QC/normalization changes. Isolated PostgreSQL 17 migrations 04–07 applied twice without error; migration 08 was also applied twice in isolation. Isolated cycle API tests covered exclusive claim, `WAITING_FOR_SOURCE`, successful retry and HTTP 429 backoff; derived candidate insert/idempotency/reconciliation was also exercised. An isolated HTTP transport test verified that a failed preprocessing cycle replays its pinned GCS generation and resumes QC, derived reconciliation and Gold without another NOAA poll. A separate isolated test verified bounded orphan replay by generation. The bounded archive plan was tested to reject years outside the verified annual range and files above its row budget. The production Compose configuration passed validation and all four service checks passed.

## 17. Live Production Observations

Database `/health/live` and `/health/ready`, public `/health/ready`, and public `/api/v1/observations/latest` returned HTTP 200; public `/internal/v1/pipeline/status` returned 404. The scheduler naturally ran 13:00, 13:30, 14:00, 14:30 and 15:00 UTC slots. It detected new source points from 12:26 to 14:26 UTC with first-detection delays varying by point. The 13:00 and 14:00 slots eventually succeeded with two new rows each; 13:30 and 14:30 ended `NO_NEW_DATA`; corrected-policy 15:00 succeeded on attempt 2 with one new row. At 15:03 UTC production contained 2,172/2,172 QC-valid Silver rows, 47 Daily rows, 2,233 Gold rows (2,224 descriptive ready, four insufficient, five waiting), 17 open gap candidates and zero derived candidates. The internal TL08 readiness route returned `ml_ready=false` for daily/24h despite 332 recent valid rows.

## 18. Known Limitations

The first production poll caught up seven observations; its detection timestamps cannot calibrate NOAA publication latency. The subsequent first-detection delays are measured from polling and only bound actual publication time. Request ETag/Last-Modified caching is in process memory, not persisted across restarts. Automatic correction reconciliation outside the realtime window is not scheduled. The job table tracks cycles rather than a separate durable row for every QC/Gold dependency; leases are 10 minutes and have no heartbeat. The orphan scan covers only current/previous UTC days and at most 100 listed objects per run; `truncated=true` requires operator review. It detects missing registration and Raw-incomplete files, but not a Silver-partial write after Raw is complete. Correlation, autocorrelation and cyclical descriptors are now persisted where source sample size supports them; cross-season climatology still needs validated historical backfill. Gold readiness is descriptive; horizon-specific policy, freshness, uncertainty and model training remain gated. No VM reboot, failover or automatic pinned-replay failure occurred during live validation.

## 19. TL08 Handoff Requirements

Approve horizon-specific data quality and publication-wait policies from ongoing arrivals. Validate several years of wave archives and safe, resumable backfill capacity before loading them. Train and evaluate models on verified observed history with chronological holdouts and no recursive imputation. Persist versioned derived candidates separately with uncertainty and provenance before enabling any auto-imputation. Use internal Gold pagination and QC/coverage fields; do not treat descriptive `GOLD_READY` as permission to forecast.

## 20. Acceptance Matrix

| Criterion | Status | Evidence/limit |
|---|---|---|
| Real authenticated QC of existing Silver | VERIFIED_IN_PRODUCTION | 2,160 reviewed; zero lineage breaks; repeat zero writes |
| Valid-only HST Daily and six-resolution Gold | VERIFIED_IN_PRODUCTION | 47 Daily, 2,228 Gold after real poll |
| 30-minute scheduler and durable lease | IMPLEMENTED, ENABLED_IN_PRODUCTION, VERIFIED_IN_PRODUCTION | 13:00–14:30 UTC slots ran naturally; lease persisted |
| Fast/fallback retry and 429 backoff | IMPLEMENTED, TESTED_IN_ISOLATION, VERIFIED_IN_PRODUCTION | Corrected 15:00 cycle succeeded on retry 2; 429 isolated only |
| Seven-day gaps and watermark | VERIFIED_IN_PRODUCTION | 17 historical candidate gaps; latest watermark persisted |
| Incremental late/correction propagation | IMPLEMENTED, TESTED_IN_ISOLATION | Seven new rows propagated; rolling successor selection tested; older correction not observed |
| Pinned GCS replay and idempotency | VERIFIED_IN_PRODUCTION | Manual pinned replay: zero new rows; automatic failure replay unobserved |
| Bounded GCS orphan scan | IMPLEMENTED, TESTED_IN_ISOLATION, VERIFIED_IN_PRODUCTION | Startup scanned seven registered/complete objects; no live orphan found |
| Derived candidate contract | IMPLEMENTED, TESTED_IN_ISOLATION | Separate table/API; no production estimates |
| Automatic imputation and model readiness | NOT READY | Disabled; policy and validation absent |
| Historical archive coverage and bulk backfill | IMPLEMENTED, NOT READY | 28 files scanned read-only; no production bulk import |
| Container, public API and reboot configuration | VERIFIED_IN_PRODUCTION, ENABLED_IN_PRODUCTION | Health 200; systemd unit installed/enabled; reboot untested |

No Git commit or push was performed by this assistant. The shared checkout received the external commit described above during validation.
