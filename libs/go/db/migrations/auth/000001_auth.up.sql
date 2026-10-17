-- Owner authentication state (rules SE-004..SE-011). Runs as auth_owner; only svc_api_gateway gets access.
-- Every table carries owner_id so multi-user (U7) needs no reshaping.

CREATE TABLE auth.owners (
    id           text        PRIMARY KEY,
    display_name text        NOT NULL,
    -- WebAuthn user handle (random, never the owner ID itself).
    user_handle  bytea       NOT NULL UNIQUE,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE auth.webauthn_credentials (
    id           bytea       PRIMARY KEY,
    owner_id     text        NOT NULL REFERENCES auth.owners (id),
    name         text        NOT NULL,
    -- Full go-webauthn Credential (public key, sign count, flags, transports, AAGUID). No secrets.
    credential   jsonb       NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz
);
CREATE INDEX webauthn_credentials_owner_idx ON auth.webauthn_credentials (owner_id);

CREATE TABLE auth.totp (
    owner_id     text        PRIMARY KEY REFERENCES auth.owners (id),
    -- TOTP shared secret, AES-256-GCM encrypted with the gateway key (SE-008).
    secret_enc   bytea       NOT NULL,
    confirmed    boolean     NOT NULL DEFAULT false,
    -- Last accepted time-step counter; codes at or below it are rejected (replay protection).
    last_counter bigint      NOT NULL DEFAULT 0,
    created_at   timestamptz NOT NULL DEFAULT now(),
    confirmed_at timestamptz
);

CREATE TABLE auth.recovery_codes (
    owner_id   text        NOT NULL REFERENCES auth.owners (id),
    -- HMAC-SHA256(gateway key, code); plaintext is shown once and never stored (SE-009).
    code_hash  bytea       PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now(),
    used_at    timestamptz
);
CREATE INDEX recovery_codes_owner_idx ON auth.recovery_codes (owner_id);

CREATE TABLE auth.sessions (
    -- SHA-256 of the opaque session token; the token itself only lives in the cookie (SE-005).
    id_hash             bytea       PRIMARY KEY,
    -- Short public ID for listing and revoking sessions.
    public_id           text        NOT NULL UNIQUE,
    owner_id            text        NOT NULL REFERENCES auth.owners (id),
    auth_method         text        NOT NULL CHECK (auth_method IN ('passkey', 'totp', 'recovery')),
    created_at          timestamptz NOT NULL DEFAULT now(),
    last_seen_at        timestamptz NOT NULL DEFAULT now(),
    idle_expires_at     timestamptz NOT NULL,
    absolute_expires_at timestamptz NOT NULL,
    step_up_method      text        NOT NULL DEFAULT '' CHECK (step_up_method IN ('', 'passkey', 'totp', 'recovery')),
    step_up_until       timestamptz,
    ip                  text        NOT NULL DEFAULT '',
    user_agent          text        NOT NULL DEFAULT '',
    revoked_at          timestamptz
);
CREATE INDEX sessions_owner_idx ON auth.sessions (owner_id, revoked_at);

CREATE TABLE auth.ceremonies (
    id               text        PRIMARY KEY,
    kind             text        NOT NULL CHECK (kind IN ('setup', 'register', 'login', 'step_up')),
    owner_id         text,
    session_id_hash  bytea,
    -- go-webauthn SessionData (challenge, allowed credentials); single use.
    data             jsonb       NOT NULL,
    expires_at       timestamptz NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE auth.setup_tokens (
    token_hash bytea       PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    used_at    timestamptz
);

CREATE TABLE auth.throttle (
    -- e.g. 'account:owner', 'ip:203.0.113.5'
    key          text        PRIMARY KEY,
    failures     integer     NOT NULL DEFAULT 0,
    window_start timestamptz NOT NULL DEFAULT now(),
    locked_until timestamptz
);

GRANT USAGE ON SCHEMA auth TO svc_api_gateway;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA auth TO svc_api_gateway;
