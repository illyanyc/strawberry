"""The IBKR broker adapter: section 04's ``BrokerGateway`` surface for one environment.

One process serves one environment (paper or live) through one IB Gateway; the gateway login may
manage several IBKR accounts (D19 linked "bot" account), of which only the configured ones are
exposed as ``broker_account_id``s (IB-005). The order connection uses the gateway's master client ID
so it also sees orders placed in TWS/IBKR Mobile (external, ``manual``-owned).

Rules enforced here (see ``docs/business-logic/brokers-ibkr.md``): OE-002..OE-008, OE-011 (audit via
the event stream), VP-001, IB-001..IB-006, IB-016, IB-020..IB-026, IB-030..IB-033, IB-041.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import assert_never

from tb_broker_ibkr.attribution import (
    ATTRIBUTION_MAX_LEN,
    AttributionError,
    parse_attribution,
    resolve_owner,
    validate_order_ref,
)
from tb_broker_ibkr.audit import AuditEvent, AuditLog, MemoryAuditLog
from tb_broker_ibkr.clock import Clock, to_ns, with_timeout
from tb_broker_ibkr.cursor_store import CursorStore
from tb_broker_ibkr.error_codes import Action, Category, classify, is_pacing_message
from tb_broker_ibkr.events import RUNBOOK_OUTAGE, AlertEvent, EventSink, ReconciliationRequest
from tb_broker_ibkr.instruments import InstrumentResolver, UnknownInstrumentError
from tb_broker_ibkr.models import (
    TERMINAL_STATES,
    AccountNotFoundError,
    AccountSnapshot,
    Bar,
    BrokerAccount,
    BrokerOrder,
    BrokerUnavailableError,
    CancelAck,
    Capabilities,
    Environment,
    ExecutionReport,
    Instrument,
    InvalidOrderError,
    OrderAlreadyFinalError,
    OrderChanges,
    OrderNotFoundError,
    OrderRejectedError,
    OrderRequest,
    OrderState,
    OrderType,
    OwnerKind,
    Position,
    ReplaceAck,
    SessionState,
    SessionStatus,
    Side,
    SubmitAck,
    SubmitOutcomeUnknownError,
    TimeInForce,
)
from tb_broker_ibkr.pacing import HistoricalPacer
from tb_broker_ibkr.port import (
    CommissionFill,
    ExecFill,
    HistoricalRequest,
    IbPort,
    OrderSnapshot,
    PlaceOrder,
    PortHandlers,
)
from tb_broker_ibkr.schedule import ET, MaintenanceSchedule, Severity
from tb_broker_ibkr.session import FatalSessionError, SessionMetrics, SessionSupervisor, SupervisorConfig
from tb_broker_ibkr.status_map import StatusContext, map_status

log = logging.getLogger("tb_broker_ibkr.adapter")

PAPER_ACCOUNT_PREFIXES = ("DU", "DF")
RESYNC_OVERLAP = timedelta(minutes=5)
SUB_DOLLAR_TICK = Decimal("0.0001")

_TIF_TO_IB = {TimeInForce.DAY: "DAY", TimeInForce.GTC: "GTC", TimeInForce.IOC: "IOC", TimeInForce.FOK: "FOK"}

CAPABILITY_ORDER_TYPES = (OrderType.MARKET, OrderType.LIMIT, OrderType.STOP, OrderType.STOP_LIMIT)
CAPABILITY_TIFS = tuple(TimeInForce)


@dataclass(frozen=True, slots=True)
class AdapterConfig:
    environment: Environment
    # broker_account_id -> IBKR account code. Only these accounts are visible and tradable (IB-005).
    accounts: dict[str, str]
    order_ref_max_len: int = ATTRIBUTION_MAX_LEN
    submit_timeout: float = 5.0
    lookup_retry_delay: float = 2.0
    commission_wait: float = 2.0
    snapshot_interval: float = 30.0
    is_margin: bool = True
    heartbeat_interval: float = 15.0
    heartbeat_timeout: float = 10.0


@dataclass
class _OrderRecord:
    ib_client_id: int
    ib_order_id: int
    perm_id: int
    broker_account_id: str
    client_order_id: str
    owner: OwnerKind
    strategy_short_id: str
    instrument_id: str
    placed_at: datetime
    request: OrderRequest | None = None
    placed: PlaceOrder | None = None
    snapshot: OrderSnapshot | None = None
    state: OrderState = OrderState.PENDING_SUBMIT
    filled: Decimal = Decimal(0)
    cancel_requested: bool = False
    reject: tuple[int, str] | None = None
    ref_checked: bool = False
    aliases: list[str] = field(default_factory=list)
    ack: asyncio.Future[None] | None = None
    # (client_order_id, request, placed) before an unconfirmed replace, restored if IBKR rejects it.
    pending_replace: tuple[str, OrderRequest, PlaceOrder] | None = None


# Cancel/modify rejections: the order itself stays live.
_ACTION_REJECT_CODES = frozenset({104, 105, 161, 10147, 10148})


def ib_action(side: Side) -> str:
    return "BUY" if side is Side.BUY else "SELL"


def side_from_action(action: str) -> Side:
    action = action.upper()
    if action in {"BUY", "BOT"}:
        return Side.BUY
    if action == "SSHORT":
        return Side.SELL_SHORT
    return Side.SELL


def round_to_tick(price: Decimal, tick: Decimal, *, up: bool) -> Decimal:
    """Round to a multiple of ``tick`` in an explicit direction (BR-017, OE-007)."""
    steps = (price / tick).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR)
    return (steps * tick).quantize(tick)


def tick_round_prices(
    side: Side, order_type: OrderType, limit: Decimal | None, stop: Decimal | None, tick: Decimal
) -> tuple[Decimal | None, Decimal | None]:
    """BR-017: buy limits round down, sell limits up; stops round away from the market."""
    buying = side is Side.BUY
    if limit is not None:
        limit = round_to_tick(limit, tick, up=not buying)
    if stop is not None:
        stop = round_to_tick(stop, tick, up=buying)
    return limit, stop


def build_place_order(req: OrderRequest, instrument: Instrument, account_code: str, order_ref: str) -> PlaceOrder:
    """IB-025/IB-026, BR-002, BR-017: translate a platform order into IBKR order fields."""
    if not instrument.tradable:
        raise InvalidOrderError(f"{req.instrument_id} is not tradable", "not_tradable")
    if req.side is Side.SELL_SHORT and not instrument.shortable:
        raise InvalidOrderError(f"{req.instrument_id} is not shortable", "not_shortable")
    if req.qty != req.qty.to_integral_value():
        raise InvalidOrderError("IBKR API orders must be whole shares (fractional unsupported)")
    for name, price in (("limit_price", req.limit_price), ("stop_price", req.stop_price)):
        if price is not None and price <= 0:
            raise InvalidOrderError(f"{name} {price} must be positive")
    if instrument.min_tick <= 0:
        raise InvalidOrderError(f"{req.instrument_id} has no valid min_tick")
    reference = req.limit_price or req.stop_price
    tick = instrument.min_tick if reference is None or reference >= 1 else min(instrument.min_tick, SUB_DOLLAR_TICK)
    limit_price, stop_price = tick_round_prices(req.side, req.order_type, req.limit_price, req.stop_price, tick)

    lmt: Decimal | None = None
    aux: Decimal | None = None
    tif = req.tif
    if req.order_type is OrderType.MARKET:
        if req.limit_price is not None or req.stop_price is not None:
            raise InvalidOrderError("market orders take no prices")
        if req.extended_hours:
            raise InvalidOrderError("market orders cannot run outside regular hours")
        ib_type = "MOC" if tif is TimeInForce.CLS else "MKT"
    elif req.order_type is OrderType.LIMIT:
        if req.limit_price is None or req.stop_price is not None:
            raise InvalidOrderError("limit orders need limit_price only")
        ib_type = "LOC" if tif is TimeInForce.CLS else "LMT"
        lmt = limit_price
    elif req.order_type is OrderType.STOP:
        if req.stop_price is None or req.limit_price is not None:
            raise InvalidOrderError("stop orders need stop_price only")
        ib_type, aux = "STP", stop_price
    elif req.order_type is OrderType.STOP_LIMIT:
        if req.stop_price is None or req.limit_price is None:
            raise InvalidOrderError("stop-limit orders need limit_price and stop_price")
        ib_type, lmt, aux = "STP LMT", limit_price, stop_price
    else:
        assert_never(req.order_type)

    if tif in (TimeInForce.OPG, TimeInForce.CLS) and req.order_type not in (OrderType.MARKET, OrderType.LIMIT):
        raise InvalidOrderError(f"{tif.value} supports market and limit orders only")
    if tif is TimeInForce.CLS:
        ib_tif = "DAY"
    elif tif is TimeInForce.OPG:
        ib_tif = "OPG"
    else:
        ib_tif = _TIF_TO_IB[tif]
    return PlaceOrder(
        account=account_code,
        con_id=instrument.con_id,
        exchange=instrument.exchange,
        primary_exchange=instrument.primary_exchange,
        currency=instrument.currency,
        symbol=instrument.symbol,
        action=ib_action(req.side),
        total_qty=req.qty,
        order_type=ib_type,
        tif=ib_tif,
        order_ref=order_ref,
        lmt_price=lmt,
        aux_price=aux,
        outside_rth=req.extended_hours,
    )


def exec_base_and_revision(exec_id: str) -> tuple[str, int]:
    """IBKR exec IDs end in a revision segment; a correction keeps the base and bumps it (IB-032)."""
    base, _, rev = exec_id.rpartition(".")
    if not base:
        return exec_id, 0
    try:
        return base, int(rev, 16)
    except ValueError:
        return exec_id, 0


class IbkrAdapter:
    """Section 04 broker adapter over one IB Gateway (orders connection)."""

    def __init__(
        self,
        *,
        config: AdapterConfig,
        port: IbPort,
        clock: Clock,
        sink: EventSink,
        resolver: InstrumentResolver,
        cursor: CursorStore,
        schedule: MaintenanceSchedule | None = None,
        metrics: SessionMetrics | None = None,
        md_port: IbPort | None = None,
        pacer: HistoricalPacer | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        self._cfg = config
        self._port = port
        self._md_port = md_port or port
        self._clock = clock
        self._sink = sink
        self._resolver = resolver
        self._cursor = cursor
        self._schedule = schedule or MaintenanceSchedule()
        self._pacer = pacer or HistoricalPacer(clock)
        self._audit = audit or MemoryAuditLog()
        self._code_to_id = {code: acct for acct, code in config.accounts.items()}
        self._by_key: dict[tuple[int, int], _OrderRecord] = {}
        self._by_perm: dict[int, _OrderRecord] = {}
        self._by_cid: dict[str, _OrderRecord] = {}
        self._pending_execs: dict[str, tuple[ExecFill, asyncio.Task[None]]] = {}
        self._early_commissions: dict[str, CommissionFill] = {}
        self._late_fees: dict[str, CommissionFill] = {}
        self._malformed_alerted: set[int] = set()
        self._holds: dict[str, set[str]] = {acct: set() for acct in config.accounts}
        self._background: set[asyncio.Task[None]] = set()
        self._validate_accounts_config()
        self.supervisor = SessionSupervisor(
            config=SupervisorConfig(
                connection="orders",
                environment=config.environment,
                weekly_reminder=config.environment is Environment.LIVE,
                heartbeat_interval=config.heartbeat_interval,
                heartbeat_timeout=config.heartbeat_timeout,
            ),
            port=port,
            clock=clock,
            schedule=self._schedule,
            sink=sink,
            after_connect=self._after_connect,
            resync=self._resync,
            status_targets=lambda: list(config.accounts),
            status_overlay=self._overlay,
            metrics=metrics,
        )
        port.set_handlers(
            PortHandlers(
                on_disconnected=self.supervisor.notify_disconnected,
                on_error=self._on_error,
                on_order=self._on_order,
                on_execution=self._on_execution,
                on_commission=self._on_commission,
            )
        )

    # ---- configuration and session gates -------------------------------------------------------
    def _validate_accounts_config(self) -> None:
        """IB-006: every broker_account_id names this process's environment."""
        if not self._cfg.accounts:
            raise ValueError("no IBKR accounts configured (TB_IBKR_ACCOUNTS)")
        prefix = f"ibkr-{self._cfg.environment.value}-"
        for acct in self._cfg.accounts:
            if not acct.startswith(prefix):
                raise ValueError(f"broker_account_id {acct!r} does not belong to environment {prefix!r}")

    async def _after_connect(self, managed: list[str]) -> None:
        """IB-005/IB-006: mapped accounts must be managed by this login and match the environment."""
        managed_set = set(managed)
        for acct, code in self._cfg.accounts.items():
            if code not in managed_set:
                raise FatalSessionError("account_not_managed", f"{acct} is not managed by this IBKR login")
            is_paper = code.startswith(PAPER_ACCOUNT_PREFIXES)
            if is_paper != (self._cfg.environment is Environment.PAPER):
                raise FatalSessionError(
                    "environment_mismatch",
                    f"{acct} maps to a {'paper' if is_paper else 'live'} IBKR account but this adapter is "
                    f"{self._cfg.environment.value}; refusing to trade",
                )

    def _account_code(self, broker_account_id: str) -> str:
        code = self._cfg.accounts.get(broker_account_id)
        if code is None:
            raise AccountNotFoundError(f"unknown or disallowed broker_account_id {broker_account_id!r}")
        return code

    def _require_ready(self, broker_account_id: str) -> None:
        """OE-004: only a ``ready`` session accepts orders (an account hold also blocks, BR-021)."""
        status = self.session_status(broker_account_id)
        if status.state is not SessionState.READY:
            raise BrokerUnavailableError(broker_account_id, status.state, status.degraded_reasons)

    def _overlay(self, status: SessionStatus) -> SessionStatus:
        holds = self._holds.get(status.broker_account_id)
        if status.state is SessionState.READY and holds:
            return status.model_copy(update={"state": SessionState.DEGRADED, "degraded_reasons": tuple(sorted(holds))})
        return status

    async def _set_hold(self, broker_account_id: str, reason: str, active: bool) -> bool:
        holds = self._holds[broker_account_id]
        if (reason in holds) == active:
            return False
        if active:
            holds.add(reason)
        else:
            holds.discard(reason)
        await self._sink.session_status(self.session_status(broker_account_id))
        return True

    def _require_connected(self, broker_account_id: str) -> None:
        if not self._port.is_connected() or self.supervisor.state in {
            SessionState.DISCONNECTED,
            SessionState.CONNECTING,
        }:
            raise BrokerUnavailableError(broker_account_id, self.supervisor.state, self.supervisor.reasons)

    # ---- section 04 surface ----------------------------------------------------------------
    def capabilities(self) -> Capabilities:
        return Capabilities(
            order_types=CAPABILITY_ORDER_TYPES,
            tifs=CAPABILITY_TIFS,
            supports_bracket=False,
            supports_oco=False,
            supports_oto=False,
            supports_replace=True,
            supports_trailing_stop=False,
            supports_fractional=False,
            supports_short=True,
            supports_extended_hours=True,
            max_client_order_id_len=self._cfg.order_ref_max_len,
            pdt_applies=False,
            is_margin=self._cfg.is_margin,
            data_feeds=("ibkr",),
        )

    def list_accounts(self) -> list[BrokerAccount]:
        caps = self.capabilities()
        return [
            BrokerAccount(broker_account_id=a, environment=self._cfg.environment, capabilities=caps)
            for a in self._cfg.accounts
        ]

    def session_status(self, broker_account_id: str) -> SessionStatus:
        self._account_code(broker_account_id)
        return self._overlay(self.supervisor.status_for(broker_account_id))

    async def submit_order(self, req: OrderRequest) -> SubmitAck:
        """Place an order exactly once (OE-002, OE-003, OE-004, OE-011, OE-I1; IB-001, IB-021)."""
        account_code = self._account_code(req.broker_account_id)
        existing = self._by_cid.get(req.client_order_id)
        if existing is not None:
            return self._existing_ack(existing)
        self._require_ready(req.broker_account_id)
        try:
            attribution = validate_order_ref(req.client_order_id, self._cfg.order_ref_max_len)
            instrument = await self._instrument_for(req)
            placed = build_place_order(req, instrument, account_code, req.client_order_id)
        except AttributionError as exc:
            await self._audit_reject(req.client_order_id, req.broker_account_id, "invalid_attribution", str(exc), True)
            raise InvalidOrderError(str(exc), "invalid_attribution") from None
        except InvalidOrderError as exc:
            await self._audit_reject(req.client_order_id, req.broker_account_id, exc.reason, str(exc), True)
            raise
        # SE-021: the submit is audited before it is sent; a failed append means no order.
        await self._audit.append(
            AuditEvent(
                action="broker.order.submit",
                target=req.client_order_id,
                correlation_id=req.correlation_id,
                details={
                    "broker_account_id": req.broker_account_id,
                    "instrument_id": req.instrument_id,
                    "side": req.side.value,
                    "qty": str(req.qty),
                    "order_type": placed.order_type,
                    "tif": placed.tif,
                    "limit_price": str(placed.lmt_price) if placed.lmt_price is not None else None,
                    "stop_price": str(placed.aux_price) if placed.aux_price is not None else None,
                },
            )
        )

        loop = asyncio.get_running_loop()
        record = _OrderRecord(
            ib_client_id=self._port.client_id,
            ib_order_id=0,
            perm_id=0,
            broker_account_id=req.broker_account_id,
            client_order_id=req.client_order_id,
            owner=attribution.owner,
            strategy_short_id=attribution.short_id if attribution.owner is OwnerKind.STRATEGY else "",
            instrument_id=req.instrument_id,
            placed_at=self._clock.now(),
            request=req,
            placed=placed,
            ack=loop.create_future(),
        )
        self._by_cid[req.client_order_id] = record
        record.ib_order_id = self._port.place_order(placed)
        self._by_key[(record.ib_client_id, record.ib_order_id)] = record
        log.info(
            "order submitted",
            extra={"client_order_id": req.client_order_id, "broker_account_id": req.broker_account_id},
        )
        assert record.ack is not None
        try:
            await with_timeout(self._clock, asyncio.shield(record.ack), self._cfg.submit_timeout)
        except TimeoutError:
            return await self._recover_submit(record)
        if record.reject is not None:
            raise OrderRejectedError(*record.reject)
        return SubmitAck(
            client_order_id=record.client_order_id,
            broker_order_id=str(record.perm_id),
            accepted_at=to_ns(self._clock.now()),
        )

    async def _instrument_for(self, req: OrderRequest) -> Instrument:
        try:
            known = await self._resolver.resolve(req.instrument_id)
        except UnknownInstrumentError:
            known = None
        if req.instrument is not None:
            if known is not None and known.con_id != req.instrument.con_id:
                raise InvalidOrderError(f"conId mismatch for {req.instrument_id}", "unknown_instrument")
            return known or req.instrument
        if known is None:
            raise InvalidOrderError(f"unknown instrument {req.instrument_id!r}", "unknown_instrument")
        return known

    async def _audit_reject(self, target: str, broker_account_id: str, reason: str, message: str, local: bool) -> None:
        await self._audit.append(
            AuditEvent(
                action="broker.order.reject",
                target=target,
                details={"broker_account_id": broker_account_id, "reason": reason, "message": message, "local": local},
            )
        )

    def _existing_ack(self, record: _OrderRecord) -> SubmitAck:
        if record.reject is not None:
            raise OrderRejectedError(*record.reject)
        if record.perm_id == 0:
            raise SubmitOutcomeUnknownError(f"{record.client_order_id} is awaiting broker confirmation")
        return SubmitAck(
            client_order_id=record.client_order_id,
            broker_order_id=str(record.perm_id),
            accepted_at=to_ns(record.placed_at),
            recovered=True,
        )

    async def _recover_submit(self, record: _OrderRecord) -> SubmitAck:
        """OE-003: a timed-out submit is looked up by orderRef / order id; never resubmitted."""
        for attempt in range(2):
            if attempt:
                await self._clock.sleep(self._cfg.lookup_retry_delay)
            found = await self._lookup(record)
            if found is not None:
                self._ingest_snapshot(found)
                if record.reject is not None:
                    raise OrderRejectedError(*record.reject)
                return SubmitAck(
                    client_order_id=record.client_order_id,
                    broker_order_id=str(record.perm_id),
                    accepted_at=to_ns(self._clock.now()),
                    recovered=True,
                )
        record.state = OrderState.UNKNOWN
        await self._publish_order(record)
        raise SubmitOutcomeUnknownError(
            f"{record.client_order_id}: no IBKR acknowledgement after {self._cfg.submit_timeout}s and lookup; "
            "not resubmitting, reconciliation will resolve it"
        )

    async def _lookup(self, record: _OrderRecord) -> OrderSnapshot | None:
        try:
            snapshots = [*await self._port.open_orders(), *await self._port.completed_orders()]
        except Exception:
            log.warning("order lookup failed", extra={"client_order_id": record.client_order_id})
            return None
        for snap in snapshots:
            if snap.order_ref == record.client_order_id or (
                snap.ib_client_id == record.ib_client_id and snap.ib_order_id == record.ib_order_id
            ):
                return snap
        return None

    async def cancel_order(
        self, broker_account_id: str, *, client_order_id: str = "", broker_order_id: str = ""
    ) -> CancelAck:
        """IB-023: cancel one platform-attributed order; never external orders, never a global cancel."""
        self._account_code(broker_account_id)
        record = self._find(broker_account_id, client_order_id, broker_order_id)
        if record.owner is OwnerKind.EXTERNAL:
            raise InvalidOrderError("orders placed outside the platform are managed in IBKR, not by the platform")
        self._require_connected(broker_account_id)
        if record.state in TERMINAL_STATES:
            raise OrderAlreadyFinalError(f"order is already {record.state.value}")
        await self._audit.append(
            AuditEvent(
                action="broker.order.cancel",
                target=record.client_order_id,
                details={"broker_account_id": broker_account_id, "broker_order_id": str(record.perm_id)},
            )
        )
        record.cancel_requested = True
        self._port.cancel_order(record.ib_order_id)
        log.info("cancel requested", extra={"client_order_id": record.client_order_id})
        return CancelAck(
            client_order_id=record.client_order_id,
            broker_order_id=str(record.perm_id),
            requested_at=to_ns(self._clock.now()),
        )

    async def replace_order(
        self,
        broker_account_id: str,
        client_order_id: str,
        changes: OrderChanges,
        new_client_order_id: str = "",
    ) -> ReplaceAck:
        """IB-022: modify in place under the next attempt's attribution ID; the permId is unchanged."""
        account_code = self._account_code(broker_account_id)
        record = self._find(broker_account_id, client_order_id, "")
        if record.owner is OwnerKind.EXTERNAL or record.request is None or record.placed is None:
            raise InvalidOrderError("only orders placed by this adapter can be replaced")
        self._require_ready(broker_account_id)
        if record.state in TERMINAL_STATES:
            raise OrderAlreadyFinalError(f"order is already {record.state.value}")
        if record.state is OrderState.UNKNOWN:
            raise InvalidOrderError(f"order is {record.state.value}")
        current = parse_attribution(record.client_order_id)
        new_cid = new_client_order_id or current.with_attempt(current.attempt + 1)
        try:
            new_attr = validate_order_ref(new_cid, self._cfg.order_ref_max_len)
        except AttributionError as exc:
            raise InvalidOrderError(str(exc), "invalid_attribution") from None
        if (new_attr.short_id, new_attr.intent_ulid) != (current.short_id, current.intent_ulid):
            raise InvalidOrderError(
                "a replace keeps the strategy and intent; only the attempt changes", "invalid_attribution"
            )
        if new_cid in self._by_cid:
            raise InvalidOrderError(f"{new_cid} is already in use")
        new_req = record.request.model_copy(
            update={
                "client_order_id": new_cid,
                "qty": changes.qty if changes.qty is not None else record.request.qty,
                "limit_price": changes.limit_price if changes.limit_price is not None else record.request.limit_price,
                "stop_price": changes.stop_price if changes.stop_price is not None else record.request.stop_price,
                "tif": changes.tif if changes.tif is not None else record.request.tif,
            }
        )
        if new_req.qty <= record.filled:
            raise InvalidOrderError("new quantity must exceed the filled quantity")
        instrument = await self._instrument_for(new_req)
        placed = build_place_order(new_req, instrument, account_code, new_cid)
        await self._audit.append(
            AuditEvent(
                action="broker.order.replace",
                target=record.client_order_id,
                details={
                    "broker_account_id": broker_account_id,
                    "new_client_order_id": new_cid,
                    "broker_order_id": str(record.perm_id),
                    "qty": str(new_req.qty),
                    "limit_price": str(placed.lmt_price) if placed.lmt_price is not None else None,
                    "stop_price": str(placed.aux_price) if placed.aux_price is not None else None,
                },
            )
        )
        old_cid = record.client_order_id
        record.pending_replace = (old_cid, record.request, record.placed)
        record.aliases.append(old_cid)
        record.client_order_id = new_cid
        record.request = new_req
        record.placed = placed
        record.ref_checked = False
        self._by_cid[new_cid] = record
        self._port.modify_order(record.ib_order_id, placed)
        await self._sink.order_update(self._replaced_view(record, old_cid))
        await self._publish_order(record)
        return ReplaceAck(
            client_order_id=old_cid,
            new_client_order_id=new_cid,
            broker_order_id=str(record.perm_id),
            requested_at=to_ns(self._clock.now()),
        )

    def _revert_replace(self, record: _OrderRecord) -> None:
        assert record.pending_replace is not None
        old_cid, old_req, old_placed = record.pending_replace
        self._by_cid.pop(record.client_order_id, None)
        record.client_order_id = old_cid
        record.request = old_req
        record.placed = old_placed
        record.aliases.remove(old_cid)
        record.pending_replace = None
        record.ref_checked = True

    def _find(self, broker_account_id: str, client_order_id: str, broker_order_id: str) -> _OrderRecord:
        record: _OrderRecord | None = None
        if client_order_id:
            record = self._by_cid.get(client_order_id)
        elif broker_order_id:
            try:
                record = self._by_perm.get(int(broker_order_id))
            except ValueError:
                record = None
        if record is None or record.broker_account_id != broker_account_id:
            raise OrderNotFoundError(client_order_id or broker_order_id)
        return record

    async def get_order(
        self, broker_account_id: str, *, client_order_id: str = "", broker_order_id: str = ""
    ) -> BrokerOrder | None:
        """One order by attribution ID or permId, open or closed (contract GetOrder)."""
        self._account_code(broker_account_id)
        try:
            return self._view(self._find(broker_account_id, client_order_id, broker_order_id))
        except OrderNotFoundError:
            pass
        if not self._port.is_connected():
            return None
        await self.list_orders(broker_account_id)
        try:
            return self._view(self._find(broker_account_id, client_order_id, broker_order_id))
        except OrderNotFoundError:
            return None

    def order_view(self, client_order_id: str) -> BrokerOrder | None:
        record = self._by_cid.get(client_order_id)
        return self._view(record) if record is not None else None

    def last_exec_time(self, broker_account_id: str) -> int:
        return self._cursor.last_exec_time(broker_account_id)

    @property
    def environment(self) -> Environment:
        return self._cfg.environment

    @property
    def resolver(self) -> InstrumentResolver:
        return self._resolver

    async def list_open_orders(self, broker_account_id: str) -> list[BrokerOrder]:
        code = self._account_code(broker_account_id)
        self._require_connected(broker_account_id)
        out = []
        for snap in await self._port.open_orders():
            if snap.account == code:
                out.append(self._view(await self._ingest_and_publish(snap)))
        return out

    async def list_orders(self, broker_account_id: str, since: datetime | None = None) -> list[BrokerOrder]:
        """Open and completed orders, including manual ones placed outside the platform (VP-001)."""
        code = self._account_code(broker_account_id)
        self._require_connected(broker_account_id)
        snaps = [*await self._port.open_orders(), *await self._port.completed_orders()]
        out: list[BrokerOrder] = []
        for snap in snaps:
            if snap.account != code:
                continue
            record = await self._ingest_and_publish(snap)
            if since is None or record.placed_at >= since:
                out.append(self._view(record))
        return out

    async def list_executions(self, broker_account_id: str, since: datetime | None = None) -> list[ExecutionReport]:
        """Executions for gap fill (IBKR serves the current day at most, IB-033)."""
        code = self._account_code(broker_account_id)
        self._require_connected(broker_account_id)
        out = []
        for fill, commission in await self._port.executions(since):
            if fill.account != code:
                continue
            out.append(self._execution_report(fill, commission or self._late_fees.get(fill.exec_id)))
        return out

    async def get_account_snapshot(self, broker_account_id: str) -> AccountSnapshot:
        code = self._account_code(broker_account_id)
        self._require_connected(broker_account_id)
        values = await self._port.account_values(code)
        positions = [p for p in await self._port.positions() if p.account == code]
        tags: dict[str, Decimal] = {}
        for v in values:
            if v.currency not in ("USD", "BASE", ""):
                continue
            with contextlib.suppress(ArithmeticError, ValueError):
                tags.setdefault(v.tag, Decimal(v.value))
        excess = tags.get("ExcessLiquidity")
        snapshot = AccountSnapshot(
            broker_account_id=broker_account_id,
            net_liquidation=tags.get("NetLiquidation"),
            cash=tags.get("TotalCashValue"),
            buying_power=tags.get("BuyingPower"),
            excess_liquidity=excess,
            maintenance_margin=tags.get("MaintMarginReq"),
            margin_deficit=excess is not None and excess < 0,
            positions=tuple(
                Position(
                    con_id=p.con_id,
                    instrument_id=self._resolver.instrument_id_for(p.con_id),
                    symbol=p.symbol,
                    qty=p.qty,
                    avg_cost=p.avg_cost,
                )
                for p in positions
                if p.qty != 0
            ),
            as_of=to_ns(self._clock.now()),
        )
        await self._apply_margin(snapshot)
        return snapshot

    async def _apply_margin(self, snapshot: AccountSnapshot) -> None:
        """BR-021 / RM-013: a margin deficit degrades the account until a clean snapshot."""
        acct = snapshot.broker_account_id
        key = f"ibkr-{acct}-margin-deficit"
        if snapshot.margin_deficit:
            if await self._set_hold(acct, "margin_deficit", True):
                await self._sink.alert(
                    AlertEvent(
                        severity=Severity.CRITICAL,
                        subject=f"broker.ibkr.{self._cfg.environment.value}.margin_deficit",
                        message=f"{acct} reports negative excess liquidity (margin deficit): orders halted (RM-013)",
                        dedupe_key=key,
                    )
                )
        elif await self._set_hold(acct, "margin_deficit", False):
            await self._sink.request_reconciliation(ReconciliationRequest(acct, "margin_restored"))
            await self._sink.resolve_alert(key, f"{acct} margin deficit cleared")

    async def historical_bars(self, instrument_id: str, request: HistoricalRequest) -> list[Bar]:
        """Paced historical bars (IB-050) on the market-data connection."""
        async with self._pacer.slot(request):
            bars = await self._md_port.historical_bars(request)
        return [
            Bar(
                con_id=request.con_id,
                ts_open=to_ns(b.time),
                open=b.open,
                high=b.high,
                low=b.low,
                close=b.close,
                volume=b.volume,
                vwap=b.wap,
                trade_count=b.count,
            )
            for b in bars
        ]

    # ---- resync (IB-016, OE-008) ---------------------------------------------------------------
    async def _resync(self, resubscribe: bool) -> None:
        for snap in await self._port.open_orders():
            await self._ingest_if_ours(snap)
        for snap in await self._port.completed_orders():
            await self._ingest_if_ours(snap)
        for fill, commission in await self._port.executions(self._resync_since()):
            if fill.account in self._code_to_id:
                await self._publish_execution(fill, commission, source="resync")
        now = self._clock.now()
        gap = self._execution_gap(now)
        for acct in self._cfg.accounts:
            await self._sink.account_snapshot(await self.get_account_snapshot(acct))
            if gap:
                await self._sink.alert(
                    AlertEvent(
                        severity=Severity.WARNING,
                        subject=f"broker.ibkr.{self._cfg.environment.value}.execution_gap",
                        message=(
                            f"{acct}: the session was down across an IBKR trading-day boundary; executions "
                            "from before today cannot be fetched over the API. Reconcile against the statement."
                        ),
                        dedupe_key=f"ibkr-{acct}-execution-gap",
                        runbook=RUNBOOK_OUTAGE,
                    )
                )
            await self._sink.request_reconciliation(
                ReconciliationRequest(acct, "execution_gap" if gap else "reconnect")
            )
        self._cursor.mark_ready(to_ns(now))

    def _resync_since(self) -> datetime | None:
        """BR-011: executions since the last acknowledged one minus a 5-minute overlap."""
        times = [self._cursor.last_exec_time(a) for a in self._cfg.accounts]
        if not times or min(times) == 0:
            return None
        return datetime.fromtimestamp(min(times) / 1e9, tz=UTC) - RESYNC_OVERLAP

    def _execution_gap(self, now: datetime) -> bool:
        """IB-033: reqExecutions only reaches back to the start of the current (ET) day."""
        last = self._cursor.last_ready_at
        if not last:
            return False
        day_start = datetime.combine(now.astimezone(ET).date(), datetime.min.time(), tzinfo=ET)
        return last < to_ns(day_start)

    async def run_snapshots(self) -> None:
        """Publish account snapshots every ``snapshot_interval`` while ready (IB-040)."""
        while True:
            await self._clock.sleep(self._cfg.snapshot_interval)
            if not self.supervisor.is_ready:
                continue
            try:
                for acct in self._cfg.accounts:
                    await self._sink.account_snapshot(await self.get_account_snapshot(acct))
                self._cursor.mark_ready(to_ns(self._clock.now()))
            except Exception:
                log.warning("account snapshot failed", exc_info=True)

    # ---- inbound gateway events ----------------------------------------------------------------
    def _spawn(self, coro: object) -> None:
        task = asyncio.ensure_future(coro)  # type: ignore[call-overload]
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def _on_error(self, req_id: int, code: int, message: str) -> None:
        info = classify(code)
        record = self._by_key.get((self._port.client_id, req_id)) if req_id > 0 else None
        if record is not None:
            if info.category not in (Category.ORDER_REJECT, Category.CONTRACT):
                log.info("order message", extra={"client_order_id": record.client_order_id, "code": code})
                return
            if record.perm_id and code in _ACTION_REJECT_CODES:
                # A rejected cancel/modify leaves the working order untouched (IB-022, IB-023).
                log.warning("order action rejected", extra={"client_order_id": record.client_order_id, "code": code})
                if record.pending_replace is not None:
                    self._revert_replace(record)
                    self._spawn(self._publish_order(record))
                return
            record.reject = (code, message)
            record.state = OrderState.REJECTED
            if record.ack is not None and not record.ack.done():
                record.ack.set_result(None)
            self._spawn(self._publish_order(record))
            if record.request is not None:
                self._spawn(
                    self._audit_reject(record.client_order_id, record.broker_account_id, str(code), message, False)
                )
            return
        if is_pacing_message(code, message):
            self._pacer.penalize()
            return
        if info.category in (Category.CONNECTIVITY, Category.SESSION) or info.action in (
            Action.RESYNC,
            Action.RESYNC_RESUBSCRIBE,
            Action.DEGRADE,
            Action.RECONNECT,
        ):
            self.supervisor.notify_error(code, message)
            return
        log.info("gateway message", extra={"code": code, "category": info.category.value})

    def _on_order(self, snap: OrderSnapshot) -> None:
        self._spawn(self._ingest_if_ours(snap))

    async def _ingest_and_publish(self, snap: OrderSnapshot) -> _OrderRecord:
        record = self._by_key.get((snap.ib_client_id, snap.ib_order_id)) or self._by_perm.get(snap.perm_id)
        before = (record.state, record.filled, record.perm_id, record.client_order_id) if record else None
        record = self._ingest_snapshot(snap)
        if before != (record.state, record.filled, record.perm_id, record.client_order_id):
            await self._publish_order(record)
        await self._check_ref_echo(record, snap)
        return record

    async def _ingest_if_ours(self, snap: OrderSnapshot) -> None:
        if snap.account in self._code_to_id:
            await self._ingest_and_publish(snap)

    def _ingest_snapshot(self, snap: OrderSnapshot) -> _OrderRecord:
        record = None
        if snap.ib_order_id:
            record = self._by_key.get((snap.ib_client_id, snap.ib_order_id))
        if record is None and snap.perm_id:
            record = self._by_perm.get(snap.perm_id)
        if record is None:
            record = self._new_external_record(snap)
        if snap.perm_id and not record.perm_id:
            record.perm_id = snap.perm_id
        if record.perm_id:
            self._by_perm[record.perm_id] = record
        if snap.ib_order_id:
            self._by_key[(snap.ib_client_id, snap.ib_order_id)] = record
        record.snapshot = snap
        record.filled = max(record.filled, snap.filled)
        state = self._map(record, snap)
        sticky = record.state in TERMINAL_STATES and state not in TERMINAL_STATES
        if not sticky:
            record.state = state
        if record.ack is not None and not record.ack.done() and record.perm_id:
            record.ack.set_result(None)
        return record

    def _new_external_record(self, snap: OrderSnapshot) -> _OrderRecord:
        resolution = resolve_owner(snap.order_ref)
        if resolution.malformed and snap.perm_id not in self._malformed_alerted:
            self._malformed_alerted.add(snap.perm_id)
            self._spawn(
                self._sink.alert(
                    AlertEvent(
                        severity=Severity.WARNING,
                        subject=f"broker.ibkr.{self._cfg.environment.value}.attribution",
                        message=f"order {snap.perm_id} has a malformed tb1- orderRef; assigned to manual (VP-001)",
                        dedupe_key=f"ibkr-malformed-ref-{snap.perm_id}",
                    )
                )
            )
        record = _OrderRecord(
            ib_client_id=snap.ib_client_id,
            ib_order_id=snap.ib_order_id,
            perm_id=snap.perm_id,
            broker_account_id=self._code_to_id[snap.account],
            client_order_id=snap.order_ref if resolution.owner is not OwnerKind.EXTERNAL else "",
            owner=resolution.owner,
            strategy_short_id=resolution.strategy_short_id,
            instrument_id=self._resolver.instrument_id_for(snap.con_id),
            placed_at=self._clock.now(),
            ref_checked=True,
        )
        if record.client_order_id:
            self._by_cid.setdefault(record.client_order_id, record)
        return record

    def _map(self, record: _OrderRecord, snap: OrderSnapshot) -> OrderState:
        ctx = StatusContext(
            filled=record.filled,
            remaining=snap.remaining,
            reject_error=record.reject is not None,
            cancel_requested=record.cancel_requested,
            tif_lapsed=self._schedule.tif_lapsed(
                snap.order_type if snap.order_type in {"MOC", "LOC"} else snap.tif,
                snap.outside_rth,
                record.placed_at,
                self._clock.now(),
            ),
        )
        return map_status(snap.status, ctx)

    async def _check_ref_echo(self, record: _OrderRecord, snap: OrderSnapshot) -> None:
        """IB-002: IBKR must echo the exact orderRef we sent; otherwise attribution is unsafe."""
        if record.ref_checked or record.request is None or not snap.ref_echoed:
            return
        if snap.order_ref == record.client_order_id:
            record.ref_checked = True
            if record.pending_replace is not None:
                record.pending_replace = None
                await self._publish_order(record)
            return
        if record.pending_replace is not None and snap.order_ref == record.pending_replace[0]:
            return  # an update that predates the modification
        record.ref_checked = True
        record.state = OrderState.UNKNOWN
        await self._publish_order(record)
        await self.supervisor.fail_closed(
            "order_ref_mismatch",
            f"IBKR echoed orderRef of length {len(snap.order_ref)} for a {len(record.client_order_id)}-char "
            "attribution ID; attribution is unsafe. Orders are blocked until the limit is fixed (spike V1).",
        )

    def _on_execution(self, fill: ExecFill) -> None:
        if fill.account not in self._code_to_id:
            return
        if fill.exec_id in self._pending_execs or self._cursor.seen(self._code_to_id[fill.account], fill.exec_id):
            return  # duplicate delivery (OE-006, IB-031)
        commission = self._early_commissions.pop(fill.exec_id, None)
        if commission is not None:
            self._spawn(self._publish_execution(fill, commission, source="stream"))
            return
        timer = asyncio.ensure_future(self._commission_timeout(fill))
        self._pending_execs[fill.exec_id] = (fill, timer)

    def _on_commission(self, commission: CommissionFill) -> None:
        pending = self._pending_execs.pop(commission.exec_id, None)
        if pending is not None:
            fill, timer = pending
            timer.cancel()
            self._spawn(self._publish_execution(fill, commission, source="stream"))
            return
        if any(self._cursor.seen(acct, commission.exec_id) for acct in self._cfg.accounts):
            self._late_fees[commission.exec_id] = commission
            log.info("late commission report", extra={"broker_exec_id": commission.exec_id})
            return
        self._early_commissions[commission.exec_id] = commission

    async def _commission_timeout(self, fill: ExecFill) -> None:
        await self._clock.sleep(self._cfg.commission_wait)
        if self._pending_execs.pop(fill.exec_id, None) is not None:
            await self._publish_execution(fill, None, source="stream")

    async def _publish_execution(self, fill: ExecFill, commission: CommissionFill | None, *, source: str) -> None:
        acct = self._code_to_id[fill.account]
        if self._cursor.seen(acct, fill.exec_id):
            return
        report = self._execution_report(fill, commission)
        await self._sink.execution(report)
        # Mark after publishing: a crash in between re-publishes (consumers dedupe, OE-006) rather
        # than losing the execution.
        self._cursor.mark(acct, fill.exec_id, report.event_time)
        log.info("execution published", extra={"broker_exec_id": fill.exec_id, "source": source})

    def _execution_report(self, fill: ExecFill, commission: CommissionFill | None) -> ExecutionReport:
        acct = self._code_to_id[fill.account]
        record = self._by_perm.get(fill.perm_id)
        order_ref = fill.order_ref or (record.client_order_id if record else "")
        resolution = resolve_owner(order_ref)
        base, revision = exec_base_and_revision(fill.exec_id)
        corrects = ""
        if revision:
            prior = [
                e
                for e in self._cursor.seen_with_base(acct, base)
                if exec_base_and_revision(e)[1] < revision and e != fill.exec_id
            ]
            corrects = max(prior, key=lambda e: exec_base_and_revision(e)[1], default="")
        return ExecutionReport(
            broker_account_id=acct,
            broker_exec_id=fill.exec_id,
            client_order_id=order_ref if resolution.owner is not OwnerKind.EXTERNAL else "",
            broker_order_id=str(fill.perm_id),
            owner=resolution.owner,
            strategy_short_id=resolution.strategy_short_id,
            instrument_id=self._resolver.instrument_id_for(fill.con_id),
            con_id=fill.con_id,
            symbol=fill.symbol,
            side=side_from_action(fill.side),
            qty=fill.shares,
            price=fill.price,
            cum_qty=fill.cum_qty,
            fees=commission.commission if commission else None,
            fee_currency=commission.currency if commission else "",
            corrects_exec_id=corrects,
            event_time=to_ns(fill.time),
            ingested_at=to_ns(self._clock.now()),
        )

    # ---- views ---------------------------------------------------------------------------------
    async def _publish_order(self, record: _OrderRecord) -> None:
        await self._sink.order_update(self._view(record))

    def _view(self, record: _OrderRecord) -> BrokerOrder:
        snap = record.snapshot
        placed = record.placed
        return BrokerOrder(
            broker_account_id=record.broker_account_id,
            client_order_id=record.client_order_id,
            broker_order_id=str(record.perm_id) if record.perm_id else "",
            ib_order_id=record.ib_order_id,
            ib_client_id=record.ib_client_id,
            owner=record.owner,
            strategy_short_id=record.strategy_short_id,
            instrument_id=record.instrument_id,
            con_id=snap.con_id if snap else (placed.con_id if placed else 0),
            symbol=snap.symbol if snap else (placed.symbol if placed else ""),
            side=side_from_action(snap.action if snap else (placed.action if placed else "BUY")),
            qty=snap.total_qty if snap else (placed.total_qty if placed else Decimal(0)),
            order_type=snap.order_type if snap else (placed.order_type if placed else ""),
            limit_price=snap.lmt_price if snap else (placed.lmt_price if placed else None),
            stop_price=snap.aux_price if snap else (placed.aux_price if placed else None),
            tif=snap.tif if snap else (placed.tif if placed else ""),
            state=OrderState.PENDING_REPLACE
            if record.pending_replace is not None and record.state not in TERMINAL_STATES
            else record.state,
            raw_status=snap.status if snap else "",
            filled_qty=record.filled,
            remaining_qty=snap.remaining if snap else (placed.total_qty if placed else Decimal(0)),
            avg_fill_price=snap.avg_fill_price if snap else None,
            why_held=snap.why_held if snap else "",
            updated_at=to_ns(self._clock.now()),
        )

    def _replaced_view(self, record: _OrderRecord, old_cid: str) -> BrokerOrder:
        return self._view(record).model_copy(update={"client_order_id": old_cid, "state": OrderState.REPLACED})

    # ---- lifecycle -----------------------------------------------------------------------------
    async def run(self) -> None:
        snapshots = asyncio.ensure_future(self.run_snapshots())
        try:
            await self.supervisor.run()
        finally:
            snapshots.cancel()
            for task in list(self._background):
                task.cancel()

    def stop(self) -> None:
        self.supervisor.stop()

    def accounts(self) -> Sequence[str]:
        return list(self._cfg.accounts)

    def pending_executions(self) -> int:
        return len(self._pending_execs)
