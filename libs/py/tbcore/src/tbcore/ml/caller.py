"""Actor resolution from the verified calling service (EX-030, ADR-0035).

The request's ``CallerContext`` is advisory; the service token decides. Calls from ``assistant`` are
always actor ``analyst`` and carry no step-up; only ``api-gateway`` may assert ``owner`` and a step-up
attestation; internal workers (``ml``, ``optimizer``) may pass an actor through (for example an analyst
round's training job recorded as a trial). In-process calls (no service, tests and CLIs) keep the
request's actor.
"""

from __future__ import annotations

import time

from tb.ml.v1 import training_pb2 as tp

INTERNAL = {"ml", "optimizer", "monitor"}
ACTOR_NAMES = {
    tp.ACTOR_OWNER: "owner",
    tp.ACTOR_ANALYST: "analyst",
    tp.ACTOR_SCHEDULE: "schedule",
    tp.ACTOR_SYSTEM: "system",
    tp.ACTOR_UNSPECIFIED: "unknown",
}


class PermissionDenied(Exception):
    """A caller is not allowed to do this (mapped to gRPC PERMISSION_DENIED and audited)."""


def resolve(ctx: tp.CallerContext | None, service: str) -> tp.CallerContext:
    c = tp.CallerContext()
    if ctx is not None:
        c.CopyFrom(ctx)
    if service == "assistant":
        c.actor = tp.ACTOR_ANALYST
        c.actor_id = c.actor_id or "analyst"
        c.ClearField("step_up")
        return c
    if service == "api-gateway":
        if c.actor in (tp.ACTOR_UNSPECIFIED, tp.ACTOR_OWNER):
            c.actor = tp.ACTOR_OWNER
            return c
        # The gateway serves the owner only; any other claimed actor is downgraded to analyst rights.
        c.actor = tp.ACTOR_ANALYST
        c.ClearField("step_up")
        return c
    if service in INTERNAL:
        if c.actor == tp.ACTOR_OWNER:
            c.actor = tp.ACTOR_SYSTEM  # internal workers never carry owner authority
            c.ClearField("step_up")
        if c.actor == tp.ACTOR_UNSPECIFIED:
            c.actor = tp.ACTOR_SYSTEM
        return c
    if service:
        raise PermissionDenied(f"service {service!r} has no actor mapping")
    if c.actor == tp.ACTOR_UNSPECIFIED:
        c.actor = tp.ACTOR_SYSTEM
    return c


def name(ctx: tp.CallerContext) -> str:
    return ACTOR_NAMES.get(ctx.actor, "unknown")


def has_fresh_step_up(ctx: tp.CallerContext, max_age_s: int = 300, now_ns: int | None = None) -> bool:
    """Owner step-up attested by api-gateway within ``max_age_s`` (SE-030, EX-011)."""
    if ctx.actor != tp.ACTOR_OWNER or not ctx.HasField("step_up") or not ctx.step_up.verified_at:
        return False
    now = now_ns if now_ns is not None else time.time_ns()
    return 0 <= now - ctx.step_up.verified_at <= max_age_s * 1_000_000_000
