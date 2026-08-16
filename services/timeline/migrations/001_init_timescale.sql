-- Boensity Timeline schema.
--
-- The table is a hypertable partitioned on scan_date. A DEXA series is small
-- per patient (a handful of rows over a decade) but the cohort query that
-- matters, "show me everyone in this department trending toward the
-- threshold", sweeps every patient across a time window. That is the access
-- pattern TimescaleDB chunk exclusion is built for.

CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS scan_observations (
    scan_date          DATE            NOT NULL,
    patient_id         TEXT            NOT NULL,
    site               TEXT            NOT NULL,
    t_score            DOUBLE PRECISION NOT NULL,
    z_score            DOUBLE PRECISION,
    bmd_g_cm2          DOUBLE PRECISION,
    manufacturer       TEXT            NOT NULL DEFAULT 'unknown',
    scanner_model      TEXT,
    source_document_id TEXT,
    ingested_at        TIMESTAMPTZ     NOT NULL DEFAULT now(),

    CONSTRAINT t_score_plausible CHECK (t_score BETWEEN -8.0 AND 6.0),
    CONSTRAINT bmd_plausible CHECK (bmd_g_cm2 IS NULL OR bmd_g_cm2 BETWEEN 0.1 AND 3.0),
    CONSTRAINT site_known CHECK (site IN ('femoral_neck', 'total_hip', 'lumbar_spine', 'forearm'))
);

-- One year chunks. Scan intervals are measured in years, so a smaller chunk
-- would produce thousands of near-empty chunks and slow planning down.
SELECT create_hypertable(
    'scan_observations',
    'scan_date',
    chunk_time_interval => INTERVAL '1 year',
    if_not_exists => TRUE
);

-- Re-ingesting the same PDF must not create a second point on the chart.
CREATE UNIQUE INDEX IF NOT EXISTS scan_observations_natural_key
    ON scan_observations (patient_id, scan_date, site);

CREATE INDEX IF NOT EXISTS scan_observations_patient_site_date
    ON scan_observations (patient_id, site, scan_date DESC);

CREATE INDEX IF NOT EXISTS scan_observations_site_date
    ON scan_observations (site, scan_date DESC);


-- Per patient and site rollup. The cohort screen reads this, not the raw
-- rows, because recomputing first and last values across every patient on
-- every dashboard load is the query that would eventually fall over.
CREATE MATERIALIZED VIEW IF NOT EXISTS patient_scan_summary AS
SELECT
    patient_id,
    site,
    count(*)                                              AS scan_count,
    min(scan_date)                                        AS first_scan_date,
    max(scan_date)                                        AS last_scan_date,
    EXTRACT(EPOCH FROM (max(scan_date) - min(scan_date))) / 31556952.0 AS span_years,
    (array_agg(t_score ORDER BY scan_date ASC))[1]        AS earliest_t_score,
    (array_agg(t_score ORDER BY scan_date DESC))[1]       AS latest_t_score,
    count(DISTINCT manufacturer) FILTER (WHERE manufacturer <> 'unknown') AS manufacturer_count
FROM scan_observations
GROUP BY patient_id, site;

CREATE UNIQUE INDEX IF NOT EXISTS patient_scan_summary_key
    ON patient_scan_summary (patient_id, site);


-- Department level scan volume, used by the operations view rather than by
-- any clinical calculation.
CREATE MATERIALIZED VIEW IF NOT EXISTS scan_volume_monthly
WITH (timescaledb.continuous) AS
SELECT
    time_bucket(INTERVAL '1 month', scan_date) AS bucket,
    site,
    manufacturer,
    count(*)     AS scan_count,
    avg(t_score) AS mean_t_score
FROM scan_observations
GROUP BY bucket, site, manufacturer
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
    'scan_volume_monthly',
    start_offset => INTERVAL '5 years',
    end_offset   => INTERVAL '1 day',
    schedule_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- Scans older than five years are still needed for the trajectory fit, so
-- nothing is ever dropped. Compression reclaims the space instead.
ALTER TABLE scan_observations SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'patient_id, site'
);

SELECT add_compression_policy('scan_observations', INTERVAL '5 years', if_not_exists => TRUE);
