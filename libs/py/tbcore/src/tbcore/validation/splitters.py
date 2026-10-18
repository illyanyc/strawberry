"""Time-series validation splitters with purging and embargo (section 08, ADR-0030, BT-030..BT-034, SM-002).

Every sample ``i`` has a decision time ``t0[i]`` and a label end time ``t1[i] >= t0[i]`` (the last
instant whose data the label uses). Splits are built over *units*: single samples, or eras
(``groups``, e.g. the session date for swing books) so an era is never split between train and test.

* **Purging** removes a training sample whose label window ``[t0, t1]`` overlaps a test block's
  span ``[min t0, max t1]`` (BT-031).
* **Embargo** additionally removes training samples whose decision time falls within ``embargo``
  nanoseconds after a test block ends (BT-032); for walk-forward (train before test) it is a gap:
  training labels must end at least ``embargo`` before the test block starts.

Random K-fold is deliberately absent (SM-002).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from itertools import combinations
from math import comb

import numpy as np
from numpy.typing import ArrayLike, NDArray

I64 = NDArray[np.int64]


@dataclass(frozen=True)
class Split:
    """One train/test split; indices are sample positions. ``test_groups`` lists CPCV group IDs."""

    fold: int
    train: I64
    test: I64
    test_groups: tuple[int, ...] = field(default_factory=tuple)


def _times(t0: ArrayLike, t1: ArrayLike | None) -> tuple[I64, I64]:
    a = (
        np.asarray(t0).astype("datetime64[ns]").astype(np.int64)
        if np.asarray(t0).dtype.kind == "M"
        else np.asarray(t0, dtype=np.int64)
    )
    if t1 is None:
        b = a.copy()
    else:
        b = (
            np.asarray(t1).astype("datetime64[ns]").astype(np.int64)
            if np.asarray(t1).dtype.kind == "M"
            else np.asarray(t1, dtype=np.int64)
        )
    if a.shape != b.shape:
        raise ValueError("t0 and label_end_times must have the same length")
    if np.any(b < a):
        raise ValueError("label end time before decision time (BT-030)")
    if np.any(np.diff(a) < 0):
        raise ValueError("samples must be sorted by decision time")
    return a, b


def _units(n: int, groups: ArrayLike | None) -> list[I64]:
    """Sample positions of each unit in time order (units = samples, or contiguous eras)."""
    if groups is None:
        return [np.array([i], dtype=np.int64) for i in range(n)]
    g = np.asarray(groups)
    if g.shape[0] != n:
        raise ValueError("groups must have one value per sample")
    starts = np.flatnonzero(np.r_[True, g[1:] != g[:-1]])
    ends = np.r_[starts[1:], n]
    if len(set(g[starts].tolist())) != len(starts):
        raise ValueError("eras must be contiguous in time order")
    return [np.arange(s, e, dtype=np.int64) for s, e in zip(starts, ends, strict=True)]


def purge(train: I64, test_blocks: Sequence[I64], t0: I64, t1: I64, embargo: int = 0) -> I64:
    """Drop training samples that overlap a test block's label span or fall in its embargo (BT-031, BT-032)."""
    keep = np.ones(train.shape[0], dtype=bool)
    tr0, tr1 = t0[train], t1[train]
    for blk in test_blocks:
        if blk.size == 0:
            continue
        start, end = t0[blk].min(), t1[blk].max()
        overlap = (tr0 <= end) & (tr1 >= start)
        embargoed = (tr0 > end) & (tr0 <= end + embargo)
        keep &= ~(overlap | embargoed)
    return train[keep]


def _blocks(test: I64) -> list[I64]:
    """Contiguous runs of test positions (each purged/embargoed separately)."""
    if test.size == 0:
        return []
    cuts = np.flatnonzero(np.diff(test) != 1) + 1
    return [b for b in np.split(test, cuts)]


class PurgedKFold:
    """K contiguous test folds over time; training excludes purged and embargoed samples (BT-033)."""

    def __init__(self, n_splits: int, embargo: int = 0, label_end_times: ArrayLike | None = None) -> None:
        if n_splits < 2:
            raise ValueError("n_splits must be >= 2")
        self.n_splits, self.embargo, self.label_end_times = n_splits, int(embargo), label_end_times

    def split(self, t0: ArrayLike, groups: ArrayLike | None = None) -> Iterator[Split]:
        a, b = _times(t0, self.label_end_times)
        units = _units(a.shape[0], groups)
        if len(units) < self.n_splits:
            raise ValueError("fewer units than folds")
        allpos = np.arange(a.shape[0], dtype=np.int64)
        for k, chunk in enumerate(np.array_split(np.arange(len(units)), self.n_splits)):
            test = np.concatenate([units[u] for u in chunk])
            train = np.setdiff1d(allpos, test, assume_unique=True)
            yield Split(k, purge(train, [test], a, b, self.embargo), test)


class WalkForward:
    """Rolling (``anchored=False``) or expanding (``anchored=True``) walk-forward (BT-034).

    ``train_len``, ``test_len`` and ``step`` count units (samples or eras). Test windows never
    overlap when ``step >= test_len``, so their out-of-sample results can be stitched.
    """

    def __init__(
        self,
        train_len: int,
        test_len: int,
        step: int | None = None,
        anchored: bool = False,
        embargo: int = 0,
        label_end_times: ArrayLike | None = None,
    ) -> None:
        if train_len < 1 or test_len < 1:
            raise ValueError("train_len and test_len must be >= 1")
        self.train_len, self.test_len = train_len, test_len
        self.step = step or test_len
        self.anchored, self.embargo, self.label_end_times = anchored, int(embargo), label_end_times

    def split(self, t0: ArrayLike, groups: ArrayLike | None = None) -> Iterator[Split]:
        a, b = _times(t0, self.label_end_times)
        units = _units(a.shape[0], groups)
        start, fold = self.train_len, 0
        while start + self.test_len <= len(units):
            lo = 0 if self.anchored else start - self.train_len
            train = np.concatenate([units[u] for u in range(lo, start)])
            test = np.concatenate([units[u] for u in range(start, start + self.test_len)])
            # Training labels must end before the test block starts, with the embargo as a gap.
            keep = b[train] < a[test].min() - self.embargo
            yield Split(fold, train[keep], test)
            start += self.step
            fold += 1


class CombinatorialPurgedCV:
    """Combinatorial purged cross-validation (López de Prado 2018, ch. 12) (BT-033).

    Units are cut into ``n_groups`` contiguous groups; every combination of ``n_test_groups``
    groups is one test set (``C(N, k)`` splits). Each group appears in ``C(N-1, k-1)`` test sets,
    which assemble into that many full out-of-sample paths.
    """

    def __init__(
        self, n_groups: int, n_test_groups: int, embargo: int = 0, label_end_times: ArrayLike | None = None
    ) -> None:
        if not 1 <= n_test_groups < n_groups:
            raise ValueError("need 1 <= n_test_groups < n_groups")
        self.n_groups, self.n_test_groups = n_groups, n_test_groups
        self.embargo, self.label_end_times = int(embargo), label_end_times
        self.combos = list(combinations(range(n_groups), n_test_groups))

    @property
    def n_splits(self) -> int:
        return comb(self.n_groups, self.n_test_groups)

    @property
    def n_paths(self) -> int:
        return comb(self.n_groups - 1, self.n_test_groups - 1)

    def groups_of(self, n_units: int) -> list[NDArray[np.int64]]:
        return [g.astype(np.int64) for g in np.array_split(np.arange(n_units), self.n_groups)]

    def split(self, t0: ArrayLike, groups: ArrayLike | None = None) -> Iterator[Split]:
        a, b = _times(t0, self.label_end_times)
        units = _units(a.shape[0], groups)
        if len(units) < self.n_groups:
            raise ValueError("fewer units than groups")
        gunits = self.groups_of(len(units))
        gpos = [np.concatenate([units[u] for u in g]) for g in gunits]
        allpos = np.arange(a.shape[0], dtype=np.int64)
        for k, combo in enumerate(self.combos):
            test = np.sort(np.concatenate([gpos[g] for g in combo]))
            train = np.setdiff1d(allpos, test, assume_unique=True)
            yield Split(k, purge(train, _blocks(test), a, b, self.embargo), test, combo)

    def paths(self) -> list[list[tuple[int, int]]]:
        """Path assembly: ``paths()[p]`` lists ``(group, split_index)`` covering every group once."""
        used = {g: 0 for g in range(self.n_groups)}
        out: list[list[tuple[int, int]]] = [[] for _ in range(self.n_paths)]
        for k, combo in enumerate(self.combos):
            for g in combo:
                out[used[g]].append((g, k))
                used[g] += 1
        return out

    def oos_paths(
        self, t0: ArrayLike, test_values: Sequence[ArrayLike], groups: ArrayLike | None = None
    ) -> NDArray[np.float64]:
        """Assemble per-split out-of-sample values (aligned with each split's ``test``) into an
        ``(n_paths, n_samples)`` matrix of full out-of-sample paths."""
        a, _ = _times(t0, self.label_end_times)
        splits = list(self.split(t0, groups))
        if len(test_values) != len(splits):
            raise ValueError("one value array per split is required")
        units = _units(a.shape[0], groups)
        gpos = [np.concatenate([units[u] for u in g]) for g in self.groups_of(len(units))]
        out = np.full((self.n_paths, a.shape[0]), np.nan)
        for p, members in enumerate(self.paths()):
            for g, k in members:
                vals = np.asarray(test_values[k], dtype=np.float64)
                where = np.searchsorted(splits[k].test, gpos[g])
                out[p, gpos[g]] = vals[where]
        return out


def stitch_walk_forward(
    splits: Sequence[Split], test_values: Sequence[ArrayLike], n_samples: int
) -> NDArray[np.float64]:
    """Stitch walk-forward out-of-sample values into one series (NaN where no fold tested)."""
    out = np.full(n_samples, np.nan)
    for s, v in zip(splits, test_values, strict=True):
        if np.any(np.isfinite(out[s.test])):
            raise ValueError("overlapping walk-forward test windows cannot be stitched")
        out[s.test] = np.asarray(v, dtype=np.float64)
    return out
