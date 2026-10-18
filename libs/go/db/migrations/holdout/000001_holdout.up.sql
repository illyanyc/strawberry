-- Sealed holdout records (ADR-0039, EX-010..EX-015): boundaries, touches, and results. Only svc_holdout
-- may read or write them; no role the analyst path uses has any grant here (EX-012). Runs as
-- holdout_owner.

CREATE TABLE holdout.docs (
    kind       text   NOT NULL,
    id         text   NOT NULL,
    lineage_id text   NOT NULL DEFAULT '',
    parent_id  text   NOT NULL DEFAULT '',
    created_at bigint NOT NULL,
    updated_at bigint NOT NULL,
    payload    bytea  NOT NULL,
    PRIMARY KEY (kind, id)
);
CREATE INDEX docs_lineage ON holdout.docs (kind, lineage_id, created_at);

-- Touch counts never decrease (EX-I2): no deletes or truncates.
CREATE FUNCTION holdout.reject_delete() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'holdout.docs is append-only (EX-I2)';
END;
$$;
CREATE TRIGGER docs_no_delete BEFORE DELETE ON holdout.docs
    FOR EACH ROW EXECUTE FUNCTION holdout.reject_delete();
CREATE TRIGGER docs_no_truncate BEFORE TRUNCATE ON holdout.docs
    FOR EACH STATEMENT EXECUTE FUNCTION holdout.reject_delete();

REVOKE ALL ON SCHEMA holdout FROM PUBLIC;
GRANT USAGE ON SCHEMA holdout TO svc_holdout;
GRANT SELECT, INSERT, UPDATE ON holdout.docs TO svc_holdout;
