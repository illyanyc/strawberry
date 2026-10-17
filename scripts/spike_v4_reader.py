#!/usr/bin/env python3
"""Spike V4 native reader (macOS host): repeatedly read every visible Parquet file the containerized
writer (`marketdata spike v4 --role writer`) produces across VirtioFS, and count partial reads.

Usage: uv run python scripts/spike_v4_reader.py <dir> <seconds>
Prints one JSON line: {"role": "native-reader", "reads": n, "ok": n, "vanished": n, "partial": n}.
"""

from __future__ import annotations

import json
import os
import sys
import time

import polars as pl

ROWS = 20000  # must match services/marketdata/internal/spikes.V4Rows


def main() -> int:
    d, secs = sys.argv[1], float(sys.argv[2])
    stats = {"role": "native-reader", "reads": 0, "ok": 0, "vanished": 0, "partial": 0, "detail": ""}
    deadline = time.time() + secs
    while time.time() < deadline:
        try:
            names = os.listdir(d)
        except FileNotFoundError:
            time.sleep(0.1)
            continue
        for n in names:
            if n.startswith(".") or not n.endswith(".parquet"):
                continue
            stats["reads"] += 1
            try:
                df = pl.read_parquet(os.path.join(d, n))
            except FileNotFoundError:
                stats["vanished"] += 1
                continue
            except Exception as e:  # any parse failure is a partial read
                stats["partial"] += 1
                stats["detail"] = f"{n}: {e}"
                continue
            if df.height != ROWS or df["seq"].n_unique() != 1:
                stats["partial"] += 1
                stats["detail"] = f"{n}: {df.height} rows"
            else:
                stats["ok"] += 1
    print(json.dumps(stats))
    return 1 if stats["partial"] else 0


if __name__ == "__main__":
    sys.exit(main())
