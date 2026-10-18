"""Backtest service entrypoint (section 08).

Without arguments it serves the ops surface (healthz, readyz, metrics). Commands:

* ``run --config CFG.json (--bars FILE.parquet | --lake ROOT) [--fill-bars FILE] --out DIR [--stress]``:
  one event-driven backtest (plus the 2x cost stress with ``--stress``, BT-015), saved as artifacts with
  a tearsheet;
* ``parity RECORDING --config CFG.json --mode recorded|model`` (BT-070, BT-071);
* ``leakage --config CFG.json --bars FILE.parquet``: the strategy-level leakage suite (BT-065);
* ``candidates watch|step JOBS_DIR``: evaluator-side checks of analyst candidate features (AN-072).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import polars as pl

from tb_backtest import candidates
from tbcore.backtest import report, tearsheet
from tbcore.backtest.config import BacktestConfig
from tbcore.backtest.event import run_event_backtest
from tbcore.backtest.leakage import run_leakage_suite
from tbcore.data.lake import Lake
from tbcore.parity import main as parity_main
from tbcore.service import ServiceSettings, run


def _bars(cfg: BacktestConfig, bars: str | None, lake: str | None, timeframe: str) -> pl.DataFrame:
    if bars:
        return pl.read_parquet(bars)
    if lake:
        instruments = sorted(set(cfg.universe.instruments) | {i for s in cfg.strategies for i in s.instruments})
        return Lake(lake).bars(instruments, timeframe, cfg.start, cfg.end, adjust="raw")
    raise SystemExit("--bars or --lake is required")


def cmd_run(a: argparse.Namespace) -> int:
    cfg = BacktestConfig.model_validate_json(Path(a.config).read_text(encoding="utf-8"))
    bars = _bars(cfg, a.bars, a.lake, cfg.timeframe)
    fill = _bars(cfg, a.fill_bars, None, cfg.fill_model.fill_timeframe or cfg.timeframe) if a.fill_bars else None
    res = run_event_backtest(cfg, bars, fill_bars=fill)
    stressed = None
    if a.stress:
        stressed = run_event_backtest(
            cfg.model_copy(update={"cost_model": cfg.cost_model.stressed()}), bars, fill_bars=fill
        )
    res.metrics = report.metrics(res)
    out = res.save(a.out)
    tearsheet.write(out / "tearsheet.html", res, stressed=stressed)
    summary = report.summary(res, stressed)
    (out / "report.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    net = summary["metrics"]["net_pnl"]
    print(f"backtest {cfg.config_hash[:16]}: {res.trades.height} trades, net {net:.2f}; artifacts in {out}")
    return 0


def cmd_leakage(a: argparse.Namespace) -> int:
    cfg = BacktestConfig.model_validate_json(Path(a.config).read_text(encoding="utf-8"))
    rep = run_leakage_suite(cfg, pl.read_parquet(a.bars))
    print(rep.summary())
    return 0 if rep.passed else 1


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        run(ServiceSettings(service="backtest"))
        return 0
    if args[0] == "parity":
        return parity_main(args[1:])
    if args[0] == "candidates":
        return candidates.main(args[1:])
    ap = argparse.ArgumentParser(prog="python -m tb_backtest")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--config", required=True)
    r.add_argument("--bars")
    r.add_argument("--lake")
    r.add_argument("--fill-bars")
    r.add_argument("--out", required=True)
    r.add_argument("--stress", action="store_true")
    lk = sub.add_parser("leakage")
    lk.add_argument("--config", required=True)
    lk.add_argument("--bars", required=True)
    a = ap.parse_args(args)
    return cmd_run(a) if a.cmd == "run" else cmd_leakage(a)


if __name__ == "__main__":
    sys.exit(main())
