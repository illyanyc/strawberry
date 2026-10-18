-- Hosted API secrets (SE-067). Ciphertext only. FMP is paper-only: it is not a broker environment.
-- There is no column for a profit, a rank, or a card number.

CREATE TABLE config.api_keys (
    owner_id     text        NOT NULL,
    kind         text        NOT NULL CHECK (kind = 'fmp'),
    environment  text        NOT NULL CHECK (environment = 'paper'),
    key_hint     text        NOT NULL CHECK (char_length(key_hint) BETWEEN 1 AND 8),
    ciphertext   bytea       NOT NULL,
    created_at   timestamptz NOT NULL,
    rotated_at   timestamptz NOT NULL,
    PRIMARY KEY (owner_id, kind, environment)
);

CREATE TABLE config.risk_disclosures (
    owner_id     text        PRIMARY KEY,
    accepted_at  timestamptz NOT NULL
);

GRANT SELECT, INSERT, UPDATE, DELETE ON config.api_keys TO svc_api_gateway;
GRANT SELECT, INSERT, UPDATE ON config.risk_disclosures TO svc_api_gateway;
