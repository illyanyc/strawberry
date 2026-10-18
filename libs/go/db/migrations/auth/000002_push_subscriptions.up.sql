-- Web Push subscriptions of the owner's browsers and installed PWAs (D13, SE-060..SE-063).
-- The endpoint is a capability URL (anyone holding it can push to the device) and the keys let a
-- holder encrypt to it, so all three are AES-256-GCM sealed with the gateway key; only the
-- endpoint's host and a SHA-256 prefix are stored in clear (shown in the UI, used for dedupe).
CREATE TABLE auth.push_subscriptions (
    id              text        PRIMARY KEY,
    owner_id        text        NOT NULL REFERENCES auth.owners (id),
    endpoint_hash   text        NOT NULL UNIQUE,
    service         text        NOT NULL,
    sealed          bytea       NOT NULL,
    label           text        NOT NULL DEFAULT '',
    created_at      timestamptz NOT NULL DEFAULT now(),
    last_success_at timestamptz,
    last_error      text        NOT NULL DEFAULT '',
    failures        integer     NOT NULL DEFAULT 0
);
CREATE INDEX push_subscriptions_owner_idx ON auth.push_subscriptions (owner_id);

GRANT SELECT, INSERT, UPDATE, DELETE ON auth.push_subscriptions TO svc_api_gateway;
