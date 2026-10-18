"""Model bundle: the persisted pipeline + model + feature-set identity used by inference (ML-040, ML-041).

``bundle.json`` holds everything needed to score a feature vector; ``bundle.sha256`` its checksum,
verified on load (ML-I2). ``check_features`` fails closed when the online engine's feature set does not
match the one the model was trained on.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from numpy.typing import NDArray

from tbcore.ml.models import Model, model_task, restore
from tbcore.ml.preprocess import Pipeline

F64 = NDArray[np.float64]


def feature_vector_id(values: list[float | None]) -> str:
    """ID of the exact feature vector behind a signal (ML-042): hash of the values at 12 significant digits."""
    text = ",".join("nan" if v is None or v != v else f"{v:.12g}" for v in values)
    return "fv_" + hashlib.sha256(text.encode()).hexdigest()[:24]


class FeatureMismatchError(RuntimeError):
    """The online feature set differs from the model's (ML-040): no signal is produced."""


@dataclass
class ModelBundle:
    family: str
    kind: str
    hyperparams: dict[str, Any]
    seed: int
    threads: int
    pipeline: Pipeline
    model: Model
    feature_set_id: str
    feature_set_hash: str
    features: list[str]
    timeframe: str
    benchmark: str
    threshold: float
    horizon: int
    label_kind: str
    primary: dict[str, Any] = field(default_factory=dict)
    run_id: str = ""
    model_version: str = ""

    @property
    def task(self) -> str:
        return model_task(self.family)

    def check_features(self, feature_set_hash: str, features: list[str], timeframe: str) -> None:
        if feature_set_hash != self.feature_set_hash or timeframe != self.timeframe:
            raise FeatureMismatchError(
                f"model {self.model_version or self.run_id} wants {self.feature_set_id} ({self.feature_set_hash[:12]}) "
                f"on {self.timeframe}; got {feature_set_hash[:12]} on {timeframe}"
            )
        missing = [f for f in self.features if f not in features]
        if missing:
            raise FeatureMismatchError(f"features missing online: {missing}")

    def score_matrix(self, x: F64) -> F64:
        z = self.pipeline.transform(x)
        p = self.model.predict_proba(z) if self.task == "classification" else None
        return p if p is not None else self.model.predict(z)

    def score_frame(self, frame: pl.DataFrame) -> F64:
        x = frame.select([pl.col(f).cast(pl.Float64) for f in self.features]).to_numpy()
        return self.score_matrix(x)

    def to_json(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "kind": self.kind,
            "hyperparams": self.hyperparams,
            "seed": self.seed,
            "threads": self.threads,
            "pipeline": self.pipeline.to_dict(),
            "model": self.model.state(),
            "feature_set_id": self.feature_set_id,
            "feature_set_hash": self.feature_set_hash,
            "features": self.features,
            "timeframe": self.timeframe,
            "benchmark": self.benchmark,
            "threshold": self.threshold,
            "horizon": self.horizon,
            "label_kind": self.label_kind,
            "primary": self.primary,
            "run_id": self.run_id,
            "model_version": self.model_version,
        }

    def save(self, directory: str | Path) -> str:
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(self.to_json(), sort_keys=True).encode()
        digest = hashlib.sha256(raw).hexdigest()
        (d / "bundle.json").write_bytes(raw)
        (d / "bundle.sha256").write_text(digest)
        return digest

    @classmethod
    def from_json(cls, j: dict[str, Any]) -> ModelBundle:
        model = restore(j["family"], j["hyperparams"], j["model"], j["kind"], int(j["seed"]), int(j["threads"]))
        return cls(
            family=j["family"],
            kind=j["kind"],
            hyperparams=j["hyperparams"],
            seed=int(j["seed"]),
            threads=int(j["threads"]),
            pipeline=Pipeline.from_dict(j["pipeline"]),
            model=model,
            feature_set_id=j["feature_set_id"],
            feature_set_hash=j["feature_set_hash"],
            features=list(j["features"]),
            timeframe=j["timeframe"],
            benchmark=j["benchmark"],
            threshold=float(j["threshold"]),
            horizon=int(j["horizon"]),
            label_kind=j["label_kind"],
            primary=dict(j.get("primary", {})),
            run_id=j.get("run_id", ""),
            model_version=j.get("model_version", ""),
        )

    @classmethod
    def load(cls, directory: str | Path) -> ModelBundle:
        d = Path(directory)
        raw = (d / "bundle.json").read_bytes()
        want = (d / "bundle.sha256").read_text().strip()
        if hashlib.sha256(raw).hexdigest() != want:
            raise ValueError(f"bundle checksum mismatch in {d} (ML-I2)")
        return cls.from_json(json.loads(raw))
