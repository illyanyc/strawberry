"""Static policy for coder output before it is even written to the sandbox (AN-070).

Candidate features are pure array functions: ``compute(close, ...) -> array`` over the series the
job declares. Anything that could reach the filesystem, the process, the network, the clock, the
interpreter's internals, or dynamic code is refused. The sandbox (no network, read-only, limits) and
the evaluator-side checks are the next walls; this one gives fast, explainable feedback.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

DEFAULT_ALLOWED_IMPORTS = frozenset({"numpy", "math"})
BANNED_CALLS = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "open",
        "getattr",
        "setattr",
        "delattr",
        "hasattr",
        "globals",
        "locals",
        "vars",
        "dir",
        "__import__",
        "input",
        "breakpoint",
        "help",
        "exit",
        "quit",
        "memoryview",
        "type",
        "super",
        "object",
        "classmethod",
        "staticmethod",
        "property",
        "id",
    }
)
# numpy entry points that touch storage, processes, the clock, or the network.
BANNED_ATTRS = frozenset(
    {
        "load",
        "save",
        "savez",
        "savez_compressed",
        "loadtxt",
        "savetxt",
        "genfromtxt",
        "fromfile",
        "tofile",
        "memmap",
        "DataSource",
        "lib",
        "ctypeslib",
        "random",
        "testing",
        "f2py",
        "distutils",
        "system",
        "popen",
    }
)


@dataclass(frozen=True)
class Violation:
    rule: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.rule} line {self.line}: {self.message}"


def check(
    source: str, *, allowed_imports: frozenset[str] = DEFAULT_ALLOWED_IMPORTS, inputs: tuple[str, ...] = ()
) -> list[Violation]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [Violation("AST000", exc.lineno or 0, f"syntax error: {exc.msg}")]
    out: list[Violation] = []

    def bad(rule: str, node: ast.AST, msg: str) -> None:
        out.append(Violation(rule, getattr(node, "lineno", 0), msg))

    compute = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] not in allowed_imports or "." in a.name:
                    bad("AST001", node, f"import {a.name!r} is not allowed (allowed: {sorted(allowed_imports)})")
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
            if node.level or mod not in allowed_imports or (node.module or "").count("."):
                bad("AST001", node, f"from-import of {node.module!r} is not allowed")
        elif isinstance(node, ast.Global | ast.Nonlocal):
            bad("AST002", node, "global/nonlocal state is not allowed")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") or node.attr.startswith("_"):
                bad("AST003", node, f"private or dunder attribute {node.attr!r}")
            if node.attr in BANNED_ATTRS:
                bad("AST004", node, f"attribute {node.attr!r} reaches storage, processes, randomness, or the network")
        elif isinstance(node, ast.Name):
            if node.id.startswith("__"):
                bad("AST003", node, f"dunder name {node.id!r}")
            if node.id in BANNED_CALLS:
                bad("AST005", node, f"builtin {node.id!r} is not allowed")
        elif isinstance(
            node,
            ast.ClassDef
            | ast.AsyncFunctionDef
            | ast.Await
            | ast.Yield
            | ast.YieldFrom
            | ast.With
            | ast.AsyncWith
            | ast.Try,
        ):
            bad("AST006", node, f"{type(node).__name__} is not allowed in feature code")
        elif isinstance(node, ast.FunctionDef) and node.name == "compute":
            compute = node
    if compute is None:
        out.append(Violation("AST007", 0, "the module must define compute(...)"))
    elif inputs:
        params = [a.arg for a in compute.args.args]
        extra = [p for p in params if p not in inputs]
        if extra or not params:
            out.append(
                Violation("AST008", compute.lineno, f"compute() parameters {params} must be a subset of {list(inputs)}")
            )
    for node in tree.body:
        if not isinstance(node, ast.Import | ast.ImportFrom | ast.FunctionDef | ast.Assign | ast.Expr):
            bad("AST009", node, "only imports, constants, and functions at module level")
        if isinstance(node, ast.Expr) and not (
            isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
        ):
            bad("AST009", node, "module-level expressions other than a docstring are not allowed")
    return out
