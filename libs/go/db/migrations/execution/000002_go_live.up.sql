-- Section 15 go-live state (ADR-0054). Runs as execution_owner; only svc_execution uses it.

-- Owner-attested readiness checklist items per live account (GL-010). Automated items are
-- recomputed on every read and never stored. Payload is tb.orders.v1.LiveReadiness.
CREATE TABLE execution.live_readiness (
    broker_account_id text        PRIMARY KEY,
    payload           bytea       NOT NULL,
    updated_at        timestamptz NOT NULL DEFAULT now()
);

-- Sessions a live account streamed read-only with a clean, fresh reconciliation (GL-012).
CREATE TABLE execution.live_clean_sessions (
    broker_account_id text NOT NULL,
    session_date      date NOT NULL,
    PRIMARY KEY (broker_account_id, session_date)
);

GRANT SELECT, INSERT, UPDATE ON execution.live_readiness TO svc_execution;
GRANT SELECT, INSERT ON execution.live_clean_sessions TO svc_execution;
