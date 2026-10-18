"""Every Prometheus alert rule has a summary, a severity, and a runbook that exists (section 13
acceptance: "every alert rule has a runbook entry"). Parsed line by line (no YAML dependency)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALERT = re.compile(r"^\s*- alert:\s*(\S+)")


def rules() -> list[tuple[str, str]]:
    out = []
    for f in sorted((ROOT / "infra/observability/prometheus/rules").glob("*.yml")):
        current: list[str] = []
        name = ""
        for line in f.read_text(encoding="utf-8").splitlines():
            m = ALERT.match(line)
            if m:
                if name:
                    out.append((f"{f.name}:{name}", "\n".join(current)))
                name, current = m.group(1), []
            elif name:
                current.append(line)
        if name:
            out.append((f"{f.name}:{name}", "\n".join(current)))
    return out


def test_every_alert_rule_has_an_existing_runbook() -> None:
    found = rules()
    assert len(found) >= 20
    for name, body in found:
        assert re.search(r"summary:\s*\S", body), f"{name} has no summary"
        assert re.search(r"severity:\s*(info|warning|critical)", body), f"{name} has no severity"
        m = re.search(r"runbook_url:\s*(docs/runbooks/[\w.-]+\.md)", body)
        assert m, f"{name} has no runbook"
        assert (ROOT / m.group(1)).is_file(), f"{name}: {m.group(1)} does not exist"
