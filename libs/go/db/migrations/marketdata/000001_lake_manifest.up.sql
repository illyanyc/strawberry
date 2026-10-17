-- Lake manifest and market-data bookkeeping owned by marketdata (sections 03/05, ADR-0020).
-- Runs as marketdata_owner; only svc_marketdata uses it (ops reads for backups/verification).

-- Every Parquet file the single lake writer created (ST-003). removed_at is set when compaction or
-- retention deletes the file. sha256 lets `marketdata lake verify` and backups detect corruption.
CREATE TABLE marketdata.lake_files (
    path        text        PRIMARY KEY,
    dataset     text        NOT NULL,
    partition   text        NOT NULL,
    kind        text        NOT NULL CHECK (kind IN ('part', 'staging', 'revisions', 'ref')),
    row_count   bigint      NOT NULL,
    bytes       bigint      NOT NULL,
    sha256      text        NOT NULL,
    min_ts      bigint      NOT NULL DEFAULT 0,
    max_ts      bigint      NOT NULL DEFAULT 0,
    created_at  timestamptz NOT NULL DEFAULT now(),
    removed_at  timestamptz
);
CREATE INDEX lake_files_live ON marketdata.lake_files (dataset, partition) WHERE removed_at IS NULL;

REVOKE ALL ON SCHEMA marketdata FROM PUBLIC;
GRANT USAGE ON SCHEMA marketdata TO svc_marketdata, svc_ops;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA marketdata TO svc_marketdata;
GRANT SELECT ON ALL TABLES IN SCHEMA marketdata TO svc_ops;
