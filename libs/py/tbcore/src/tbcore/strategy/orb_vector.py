"""Vectorized opening-range breakout signals for the screening tier (BT-006, BT-025).

Mirrors ``OpeningRangeBreakout`` (``orb.py``) on aligned bar matrices: same opening range, breakout
rule, stop and target rounding, entry cutoff, and session exit. It exists only for parameter
screening; the event-driven tier always runs ``orb.py`` itself, and ``test_backtest_vectorized``
checks that both produce the same entries, stops, and targets.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np

from tbcore.backtest.vectorized import EntrySignals, VectorData
from tbcore.strategy.orb import ORBParams

NS = 1_000_000_000


def orb_signals(d: VectorData, params: Mapping[str, Any]) -> EntrySignals:
    p = ORBParams.from_dict(dict(params))
    t_n, n_n = d.close.shape
    entry = np.zeros((t_n, n_n), dtype=bool)
    exit_ = np.zeros((t_n, n_n), dtype=bool)
    stop = np.full((t_n, n_n), np.nan)
    target = np.full((t_n, n_n), np.nan)
    buf = 1.0 - float(p.stop_buffer_bps) / 10_000.0
    r = float(p.target_r)
    k = p.opening_bars
    for s in range(int(d.session.max()) + 1 if d.session.size else 0):
        rows = np.flatnonzero(d.session == s)
        if rows.size <= k:
            continue
        cutoff = d.session_close[rows[0]] - p.exit_minutes_before_close * 60 * NS
        in_range = rows[d.bar_in_session[rows] < k]
        orh = np.max(d.high[in_range], axis=0)
        orl = np.min(d.low[in_range], axis=0)
        late = d.ts_close[rows] >= cutoff
        cand = rows[(d.bar_in_session[rows] >= k) & ~late]
        stp_all = np.floor(np.round(orl * buf * 100, 6)) / 100
        # A breakout whose stop is not below the close is skipped without using a daily trade (orb.py).
        brk = (d.close[cand] > orh) & (stp_all < d.close[cand])
        cum = np.cumsum(brk, axis=0)
        first = brk & (cum <= p.max_trades_per_day)
        tt, nn = np.nonzero(first)
        tr = cand[tt]
        ref = d.close[tr, nn]
        stp = stp_all[nn]
        entry[tr, nn] = True
        stop[tr, nn] = stp
        target[tr, nn] = np.ceil(np.round((ref + r * (ref - stp)) * 100, 6)) / 100
        exit_rows = rows[late]
        if exit_rows.size:
            exit_[exit_rows[0], :] = True
    return EntrySignals(entry=entry, stop=stop, target=target, exit=exit_)
