-- Research records (ADR-0037): ML runs and jobs, lineages, research cycles, studies, the trial ledger,
-- candidates, gate reports, and registry stage changes, each stored as its tb.ml.v1 / tb.optimizer.v1
-- message (tbcore.docstore). Runs as research_owner. Used by svc_ml and svc_optimizer.

CREATE TABLE research.docs (
    kind       text   NOT NULL,
    id         text   NOT NULL,
    lineage_id text   NOT NULL DEFAULT '',
    parent_id  text   NOT NULL DEFAULT '',
    created_at bigint NOT NULL,
    updated_at bigint NOT NULL,
    payload    bytea  NOT NULL,
    PRIMARY KEY (kind, id)
);
CREATE INDEX docs_lineage ON research.docs (kind, lineage_id, created_at);
CREATE INDEX docs_parent ON research.docs (kind, parent_id, created_at);

-- The trial ledger and every research record only grow (EX-I1, D31 rule 1): no deletes or truncates.
CREATE FUNCTION research.reject_delete() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'research.docs is append-only (EX-I1)';
END;
$$;
CREATE TRIGGER docs_no_delete BEFORE DELETE ON research.docs
    FOR EACH ROW EXECUTE FUNCTION research.reject_delete();
CREATE TRIGGER docs_no_truncate BEFORE TRUNCATE ON research.docs
    FOR EACH STATEMENT EXECUTE FUNCTION research.reject_delete();

REVOKE ALL ON SCHEMA research FROM PUBLIC;
GRANT USAGE ON SCHEMA research TO svc_ml, svc_optimizer;
GRANT SELECT, INSERT, UPDATE ON research.docs TO svc_ml, svc_optimizer;
