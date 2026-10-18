DROP TABLE IF EXISTS analyst.conversations, analyst.code_proposals, analyst.tool_calls, analyst.llm_calls,
    analyst.triggers, analyst.loops, analyst.rounds;
DROP FUNCTION IF EXISTS analyst.reject_change();
