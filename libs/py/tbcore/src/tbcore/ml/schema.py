"""Validation for the hyperparameter JSON Schema subset the model zoo uses (ML-011).

Supported keywords: ``type`` (object, number, integer, boolean, string), ``properties``,
``additionalProperties: false``, ``minimum``, ``maximum``, ``enum``, ``default``. The schemas are plain
JSON Schema (draft 2020-12) so the UI renders forms from them unchanged.
"""

from __future__ import annotations

from typing import Any


def defaults(schema: dict[str, Any]) -> dict[str, Any]:
    return {k: v["default"] for k, v in schema.get("properties", {}).items() if "default" in v}


def validate(schema: dict[str, Any], value: dict[str, Any]) -> list[str]:
    """Error messages (empty when valid)."""
    errors: list[str] = []
    props: dict[str, Any] = schema.get("properties", {})
    if schema.get("additionalProperties") is False:
        errors += [f"unknown hyperparameter {k!r}" for k in value if k not in props]
    for k, v in value.items():
        p = props.get(k)
        if p is None:
            continue
        t = p.get("type")
        if t == "integer":
            if isinstance(v, bool) or not (isinstance(v, int) or (isinstance(v, float) and float(v).is_integer())):
                errors.append(f"{k}: expected integer, got {v!r}")
                continue
        elif t == "number":
            if isinstance(v, bool) or not isinstance(v, int | float):
                errors.append(f"{k}: expected number, got {v!r}")
                continue
        elif t == "boolean":
            if not isinstance(v, bool):
                errors.append(f"{k}: expected boolean, got {v!r}")
                continue
        elif t == "string" and not isinstance(v, str):
            errors.append(f"{k}: expected string, got {v!r}")
            continue
        if "enum" in p and v not in p["enum"]:
            errors.append(f"{k}: {v!r} not in {p['enum']}")
        if isinstance(v, int | float) and not isinstance(v, bool):
            if "minimum" in p and v < p["minimum"]:
                errors.append(f"{k}: {v} < minimum {p['minimum']}")
            if "maximum" in p and v > p["maximum"]:
                errors.append(f"{k}: {v} > maximum {p['maximum']}")
    return errors


def coerce(schema: dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
    """Defaults merged with ``value``; integers given as floats (protobuf Struct) become ints."""
    out = {**defaults(schema), **value}
    for k, p in schema.get("properties", {}).items():
        if p.get("type") == "integer" and isinstance(out.get(k), float):
            out[k] = int(out[k])
    return out
