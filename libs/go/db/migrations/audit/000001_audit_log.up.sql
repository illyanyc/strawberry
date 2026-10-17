-- Append-only, hash-chained audit log (rules SE-020..SE-024). Runs as audit_owner.
-- Writers (audit_writer) may only EXECUTE audit.append; readers (audit_reader) may only SELECT.
-- The canonical encoding below is mirrored in libs/go/audit (Verify recomputes it).

CREATE TABLE audit.events (
    seq            bigint      PRIMARY KEY,
    event_id       text        NOT NULL UNIQUE,
    occurred_at    bigint      NOT NULL,
    actor          text        NOT NULL,
    action         text        NOT NULL,
    target         text        NOT NULL DEFAULT '',
    before_hash    text        NOT NULL DEFAULT '',
    after_hash     text        NOT NULL DEFAULT '',
    ip             text        NOT NULL DEFAULT '',
    device         text        NOT NULL DEFAULT '',
    correlation_id text        NOT NULL DEFAULT '',
    details_json   text        NOT NULL DEFAULT '{}' CHECK (details_json::jsonb IS NOT NULL),
    prev_hash      bytea       NOT NULL CHECK (octet_length(prev_hash) = 32),
    hash           bytea       NOT NULL CHECK (octet_length(hash) = 32),
    recorded_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX events_action_idx ON audit.events (action, seq);
CREATE INDEX events_occurred_idx ON audit.events (occurred_at);

-- field(v) = "<octet length>:" || v || ";"  (length prefix makes the encoding unambiguous)
CREATE FUNCTION audit.field(v text) RETURNS bytea
    LANGUAGE sql IMMUTABLE STRICT
    AS $$ SELECT convert_to(octet_length(convert_to(v, 'UTF8'))::text || ':', 'UTF8')
                 || convert_to(v, 'UTF8') || convert_to(';', 'UTF8') $$;

CREATE FUNCTION audit.append(
    p_event_id text, p_occurred_at bigint, p_actor text, p_action text, p_target text,
    p_before_hash text, p_after_hash text, p_ip text, p_device text, p_correlation_id text,
    p_details_json text
) RETURNS TABLE (seq bigint, hash text)
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = pg_catalog, audit
AS $$
DECLARE
    v_seq  bigint;
    v_prev bytea;
    v_hash bytea;
BEGIN
    IF p_event_id IS NULL OR p_event_id = '' OR p_actor IS NULL OR p_actor = ''
       OR p_action IS NULL OR p_action = '' OR p_occurred_at IS NULL THEN
        RAISE EXCEPTION 'audit.append: event_id, occurred_at, actor, action are required';
    END IF;
    -- Serialize appends so the chain is linear and gapless.
    PERFORM pg_advisory_xact_lock(hashtext('audit.events'));
    SELECT e.seq, e.hash INTO v_seq, v_prev FROM audit.events e ORDER BY e.seq DESC LIMIT 1;
    IF v_seq IS NULL THEN
        v_seq := 0;
        v_prev := decode(repeat('00', 32), 'hex');
    END IF;
    v_seq := v_seq + 1;
    v_hash := sha256(
        v_prev
        || audit.field('tb.audit.v1')
        || audit.field(p_event_id)
        || audit.field(p_occurred_at::text)
        || audit.field(p_actor)
        || audit.field(p_action)
        || audit.field(coalesce(p_target, ''))
        || audit.field(coalesce(p_before_hash, ''))
        || audit.field(coalesce(p_after_hash, ''))
        || audit.field(coalesce(p_ip, ''))
        || audit.field(coalesce(p_device, ''))
        || audit.field(coalesce(p_correlation_id, ''))
        || audit.field(coalesce(p_details_json, '{}'))
    );
    INSERT INTO audit.events (seq, event_id, occurred_at, actor, action, target, before_hash, after_hash,
                              ip, device, correlation_id, details_json, prev_hash, hash)
    VALUES (v_seq, p_event_id, p_occurred_at, p_actor, p_action, coalesce(p_target, ''),
            coalesce(p_before_hash, ''), coalesce(p_after_hash, ''), coalesce(p_ip, ''),
            coalesce(p_device, ''), coalesce(p_correlation_id, ''), coalesce(p_details_json, '{}'),
            v_prev, v_hash);
    RETURN QUERY SELECT v_seq, encode(v_hash, 'hex');
END;
$$;

-- Append-only even for the owner: updates, deletes, and truncates raise (SE-020).
CREATE FUNCTION audit.reject_mutation() RETURNS trigger
    LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'audit.events is append-only (% rejected)', TG_OP;
END;
$$;

CREATE TRIGGER events_no_update BEFORE UPDATE OR DELETE ON audit.events
    FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation();
CREATE TRIGGER events_no_truncate BEFORE TRUNCATE ON audit.events
    FOR EACH STATEMENT EXECUTE FUNCTION audit.reject_mutation();

REVOKE ALL ON audit.events FROM PUBLIC;
REVOKE ALL ON FUNCTION audit.append(text, bigint, text, text, text, text, text, text, text, text, text) FROM PUBLIC;
GRANT USAGE ON SCHEMA audit TO audit_writer, audit_reader;
GRANT EXECUTE ON FUNCTION audit.append(text, bigint, text, text, text, text, text, text, text, text, text) TO audit_writer;
GRANT EXECUTE ON FUNCTION audit.field(text) TO audit_writer;
GRANT SELECT ON audit.events TO audit_reader;
