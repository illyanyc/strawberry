-- AI Research Analyst journal and state (ADR-0041, AN-003, AN-055). Runs as analyst_owner; used only
-- by svc_assistant. Rounds, proposals, and conversations are stored as their tb.analyst.v1 messages
-- with indexed columns. The LLM and tool call logs are append-only (replay and audit evidence).

CREATE TABLE analyst.rounds (
    round_id     text   PRIMARY KEY,
    lineage_id   text   NOT NULL,
    seq          integer NOT NULL,
    decision     integer NOT NULL,
    started_at   bigint NOT NULL,
    completed_at bigint NOT NULL DEFAULT 0,
    payload      bytea  NOT NULL
);
CREATE INDEX rounds_lineage ON analyst.rounds (lineage_id, started_at DESC);

-- Per-lineage loop state (JSON): pause flag, round counters, deflated history, pending launches.
CREATE TABLE analyst.loops (
    lineage_id text        PRIMARY KEY,
    state      text        NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Handled trigger IDs: a redelivered or duplicate trigger never opens a second round (AN-001).
CREATE TABLE analyst.triggers (
    trigger_id text        PRIMARY KEY,
    handled_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE analyst.llm_calls (
    call_id       text        PRIMARY KEY,
    role          text        NOT NULL,
    provider      text        NOT NULL,
    model         text        NOT NULL,
    prompt_sha256 text        NOT NULL,
    prompt        text        NOT NULL,
    response      text        NOT NULL,
    input_tokens  integer     NOT NULL,
    output_tokens integer     NOT NULL,
    cost_usd      double precision NOT NULL,
    latency_ms    double precision NOT NULL,
    ok            boolean     NOT NULL,
    error         text        NOT NULL,
    lineage_id    text        NOT NULL,
    round_id      text        NOT NULL,
    created_at    timestamptz NOT NULL
);
CREATE INDEX llm_calls_time ON analyst.llm_calls (created_at);
CREATE INDEX llm_calls_round ON analyst.llm_calls (round_id);

CREATE TABLE analyst.tool_calls (
    id            bigserial   PRIMARY KEY,
    tool          text        NOT NULL,
    lineage_id    text        NOT NULL,
    round_id      text        NOT NULL,
    level         integer     NOT NULL,
    outcome       text        NOT NULL,
    input_sha256  text        NOT NULL,
    output_sha256 text        NOT NULL,
    detail        text        NOT NULL,
    created_at    timestamptz NOT NULL
);
CREATE INDEX tool_calls_round ON analyst.tool_calls (round_id);

CREATE FUNCTION analyst.reject_change() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'analyst call logs are append-only (AN-055)';
END;
$$;
CREATE TRIGGER llm_calls_append_only BEFORE UPDATE OR DELETE ON analyst.llm_calls
    FOR EACH ROW EXECUTE FUNCTION analyst.reject_change();
CREATE TRIGGER tool_calls_append_only BEFORE UPDATE OR DELETE ON analyst.tool_calls
    FOR EACH ROW EXECUTE FUNCTION analyst.reject_change();

CREATE TABLE analyst.code_proposals (
    proposal_id text        PRIMARY KEY,
    lineage_id  text        NOT NULL,
    status      integer     NOT NULL,
    payload     bytea       NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE analyst.conversations (
    conversation_id text        PRIMARY KEY,
    updated_at      timestamptz NOT NULL,
    payload         bytea       NOT NULL
);
CREATE INDEX conversations_updated ON analyst.conversations (updated_at);

REVOKE ALL ON SCHEMA analyst FROM PUBLIC;
GRANT USAGE ON SCHEMA analyst TO svc_assistant;
GRANT SELECT, INSERT, UPDATE ON analyst.rounds, analyst.loops, analyst.code_proposals TO svc_assistant;
GRANT SELECT, INSERT ON analyst.triggers, analyst.llm_calls, analyst.tool_calls TO svc_assistant;
GRANT SELECT, INSERT, UPDATE, DELETE ON analyst.conversations TO svc_assistant;
GRANT USAGE ON SEQUENCE analyst.tool_calls_id_seq TO svc_assistant;
