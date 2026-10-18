"""Point-in-time lint for feature code (section 08, BT-064; N2, DF-003, DF-012).

Static checks on the source of every registered feature (``@feature``) and on generated feature
files (the AI analyst's output, section 12). A finding names a construct that can read data from
after the decision time or outside the frame a feature is given:

* PIT001 negative ``shift`` (reads the next row);
* PIT002 centered rolling window (``center=True``);
* PIT003 whole-history aggregate in a per-instrument series feature (``pl.col(x).mean()`` over
  ``.over(instrument)`` uses every row, future included); rolling, cumulative, and exponentially
  weighted forms are causal; cross-sectional features aggregate per timestamp and frame features
  group explicitly, so the rule applies to plain series features only;
* PIT004 backward fill; PIT005 forward or nearest as-of join;
* PIT006 storage, network, or clock access (features see only the frame they are given);
* PIT007 ``event_time``/``ingested_at`` instead of ``available_at``;
* PIT008 fitting on the data passed in (``fit``, ``fit_transform``; scalers belong in the training fold).

A line ending in ``# pit: ok <reason>`` is exempt (the reason is required and reviewed).
Run: ``python -m tbcore.features.lint [FILES...]`` (no files: every registered feature).
"""

from __future__ import annotations

import ast
import inspect
import sys
import textwrap
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from tbcore.features import library  # registers the MVP features
from tbcore.features.core import REGISTRY

AGGREGATES = {
    "mean",
    "std",
    "var",
    "sum",
    "min",
    "max",
    "median",
    "quantile",
    "rank",
    "skew",
    "kurtosis",
    "n_unique",
    "count",
    "product",
}
CAUSAL_MARKERS = ("rolling", "cum_", "ewm_", "shift")
IO_CALLS = {
    "open",
    "read_parquet",
    "read_csv",
    "read_ipc",
    "read_json",
    "read_database",
    "scan_parquet",
    "scan_csv",
    "scan_ipc",
    "connect",
    "urlopen",
    "now",
    "utcnow",
    "today",
}
IO_MODULES = {"duckdb", "requests", "httpx", "urllib", "socket", "os", "subprocess", "time", "Lake", "ContextStore"}
FORBIDDEN_COLUMNS = {"event_time", "ingested_at"}


@dataclass(frozen=True)
class Finding:
    rule: str
    where: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.where}:{self.line}: {self.rule} {self.message}"


def _chain_text(node: ast.AST) -> str:
    return ast.unparse(node)


def _is_negative(node: ast.AST) -> bool:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return True
    return isinstance(node, ast.Constant) and isinstance(node.value, int | float) and node.value < 0


def _exempt(lines: list[str], lineno: int) -> bool:
    if 0 < lineno <= len(lines):
        line = lines[lineno - 1]
        return "# pit: ok" in line and len(line.split("# pit: ok", 1)[1].strip()) > 0
    return False


def lint_source(
    source: str, where: str = "<source>", kind: str = "series", frame: bool = False, first_line: int = 1
) -> list[Finding]:
    """Lint one function (or module) source. ``kind``/``frame`` are the feature's declaration."""
    src = textwrap.dedent(source)
    tree = ast.parse(src)
    lines = src.splitlines()
    out: list[Finding] = []

    def add(rule: str, node: ast.AST, msg: str) -> None:
        ln = getattr(node, "lineno", 1)
        if not _exempt(lines, ln):
            out.append(Finding(rule, where, ln + first_line - 1, msg))

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.value in FORBIDDEN_COLUMNS:
            add("PIT007", node, f"uses `{node.value}`; point-in-time code filters and joins on `available_at` (DF-002)")
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else fn.id if isinstance(fn, ast.Name) else ""
        kwargs = {k.arg: k.value for k in node.keywords if k.arg}
        if name == "shift":
            arg = node.args[0] if node.args else kwargs.get("n", kwargs.get("periods"))
            if arg is not None and _is_negative(arg):
                add("PIT001", node, "negative shift reads later rows (look-ahead)")
        if "center" in kwargs and isinstance(kwargs["center"], ast.Constant) and kwargs["center"].value is True:
            add("PIT002", node, "centered window includes later rows")
        fill = kwargs.get("strategy")
        if name in {"backward_fill", "bfill"} or (
            name == "fill_null" and isinstance(fill, ast.Constant) and fill.value == "backward"
        ):
            add("PIT004", node, "backward fill copies later values into earlier rows")
        if name == "join_asof":
            st = kwargs.get("strategy")
            if isinstance(st, ast.Constant) and st.value in ("forward", "nearest"):
                add("PIT005", node, f"as-of join with strategy={st.value!r} can match later rows")
        if (
            name in IO_CALLS
            or (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) and fn.value.id in IO_MODULES)
            or (isinstance(fn, ast.Name) and fn.id in IO_MODULES)
        ):
            add(
                "PIT006",
                node,
                f"`{_chain_text(fn)}`: features may not read storage, the network, or the clock (DF-003)",
            )
        if name in {"fit", "fit_transform", "partial_fit"}:
            add(
                "PIT008", node, "fitting on the frame passed in uses future rows; fit inside the training fold (DF-008)"
            )
        if kind == "series" and not frame and name in AGGREGATES and isinstance(fn, ast.Attribute):
            chain = _chain_text(fn.value)
            if not any(m in chain for m in CAUSAL_MARKERS) and "pl.col" in chain:
                add("PIT003", node, f"`{chain}.{name}()` aggregates the whole history of the instrument")
    return out


def lint_registry() -> list[Finding]:
    """Lint every registered feature (the MVP library is registered on import)."""
    assert library.mvp_feature_set is not None
    out: list[Finding] = []
    for key, f in sorted(REGISTRY.items()):
        src, first = inspect.getsourcelines(f.fn)
        out += lint_source("".join(src), f"{inspect.getsourcefile(f.fn)}::{key}", f.kind, f.frame, first)
    return out


def _decorator_kind(fn: ast.FunctionDef) -> tuple[bool, str, bool]:
    for d in fn.decorator_list:
        if isinstance(d, ast.Call) and getattr(d.func, "id", getattr(d.func, "attr", "")) == "feature":
            kw = {k.arg: k.value for k in d.keywords if k.arg}
            k, f = kw.get("kind"), kw.get("frame")
            kind = k.value if isinstance(k, ast.Constant) else "series"
            frame = bool(f.value) if isinstance(f, ast.Constant) else False
            return True, str(kind), frame
    return False, "series", False


def lint_file(path: str | Path) -> list[Finding]:
    """Lint a (generated) feature module: every ``@feature`` function with its declared kind, and the
    module's top level for storage, network, and clock access."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    tree = ast.parse(text)
    out: list[Finding] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            is_feature, kind, frame = _decorator_kind(node)
            seg = ast.get_source_segment(text, node) or ""
            out += lint_source(seg, f"{p}::{node.name}", kind if is_feature else "module", frame, node.lineno)
        elif not isinstance(node, ast.Import | ast.ImportFrom):
            out += [
                f for f in lint_source(ast.get_source_segment(text, node) or "", str(p), "module", False, node.lineno)
            ]
    return out


def main(argv: Iterable[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    findings = [f for a in args for f in lint_file(a)] if args else lint_registry()
    for f in findings:
        print(f)
    print(f"pit-lint: {len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
