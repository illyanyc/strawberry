-- Envelope-encrypted broker credentials (rules SE-030..SE-033). Runs as secrets_owner.
-- Only svc_broker_gateway may touch this schema; no other role has USAGE (SE-032).

CREATE TABLE secrets.broker_credentials (
    broker_account_id text        NOT NULL,
    environment       text        NOT NULL CHECK (environment IN ('paper', 'live')),
    broker            text        NOT NULL,
    owner_id          text        NOT NULL,
    -- AES-256-GCM(dek, secret JSON, aad = "tb1|<broker_account_id>|<environment>")
    ciphertext        bytea       NOT NULL,
    nonce             bytea       NOT NULL CHECK (octet_length(nonce) = 12),
    -- AES-256-GCM(kek[kek_id], dek, aad = "tb1-dek|<broker_account_id>|<environment>")
    wrapped_dek       bytea       NOT NULL,
    dek_nonce         bytea       NOT NULL CHECK (octet_length(dek_nonce) = 12),
    kek_id            text        NOT NULL,
    -- Names of the secret fields (never values), for status display.
    fields            text[]      NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    rotated_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (broker_account_id, environment)
);

REVOKE ALL ON SCHEMA secrets FROM PUBLIC;
REVOKE ALL ON secrets.broker_credentials FROM PUBLIC;
GRANT USAGE ON SCHEMA secrets TO svc_broker_gateway;
GRANT SELECT, INSERT, UPDATE, DELETE ON secrets.broker_credentials TO svc_broker_gateway;
