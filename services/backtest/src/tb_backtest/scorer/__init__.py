"""The D31 promotion scorer (BT-080). Evaluator code: listed in ``services/backtest/evaluator_manifest.json``
and never imported by assistant or analyst code (BT-081, D31 rule 9)."""

from tb_backtest.scorer.gate import AnalystView, CandidateEvidence, GatePolicy, Verdict, analyst_view, score

__all__ = ["AnalystView", "CandidateEvidence", "GatePolicy", "Verdict", "analyst_view", "score"]
