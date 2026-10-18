"""Named feature sets for training and their feature groups (ML-004, ML-025; DF-003).

Every feature comes from the shared registry (``tbcore.features.library``), so training, backtests,
and live inference compute it with one code path (DF-004). Groups name the units an ablation drops
or adds.
"""

from __future__ import annotations

from dataclasses import dataclass

from tbcore.features.core import FeatureSet
from tbcore.features.library import MVP_FEATURES

GROUPS: dict[str, tuple[str, ...]] = {
    "returns": ("ret_1", "ret_4", "ret_16"),
    "volatility": ("vol_16", "atr_14_pct"),
    "oscillators": ("rsi_14",),
    "volume": ("volume_z_20",),
    "session": ("vwap_dist", "gap_prev_close"),
    "calendar": ("tod_sin", "tod_cos", "dow_sin", "dow_cos"),
    "market": ("mkt_ret_4", "sector_ret_4"),
    "cross_sectional": ("xs_rank_ret_4",),
}

_CORE_INTRADAY = [
    "ret_1@1",
    "ret_4@1",
    "ret_16@1",
    "vol_16@1",
    "rsi_14@1",
    "volume_z_20@1",
    "tod_sin@1",
    "tod_cos@1",
    "mkt_ret_4@1",
    "xs_rank_ret_4@1",
]
_SWING_DAILY = [
    "ret_1@1",
    "ret_4@1",
    "ret_16@1",
    "vol_16@1",
    "rsi_14@1",
    "atr_14_pct@1",
    "volume_z_20@1",
    "dow_sin@1",
    "dow_cos@1",
    "mkt_ret_4@1",
    "xs_rank_ret_4@1",
]


@dataclass(frozen=True)
class FeatureSetDef:
    name: str
    version: int
    keys: tuple[str, ...]
    timeframes: tuple[str, ...]
    description: str

    def resolve(self, market_instrument_id: str) -> FeatureSet:
        return FeatureSet.of(self.name, self.version, list(self.keys), context={"market": market_instrument_id})


FEATURE_SETS: dict[str, FeatureSetDef] = {
    d.name: d
    for d in (
        FeatureSetDef("mvp_intraday", 1, tuple(MVP_FEATURES), ("15m",), "Section 03 MVP set: 16 intraday features"),
        FeatureSetDef(
            "core_intraday", 1, tuple(_CORE_INTRADAY), ("15m",), "Returns, volatility, RSI, volume, time of day, market"
        ),
        FeatureSetDef(
            "swing_daily",
            1,
            tuple(_SWING_DAILY),
            ("1d",),
            "Daily returns, volatility, RSI, ATR, volume, weekday, market",
        ),
    )
}


def get(name: str, version: int = 0) -> FeatureSetDef:
    d = FEATURE_SETS.get(name)
    if d is None or (version and d.version != version):
        raise ValueError(f"unknown feature set {name}@{version or 'latest'}")
    return d


def group_of(feature: str) -> str:
    for g, names in GROUPS.items():
        if feature in names:
            return g
    return "other"


def drop_groups(features: list[str], groups: list[str]) -> list[str]:
    unknown = [g for g in groups if g not in GROUPS]
    if unknown:
        raise ValueError(f"unknown feature groups {unknown}")
    banned = {f for g in groups for f in GROUPS[g]}
    return [f for f in features if f not in banned]
