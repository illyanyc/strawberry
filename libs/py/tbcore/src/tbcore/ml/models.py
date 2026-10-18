"""Model zoo behind one interface (section 10, D23, ML-014; ADR-0036).

Tier 1: ``no_skill`` (prior), ``ridge``, ``logistic``, ``lgbm_regression``, ``lgbm_lambdarank``,
``lgbm_binary``. Tier 2: rule + ML filter families ``orb_meta`` (intraday) and ``residual_reversal_meta``
(swing), whose model is the filter classifier (ML-016). Every family declares a hyperparameter JSON
Schema (served to the UI, validated at submit, ML-011). Inputs are preprocessed by the fold pipeline
(``tbcore.ml.preprocess``); LightGBM runs deterministically with fixed seeds and threads (ML-015).
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import lightgbm as lgb
import numpy as np
from numpy.typing import NDArray

from tbcore.labels import rank_relevance
from tbcore.ml import metrics
from tbcore.ml.schema import coerce

F64 = NDArray[np.float64]


@dataclass
class Explanation:
    """Per-row additive contributions (TreeSHAP for trees, coefficient x value for linear models)."""

    values: F64  # (n, features)
    base: float


@dataclass
class EvalSet:
    x: F64
    y: F64
    groups: NDArray[Any] | None = None


class Model(Protocol):
    family: str
    task: str
    curve: list[tuple[int, float, float]]
    curve_metric: str
    best_iteration: int

    def fit(
        self,
        x: F64,
        y: F64,
        sample_weight: F64 | None = None,
        eval_set: EvalSet | None = None,
        groups: NDArray[Any] | None = None,
    ) -> Model: ...

    def predict(self, x: F64) -> F64: ...

    def predict_proba(self, x: F64) -> F64 | None: ...

    def explain(self, x: F64) -> Explanation | None: ...

    def gain_importance(self) -> F64: ...

    def interactions(self, features: list[str], top: int = 10) -> list[tuple[str, str, float]]: ...

    def state(self) -> dict[str, Any]: ...


def _num(lo: float, hi: float, default: float, desc: str) -> dict[str, Any]:
    return {"type": "number", "minimum": lo, "maximum": hi, "default": default, "description": desc}


def _int(lo: int, hi: int, default: int, desc: str) -> dict[str, Any]:
    return {"type": "integer", "minimum": lo, "maximum": hi, "default": default, "description": desc}


def _schema(props: dict[str, Any], title: str) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": title,
        "type": "object",
        "additionalProperties": False,
        "properties": props,
    }


LGBM_PROPS: dict[str, Any] = {
    "num_leaves": _int(2, 256, 15, "Maximum leaves per tree"),
    "learning_rate": _num(0.001, 0.5, 0.05, "Shrinkage per boosting round"),
    "n_estimators": _int(10, 5000, 300, "Maximum boosting rounds"),
    "min_data_in_leaf": _int(5, 5000, 100, "Minimum rows per leaf"),
    "feature_fraction": _num(0.1, 1.0, 0.8, "Features sampled per tree"),
    "bagging_fraction": _num(0.1, 1.0, 0.8, "Rows sampled per bagging round"),
    "bagging_freq": _int(0, 10, 1, "Bagging every k rounds (0 = off)"),
    "lambda_l2": _num(0.0, 100.0, 1.0, "L2 regularization"),
    "max_depth": _int(-1, 16, -1, "Maximum tree depth (-1 = unlimited)"),
    "early_stopping_rounds": _int(0, 500, 30, "Stop when the validation tail stops improving (0 = off, ML-007)"),
}


class NoSkill:
    """Predicts the training prior (mean or class rate): the baseline every run reports (ML-014)."""

    family = "no_skill"

    def __init__(self, task: str = "regression", **_: Any) -> None:
        self.task = task
        self.prior = 0.0
        self.curve: list[tuple[int, float, float]] = []
        self.curve_metric = "none"
        self.best_iteration = 0

    def fit(
        self,
        x: F64,
        y: F64,
        sample_weight: F64 | None = None,
        eval_set: EvalSet | None = None,
        groups: NDArray[Any] | None = None,
    ) -> NoSkill:
        w = sample_weight if sample_weight is not None else np.ones(y.shape[0])
        self.prior = float(np.sum(w * y) / max(np.sum(w), 1e-12)) if y.size else 0.0
        self.n_features = x.shape[1]
        return self

    def predict(self, x: F64) -> F64:
        return np.full(x.shape[0], self.prior)

    def predict_proba(self, x: F64) -> F64 | None:
        return self.predict(x) if self.task == "classification" else None

    def explain(self, x: F64) -> Explanation | None:
        return Explanation(np.zeros((x.shape[0], x.shape[1])), self.prior)

    def gain_importance(self) -> F64:
        return np.zeros(getattr(self, "n_features", 0))

    def interactions(self, features: list[str], top: int = 10) -> list[tuple[str, str, float]]:
        return []

    def state(self) -> dict[str, Any]:
        return {"prior": self.prior, "task": self.task, "n_features": getattr(self, "n_features", 0)}

    @classmethod
    def from_state(cls, s: dict[str, Any], hp: dict[str, Any]) -> NoSkill:
        m = cls(task=s["task"])
        m.prior = float(s["prior"])
        m.n_features = int(s["n_features"])
        return m


class Ridge:
    """Weighted ridge regression with an unpenalized intercept (closed form)."""

    family = "ridge"
    task = "regression"

    def __init__(self, alpha: float = 1.0, **_: Any) -> None:
        self.alpha = float(alpha)
        self.coef = np.zeros(0)
        self.intercept = 0.0
        self.curve: list[tuple[int, float, float]] = []
        self.curve_metric = "ic"
        self.best_iteration = 1

    def fit(
        self,
        x: F64,
        y: F64,
        sample_weight: F64 | None = None,
        eval_set: EvalSet | None = None,
        groups: NDArray[Any] | None = None,
    ) -> Ridge:
        w = sample_weight if sample_weight is not None else np.ones(y.shape[0])
        sw = max(float(w.sum()), 1e-12)
        xm = (w[:, None] * x).sum(0) / sw
        ym = float((w * y).sum() / sw)
        xc, yc = x - xm, y - ym
        a = (xc * w[:, None]).T @ xc + self.alpha * np.eye(x.shape[1])
        self.coef = np.linalg.solve(a, (xc * w[:, None]).T @ yc)
        self.intercept = ym - float(xm @ self.coef)
        val = metrics.ic(self.predict(eval_set.x), eval_set.y) if eval_set is not None else math.nan
        self.curve = [(1, metrics.ic(self.predict(x), y), val)]
        return self

    def predict(self, x: F64) -> F64:
        out: F64 = x @ self.coef + self.intercept
        return out

    def predict_proba(self, x: F64) -> F64 | None:
        return None

    def explain(self, x: F64) -> Explanation | None:
        return Explanation(x * self.coef, self.intercept)

    def gain_importance(self) -> F64:
        return np.abs(self.coef)

    def interactions(self, features: list[str], top: int = 10) -> list[tuple[str, str, float]]:
        return []

    def state(self) -> dict[str, Any]:
        return {"coef": self.coef.tolist(), "intercept": self.intercept}

    @classmethod
    def from_state(cls, s: dict[str, Any], hp: dict[str, Any]) -> Ridge:
        m = cls(**hp)
        m.coef = np.asarray(s["coef"], dtype=np.float64)
        m.intercept = float(s["intercept"])
        return m


class Logistic:
    """L2-regularized logistic regression by Newton's method (the linear filter for meta-labels)."""

    family = "logistic"
    task = "classification"

    def __init__(self, alpha: float = 1.0, max_iter: int = 50, **_: Any) -> None:
        self.alpha, self.max_iter = float(alpha), int(max_iter)
        self.coef = np.zeros(0)
        self.intercept = 0.0
        self.curve: list[tuple[int, float, float]] = []
        self.curve_metric = "auc"
        self.best_iteration = 0

    def _p(self, x: F64) -> F64:
        z = np.clip(x @ self.coef + self.intercept, -35, 35)
        out: F64 = 1.0 / (1.0 + np.exp(-z))
        return out

    def fit(
        self,
        x: F64,
        y: F64,
        sample_weight: F64 | None = None,
        eval_set: EvalSet | None = None,
        groups: NDArray[Any] | None = None,
    ) -> Logistic:
        n, k = x.shape
        w = sample_weight if sample_weight is not None else np.ones(n)
        xa = np.column_stack([np.ones(n), x])
        beta = np.zeros(k + 1)
        pen = self.alpha * np.eye(k + 1)
        pen[0, 0] = 0.0
        for it in range(self.max_iter):
            p = 1.0 / (1.0 + np.exp(-np.clip(xa @ beta, -35, 35)))
            g = xa.T @ (w * (p - y)) + pen @ beta
            h = (xa * (w * p * (1 - p))[:, None]).T @ xa + pen + 1e-9 * np.eye(k + 1)
            step = np.linalg.solve(h, g)
            beta -= step
            self.best_iteration = it + 1
            if float(np.max(np.abs(step))) < 1e-8:
                break
        self.intercept, self.coef = float(beta[0]), beta[1:]
        val = metrics.auc(self._p(eval_set.x), eval_set.y) if eval_set is not None else math.nan
        self.curve = [(self.best_iteration, metrics.auc(self._p(x), y), val)]
        return self

    def predict(self, x: F64) -> F64:
        return self._p(x)

    def predict_proba(self, x: F64) -> F64 | None:
        return self._p(x)

    def explain(self, x: F64) -> Explanation | None:
        return Explanation(x * self.coef, self.intercept)

    def gain_importance(self) -> F64:
        return np.abs(self.coef)

    def interactions(self, features: list[str], top: int = 10) -> list[tuple[str, str, float]]:
        return []

    def state(self) -> dict[str, Any]:
        return {"coef": self.coef.tolist(), "intercept": self.intercept}

    @classmethod
    def from_state(cls, s: dict[str, Any], hp: dict[str, Any]) -> Logistic:
        m = cls(**{k: v for k, v in hp.items() if k in ("alpha", "max_iter")})
        m.coef = np.asarray(s["coef"], dtype=np.float64)
        m.intercept = float(s["intercept"])
        return m


class _LGBM:
    family = "lgbm"
    task = "regression"
    objective = "regression"
    metric = "l2"

    def __init__(self, seed: int = 7, threads: int = 2, **hp: Any) -> None:
        self.hp = hp
        self.seed, self.threads = int(seed), int(threads)
        self.booster: lgb.Booster | None = None
        self.curve: list[tuple[int, float, float]] = []
        self.curve_metric = self.metric
        self.best_iteration = 0

    def _params(self) -> dict[str, Any]:
        hp = self.hp
        p: dict[str, Any] = {
            "objective": self.objective,
            "metric": self.metric,
            "num_leaves": int(hp["num_leaves"]),
            "learning_rate": float(hp["learning_rate"]),
            "min_data_in_leaf": int(hp["min_data_in_leaf"]),
            "feature_fraction": float(hp["feature_fraction"]),
            "bagging_fraction": float(hp["bagging_fraction"]),
            "bagging_freq": int(hp["bagging_freq"]),
            "lambda_l2": float(hp["lambda_l2"]),
            "max_depth": int(hp["max_depth"]),
            "seed": self.seed,
            "bagging_seed": self.seed,
            "feature_fraction_seed": self.seed,
            "data_random_seed": self.seed,
            "deterministic": True,
            "force_row_wise": True,
            "num_threads": self.threads,
            "verbosity": -1,
        }
        return p

    def _label(self, y: F64) -> F64:
        return y

    def _dataset(self, x: F64, y: F64, w: F64 | None, groups: NDArray[Any] | None) -> lgb.Dataset:
        return lgb.Dataset(x, label=self._label(y), weight=w, free_raw_data=False)

    def fit(
        self,
        x: F64,
        y: F64,
        sample_weight: F64 | None = None,
        eval_set: EvalSet | None = None,
        groups: NDArray[Any] | None = None,
    ) -> _LGBM:
        dtrain = self._dataset(x, y, sample_weight, groups)
        valid_sets, names = [dtrain], ["train"]
        if eval_set is not None and eval_set.x.shape[0] > 0:
            valid_sets.append(self._dataset(eval_set.x, eval_set.y, None, eval_set.groups).set_reference(dtrain))
            names.append("valid")
        rec: dict[str, dict[str, list[float]]] = {}
        callbacks: list[Callable[..., Any]] = [lgb.record_evaluation(rec)]
        es = int(self.hp.get("early_stopping_rounds", 0))
        if es > 0 and len(valid_sets) > 1:
            callbacks.append(lgb.early_stopping(es, first_metric_only=True, verbose=False))
        self.booster = lgb.train(
            self._params(),
            dtrain,
            num_boost_round=int(self.hp["n_estimators"]),
            valid_sets=valid_sets,
            valid_names=names,
            callbacks=callbacks,
        )
        self.best_iteration = int(self.booster.best_iteration or self.booster.current_iteration())
        key = next(iter(rec.get("train", {"": []})), "")
        tr = rec.get("train", {}).get(key, [])
        va = rec.get("valid", {}).get(key, [math.nan] * len(tr))
        self.curve = [(i + 1, float(a), float(b)) for i, (a, b) in enumerate(zip(tr, va, strict=False))]
        self.curve_metric = key or self.metric
        return self

    def _raw(self, x: F64) -> F64:
        assert self.booster is not None, "model not fitted"
        out: F64 = np.asarray(self.booster.predict(x, num_iteration=self.best_iteration), dtype=np.float64)
        return out

    def predict(self, x: F64) -> F64:
        return self._raw(x)

    def predict_proba(self, x: F64) -> F64 | None:
        return None

    def explain(self, x: F64) -> Explanation | None:
        assert self.booster is not None
        c = np.asarray(self.booster.predict(x, num_iteration=self.best_iteration, pred_contrib=True))
        return Explanation(c[:, :-1], float(c[0, -1]) if c.shape[0] else 0.0)

    def gain_importance(self) -> F64:
        assert self.booster is not None
        return np.asarray(
            self.booster.feature_importance(importance_type="gain", iteration=self.best_iteration), dtype=np.float64
        )

    def interactions(self, features: list[str], top: int = 10) -> list[tuple[str, str, float]]:
        """Parent-child split pairs weighted by the child's gain (a cheap interaction proxy)."""
        assert self.booster is not None
        dump = self.booster.dump_model(num_iteration=self.best_iteration)
        acc: dict[tuple[int, int], float] = {}

        def walk(node: dict[str, Any], parent: int | None) -> None:
            if "split_feature" not in node:
                return
            f = int(node["split_feature"])
            if parent is not None and parent != f:
                key = (min(parent, f), max(parent, f))
                acc[key] = acc.get(key, 0.0) + float(node.get("split_gain", 0.0))
            walk(node["left_child"], f)
            walk(node["right_child"], f)

        for t in dump.get("tree_info", []):
            walk(t["tree_structure"], None)
        pairs = sorted(acc.items(), key=lambda kv: -kv[1])[:top]
        return [(features[a], features[b], v) for (a, b), v in pairs]

    def state(self) -> dict[str, Any]:
        assert self.booster is not None
        return {"model": self.booster.model_to_string(), "best_iteration": self.best_iteration}

    @classmethod
    def from_state(cls, s: dict[str, Any], hp: dict[str, Any]) -> _LGBM:
        m = cls(**hp)
        m.booster = lgb.Booster(model_str=s["model"])
        m.best_iteration = int(s["best_iteration"])
        return m


class LGBMRegression(_LGBM):
    family = "lgbm_regression"
    task = "regression"
    objective = "regression"
    metric = "l2"


class LGBMBinary(_LGBM):
    family = "lgbm_binary"
    task = "classification"
    objective = "binary"
    metric = "binary_logloss"

    def predict_proba(self, x: F64) -> F64 | None:
        return self._raw(x)


class LGBMLambdaRank(_LGBM):
    """LambdaRank over per-date groups (swing default, D23); targets are per-date rank grades (ML-001)."""

    family = "lgbm_lambdarank"
    task = "ranking"
    objective = "lambdarank"
    metric = "ndcg"

    def _params(self) -> dict[str, Any]:
        p = super()._params()
        p["eval_at"] = [int(self.hp.get("ndcg_eval_at", 5))]
        p["label_gain"] = [float(2**i - 1) for i in range(int(self.hp.get("rank_buckets", 5)))]
        return p

    def _label(self, y: F64) -> F64:
        return rank_relevance(y, int(self.hp.get("rank_buckets", 5))).astype(np.float64)

    def _dataset(self, x: F64, y: F64, w: F64 | None, groups: NDArray[Any] | None) -> lgb.Dataset:
        if groups is None:
            raise ValueError("lambdarank needs per-date groups")
        g = np.asarray(groups)
        if np.any(g[1:] < g[:-1]):
            raise ValueError("lambdarank rows must be sorted by group")
        _, sizes = np.unique(g, return_counts=True)
        return lgb.Dataset(x, label=self._label(y), group=sizes, free_raw_data=False)


@dataclass(frozen=True)
class FamilyInfo:
    family: str
    tier: int
    task: str
    description: str
    schema: dict[str, Any]
    horizon: str
    cls: type
    filter_of: str = ""
    devices: tuple[str, ...] = ("cpu",)
    supports_gpu: bool = False
    cpu_threads_hint: int = 2
    memory_mb_hint: int = 512
    extra: dict[str, Any] = field(default_factory=dict)


_FILTER_PROPS = {
    "filter": {"type": "string", "enum": ["lgbm_binary", "logistic"], "default": "lgbm_binary"},
    "alpha": _num(1e-6, 1e4, 1.0, "L2 penalty of the logistic filter"),
    **LGBM_PROPS,
}

FAMILIES: dict[str, FamilyInfo] = {
    f.family: f
    for f in (
        FamilyInfo(
            "no_skill",
            1,
            "regression",
            "Prior-only baseline reported beside every run",
            _schema({}, "no_skill"),
            "both",
            NoSkill,
        ),
        FamilyInfo(
            "ridge",
            1,
            "regression",
            "Ridge regression on fold-standardized features (always-on linear baseline)",
            _schema({"alpha": _num(1e-6, 1e4, 1.0, "L2 penalty")}, "ridge"),
            "both",
            Ridge,
            cpu_threads_hint=1,
            memory_mb_hint=256,
        ),
        FamilyInfo(
            "logistic",
            1,
            "classification",
            "L2 logistic regression (binary labels)",
            _schema(
                {"alpha": _num(1e-6, 1e4, 1.0, "L2 penalty"), "max_iter": _int(5, 500, 50, "Newton iterations")},
                "logistic",
            ),
            "both",
            Logistic,
            cpu_threads_hint=1,
            memory_mb_hint=256,
        ),
        FamilyInfo(
            "lgbm_regression",
            1,
            "regression",
            "LightGBM regression on volatility-scaled residual returns (intraday default, D23)",
            _schema(dict(LGBM_PROPS), "lgbm_regression"),
            "intraday",
            LGBMRegression,
            cpu_threads_hint=4,
            memory_mb_hint=1024,
        ),
        FamilyInfo(
            "lgbm_lambdarank",
            1,
            "ranking",
            "LightGBM LambdaRank over per-date groups (swing default, D23)",
            _schema(
                {
                    **LGBM_PROPS,
                    "ndcg_eval_at": _int(1, 50, 5, "NDCG cutoff for early stopping"),
                    "rank_buckets": _int(2, 10, 5, "Relevance grades from per-date ranks"),
                },
                "lgbm_lambdarank",
            ),
            "swing",
            LGBMLambdaRank,
            cpu_threads_hint=4,
            memory_mb_hint=1024,
        ),
        FamilyInfo(
            "lgbm_binary",
            1,
            "classification",
            "LightGBM binary classifier",
            _schema(dict(LGBM_PROPS), "lgbm_binary"),
            "both",
            LGBMBinary,
            cpu_threads_hint=4,
            memory_mb_hint=1024,
        ),
        FamilyInfo(
            "orb_meta",
            2,
            "rule_filter",
            "Opening-range breakout with an ML filter on meta-labels (intraday challenger, D23)",
            _schema(dict(_FILTER_PROPS), "orb_meta"),
            "intraday",
            LGBMBinary,
            filter_of="orb",
            cpu_threads_hint=2,
        ),
        FamilyInfo(
            "residual_reversal_meta",
            2,
            "rule_filter",
            "Residual reversal (swing) with an ML filter on meta-labels (D23)",
            _schema(dict(_FILTER_PROPS), "residual_reversal_meta"),
            "swing",
            LGBMBinary,
            filter_of="residual_reversal",
            cpu_threads_hint=2,
        ),
    )
}


def info(family: str) -> FamilyInfo:
    if family not in FAMILIES:
        raise ValueError(f"unknown model family {family!r}")
    return FAMILIES[family]


def make(family: str, hp: dict[str, Any], seed: int = 7, threads: int = 2, task: str | None = None) -> Model:
    """Instantiate a family's model with coerced hyperparameters (defaults filled)."""
    fi = info(family)
    full = coerce(fi.schema, hp)
    if fi.task == "rule_filter":
        if full.get("filter") == "logistic":
            return Logistic(alpha=full.get("alpha", 1.0))
        return LGBMBinary(seed=seed, threads=threads, **{k: v for k, v in full.items() if k in LGBM_PROPS})
    if fi.cls is NoSkill:
        return NoSkill(task=task or "regression")
    if issubclass(fi.cls, _LGBM):
        model: Model = fi.cls(seed=seed, threads=threads, **full)
        return model
    built: Model = fi.cls(**full)  # type: ignore[assignment]
    return built


def model_task(family: str) -> str:
    fi = info(family)
    return "classification" if fi.task == "rule_filter" else fi.task


def restore(family: str, hp: dict[str, Any], state: dict[str, Any], kind: str, seed: int, threads: int) -> Model:
    """Rebuild a fitted model from ``state()`` (``kind`` is the concrete class family)."""
    fi = info(family)
    full = coerce(fi.schema, hp)
    classes: dict[str, Any] = {
        "no_skill": NoSkill,
        "ridge": Ridge,
        "logistic": Logistic,
        "lgbm_regression": LGBMRegression,
        "lgbm_binary": LGBMBinary,
        "lgbm_lambdarank": LGBMLambdaRank,
    }
    cls = classes[kind]
    if issubclass(cls, _LGBM):
        lgbm_hp = {k: v for k, v in full.items() if k in LGBM_PROPS or k in ("ndcg_eval_at", "rank_buckets")}
        restored: Model = cls.from_state(state, {"seed": seed, "threads": threads, **lgbm_hp})
        return restored
    out: Model = cls.from_state(state, {k: v for k, v in full.items() if k in ("alpha", "max_iter")})
    return out


def save_json(path: str | Path, payload: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(payload, sort_keys=True))
