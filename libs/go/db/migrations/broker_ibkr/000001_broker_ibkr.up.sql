-- IBKR adapter state (ADR-0020, IB-031). Runs as broker_ibkr_owner. Same shapes as schema broker
-- (ADR-0018) so the IBKR adapter keeps its own executions, order events, and transactional outbox
-- (libs/go/outbox convention: one outbox per owning schema). Only svc_broker_gateway_ibkr uses it.

-- Every IBKR execution, unique on (account, execId): the insert and its outbox row commit together,
-- so each execution is published exactly once across restarts (BR-008).
CREATE TABLE broker_ibkr.executions (
    broker_account_id text        NOT NULL,
    broker_exec_id    text        NOT NULL,
    broker_order_id   text        NOT NULL,
    client_order_id   text        NOT NULL DEFAULT '',
    perm_id           text        NOT NULL DEFAULT '',
    symbol            text        NOT NULL DEFAULT '',
    instrument_id     text        NOT NULL DEFAULT '',
    side              text        NOT NULL,
    qty               numeric     NOT NULL,
    price             numeric     NOT NULL,
    fees              numeric,
    event_time        bigint      NOT NULL,
    payload           bytea       NOT NULL,
    ingested_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (broker_account_id, broker_exec_id)
);
CREATE INDEX executions_time ON broker_ibkr.executions (broker_account_id, event_time);

-- Order events, deduplicated on (account, permId|status|filled|updated_at).
CREATE TABLE broker_ibkr.order_events (
    broker_account_id text        NOT NULL,
    dedupe_key        text        NOT NULL,
    broker_order_id   text        NOT NULL,
    client_order_id   text        NOT NULL DEFAULT '',
    broker_status     text        NOT NULL,
    state             text        NOT NULL,
    filled_qty        numeric     NOT NULL DEFAULT 0,
    event_time        bigint      NOT NULL,
    payload           bytea       NOT NULL,
    received_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (broker_account_id, dedupe_key)
);

-- Transactional outbox (libs/go/outbox shape), relayed to NATS by the IBKR adapter.
CREATE TABLE broker_ibkr.outbox (
    id             bigserial   PRIMARY KEY,
    subject        text        NOT NULL,
    msg_id         text        NOT NULL,
    payload        bytea       NOT NULL,
    correlation_id text        NOT NULL DEFAULT '',
    created_at     timestamptz NOT NULL DEFAULT now(),
    published_at   timestamptz,
    attempts       integer     NOT NULL DEFAULT 0,
    last_error     text        NOT NULL DEFAULT ''
);
CREATE INDEX outbox_pending ON broker_ibkr.outbox (id) WHERE published_at IS NULL;

-- Small key/value cursor (e.g. last_ready_at in UTC ns, for the execution-gap check IB-033).
CREATE TABLE broker_ibkr.state (
    key        text        PRIMARY KEY,
    value      bigint      NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

REVOKE ALL ON SCHEMA broker_ibkr FROM PUBLIC;
GRANT USAGE ON SCHEMA broker_ibkr TO svc_broker_gateway_ibkr;
GRANT SELECT, INSERT ON broker_ibkr.executions, broker_ibkr.order_events TO svc_broker_gateway_ibkr;
GRANT SELECT, INSERT, UPDATE, DELETE ON broker_ibkr.outbox TO svc_broker_gateway_ibkr;
GRANT SELECT, INSERT, UPDATE ON broker_ibkr.state TO svc_broker_gateway_ibkr;
GRANT USAGE ON SEQUENCE broker_ibkr.outbox_id_seq TO svc_broker_gateway_ibkr;
