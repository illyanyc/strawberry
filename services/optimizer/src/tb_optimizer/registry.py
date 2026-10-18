"""Registry stages, the analyst permission table, promotion limits, and evidence packs (ADR-0040;
EX-031..EX-035, EX-050..EX-057; N4).

``decide`` is the single permission function: ``CheckTransition`` returns it, ``PromoteCandidate`` and
``DeployChallengerShadow`` enforce it (EX-032). It never allows ``live`` (section 15 only).
"""

from __future__ import annotations

import html
import json
import math
from dataclasses import dataclass

from tb.ml.v1 import ml_pb2 as mlp
from tb.ml.v1 import training_pb2 as tp
from tb.optimizer.v1 import optimizer_pb2 as op
from tbcore.ml import caller as callers

S = mlp
CANDIDATE, VALIDATED, SHADOW, PAPER = (
    S.REGISTRY_STAGE_CANDIDATE,
    S.REGISTRY_STAGE_VALIDATED,
    S.REGISTRY_STAGE_SHADOW,
    S.REGISTRY_STAGE_PAPER,
)
LIVE_ELIGIBLE, LIVE, RETIRED = S.REGISTRY_STAGE_LIVE_ELIGIBLE, S.REGISTRY_STAGE_LIVE, S.REGISTRY_STAGE_RETIRED
NEXT = {CANDIDATE: VALIDATED, VALIDATED: SHADOW, SHADOW: PAPER, PAPER: LIVE_ELIGIBLE}
DEFAULT_LIMITS = op.PromotionLimits(
    max_promotions_per_window=1,
    window_seconds=14 * 86_400,
    min_shadow_sessions=10,
    cooldown_after_demotion_seconds=14 * 86_400,
)
NS = 1_000_000_000


def stage_name(s: int) -> str:
    return mlp.RegistryStage.Name(s).removeprefix("REGISTRY_STAGE_").lower()


@dataclass(frozen=True)
class Context:
    """Facts ``decide`` needs; the core gathers them from the stores."""

    lineage: op.Lineage
    gate: op.GateReport | None
    has_paper_champion: bool
    analyst_paper_promotions_in_window: int
    last_demotion_at: int
    now: int
    graduation_min_sessions: int = 20
    stepup_max_age_s: int = 300


def _deny(reason: str, step_up: bool = False, gates: list[str] | None = None) -> op.PermissionDecision:
    return op.PermissionDecision(allowed=False, reason=reason, requires_step_up=step_up, failed_gates=gates or [])


def _allow(step_up: bool = False) -> op.PermissionDecision:
    return op.PermissionDecision(allowed=True, reason="", requires_step_up=step_up)


def _gate_failures(ctx: Context, cand: op.Candidate, to: int) -> list[str]:
    g = ctx.gate
    if g is None:
        return ["no_gate_report"]
    fails = list(g.reasons) if not g.passed else []
    if g.engine != "event":
        fails.append("engine")
    if not g.leakage_passed:
        fails.append("leakage")
    if to in (PAPER, LIVE_ELIGIBLE):
        if g.survivorship_status != "complete":
            fails.append("survivorship")
        if ctx.has_paper_champion and to == PAPER and not (0 < g.spa_p <= 0.05 or 0 < g.romano_wolf_p <= 0.05):
            fails.append("champion_test")
    return sorted(set(fails))


def decide(caller: tp.CallerContext, cand: op.Candidate, to: int, ctx: Context) -> op.PermissionDecision:
    """The permission table (EX-031) plus gates (EX-051..EX-054) for one transition."""
    if to == LIVE:
        return _deny("live is set only by the section 15 go-live flow (EX-054)")
    if to == RETIRED:
        if caller.actor in (tp.ACTOR_OWNER, tp.ACTOR_SYSTEM):
            return _allow()
        return _deny("only the owner or monitoring retires a candidate (use DemoteCandidate, EX-055)")
    if NEXT.get(cand.stage) != to:
        return _deny(f"no transition {stage_name(cand.stage)} -> {stage_name(to)}")
    actor = caller.actor
    level = ctx.lineage.autonomy_level
    if cand.demoted and actor != tp.ACTOR_OWNER:
        return _deny("re-promotion after a demotion is owner-only (EX-055)")
    if actor == tp.ACTOR_ANALYST:
        if level < 1:
            return _deny("analyst autonomy level 0 is suggest-only (EX-031)")
        if to in (VALIDATED, SHADOW):
            fails = _gate_failures(ctx, cand, to) if to == VALIDATED else []
            return _deny("gates not passed", gates=fails) if fails else _allow()
        if to == PAPER:
            if level < 2:
                return _deny("replacing the paper champion needs analyst level 2 (EX-031)")
            if not ctx.has_paper_champion:
                return _deny("a new spec (no paper champion) needs owner approval with step-up (EX-052, N4)")
            lim = ctx.lineage.promotion_limits if ctx.lineage.HasField("promotion_limits") else DEFAULT_LIMITS
            if cand.shadow_sessions < lim.min_shadow_sessions:
                return _deny(f"minimum shadow period {lim.min_shadow_sessions} sessions not met (EX-033)")
            if ctx.analyst_paper_promotions_in_window >= lim.max_promotions_per_window:
                return _deny("analyst promotion limit for the window reached (EX-033)")
            if ctx.last_demotion_at and ctx.now - ctx.last_demotion_at < lim.cooldown_after_demotion_seconds * NS:
                return _deny("cooldown after a demotion is active (EX-033)")
            fails = _gate_failures(ctx, cand, to)
            return _deny("gates not passed", gates=fails) if fails else _allow()
        return _deny("the analyst may only nominate for live_eligible (EX-034)")
    if actor in (tp.ACTOR_SCHEDULE, tp.ACTOR_SYSTEM):
        if to == VALIDATED:
            fails = _gate_failures(ctx, cand, to)
            return _deny("gates not passed", gates=fails) if fails else _allow()
        if to == SHADOW:
            return _allow()
        return _deny("scheduled retrains stop at shadow; paper and beyond are human or analyst level 2 (EX-040)")
    if actor != tp.ACTOR_OWNER:
        return _deny("unknown actor")
    if to == VALIDATED:
        fails = _gate_failures(ctx, cand, to)
        return _deny("gates not passed", gates=fails) if fails else _allow()
    if to == SHADOW:
        return _allow()
    fresh = callers.has_fresh_step_up(caller, ctx.stepup_max_age_s, ctx.now)
    if to == PAPER:
        fails = _gate_failures(ctx, cand, to)
        if fails:
            return _deny("gates not passed", step_up=True, gates=fails)
        return _allow(step_up=True) if fresh else _deny("promotion to paper needs a fresh step-up (N4)", step_up=True)
    if to == LIVE_ELIGIBLE:
        if cand.synthetic_data:
            return _deny("candidates trained on synthetic data are never live_eligible (EX-052)", step_up=True)
        if cand.shadow_sessions < ctx.graduation_min_sessions:
            return _deny(
                f"graduation needs >= {ctx.graduation_min_sessions} paper sessions (EX-053, SM-005)", step_up=True
            )
        fails = _gate_failures(ctx, cand, to)
        if fails:
            return _deny("gates not passed", step_up=True, gates=fails)
        return _allow(step_up=True) if fresh else _deny("live_eligible needs a fresh step-up (EX-053)", step_up=True)
    return _deny("not allowed")


def registry_entry(cand: op.Candidate, mode: str, account_intraday: str, account_swing: str) -> dict[str, object]:
    """Strategy registry entry (infra/compose/strategies.json format) for shadow/paper deployment (EX-056)."""
    cfg = cand.config
    swing = cfg.timeframe == "1d"
    fam_task = "rule_filter" if cfg.family in ("orb_meta", "residual_reversal_meta") else "model"
    bench = cfg.label.benchmark or cfg.universe.benchmark
    params: dict[str, object] = {
        "bundle": cand.model_version.artifact_uri,
        "universe": list(cfg.universe.instrument_ids),
        "horizon": int(cfg.label.horizon),
        "benchmark": bench,
        "swing": swing,
    }
    if fam_task == "rule_filter":
        params["primary_class"] = cfg.primary.rule
        params["primary_params"] = (
            json.loads(json.dumps(dict(cfg.primary.params))) if cfg.primary.HasField("params") else {}
        )
    sid = cand.strategy_id
    return {
        "strategy_id": sid,
        "short_id": ("m" + cand.candidate_id[-7:]).lower(),
        "name": f"{cfg.family} {cand.candidate_id[-6:]}",
        "horizon": "swing" if swing else "intraday",
        "broker_account_id": account_swing if swing else account_intraday,
        "mode": mode,
        "allocation": "10000",
        "allow_short": False,
        "segregation_allowlist": [],
        "version": cand.model_version.version or "1",
        "code_commit": cand.model_version.code_commit,
        "params_hash": cand.model_version.config_hash[:16],
        "runtime": {
            "class": "meta_filter" if fam_task == "rule_filter" else "model_signal",
            "timeframe": cfg.timeframe,
            "instruments": [*cfg.universe.instrument_ids, bench],
            "params": params,
        },
        "limits": {},
    }


def evidence_pack(
    cand: op.Candidate, gate: op.GateReport | None, stats: op.CumulativeOverfittingStats, lineage: op.Lineage, to: int
) -> op.EvidencePack:
    """One-page evidence pack for a human-gated promotion (EX-057, D31 rule 11)."""

    def f(x: float, nd: int = 3) -> str:
        return "n/a" if x is None or not math.isfinite(x) else f"{x:.{nd}f}"

    g = gate or op.GateReport()
    mv = cand.model_version
    nr = g.cost_sensitivity.net_return
    verdict = "PASS" if g.passed else "FAIL (" + ", ".join(g.reasons) + ")"
    synth = " (synthetic: never live)" if cand.synthetic_data else ""
    lines = [
        f"# Evidence pack: {cand.candidate_id} → {stage_name(to)}",
        "",
        f"- Lineage `{lineage.lineage_id}` ({lineage.family}, {lineage.timeframe}), study `{cand.study_id}`, "
        f"run `{cand.run_id}`",
        f"- Model `{mv.name}:{mv.version}`, snapshot `{mv.dataset_snapshot_id}`, code `{mv.code_commit[:12]}`",
        f"- Data: `{cand.config.data_source}`{synth}; survivorship `{g.survivorship_status or 'unknown'}`",
        "",
        "## Gate (outer walk-forward, event-driven)",
        "",
        f"- Verdict: **{verdict}**; engine `{g.engine}`",
        f"- Outer Sharpe/session {f(g.outer.sharpe)} (95 % CI {f(g.outer.ci_low)}..{f(g.outer.ci_high)}), "
        f"{g.outer.n_obs} sessions, {g.outer.trades} trades, max DD {f(g.outer.max_drawdown)}",
        f"- DSR {f(g.dsr)} at effective N {f(g.n_effective, 1)}; PBO {f(g.pbo)}; SPA p {f(g.spa_p)}; "
        f"Romano-Wolf p {f(g.romano_wolf_p)}",
        f"- Net return 1x / 1.5x / 2x: {f(nr.get('1x', math.nan))} / {f(nr.get('1.5x', math.nan))} / "
        f"{f(nr.get('2x', math.nan))}",
        f"- Leakage suite {'passed' if g.leakage_passed else 'FAILED'}; positive outer folds "
        f"{f(g.outer.positive_fold_fraction, 2)}",
        "",
        "## Lineage (all trials, D31)",
        "",
        f"- Trials {stats.n_trials}, effective N {f(stats.n_effective, 1)}, best DSR {f(stats.deflated_sharpe_prob)}, "
        f"PBO {f(stats.pbo)}, significance reachable: {stats.significance_reachable}",
        "",
        "## Risks",
        "",
        *[f"- {w}" for w in g.warnings],
        f"- Sessions observed in shadow/paper: {cand.shadow_sessions}",
    ]
    md = "\n".join(lines) + "\n"
    body = html.escape(md).replace("\n", "<br>\n")
    page = (
        "<!doctype html><html><head><meta charset='utf-8'><title>Evidence pack</title>"
        "<style>body{font:14px system-ui;max-width:860px;margin:24px auto;color:#111}</style></head>"
        f"<body>{body}</body></html>"
    )
    return op.EvidencePack(candidate_id=cand.candidate_id, markdown=md, html=page)
