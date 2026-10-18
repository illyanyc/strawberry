"""Config diffs from the planner → validated section 10/11 configs (AN-011).

A plan action carries an RFC 7396 JSON merge patch. It is applied to the *unmasked* current config
(the LLM only ever saw the masked copy), parsed back into the proto (unknown fields are errors), and
then validated by the owning service (``EstimateTrainingJob`` for training configs) or against the
family's hyperparameter JSON Schema for study search spaces. Invalid diffs never launch.
"""

from __future__ import annotations

import copy
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from google.protobuf import json_format, struct_pb2

from tb.ml.v1 import training_pb2
from tb.optimizer.v1 import optimizer_pb2

_REL_DAY = re.compile(r"^D([+-]\d+)$")
ABLATION_KEYS = frozenset({"drop_feature_groups", "add_feature_groups", "label", "hyperparams", "exclude_features"})
STUDY_KEYS = frozenset(
    {"families", "search_space", "budget", "objective", "sampler", "pruner", "feature_sets", "label_specs"}
)


class DiffError(ValueError):
    """A config diff that cannot be applied or does not validate."""


def merge_patch(target: Any, patch: Any) -> Any:
    """RFC 7396."""
    if not isinstance(patch, dict):
        return copy.deepcopy(patch)
    out = copy.deepcopy(target) if isinstance(target, dict) else {}
    for k, v in patch.items():
        if v is None:
            out.pop(k, None)
        else:
            out[k] = merge_patch(out.get(k), v)
    return out


def _rel_days_to_ns(obj: Any, anchor: datetime) -> Any:
    """Turn masked ``D±n`` values back into UTC ns at 00:00 of that day (periods only)."""
    if isinstance(obj, dict):
        return {k: _rel_days_to_ns(v, anchor) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_rel_days_to_ns(v, anchor) for v in obj]
    if isinstance(obj, str):
        m = _REL_DAY.match(obj)
        if m:
            day = anchor.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
                days=int(m.group(1))
            )
            return str(int(day.timestamp()) * 10**9)
    return obj


def config_dict(cfg: training_pb2.TrainingConfig) -> dict[str, Any]:
    out: dict[str, Any] = json_format.MessageToDict(cfg, preserving_proto_field_name=True)
    return out


def apply_training_diff(
    base: training_pb2.TrainingConfig, diff: dict[str, Any], *, anchor: datetime, name_suffix: str = ""
) -> training_pb2.TrainingConfig:
    if not diff:
        raise DiffError("empty config diff")
    patched = merge_patch(config_dict(base), _rel_days_to_ns(diff, anchor))
    if name_suffix:
        patched["name"] = (str(patched.get("name") or "analyst") + "-" + name_suffix)[:120]
    out = training_pb2.TrainingConfig()
    try:
        json_format.ParseDict(patched, out, ignore_unknown_fields=False)
    except json_format.ParseError as exc:
        raise DiffError(f"config diff does not fit TrainingConfig: {str(exc)[:200]}") from None
    if out.lineage_id != base.lineage_id:
        raise DiffError("a config diff may not move the run to another lineage")
    if out.data_source != base.data_source:
        raise DiffError("a config diff may not change the data source")
    return out


def ablation_variant(name: str, diff: dict[str, Any]) -> training_pb2.AblationVariant:
    unknown = set(diff) - ABLATION_KEYS
    if unknown:
        raise DiffError(f"ablation diff has unsupported keys {sorted(unknown)}")
    if not diff:
        raise DiffError("empty ablation diff")
    v = training_pb2.AblationVariant(name=name[:80])
    drops = list(diff.get("drop_feature_groups", [])) + list(diff.get("exclude_features", []))
    v.drop_feature_groups.extend(str(x) for x in drops)
    v.add_feature_groups.extend(str(x) for x in diff.get("add_feature_groups", []))
    if "label" in diff:
        try:
            json_format.ParseDict(diff["label"], v.label)
        except json_format.ParseError as exc:
            raise DiffError(f"label diff invalid: {str(exc)[:200]}") from None
    if "hyperparams" in diff:
        if not isinstance(diff["hyperparams"], dict):
            raise DiffError("hyperparams must be an object")
        v.hyperparams.update(diff["hyperparams"])
    return v


def _schema_props(schema_json: str) -> dict[str, dict[str, Any]]:
    try:
        schema = json.loads(schema_json) if schema_json else {}
    except json.JSONDecodeError:
        return {}
    props = schema.get("properties") if isinstance(schema, dict) else None
    return props if isinstance(props, dict) else {}


def check_hyperparams(schema_json: str, params: dict[str, Any]) -> list[str]:
    """Minimal JSON Schema checks (known keys, numeric bounds, enums) for drafts; the ml service
    performs full validation on submit (ML-011)."""
    props = _schema_props(schema_json)
    if not props:
        return []
    errors: list[str] = []
    for k, v in params.items():
        p = props.get(k)
        if p is None:
            errors.append(f"unknown hyperparameter {k!r}")
            continue
        if "enum" in p and v not in p["enum"]:
            errors.append(f"{k}: {v!r} not in {p['enum']}")
        if isinstance(v, int | float) and not isinstance(v, bool):
            lo, hi = p.get("minimum"), p.get("maximum")
            if isinstance(lo, int | float) and v < lo:
                errors.append(f"{k}: {v} < minimum {lo}")
            if isinstance(hi, int | float) and v > hi:
                errors.append(f"{k}: {v} > maximum {hi}")
    return errors


def apply_study_diff(
    base: optimizer_pb2.StudySpec,
    diff: dict[str, Any],
    *,
    family_schemas: dict[str, str],
    preregistration: optimizer_pb2.PreRegistration,
    name: str,
    max_trials: int,
) -> optimizer_pb2.StudySpec:
    unknown = set(diff) - STUDY_KEYS
    if unknown:
        raise DiffError(f"study diff has unsupported keys {sorted(unknown)}")
    spec = optimizer_pb2.StudySpec()
    spec.CopyFrom(base)
    spec.name = name[:120]
    patched = merge_patch(json_format.MessageToDict(spec, preserving_proto_field_name=True), diff)
    out = optimizer_pb2.StudySpec()
    try:
        json_format.ParseDict(patched, out, ignore_unknown_fields=False)
    except json_format.ParseError as exc:
        raise DiffError(f"study diff does not fit StudySpec: {str(exc)[:200]}") from None
    out.lineage_id = base.lineage_id
    out.period.CopyFrom(base.period)
    out.preregistration.CopyFrom(preregistration)
    # EX-026: a study is pre-registered on the metric it optimizes; the optimizer refuses a mismatch.
    out.objective.metric = out.objective.metric or "sharpe"
    out.preregistration.metric = out.objective.metric
    for ps in out.search_space:
        fams = list(ps.families) or list(out.families)
        for fam in fams:
            props = _schema_props(family_schemas.get(fam, ""))
            if props and ps.name not in props:
                raise DiffError(f"search space parameter {ps.name!r} is not in the {fam} schema")
            p = props.get(ps.name, {})
            lo, hi = p.get("minimum"), p.get("maximum")
            if ps.kind in {"float", "log_float", "int"}:
                if ps.low > ps.high:
                    raise DiffError(f"{ps.name}: low > high")
                if (isinstance(lo, int | float) and ps.low < lo) or (isinstance(hi, int | float) and ps.high > hi):
                    raise DiffError(f"{ps.name}: range outside the schema bounds")
    if max_trials and (out.budget.max_trials == 0 or out.budget.max_trials > max_trials):
        out.budget.max_trials = max_trials
    return out


def struct_from(d: dict[str, Any]) -> struct_pb2.Struct:
    s = struct_pb2.Struct()
    s.update(d)
    return s
