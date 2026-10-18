"""Persist a training run (ML-013, ADR-0037): artifacts on the filesystem (restic-backed, D8), the
``TrainingRun`` and ``DiagnosticsReport`` messages in the document store, and an MLflow mirror.

Layout under ``<artifacts>/runs/<run_id>/``: ``config.json``, ``dataset.json``, ``oof.parquet``,
``returns.parquet`` (tested sessions at 1x and 2x costs), ``trades.parquet``, ``model/`` (bundle with
checksum), ``diagnostics.binpb``, ``model_card.md``, ``run.binpb``, and ``tearsheet.html`` when the
event-driven evaluation ran.
"""

from __future__ import annotations

import hashlib
import math
import os
import subprocess
import time
from pathlib import Path

import numpy as np
import polars as pl

from tb.ml.v1 import training_pb2 as tp
from tbcore.backtest.tearsheet import render_series
from tbcore.docstore import DocStore
from tbcore.ml.config import canonical_json, to_dict
from tbcore.ml.diagnostics import build_report, importance
from tbcore.ml.models import info
from tbcore.ml.tracking import NullTracker, Tracker
from tbcore.ml.train import TrainingResult, skill
from tbcore.validation.stats import bootstrap_ci, sharpe

KIND_RUN = "run"
KIND_DIAG = "diagnostics"
KIND_PROFILE = "dataset_profile"
KIND_IMPORTANCE = "importance"


def _repo_root() -> Path | None:
    for p in Path(__file__).resolve().parents:
        if (p / "uv.lock").exists() and (p / "go.work").exists():
            return p
    return None


def code_commit() -> str:
    env = os.environ.get("TB_CODE_COMMIT", "")
    if env:
        return env
    root = _repo_root()
    if root is None:
        return "unknown"
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    return out.stdout.strip() or "unknown"


def env_lock_hash() -> str:
    root = _repo_root()
    lock = root / "uv.lock" if root else None
    return hashlib.sha256(lock.read_bytes()).hexdigest()[:16] if lock and lock.exists() else "unknown"


def _fold_msg(res: TrainingResult, k: int) -> tp.FoldMetrics:
    fr = res.folds[k]
    ts = res.dataset.frame["decision_time"].to_numpy()
    fin = {kk: float(v) for kk, v in fr.metrics.items() if math.isfinite(float(v))}
    bt = {kk: float(v) for kk, v in fr.backtest.items() if math.isfinite(float(v))}
    return tp.FoldMetrics(
        fold=fr.fold,
        train_rows=int(fr.train.size),
        test_rows=int(fr.test.size),
        train=tp.Period(start=int(ts[fr.train].min()), end=int(ts[fr.train].max()) + 1),
        test=tp.Period(start=int(ts[fr.test].min()), end=int(ts[fr.test].max()) + 1),
        metrics=fin,
        backtest=bt,
        best_iteration=fr.best_iteration,
    )


def model_card(res: TrainingResult, run: tp.TrainingRun, report: tp.DiagnosticsReport) -> str:
    """Model card (section 10): purpose, data, features, labels, validation, OOS metrics with CIs,
    limitations, training cutoff, compute cost."""
    cfg = res.config
    fam = info(cfg.family)
    r = res.session_returns
    lo, hi = bootstrap_ci(r) if r.size >= 3 else (math.nan, math.nan)
    sr = sharpe(r) if r.size >= 3 else math.nan
    m = res.oos_metrics

    def f(x: float, nd: int = 4) -> str:
        return "n/a" if x is None or not math.isfinite(x) else f"{x:.{nd}f}"

    def day(ns: int) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime(ns / 1e9)) if ns else "n/a"

    lines = [
        f"# Model card: {cfg.name or run.run_id}",
        "",
        f"- **Run:** `{run.run_id}` · **family:** `{cfg.family}` (tier {fam.tier}, {fam.task}) · "
        f"**timeframe:** {cfg.timeframe}",
        f"- **Purpose:** {fam.description}.",
        f"- **Data:** `{cfg.data_source}`, snapshot `{res.dataset.snapshot_id}`, {res.dataset.manifest.rows} rows, "
        f"{res.dataset.manifest.instruments} instruments, {day(res.dataset.manifest.period.start)} to "
        f"{day(res.dataset.manifest.period.end)} (training cutoff; holdout excluded).",
        f"- **Features:** `{res.dataset.feature_set.id}` ({len(res.dataset.features)}): "
        f"{', '.join(res.dataset.features)}.",
        f"- **Label:** `{cfg.label.kind}`, horizon {cfg.label.horizon}, benchmark `{cfg.label.benchmark or 'none'}`, "
        f"volatility window {cfg.label.vol_window}; sample weights `{cfg.sample_weighting.method}`.",
        f"- **Validation:** `{cfg.splitter.kind}` with {len(res.folds)} folds, eras = sessions, embargo "
        f"{cfg.splitter.embargo_bars} bars; early stopping on a purged training tail.",
        "",
        "## Out-of-sample results (stitched test folds)",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Skill ({'AUC - 0.5' if res.task == 'classification' else 'IC'}) | {f(skill(res.task, m, 'oos_'))} |",
        f"| Session Sharpe (95 % CI) | {f(sr)} ({f(lo)}, {f(hi)}) |",
        f"| Net return 1x / 2x costs | {f(m.get('bt_net_return', math.nan))} / "
        f"{f(m.get('bt_net_return_2x', math.nan))} |",
        f"| Trades | {int(m.get('bt_trades', 0))} |",
        f"| Max drawdown | {f(m.get('bt_max_drawdown', math.nan))} |",
        "",
        "Deflated Sharpe of the associated strategy is computed over the lineage's trial ledger in the gate "
        "report (EX-021); a single run's Sharpe is not evidence of edge.",
        "",
        "## Known limitations",
        "",
    ]
    lim = [f"- `{x.code}`: {x.message}" for x in report.findings]
    if cfg.data_source.startswith("synthetic:"):
        lim.append("- Trained on synthetic data: never eligible for live (EX-052).")
    lim.append("- The vectorized evaluation does not simulate protective stops (the event-driven tier does).")
    lines += lim
    lines += [
        "",
        f"Compute: {f(run.repro.runtime_seconds, 1)} s on {run.repro.hardware} with {run.repro.threads} threads; "
        f"code `{run.repro.code_commit[:12]}`, lockfile `{run.repro.env_lock_hash}`.",
    ]
    return "\n".join(lines) + "\n"


def persist(
    res: TrainingResult,
    run_id: str,
    job_id: str,
    store: DocStore,
    artifacts_root: str | Path,
    caller: tp.CallerContext | None = None,
    tracker: Tracker | None = None,
    base_run_id: str = "",
    trial_id: str = "",
) -> tp.TrainingRun:
    d = Path(artifacts_root) / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    cfg = res.config
    (d / "config.json").write_text(canonical_json(cfg))
    (d / "dataset.json").write_text(canonical_json(res.dataset.manifest))
    res.oof_frame().write_parquet(d / "oof.parquet")
    pl.DataFrame(
        {"session_day": res.session_days, "ret": res.session_returns, "ret_2x": res.session_returns_2x}
    ).write_parquet(d / "returns.parquet")
    res.trades.write_parquet(d / "trades.parquet")
    if res.final is None:
        raise ValueError("persist needs a run trained with refit_final=True")
    res.final.run_id = run_id
    res.final.save(d / "model")
    report = build_report(res, run_id)
    (d / "diagnostics.binpb").write_bytes(report.SerializeToString())
    repro = tp.Reproducibility()
    repro.CopyFrom(res.repro)
    repro.code_commit = code_commit()
    repro.env_lock_hash = env_lock_hash()
    run = tp.TrainingRun(
        run_id=run_id,
        job_id=job_id,
        config=cfg,
        dataset=res.dataset.manifest,
        folds=[_fold_msg(res, k) for k in range(len(res.folds))],
        oos_metrics={k: float(v) for k, v in res.oos_metrics.items() if v is not None and math.isfinite(float(v))},
        artifact_uri=str(d),
        model_card_uri=str(d / "model_card.md"),
        diagnostics_report_id=report.report_id,
        repro=repro,
        status=tp.JOB_STATUS_SUCCEEDED,
        base_run_id=base_run_id,
        lineage_id=cfg.lineage_id,
        trial_id=trial_id,
        created_at=time.time_ns(),
    )
    if caller is not None:
        run.launched_by.CopyFrom(caller)
    (d / "model_card.md").write_text(model_card(res, run, report))
    folds = pl.DataFrame(
        {
            "fold": [f.fold for f in run.folds],
            "test_rows": [f.test_rows for f in run.folds],
            "skill": [skill(res.task, dict(f.metrics), "val_") for f in run.folds],
            "net_return": [f.backtest.get("net_return", math.nan) for f in run.folds],
            "trades": [f.backtest.get("trades", 0.0) for f in run.folds],
        }
    )
    (d / "tearsheet.html").write_text(
        render_series(
            f"Training run {run_id}",
            f"{cfg.family} on {cfg.data_source} · {cfg.timeframe} · vectorized out-of-fold evaluation "
            "(screening, BT-006)",
            res.session_returns,
            res.session_returns_2x,
            summary={k: v for k, v in run.oos_metrics.items() if k.startswith(("bt_", "oos_", "primary_"))},
            tables={"Folds": folds},
            warnings=[f"{x.code}: {x.message}" for x in report.findings],
        )
    )
    run.tearsheet_uri = str(d / "tearsheet.html")
    t = tracker or NullTracker()
    try:
        params = {k: str(v) for k, v in _flatten(to_dict(cfg)).items()}
        step = [
            (f"fold_{fr.fold}_{k}", float(v), fr.fold)
            for fr in res.folds
            for k, v in fr.metrics.items()
            if isinstance(v, float)
        ]
        run.mlflow_run_id = t.log_run(
            experiment=f"lineage/{cfg.lineage_id or 'adhoc'}",
            run_name=cfg.name or run_id,
            params=params,
            metrics=dict(run.oos_metrics),
            tags={
                "tb.run_id": run_id,
                "tb.family": cfg.family,
                "tb.snapshot_id": res.dataset.snapshot_id,
                "tb.launched_by": str(run.launched_by.actor),
                "tb.analyst_round_id": run.launched_by.analyst_round_id,
                "tb.base_run_id": base_run_id,
                "tb.code_commit": repro.code_commit,
            },
            artifacts={
                "model_card.md": d / "model_card.md",
                "diagnostics.binpb": d / "diagnostics.binpb",
                "model/bundle.json": d / "model" / "bundle.json",
                "config.json": d / "config.json",
            },
            step_metrics=step,
        )
    except Exception as exc:  # MLflow is a mirror: a failure is recorded, never fatal (ML-013)
        run.notes.append(f"mlflow mirror failed: {type(exc).__name__}")
    (d / "run.binpb").write_bytes(run.SerializeToString())
    store.put(KIND_RUN, run_id, run, lineage_id=cfg.lineage_id, parent_id=base_run_id)
    store.put(KIND_DIAG, report.report_id, report, lineage_id=cfg.lineage_id, parent_id=run_id)
    store.put(KIND_PROFILE, res.dataset.snapshot_id, profile(res, report))
    for method in ("gain", "permutation", "shap"):
        store.put(
            KIND_IMPORTANCE, f"{run_id}:{method}", importance(res, method), lineage_id=cfg.lineage_id, parent_id=run_id
        )
    return run


def profile(res: TrainingResult, report: tp.DiagnosticsReport) -> tp.DatasetProfile:
    m = res.dataset.manifest
    return tp.DatasetProfile(
        snapshot_id=m.snapshot_id,
        rows=m.rows,
        instruments=m.instruments,
        eras=m.eras,
        period=m.period,
        features=list(report.data_quality.features),
        label_summary=dict(m.label_summary),
        avg_uniqueness=report.data_quality.avg_uniqueness,
        rows_dropped=dict(m.rows_dropped),
    )


def _flatten(d: dict[str, object], prefix: str = "") -> dict[str, object]:
    out: dict[str, object] = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


def returns_of(run: tp.TrainingRun) -> tuple[np.ndarray, np.ndarray]:
    """(session_days, returns) of a stored run (tested sessions, 1x costs)."""
    f = pl.read_parquet(Path(run.artifact_uri) / "returns.parquet")
    return f["session_day"].to_numpy(), f["ret"].to_numpy()
