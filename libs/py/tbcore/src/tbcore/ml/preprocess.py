"""Fold-fitted preprocessing persisted with each model (ML-006): winsorize at the training fold's
1st/99th percentiles, impute the training median, standardize with the training mean and std."""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

F64 = NDArray[np.float64]


@dataclass
class Pipeline:
    features: list[str]
    lo: F64
    hi: F64
    median: F64
    mean: F64
    std: F64

    @classmethod
    def fit(cls, features: list[str], x: F64) -> Pipeline:
        """Fit on training rows only (never call with test rows, ML-006)."""
        with np.errstate(all="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN columns (e.g. an unmapped sector) impute to 0
            lo = np.nanpercentile(x, 1, axis=0) if x.shape[0] else np.zeros(x.shape[1])
            hi = np.nanpercentile(x, 99, axis=0) if x.shape[0] else np.zeros(x.shape[1])
            med = np.nanmedian(x, axis=0) if x.shape[0] else np.zeros(x.shape[1])
        lo, hi, med = (np.nan_to_num(a, nan=0.0) for a in (lo, hi, med))
        z = np.clip(np.where(np.isnan(x), med, x), lo, hi)
        mean = z.mean(axis=0) if z.shape[0] else np.zeros(x.shape[1])
        std = z.std(axis=0) if z.shape[0] else np.ones(x.shape[1])
        std = np.where(std > 1e-12, std, 1.0)
        return cls(list(features), lo, hi, med, mean, std)

    def transform(self, x: F64) -> F64:
        z = np.clip(np.where(np.isnan(x), self.median, x), self.lo, self.hi)
        out: F64 = (z - self.mean) / self.std
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "features": self.features,
            "lo": self.lo.tolist(),
            "hi": self.hi.tolist(),
            "median": self.median.tolist(),
            "mean": self.mean.tolist(),
            "std": self.std.tolist(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Pipeline:
        return cls(
            list(d["features"]),
            *(np.asarray(d[k], dtype=np.float64) for k in ("lo", "hi", "median", "mean", "std")),
        )
