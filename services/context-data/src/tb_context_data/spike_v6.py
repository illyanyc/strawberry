"""Spike V6: does FMP ``/stable`` expose earnings report timing (BMO/AMC), and is a real-time
WebSocket entitled on the owner's plan?

Probes, for known reports, ``/stable/earnings-calendar`` with and without
``includeReportTimes=true`` and the per-symbol ``/stable/earnings``, and records which timing fields
appear and their values. The WebSocket question is recorded as not depended on (research 04 §4.3):
the platform never uses FMP real time. Output never contains the key (SE-012).
"""

from __future__ import annotations

from typing import Any

import httpx

from tbcore.redact import redact

# Known reports (date, expected timing) used to validate whatever timing field FMP returns.
PROBES = [
    ("AAPL", "2026-07-30", "amc"),
    ("MSFT", "2026-07-28", "amc"),
    ("JPM", "2026-07-14", "bmo"),
    ("NVDA", "2026-08-26", "amc"),
]
TIMING_KEYS = ("time", "when", "reportTime", "timing", "hour")


def _timing_fields(rows: Any) -> dict[str, list[str]]:
    seen: dict[str, list[str]] = {}
    for r in rows if isinstance(rows, list) else []:
        for k in TIMING_KEYS:
            if isinstance(r, dict) and k in r:
                seen.setdefault(k, []).append(str(r[k]))
    return seen


async def run_spike_v6(
    key: str, base: str = "https://financialmodelingprep.com", transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any]:
    if not key:
        return {
            "summary": {
                "status": "skipped",
                "reason": "fmp_api_key not set; write it to .secrets/fmp_api_key and rerun make spike-v6",
            }
        }
    out: dict[str, Any] = {"probes": []}
    async with httpx.AsyncClient(base_url=base, timeout=30, transport=transport, follow_redirects=False) as c:
        for sym, day, expected in PROBES:
            row: dict[str, Any] = {"symbol": sym, "date": day, "expected": expected}
            probes: tuple[tuple[str, str, dict[str, Any]], ...] = (
                ("calendar", "/stable/earnings-calendar", {"from": day, "to": day}),
                (
                    "calendar_report_times",
                    "/stable/earnings-calendar",
                    {"from": day, "to": day, "includeReportTimes": "true"},
                ),
                ("company", "/stable/earnings", {"symbol": sym, "limit": 8}),
            )
            for label, path, params in probes:
                try:
                    r = await c.get(path, params={**params, "apikey": key})
                    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else []
                    rows = (
                        [x for x in body if isinstance(x, dict) and x.get("symbol", sym) == sym]
                        if isinstance(body, list)
                        else []
                    )
                    row[label] = {
                        "status": r.status_code,
                        "rows": len(rows),
                        "timing_fields": _timing_fields(rows),
                        "keys": sorted(rows[0].keys()) if rows else [],
                    }
                except (httpx.HTTPError, ValueError) as exc:
                    row[label] = {"error": redact(f"{type(exc).__name__}: {exc}")[:200]}
            out["probes"].append(row)
    found = {
        k
        for p in out["probes"]
        for lbl in ("calendar", "calendar_report_times", "company")
        for k in p.get(lbl, {}).get("timing_fields", {})
    }
    agree = sum(
        1
        for p in out["probes"]
        for v in p.get("calendar_report_times", {}).get("timing_fields", {}).values()
        if any(x.lower().startswith(p["expected"]) for x in v)
    )
    out["summary"] = {
        "status": "done",
        "timing_fields_found": sorted(found),
        "report_times_agree_with_known": agree,
        "probes": len(PROBES),
        "recommendation": "set TB_CTX_FMP_REPORT_TIMES=true"
        if agree >= len(PROBES) - 1
        else "keep TB_CTX_FMP_REPORT_TIMES=false (unknown timing is treated as AMC, CX-007)",
        "websocket": "not depended on (research 04 §4.3); no FMP real-time path exists in the platform",
    }
    return out
