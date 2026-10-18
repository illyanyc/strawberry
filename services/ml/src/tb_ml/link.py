"""ml → optimizer/holdout calls: lineage budgets, the holdout data boundary, and trial records.

``GrpcLink`` talks to the optimizer and holdout services with ml's service token; tests and the dev
profile pass an in-process object with the same methods, or ``NoLink`` (no lineage features).
"""

from __future__ import annotations

from typing import Protocol

import grpc

from tb.ml.v1 import training_pb2 as tp
from tb.optimizer.v1 import holdout_pb2 as hp
from tb.optimizer.v1 import optimizer_pb2 as op
from tbcore.rpc import Client
from tbcore.svcauth import Signer


class ResearchLink(Protocol):
    def lineage(self, lineage_id: str) -> op.Lineage | None: ...

    def check_data(self, lineage_id: str, period: tp.Period, caller: tp.CallerContext) -> tuple[bool, str, int]: ...

    def record_trial(self, trial: op.Trial, caller: tp.CallerContext) -> None: ...


class NoLink:
    def lineage(self, lineage_id: str) -> op.Lineage | None:
        return None

    def check_data(self, lineage_id: str, period: tp.Period, caller: tp.CallerContext) -> tuple[bool, str, int]:
        return True, "", 0

    def record_trial(self, trial: op.Trial, caller: tp.CallerContext) -> None:
        return None


class GrpcLink:
    def __init__(self, optimizer_addr: str, holdout_addr: str, signer: Signer | None) -> None:
        self._opt = Client(
            grpc.insecure_channel(optimizer_addr),
            op.DESCRIPTOR.services_by_name["OptimizerService"],
            signer,
            "optimizer",
        )
        self._ho = Client(
            grpc.insecure_channel(holdout_addr), hp.DESCRIPTOR.services_by_name["HoldoutService"], signer, "holdout"
        )

    def lineage(self, lineage_id: str) -> op.Lineage | None:
        try:
            r = self._opt.call("GetLineage", op.GetLineageRequest(lineage_id=lineage_id))
        except grpc.RpcError as exc:
            if exc.code() == grpc.StatusCode.NOT_FOUND:
                return None
            raise
        lin: op.Lineage = r.lineage
        return lin

    def check_data(self, lineage_id: str, period: tp.Period, caller: tp.CallerContext) -> tuple[bool, str, int]:
        r = self._ho.call(
            "CheckDataAccess", hp.CheckDataAccessRequest(lineage_id=lineage_id, period=period, caller=caller)
        )
        return bool(r.allowed), str(r.reason), int(r.boundary)

    def record_trial(self, trial: op.Trial, caller: tp.CallerContext) -> None:
        self._opt.call("RecordTrial", op.RecordTrialRequest(trial=trial, caller=caller))
