"""Canonical payload JSON, byte-identical to marketdata's Go sink (CX-003).

The sink stores ``payload_json`` as Go ``encoding/json`` renders a ``google.protobuf.Struct``
(``map[string]any``): sorted keys, no spaces, every number a float64 (integral values without a
decimal point), and ``<``, ``>``, ``&``, U+2028, U+2029 escaped. context-data hashes payloads the
same way so its change detection agrees with what compaction keeps.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

_ESCAPES = {"<": "\\u003c", ">": "\\u003e", "&": "\\u0026", "\u2028": "\\u2028", "\u2029": "\\u2029"}


def _norm(v: Any) -> Any:
    if isinstance(v, bool) or v is None or isinstance(v, str):
        return v
    if isinstance(v, int | float):
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            raise ValueError("payload numbers must be finite")
        return int(f) if f.is_integer() and abs(f) < 1e21 else f
    if isinstance(v, dict):
        return {str(k): _norm(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_norm(x) for x in v]
    raise TypeError(f"unsupported payload value {type(v).__name__}")


def canonical_json(payload: dict[str, Any]) -> str:
    s = json.dumps(_norm(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    for k, v in _ESCAPES.items():
        s = s.replace(k, v)
    return s


def payload_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()
