-- OMS, risk, and kill-switch state owned by the execution service (section 07, ADR-0025).
-- Runs as execution_owner. Only svc_execution uses this schema. Payloads are tb.orders.v1 protobuf.

CREATE TABLE execution.strategies (
    strategy_id text        PRIMARY KEY,
    short_id    text        NOT NULL UNIQUE,
    payload     bytea       NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE execution.risk_limits (
    scope      text        PRIMARY KEY,
    payload    bytea       NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Every intent with its risk decision (RM-002), idempotent on intent_id (OE-006).
CREATE TABLE execution.intents (
    intent_id         text        PRIMARY KEY,
    strategy_id       text        NOT NULL,
    broker_account_id text        NOT NULL,
    state             text        NOT NULL,
    correlation_id    text        NOT NULL DEFAULT '',
    received_at       bigint      NOT NULL,
    payload           bytea       NOT NULL,
    updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX intents_strategy ON execution.intents (strategy_id, received_at);
CREATE INDEX intents_correlation ON execution.intents (correlation_id);

-- Orders, persisted with their attribution ID before submission (VP-002); one row per client
-- order and per broker-side protective leg (OE-009).
CREATE TABLE execution.orders (
    order_id          text        PRIMARY KEY,
    intent_id         text        NOT NULL REFERENCES execution.intents,
    client_order_id   text        NOT NULL,
    broker_account_id text        NOT NULL,
    broker_order_id   text        NOT NULL DEFAULT '',
    strategy_id       text        NOT NULL,
    state             text        NOT NULL,
    created_at        bigint      NOT NULL,
    payload           bytea       NOT NULL,
    updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX orders_client ON execution.orders (broker_account_id, client_order_id);
CREATE INDEX orders_open ON execution.orders (broker_account_id) WHERE state NOT IN
    ('ORDER_STATE_FILLED','ORDER_STATE_CANCELED','ORDER_STATE_REJECTED','ORDER_STATE_EXPIRED','ORDER_STATE_REPLACED');

-- Fills deduplicated on the canonical broker_exec_id (OE-006), including manual fills (PDT count).
CREATE TABLE execution.fills (
    broker_account_id text   NOT NULL,
    broker_exec_id    text   NOT NULL,
    order_id          text   NOT NULL DEFAULT '',
    strategy_id       text   NOT NULL DEFAULT '',
    event_time        bigint NOT NULL,
    payload           bytea  NOT NULL,
    PRIMARY KEY (broker_account_id, broker_exec_id)
);

-- Latched kill switches (RM-006) and halts (OE-005, VP-008, RM-013, RM-017).
CREATE TABLE execution.kill_switches (
    event_id   text        PRIMARY KEY,
    latched    boolean     NOT NULL,
    payload    bytea       NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE execution.halts (
    halt_id    text        PRIMARY KEY,
    cleared    boolean     NOT NULL DEFAULT false,
    payload    bytea       NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Transactional outbox (libs/go/outbox): ord.decision, ord.order.*, risk.kill, alert.*.
CREATE TABLE execution.outbox (
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
CREATE INDEX outbox_pending ON execution.outbox (id) WHERE published_at IS NULL;

REVOKE ALL ON SCHEMA execution FROM PUBLIC;
GRANT USAGE ON SCHEMA execution TO svc_execution;
GRANT SELECT, INSERT, UPDATE ON execution.strategies, execution.risk_limits, execution.intents, execution.orders,
    execution.kill_switches, execution.halts TO svc_execution;
GRANT SELECT, INSERT ON execution.fills TO svc_execution;
GRANT SELECT, INSERT, UPDATE, DELETE ON execution.outbox TO svc_execution;
GRANT USAGE ON SEQUENCE execution.outbox_id_seq TO svc_execution;
