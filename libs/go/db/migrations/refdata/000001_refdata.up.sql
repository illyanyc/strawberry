-- Shared reference data (section 03, ADR-0020). Runs as refdata_owner.
-- Single writer: svc_marketdata. Readers (refdata_reader): ledger, execution, api-gateway,
-- broker-gateway (instrument resolution). Times are UTC ns (bigint) like every contract.
-- Python research reads the Parquet mirror in the lake (ref/), never these tables directly.

-- Instrument master: instrument_id is stable across ticker changes (DF-006, VP-010).
CREATE TABLE refdata.instruments (
    instrument_id     text        PRIMARY KEY,
    asset_class       text        NOT NULL,
    currency          text        NOT NULL DEFAULT 'USD',
    name              text        NOT NULL DEFAULT '',
    primary_exchange  text        NOT NULL DEFAULT '',
    -- Sector ETF used by the market-context feature (e.g. XLK); empty when unknown.
    sector_etf        text        NOT NULL DEFAULT '',
    listed_at         bigint,
    delisted_at       bigint,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

-- Ticker history with validity ranges [valid_from, valid_to); valid_to NULL = current.
CREATE TABLE refdata.symbols (
    instrument_id text   NOT NULL REFERENCES refdata.instruments,
    symbol        text   NOT NULL,
    exchange      text   NOT NULL DEFAULT '',
    valid_from    bigint NOT NULL,
    valid_to      bigint,
    source        text   NOT NULL,
    PRIMARY KEY (instrument_id, valid_from),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);
CREATE INDEX symbols_symbol ON refdata.symbols (symbol, valid_from);

-- Broker identifiers (Alpaca asset ID, IBKR conId) with validity ranges.
CREATE TABLE refdata.broker_ids (
    instrument_id        text   NOT NULL REFERENCES refdata.instruments,
    broker               text   NOT NULL,
    broker_instrument_id text   NOT NULL,
    valid_from           bigint NOT NULL,
    valid_to             bigint,
    PRIMARY KEY (broker, broker_instrument_id, valid_from)
);
CREATE INDEX broker_ids_instrument ON refdata.broker_ids (instrument_id);

-- Tradability attributes over time (shortable / easy-to-borrow snapshots, section 05 daily sync).
CREATE TABLE refdata.attributes (
    instrument_id  text    NOT NULL REFERENCES refdata.instruments,
    source         text    NOT NULL,
    tradable       boolean NOT NULL,
    shortable      boolean NOT NULL,
    easy_to_borrow boolean NOT NULL,
    fractionable   boolean NOT NULL,
    marginable     boolean NOT NULL,
    valid_from     bigint  NOT NULL,
    valid_to       bigint,
    PRIMARY KEY (instrument_id, source, valid_from)
);

-- Exchange calendar mirror of libs/go/calendar/xnys_sessions.csv (MD-005) for SQL users.
CREATE TABLE refdata.sessions (
    exchange     text    NOT NULL,
    session_date date    NOT NULL,
    pre_open     bigint  NOT NULL,
    open         bigint  NOT NULL,
    close        bigint  NOT NULL,
    post_close   bigint  NOT NULL,
    early_close  boolean NOT NULL,
    PRIMARY KEY (exchange, session_date)
);

-- Corporate actions, one row per source report (DF-005, MD-011, MD-012). Idempotent on
-- (source, source_id). available_at follows MD-011 (declaration, else ex-date, never later than the
-- first fetch); agreement is the cross-source status.
CREATE TABLE refdata.corporate_actions (
    source                  text    NOT NULL,
    source_id               text    NOT NULL,
    instrument_id           text    NOT NULL DEFAULT '',
    symbol                  text    NOT NULL,
    type                    text    NOT NULL,
    ex_date                 bigint  NOT NULL,
    ratio                   numeric,
    amount                  numeric,
    new_symbol              text    NOT NULL DEFAULT '',
    successor_instrument_id text    NOT NULL DEFAULT '',
    record_date             bigint  NOT NULL DEFAULT 0,
    payable_date            bigint  NOT NULL DEFAULT 0,
    declared_at             bigint  NOT NULL DEFAULT 0,
    available_at            bigint  NOT NULL,
    ingested_at             bigint  NOT NULL,
    agreement               text    NOT NULL DEFAULT 'single_source',
    PRIMARY KEY (source, source_id)
);
CREATE INDEX corporate_actions_instrument ON refdata.corporate_actions (instrument_id, ex_date);

-- Point-in-time universe membership [valid_from, valid_to) (DF-006).
CREATE TABLE refdata.universe_members (
    universe      text   NOT NULL,
    instrument_id text   NOT NULL REFERENCES refdata.instruments,
    valid_from    bigint NOT NULL,
    valid_to      bigint,
    PRIMARY KEY (universe, instrument_id, valid_from)
);

REVOKE ALL ON SCHEMA refdata FROM PUBLIC;
GRANT USAGE ON SCHEMA refdata TO svc_marketdata, refdata_reader;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA refdata TO svc_marketdata;
GRANT SELECT ON ALL TABLES IN SCHEMA refdata TO refdata_reader;
