#!/usr/bin/env python3
"""Documentation protocol gate (master prompt N6).

Two checks, both stdlib-only so they run anywhere:

* ``coverage``: every git-tracked file is covered by a path entry in ``docs/file-map.md``
  (an exact file entry or a directory entry ending in ``/``), and no row marked
  ``Planned`` points at a path that already exists.
* ``diff --base <ref>``: for the change set ``<ref>...HEAD``:
  - files added, deleted, or renamed  -> ``docs/file-map.md`` and ``CHANGELOG.md`` must change;
  - anything under ``contracts/``     -> ``docs/architecture/overview.md`` or a new ADR must change;
  - a new file in ``docs/adr/``       -> ``docs/decisions.md`` must change.

Set ``DOCS_CHECK_SKIP=1`` (CI does this for PRs labelled ``docs-check-skip``) to bypass the
diff check for reviewed pure refactors. Exit code 1 on any violation.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILE_MAP = "docs/file-map.md"
CHANGELOG = "CHANGELOG.md"
OVERVIEW = "docs/architecture/overview.md"
DECISIONS = "docs/decisions.md"

# Generated or vendored trees that the file map covers by their parent directory entry.
_ENTRY_RE = re.compile(r"`([^`\s]+)`")


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


def _file_map_rows() -> list[tuple[str, str]]:
    """Return (path, row_text) for every table row whose first cell is a backticked path."""
    rows: list[tuple[str, str]] = []
    for line in (ROOT / FILE_MAP).read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells:
            continue
        for match in _ENTRY_RE.finditer(cells[0]):
            rows.append((match.group(1), line))
    return rows


def check_coverage() -> list[str]:
    """Every tracked file must be covered by a file-map entry; Planned rows must not exist."""
    errors: list[str] = []
    rows = _file_map_rows()
    entries = {path for path, _ in rows}
    dirs = sorted((p for p in entries if p.endswith("/")), key=len, reverse=True)
    tracked = [p for p in _git("ls-files").splitlines() if p]
    for path in tracked:
        if path in entries:
            continue
        if any(path.startswith(d) for d in dirs):
            continue
        errors.append(f"not in {FILE_MAP}: {path}")
    for path, row in rows:
        if "| Planned |" in row and (ROOT / path.rstrip("/")).exists():
            errors.append(f"{FILE_MAP} marks `{path}` Planned but it exists; mark it Exists")
    return errors


def check_diff(base: str) -> list[str]:
    """Apply the same-commit documentation rules to the change set base...HEAD."""
    if os.environ.get("DOCS_CHECK_SKIP") == "1":
        print("docs-check diff skipped (DOCS_CHECK_SKIP=1, reviewed opt-out)")
        return []
    out = _git("diff", "--name-status", "--find-renames", f"{base}...HEAD")
    changed: set[str] = set()
    structural: list[str] = []
    new_adrs: list[str] = []
    for line in out.splitlines():
        parts = line.split("\t")
        status, paths = parts[0], parts[1:]
        changed.update(paths)
        if status[0] in {"A", "D", "R"}:
            structural.append(f"{status[0]} {' -> '.join(paths)}")
        if status[0] == "A" and paths[-1].startswith("docs/adr/"):
            new_adrs.append(paths[-1])
    errors: list[str] = []
    if structural:
        for required in (FILE_MAP, CHANGELOG):
            if required not in changed:
                errors.append(f"files added/moved/removed but {required} not updated: {structural[:5]}")
    if any(p.startswith("contracts/") for p in changed) and OVERVIEW not in changed and not new_adrs:
        errors.append(f"contracts/ changed without updating {OVERVIEW} or adding an ADR")
    if new_adrs and DECISIONS not in changed:
        errors.append(f"new ADR(s) {new_adrs} without updating {DECISIONS}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("coverage")
    diff = sub.add_parser("diff")
    diff.add_argument("--base", required=True, help="git ref to diff against, e.g. origin/main")
    args = parser.parse_args()
    errors = check_coverage() if args.cmd == "coverage" else check_diff(args.base)
    for err in errors:
        print(f"docs-check: {err}", file=sys.stderr)
    if not errors:
        print(f"docs-check {args.cmd}: ok")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
