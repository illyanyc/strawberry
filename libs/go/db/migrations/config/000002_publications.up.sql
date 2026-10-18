-- User-published strategy definitions (UI-052).
-- Columns are the definition and the non-performance parameters of a hypothetical backtest
-- (window, bar size, universe description). There is no profit, return, rank, or signal column:
-- a cross-user money-made board is not stored here.

CREATE TABLE config.publications (
    id            text        PRIMARY KEY,
    owner_id      text        NOT NULL,
    display_name  text        NOT NULL CHECK (char_length(display_name) BETWEEN 1 AND 40),
    title         text        NOT NULL CHECK (char_length(title) BETWEEN 1 AND 120),
    definition    text        NOT NULL CHECK (char_length(definition) BETWEEN 1 AND 8000),
    window_start  date        NOT NULL,
    window_end    date        NOT NULL CHECK (window_end >= window_start),
    bar_size      text        NOT NULL CHECK (bar_size IN ('1m', '5m', '15m', '30m', '1h', '1d')),
    universe      text        NOT NULL CHECK (char_length(universe) BETWEEN 1 AND 240),
    published_at  timestamptz NOT NULL,
    withdrawn_at  timestamptz
);

CREATE INDEX publications_active_published_at
    ON config.publications (published_at DESC, id DESC)
    WHERE withdrawn_at IS NULL;

CREATE INDEX publications_active_title
    ON config.publications (lower(title), id)
    WHERE withdrawn_at IS NULL;

GRANT SELECT, INSERT, UPDATE ON config.publications TO svc_api_gateway;
