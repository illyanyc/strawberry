-- Broker adapter state owned by broker-gateway (ADR-0018). Runs as broker_owner.
-- Only svc_broker_gateway may use this schema (credential custody stays in schema `secrets`).

-- Every execution seen from a broker, unique on the canonical broker_exec_id (BR-008). The insert and
-- its outbox rows commit in one transaction, so each execution is published exactly once.
CREATE TABLE broker.executions (
    broker_account_id text        NOT NULL,
    broker_exec_id    text        NOT NULL,
    broker_order_id   text        NOT NULL,
    client_order_id   text        NOT NULL DEFAULT '',
    perm_id           text        NOT NULL DEFAULT '',
    raw_exec_id       text        NOT NULL DEFAULT '',
    symbol            text        NOT NULL DEFAULT '',
    instrument_id     text        NOT NULL DEFAULT '',
    side              text        NOT NULL,
    qty               numeric     NOT NULL,
    price             numeric     NOT NULL,
    fees              numeric     NOT NULL DEFAULT 0,
    event_time        bigint      NOT NULL,
    sequence          bigint      NOT NULL DEFAULT 0,
    payload           bytea       NOT NULL,
    ingested_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (broker_account_id, broker_exec_id)
);
CREATE INDEX executions_time ON broker.executions (broker_account_id, event_time);

-- Order events, deduplicated on (account, order, status, filled qty, broker update time) (BR-008).
CREATE TABLE broker.order_events (
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
CREATE INDEX order_events_order ON broker.order_events (broker_account_id, broker_order_id);

-- Per-account live enablement written by an audited owner action (section 15). broker-gateway only
-- reads it (BR-013, OE-010); without a row with enabled = true a live adapter stays stopped.
CREATE TABLE broker.live_enablement (
    broker_account_id text        PRIMARY KEY,
    enabled           boolean     NOT NULL DEFAULT false,
    enabled_by        text        NOT NULL,
    audit_seq         bigint      NOT NULL,
    updated_at        timestamptz NOT NULL DEFAULT now()
);

-- Transactional outbox (libs/go/outbox).
CREATE TABLE broker.outbox (
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
CREATE INDEX outbox_pending ON broker.outbox (id) WHERE published_at IS NULL;

REVOKE ALL ON SCHEMA broker FROM PUBLIC;
GRANT USAGE ON SCHEMA broker TO svc_broker_gateway;
GRANT SELECT, INSERT ON broker.executions, broker.order_events TO svc_broker_gateway;
GRANT SELECT ON broker.live_enablement TO svc_broker_gateway;
GRANT SELECT, INSERT, UPDATE, DELETE ON broker.outbox TO svc_broker_gateway;
GRANT USAGE ON SEQUENCE broker.outbox_id_seq TO svc_broker_gateway;
