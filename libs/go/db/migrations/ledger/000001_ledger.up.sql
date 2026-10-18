-- Ledger state owned by the ledger service (section 06, ADR-0024). Runs as ledger_owner.
-- Only svc_ledger uses this schema.

-- Append-only event log: every change to owner positions, lots, cash, allocations, and break
-- resolutions has exactly one row with a source ID unique per (account, kind) (VP-I3, invariant 5).
-- No UPDATE or DELETE is granted on it.
CREATE TABLE ledger.events (
    seq         bigserial   PRIMARY KEY,
    account     text        NOT NULL,
    kind        text        NOT NULL,
    source      text        NOT NULL,
    event_time  bigint      NOT NULL,
    actor       text        NOT NULL DEFAULT '',
    payload     jsonb       NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (account, kind, source)
);

-- Materialized owner positions, written in the same transaction as their event; rebuildable from
-- ledger.events (rebuild test).
CREATE TABLE ledger.positions (
    account       text        NOT NULL,
    owner         text        NOT NULL,
    instrument_id text        NOT NULL,
    qty           numeric     NOT NULL,
    cost          numeric     NOT NULL,
    realized      numeric     NOT NULL,
    lots          jsonb       NOT NULL DEFAULT '[]',
    updated_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (account, owner, instrument_id)
);

-- Breaks are operational state (status changes are audited, VP-014, VP-019).
CREATE TABLE ledger.breaks (
    break_id      text        PRIMARY KEY,
    account       text        NOT NULL,
    type          text        NOT NULL,
    status        text        NOT NULL,
    instrument_id text        NOT NULL DEFAULT '',
    detected_at   bigint      NOT NULL,
    resolved_at   bigint      NOT NULL DEFAULT 0,
    payload       jsonb       NOT NULL,
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX breaks_open ON ledger.breaks (account) WHERE status = 'open';

-- Open-order reservations (VP-020).
CREATE TABLE ledger.reservations (
    reservation_id text        PRIMARY KEY,
    intent_id      text        NOT NULL UNIQUE,
    payload        jsonb       NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now()
);

-- Stored reconciliation reports: every status change, every non-snapshot trigger, and the daily
-- session-close report (VP-025).
CREATE TABLE ledger.recon_reports (
    id       bigserial PRIMARY KEY,
    account  text      NOT NULL,
    as_of    bigint    NOT NULL,
    status   text      NOT NULL,
    trigger  text      NOT NULL,
    payload  bytea     NOT NULL
);
CREATE INDEX recon_reports_account ON ledger.recon_reports (account, as_of);

CREATE TABLE ledger.equity_points (
    account text   NOT NULL,
    owner   text   NOT NULL,
    ts      bigint NOT NULL,
    payload jsonb  NOT NULL,
    PRIMARY KEY (account, owner, ts)
);

CREATE TABLE ledger.wash_sales (
    flag_id   text   PRIMARY KEY,
    sell_time bigint NOT NULL,
    payload   jsonb  NOT NULL
);

-- Broker snapshots as received (latest per account is loaded at startup; 30-day retention).
CREATE TABLE ledger.snapshots (
    account text        NOT NULL,
    as_of   bigint      NOT NULL,
    payload bytea       NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (account, as_of)
);

-- Corporate actions waiting for their ex-date (VP-010, VP-023).
CREATE TABLE ledger.pending_actions (
    action_key text        PRIMARY KEY,
    payload    bytea       NOT NULL,
    applied_at timestamptz
);

-- Transactional outbox (libs/go/outbox).
CREATE TABLE ledger.outbox (
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
CREATE INDEX outbox_pending ON ledger.outbox (id) WHERE published_at IS NULL;

REVOKE ALL ON SCHEMA ledger FROM PUBLIC;
GRANT USAGE ON SCHEMA ledger TO svc_ledger;
GRANT SELECT, INSERT ON ledger.events, ledger.recon_reports, ledger.equity_points, ledger.wash_sales TO svc_ledger;
GRANT SELECT, INSERT, UPDATE ON ledger.positions, ledger.breaks, ledger.pending_actions TO svc_ledger;
GRANT SELECT, INSERT, UPDATE, DELETE ON ledger.reservations, ledger.snapshots, ledger.outbox TO svc_ledger;
GRANT USAGE ON SEQUENCE ledger.events_seq_seq, ledger.recon_reports_id_seq, ledger.outbox_id_seq TO svc_ledger;
