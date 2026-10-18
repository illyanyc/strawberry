"""TrainingConfig defaults, hashing, and helpers (ML-010, ML-011).

The canonical config is the contract message ``tb.ml.v1.TrainingConfig``; ``resolve`` fills every
default explicitly so the stored config (and its hash) says exactly what ran.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from google.protobuf import json_format, struct_pb2

from tb.ml.v1 import training_pb2 as tp
from tbcore.data.calendar import TIMEFRAMES

NS = 1_000_000_000
DAILY = "1d"
RULE_FAMILIES = {"orb_meta": "orb", "residual_reversal_meta": "residual_reversal"}


def bar_ns(timeframe: str) -> int:
    if timeframe == DAILY:
        return 86_400 * NS
    return int(TIMEFRAMES[timeframe].total_seconds() * NS)


def to_dict(msg: Any) -> dict[str, Any]:
    out: dict[str, Any] = json_format.MessageToDict(msg, preserving_proto_field_name=True)
    return out


def struct_of(d: dict[str, Any]) -> struct_pb2.Struct:
    s = struct_pb2.Struct()
    s.update(d)
    return s


def hyperparams(cfg: tp.TrainingConfig) -> dict[str, Any]:
    out: dict[str, Any] = json_format.MessageToDict(cfg.hyperparams) if cfg.HasField("hyperparams") else {}
    return out


def canonical_json(msg: Any) -> str:
    return json.dumps(to_dict(msg), sort_keys=True, separators=(",", ":"))


def config_hash(cfg: tp.TrainingConfig) -> str:
    """Input identity of a run (ML-010): hash of the resolved config without free-text fields."""
    c = tp.TrainingConfig()
    c.CopyFrom(cfg)
    c.name = ""
    return hashlib.sha256(canonical_json(c).encode()).hexdigest()


def resolve(cfg: tp.TrainingConfig, schema_defaults: dict[str, Any] | None = None) -> tp.TrainingConfig:
    """A copy of ``cfg`` with every default filled in."""
    c = tp.TrainingConfig()
    c.CopyFrom(cfg)
    daily = (c.timeframe or "15m") == DAILY
    c.timeframe = c.timeframe or "15m"
    c.data_source = c.data_source or "lake"
    if not c.feature_set.name:
        c.feature_set.name = "swing_daily" if daily else "core_intraday"
    if not c.feature_set.version:
        c.feature_set.version = 1
    lab = c.label
    if not lab.kind:
        lab.kind = (
            "meta_label"
            if c.family in RULE_FAMILIES
            else ("residual_rank" if c.family == "lgbm_lambdarank" else "residual_return")
        )
    lab.horizon = lab.horizon or (5 if daily else 4)
    lab.vol_window = lab.vol_window or 20
    lab.benchmark = lab.benchmark or c.universe.benchmark
    lab.pt = lab.pt or 1.0
    lab.sl = lab.sl or 1.0
    lab.rank_buckets = lab.rank_buckets or 5
    sp = c.splitter
    sp.kind = sp.kind or "walk_forward"
    if sp.kind == "walk_forward":
        sp.train_eras = sp.train_eras or (250 if daily else 40)
        sp.test_eras = sp.test_eras or (60 if daily else 20)
        if not cfg.HasField("splitter"):
            sp.anchored = True
    elif sp.kind == "purged_kfold":
        sp.n_splits = sp.n_splits or 5
    elif sp.kind == "cpcv":
        sp.n_groups = sp.n_groups or 6
        sp.n_test_groups = sp.n_test_groups or 2
    else:
        raise ValueError(f"unknown splitter {sp.kind!r} (SM-002: no random k-fold)")
    sp.embargo_bars = max(sp.embargo_bars, lab.horizon)  # ML-005
    c.sample_weighting.method = c.sample_weighting.method or "uniqueness"
    if c.sample_weighting.method == "time_decay" and not c.sample_weighting.decay:
        c.sample_weighting.decay = 0.5
    c.seed = c.seed or 7
    c.resources.cpu_threads = c.resources.cpu_threads or 2
    c.resources.device = c.resources.device or "auto"
    ev = c.evaluation
    ev.top_quantile = ev.top_quantile or 0.2
    if not cfg.HasField("evaluation"):
        ev.long_only = True
    ev.cost_multiplier = ev.cost_multiplier or 1.0
    if c.family in RULE_FAMILIES:
        c.primary.rule = c.primary.rule or RULE_FAMILIES[c.family]
    if schema_defaults:
        hp = {**schema_defaults, **hyperparams(c)}
        c.hyperparams.Clear()
        c.hyperparams.update(hp)
    return c
