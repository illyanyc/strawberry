"""Device selection and the cloud GPU burst hook (ML-032, D25, D29).

Linear and tree families run on CPU. MPS/MLX are offered only to families that declare them and only
when the native macOS worker has them. ``cloud_gpu`` runs only through the burst hook
(``TB_ML_BURST_URL``), off by default; requesting it without a hook, or for a family without GPU
support, fails the job before training. The hook receives the config and dataset reference only,
never credentials (beyond its own bearer token) or account data.
"""

from __future__ import annotations

import importlib.util
import platform
from typing import Any

import httpx

from tb.ml.v1 import training_pb2 as tp
from tbcore.ml.config import to_dict
from tbcore.ml.models import FamilyInfo


class DeviceError(ValueError):
    pass


def available() -> list[str]:
    out = ["cpu"]
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        if importlib.util.find_spec("torch") is not None:
            out.append("mps")
        if importlib.util.find_spec("mlx") is not None:
            out.append("mlx")
    return out


def select(requested: str, fam: FamilyInfo, burst_configured: bool) -> str:
    req = requested or "auto"
    if req == "auto":
        for d in ("mlx", "mps"):
            if d in fam.devices and d in available():
                return d
        return "cpu"
    if req == "cloud_gpu":
        if not fam.supports_gpu:
            raise DeviceError(f"family {fam.family} does not support GPU training (ML-032)")
        if not burst_configured:
            raise DeviceError("cloud_gpu requested but no burst hook is configured (TB_ML_BURST_URL, ML-032)")
        return "cloud_gpu"
    if req not in fam.devices:
        raise DeviceError(f"family {fam.family} cannot run on {req} (devices: {list(fam.devices)})")
    if req not in available():
        raise DeviceError(f"device {req} is not available on this worker")
    return req


class BurstHook:
    """POSTs ``{job_id, config, snapshot_id}`` to the burst endpoint; returns the remote job ID."""

    def __init__(self, url: str, token: str = "", client: httpx.Client | None = None) -> None:
        self.url = url
        self._token = token
        self._http = client or httpx.Client(timeout=30.0)

    def submit(self, job_id: str, cfg: tp.TrainingConfig, snapshot_id: str) -> str:
        headers = {"authorization": f"Bearer {self._token}"} if self._token else {}
        body: dict[str, Any] = {"job_id": job_id, "config": to_dict(cfg), "snapshot_id": snapshot_id}
        r = self._http.post(self.url, json=body, headers=headers)
        r.raise_for_status()
        return str(r.json().get("remote_job_id", ""))
