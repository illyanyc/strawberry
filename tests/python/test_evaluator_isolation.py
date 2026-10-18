"""Evaluator isolation (BT-081, D31 rule 9): the scorer lives outside tbcore, every manifest path exists,
and assistant code imports no evaluator module."""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = json.loads((ROOT / "services/backtest/evaluator_manifest.json").read_text())


def _imports(path: Path) -> set[str]:
    mods: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
            mods.update(f"{node.module}.{a.name}" for a in node.names)
    return mods


def _forbidden(mod: str) -> bool:
    return any(mod == m or mod.startswith(m + ".") for m in MANIFEST["modules"])


def test_bt081_manifest_paths_exist() -> None:
    for p in MANIFEST["paths"]:
        assert (ROOT / p).exists(), f"evaluator manifest lists a missing path: {p}"


def test_bt081_assistant_imports_no_evaluator_module() -> None:
    offenders = []
    for d in MANIFEST["restricted_importers"]:
        for f in (ROOT / d).rglob("*.py"):
            offenders += [f"{f.relative_to(ROOT)}: {m}" for m in _imports(f) if _forbidden(m)]
    assert offenders == []


def test_bt081_scorer_is_outside_tbcore() -> None:
    lib = ROOT / "libs/py/tbcore/src"
    offenders = [
        str(f.relative_to(ROOT)) for f in lib.rglob("*.py") if any(m.startswith("tb_backtest") for m in _imports(f))
    ]
    assert offenders == []
    assert not list(lib.rglob("scorer*"))
