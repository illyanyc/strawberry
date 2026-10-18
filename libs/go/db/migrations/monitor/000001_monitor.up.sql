-- Monitoring records (ADR-0045): containment policies and actions, the alert inbox, strategy health,
-- performance/drift/divergence/execution reports, daily reports, graduation evidence, and the latest
-- live feedback per strategy, each stored as its tb.monitor.v1 message (tbcore.docstore). Runs as
-- monitor_owner. Used only by svc_monitor (SE-036).

CREATE TABLE monitor.docs (
    kind       text   NOT NULL,
    id         text   NOT NULL,
    lineage_id text   NOT NULL DEFAULT '',
    parent_id  text   NOT NULL DEFAULT '',
    created_at bigint NOT NULL,
    updated_at bigint NOT NULL,
    payload    bytea  NOT NULL,
    PRIMARY KEY (kind, id)
);
CREATE INDEX docs_lineage ON monitor.docs (kind, lineage_id, created_at);

-- Containment actions and policy versions are evidence (MN-035, MN-036): nothing is ever deleted.
CREATE FUNCTION monitor.reject_delete() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'monitor.docs is append-only (MN-035)';
END;
$$;
CREATE TRIGGER docs_no_delete BEFORE DELETE ON monitor.docs
    FOR EACH ROW EXECUTE FUNCTION monitor.reject_delete();
CREATE TRIGGER docs_no_truncate BEFORE TRUNCATE ON monitor.docs
    FOR EACH STATEMENT EXECUTE FUNCTION monitor.reject_delete();

REVOKE ALL ON SCHEMA monitor FROM PUBLIC;
GRANT USAGE ON SCHEMA monitor TO svc_monitor;
GRANT SELECT, INSERT, UPDATE ON monitor.docs TO svc_monitor;
