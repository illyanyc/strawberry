-- Market-data ingestion bookkeeping (section 05, ADR-0022). Runs as marketdata_owner.

-- Backfill jobs and chunk checkpoints (MD-001: resumable, idempotent).
CREATE TABLE marketdata.backfill_jobs (
    job_id       text        PRIMARY KEY,
    source       text        NOT NULL,
    timeframes   text[]      NOT NULL,
    instruments  text[]      NOT NULL,
    start_ns     bigint      NOT NULL,
    end_ns       bigint      NOT NULL,
    state        text        NOT NULL,
    report       jsonb       NOT NULL DEFAULT '{}',
    error        text        NOT NULL DEFAULT '',
    started_at   timestamptz,
    finished_at  timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE marketdata.backfill_chunks (
    job_id     text        NOT NULL REFERENCES marketdata.backfill_jobs ON DELETE CASCADE,
    chunk_key  text        NOT NULL,
    rows       bigint      NOT NULL,
    done_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id, chunk_key)
);

-- Every gap found by data-quality checks and its outcome (MD-006): repaired, confirmed_missing
-- (source has no data: per-source coverage gap), or open.
CREATE TABLE marketdata.gaps (
    id            bigserial   PRIMARY KEY,
    job_id        text        NOT NULL DEFAULT '',
    source        text        NOT NULL,
    instrument_id text        NOT NULL,
    timeframe     text        NOT NULL,
    from_ns       bigint      NOT NULL,
    to_ns         bigint      NOT NULL,
    missing       integer     NOT NULL,
    status        text        NOT NULL CHECK (status IN ('repaired', 'confirmed_missing', 'open')),
    recorded_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX gaps_instrument ON marketdata.gaps (instrument_id, timeframe, from_ns);

-- Cross-source reconciliation results (MD-010) and quarantined symbol-days.
CREATE TABLE marketdata.recon_results (
    session_date   date        NOT NULL,
    instrument_id  text        NOT NULL,
    primary_feed   text        NOT NULL,
    secondary      text        NOT NULL,
    status         text        NOT NULL CHECK (status IN ('ok', 'breach', 'missing_primary', 'missing_secondary')),
    close_diff_bps double precision,
    high_diff_bps  double precision,
    low_diff_bps   double precision,
    volume_diff_pct double precision,
    detail         text        NOT NULL DEFAULT '',
    checked_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (session_date, instrument_id, primary_feed, secondary)
);

CREATE TABLE marketdata.quarantine (
    instrument_id text        NOT NULL,
    session_date  date        NOT NULL,
    reason        text        NOT NULL,
    detail        text        NOT NULL DEFAULT '',
    created_at    timestamptz NOT NULL DEFAULT now(),
    resolved_at   timestamptz,
    resolved_by   text        NOT NULL DEFAULT '',
    PRIMARY KEY (instrument_id, session_date, reason)
);

-- Per-source daily request/byte usage (FMP bandwidth budget, MD-008).
CREATE TABLE marketdata.source_usage (
    source   text   NOT NULL,
    day      date   NOT NULL,
    requests bigint NOT NULL DEFAULT 0,
    bytes    bigint NOT NULL DEFAULT 0,
    PRIMARY KEY (source, day)
);

GRANT SELECT, INSERT, UPDATE, DELETE ON marketdata.backfill_jobs, marketdata.backfill_chunks, marketdata.gaps,
    marketdata.recon_results, marketdata.quarantine, marketdata.source_usage TO svc_marketdata;
GRANT USAGE ON SEQUENCE marketdata.gaps_id_seq TO svc_marketdata;
GRANT SELECT ON marketdata.backfill_jobs, marketdata.gaps, marketdata.recon_results, marketdata.quarantine TO svc_ops;
