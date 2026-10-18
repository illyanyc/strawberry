from tb.ml.v1 import training_pb2 as _training_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class HoldoutTouch(_message.Message):
    __slots__ = ("candidate_id", "family", "quarter", "at", "actor_id", "reason")
    CANDIDATE_ID_FIELD_NUMBER: _ClassVar[int]
    FAMILY_FIELD_NUMBER: _ClassVar[int]
    QUARTER_FIELD_NUMBER: _ClassVar[int]
    AT_FIELD_NUMBER: _ClassVar[int]
    ACTOR_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    candidate_id: str
    family: str
    quarter: str
    at: int
    actor_id: str
    reason: str
    def __init__(self, candidate_id: _Optional[str] = ..., family: _Optional[str] = ..., quarter: _Optional[str] = ..., at: _Optional[int] = ..., actor_id: _Optional[str] = ..., reason: _Optional[str] = ...) -> None: ...

class HoldoutResult(_message.Message):
    __slots__ = ("candidate_id", "passed", "dsr", "evaluated_at", "family", "quarter")
    CANDIDATE_ID_FIELD_NUMBER: _ClassVar[int]
    PASSED_FIELD_NUMBER: _ClassVar[int]
    DSR_FIELD_NUMBER: _ClassVar[int]
    EVALUATED_AT_FIELD_NUMBER: _ClassVar[int]
    FAMILY_FIELD_NUMBER: _ClassVar[int]
    QUARTER_FIELD_NUMBER: _ClassVar[int]
    candidate_id: str
    passed: bool
    dsr: float
    evaluated_at: int
    family: str
    quarter: str
    def __init__(self, candidate_id: _Optional[str] = ..., passed: _Optional[bool] = ..., dsr: _Optional[float] = ..., evaluated_at: _Optional[int] = ..., family: _Optional[str] = ..., quarter: _Optional[str] = ...) -> None: ...

class HoldoutStatus(_message.Message):
    __slots__ = ("lineage_id", "family", "boundary", "touches", "touches_this_quarter", "remaining_this_quarter", "trial_inflation_per_touch", "retired_boundaries")
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    FAMILY_FIELD_NUMBER: _ClassVar[int]
    BOUNDARY_FIELD_NUMBER: _ClassVar[int]
    TOUCHES_FIELD_NUMBER: _ClassVar[int]
    TOUCHES_THIS_QUARTER_FIELD_NUMBER: _ClassVar[int]
    REMAINING_THIS_QUARTER_FIELD_NUMBER: _ClassVar[int]
    TRIAL_INFLATION_PER_TOUCH_FIELD_NUMBER: _ClassVar[int]
    RETIRED_BOUNDARIES_FIELD_NUMBER: _ClassVar[int]
    lineage_id: str
    family: str
    boundary: int
    touches: _containers.RepeatedCompositeFieldContainer[HoldoutTouch]
    touches_this_quarter: int
    remaining_this_quarter: int
    trial_inflation_per_touch: int
    retired_boundaries: _containers.RepeatedScalarFieldContainer[int]
    def __init__(self, lineage_id: _Optional[str] = ..., family: _Optional[str] = ..., boundary: _Optional[int] = ..., touches: _Optional[_Iterable[_Union[HoldoutTouch, _Mapping]]] = ..., touches_this_quarter: _Optional[int] = ..., remaining_this_quarter: _Optional[int] = ..., trial_inflation_per_touch: _Optional[int] = ..., retired_boundaries: _Optional[_Iterable[int]] = ...) -> None: ...

class CheckDataAccessRequest(_message.Message):
    __slots__ = ("lineage_id", "period", "caller")
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    PERIOD_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    lineage_id: str
    period: _training_pb2.Period
    caller: _training_pb2.CallerContext
    def __init__(self, lineage_id: _Optional[str] = ..., period: _Optional[_Union[_training_pb2.Period, _Mapping]] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ...) -> None: ...

class CheckDataAccessResponse(_message.Message):
    __slots__ = ("allowed", "reason", "boundary")
    ALLOWED_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    BOUNDARY_FIELD_NUMBER: _ClassVar[int]
    allowed: bool
    reason: str
    boundary: int
    def __init__(self, allowed: _Optional[bool] = ..., reason: _Optional[str] = ..., boundary: _Optional[int] = ...) -> None: ...

class EvaluateOnHoldoutRequest(_message.Message):
    __slots__ = ("candidate_id", "reason", "caller")
    CANDIDATE_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    candidate_id: str
    reason: str
    caller: _training_pb2.CallerContext
    def __init__(self, candidate_id: _Optional[str] = ..., reason: _Optional[str] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ...) -> None: ...

class EvaluateOnHoldoutResponse(_message.Message):
    __slots__ = ("result",)
    RESULT_FIELD_NUMBER: _ClassVar[int]
    result: HoldoutResult
    def __init__(self, result: _Optional[_Union[HoldoutResult, _Mapping]] = ...) -> None: ...

class GetHoldoutStatusRequest(_message.Message):
    __slots__ = ("lineage_id",)
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    lineage_id: str
    def __init__(self, lineage_id: _Optional[str] = ...) -> None: ...

class GetHoldoutStatusResponse(_message.Message):
    __slots__ = ("status",)
    STATUS_FIELD_NUMBER: _ClassVar[int]
    status: HoldoutStatus
    def __init__(self, status: _Optional[_Union[HoldoutStatus, _Mapping]] = ...) -> None: ...

class SetHoldoutBoundaryRequest(_message.Message):
    __slots__ = ("lineage_id", "boundary", "caller")
    LINEAGE_ID_FIELD_NUMBER: _ClassVar[int]
    BOUNDARY_FIELD_NUMBER: _ClassVar[int]
    CALLER_FIELD_NUMBER: _ClassVar[int]
    lineage_id: str
    boundary: int
    caller: _training_pb2.CallerContext
    def __init__(self, lineage_id: _Optional[str] = ..., boundary: _Optional[int] = ..., caller: _Optional[_Union[_training_pb2.CallerContext, _Mapping]] = ...) -> None: ...

class SetHoldoutBoundaryResponse(_message.Message):
    __slots__ = ("status",)
    STATUS_FIELD_NUMBER: _ClassVar[int]
    status: HoldoutStatus
    def __init__(self, status: _Optional[_Union[HoldoutStatus, _Mapping]] = ...) -> None: ...
