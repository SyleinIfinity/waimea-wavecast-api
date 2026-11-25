-- Causal descriptive signals for TL07-B; additive to existing Gold rows.
BEGIN;
ALTER TABLE gold.resolution_metrics
    ADD COLUMN IF NOT EXISTS lag1_median_value DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS autocorrelation_lag1 DOUBLE PRECISION CHECK (autocorrelation_lag1 BETWEEN -1 AND 1),
    ADD COLUMN IF NOT EXISTS correlation_wvht_dpd DOUBLE PRECISION CHECK (correlation_wvht_dpd BETWEEN -1 AND 1),
    ADD COLUMN IF NOT EXISTS hour_sin DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS hour_cos DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS day_of_year_sin DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS day_of_year_cos DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS month_sin DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS month_cos DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS calendar_season VARCHAR(3) CHECK (calendar_season IN ('DJF','MAM','JJA','SON'));
COMMIT;
