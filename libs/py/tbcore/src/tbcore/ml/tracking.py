"""Run tracking backends (ML-013, ADR-0037): MLflow over its REST API (no MLflow client dependency) and
a no-op tracker. The run store (``tbcore.ml.runs``) is the system of record; MLflow mirrors it for the
MLflow UI and comparison views.
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any, Protocol

import httpx


class Tracker(Protocol):
    def log_run(
        self,
        experiment: str,
        run_name: str,
        params: dict[str, str],
        metrics: dict[str, float],
        tags: dict[str, str],
        artifacts: dict[str, Path],
        step_metrics: list[tuple[str, float, int]] | None = None,
    ) -> str: ...


class NullTracker:
    def log_run(
        self,
        experiment: str,
        run_name: str,
        params: dict[str, str],
        metrics: dict[str, float],
        tags: dict[str, str],
        artifacts: dict[str, Path],
        step_metrics: list[tuple[str, float, int]] | None = None,
    ) -> str:
        return ""


class MlflowTracker:
    """MLflow 3 REST: experiments/get-by-name|create, runs/create, runs/log-batch, mlflow-artifacts upload,
    runs/update. Artifacts need the server's ``--serve-artifacts`` (infra/docker/mlflow-entrypoint.sh)."""

    def __init__(self, base_url: str, timeout: float = 30.0, client: httpx.Client | None = None) -> None:
        self.base = base_url.rstrip("/")
        self._http = client or httpx.Client(timeout=timeout)

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        r = self._http.post(f"{self.base}/api/2.0/mlflow/{path}", json=body)
        r.raise_for_status()
        out: dict[str, Any] = r.json() if r.content else {}
        return out

    def experiment_id(self, name: str) -> str:
        r = self._http.get(f"{self.base}/api/2.0/mlflow/experiments/get-by-name", params={"experiment_name": name})
        if r.status_code == 200:
            return str(r.json()["experiment"]["experiment_id"])
        return str(self._post("experiments/create", {"name": name})["experiment_id"])

    def log_run(
        self,
        experiment: str,
        run_name: str,
        params: dict[str, str],
        metrics: dict[str, float],
        tags: dict[str, str],
        artifacts: dict[str, Path],
        step_metrics: list[tuple[str, float, int]] | None = None,
    ) -> str:
        exp = self.experiment_id(experiment)
        now = int(time.time() * 1000)
        run = self._post(
            "runs/create",
            {
                "experiment_id": exp,
                "run_name": run_name,
                "start_time": now,
                "tags": [{"key": k, "value": v} for k, v in tags.items()],
            },
        )["run"]["info"]
        run_id = str(run["run_id"])
        ms = [{"key": k, "value": v, "timestamp": now, "step": 0} for k, v in metrics.items() if math.isfinite(v)]
        ms += [
            {"key": k, "value": v, "timestamp": now, "step": s} for k, v, s in (step_metrics or []) if math.isfinite(v)
        ]
        ps = [{"key": k, "value": str(v)[:6000]} for k, v in params.items()]
        for i in range(0, max(len(ms), len(ps), 1), 900):
            self._post("runs/log-batch", {"run_id": run_id, "metrics": ms[i : i + 900], "params": ps[i : i + 100]})
        for name, path in artifacts.items():
            if path.is_file():
                url = f"{self.base}/api/2.0/mlflow-artifacts/artifacts/{exp}/{run_id}/artifacts/{name}"
                self._http.put(url, content=path.read_bytes()).raise_for_status()
        self._post("runs/update", {"run_id": run_id, "status": "FINISHED", "end_time": int(time.time() * 1000)})
        return run_id
