-- Platform settings changed from the UI (rule SE-025: every change audited with before/after hashes).
-- Runs as config_owner; only svc_api_gateway gets access.

CREATE TABLE config.settings (
    owner_id   text        NOT NULL,
    key        text        NOT NULL,
    value      jsonb       NOT NULL,
    version    integer     NOT NULL DEFAULT 1,
    updated_at timestamptz NOT NULL DEFAULT now(),
    updated_by text        NOT NULL,
    PRIMARY KEY (owner_id, key)
);

GRANT USAGE ON SCHEMA config TO svc_api_gateway;
GRANT SELECT, INSERT, UPDATE ON config.settings TO svc_api_gateway;
