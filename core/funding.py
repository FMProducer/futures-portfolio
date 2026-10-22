"""B1 funding: V-only accounting, shortfall halt, per-leg close recovery.

Funding is charged to V (virt_debt), not CASH.  If funding exceeds V,
the full debit is applied once, V becomes negative, a stable halt is
committed before any close, and positions are closed with tracked per-leg
status for crash-safe recovery.

Durable state: journal + halt + close intents survive JSON save/reload.
"""
import json
import logging
import os
import time
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    pass  # BacktestState is referenced by string name in signatures only.

logger = logging.getLogger(__name__)

# ── data model ───────────────────────────────────────────────────────────────

@dataclass
class FundingEvent:
    event_id: str
    signed_amount: Decimal   # positive = receipt, negative = payment
    event_time: float        # unix timestamp
    source: str = "scenario"
    source_ref: str = ""
    portfolio_id: str = "default"
    symbol: str = ""
    asset: str = "USDT"
    raw_payload_hash: str = ""
    status: str = "received"  # received | applied | conflict | quarantined
    applied_at: Optional[float] = None
    kind: str = "FUNDING_FEE"

    @property
    def payload_key(self) -> str:
        """Stable payload fingerprint for duplicate detection."""
        return f"{self.signed_amount}:{self.event_time}:{self.source_ref}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "signed_amount": float(self.signed_amount),
            "event_time": self.event_time,
            "source": self.source, "source_ref": self.source_ref,
            "portfolio_id": self.portfolio_id, "symbol": self.symbol,
            "asset": self.asset, "raw_payload_hash": self.raw_payload_hash,
            "status": self.status, "applied_at": self.applied_at,
            "kind": self.kind,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "FundingEvent":
        return cls(
            event_id=d["event_id"],
            signed_amount=Decimal(str(d["signed_amount"])),
            event_time=d["event_time"],
            source=d.get("source", "scenario"),
            source_ref=d.get("source_ref", ""),
            portfolio_id=d.get("portfolio_id", "default"),
            symbol=d.get("symbol", ""),
            asset=d.get("asset", "USDT"),
            raw_payload_hash=d.get("raw_payload_hash", ""),
            status=d.get("status", "received"),
            applied_at=d.get("applied_at"),
            kind=d.get("kind", "FUNDING_FEE"),
        )


@dataclass
class CloseLegState:
    leg: str                    # "BASE_LONG" | "BASE_SHORT"
    close_event_id: str
    status: str = "pending"     # pending | filled | partial | error | skipped | quarantined
    qty_closed: Decimal = Decimal("0")
    qty_remaining: Decimal = Decimal("0")  # not yet closed
    fill_price: Decimal = Decimal("0")
    commission: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    filled_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "leg": self.leg,
            "close_event_id": self.close_event_id,
            "status": self.status,
            "qty_closed": float(self.qty_closed),
            "qty_remaining": float(self.qty_remaining),
            "fill_price": float(self.fill_price),
            "commission": float(self.commission),
            "realized_pnl": float(self.realized_pnl),
            "filled_at": self.filled_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CloseLegState":
        return cls(
            leg=d["leg"],
            close_event_id=d["close_event_id"],
            status=d.get("status", "pending"),
            qty_closed=Decimal(str(d.get("qty_closed", 0))),
            qty_remaining=Decimal(str(d.get("qty_remaining", 0))),
            fill_price=Decimal(str(d.get("fill_price", 0))),
            commission=Decimal(str(d.get("commission", 0))),
            realized_pnl=Decimal(str(d.get("realized_pnl", 0))),
            filled_at=d.get("filled_at"),
        )


@dataclass
class FundingHalt:
    active: bool = False
    reason: str = ""                     # "funding_shortfall"
    event_id: str = ""
    halted_at: float = 0.0
    shortfall_amount: Decimal = Decimal("0")
    increases_blocked: bool = False
    close_state: Dict[str, CloseLegState] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "active": self.active,
            "reason": self.reason,
            "event_id": self.event_id,
            "halted_at": self.halted_at,
            "shortfall_amount": float(self.shortfall_amount),
            "increases_blocked": self.increases_blocked,
            "close_state": {k: v.to_dict() for k, v in self.close_state.items()},
        }

    @classmethod
    def from_dict(cls, d: Optional[Dict]) -> "FundingHalt":
        if not d:
            return cls()
        cs = {}
        for k, v in d.get("close_state", {}).items():
            cs[k] = CloseLegState.from_dict(v)
        return cls(
            active=d.get("active", False),
            reason=d.get("reason", ""),
            event_id=d.get("event_id", ""),
            halted_at=d.get("halted_at", 0.0),
            shortfall_amount=Decimal(str(d.get("shortfall_amount", 0))),
            increases_blocked=d.get("increases_blocked", False),
            close_state=cs,
        )


# ── journal ──────────────────────────────────────────────────────────────────

class FundingJournal:
    """Ordered journal of funding events with duplicate/conflict detection."""

    def __init__(self):
        self.events: Dict[str, FundingEvent] = {}       # event_id → event
        self._order: List[str] = []                     # insertion order

    def __len__(self) -> int:
        return len(self._order)

    def to_list(self) -> List[Dict]:
        return [self.events[eid].to_dict() for eid in self._order]

    @classmethod
    def from_list(cls, data: Optional[List[Dict]]) -> "FundingJournal":
        journal = cls()
        if not data:
            return journal
        for d in data:
            event = FundingEvent.from_dict(d)
            journal.events[event.event_id] = event
            journal._order.append(event.event_id)
        return journal

    # ── idempotent receive ────────────────────────────────────────────────

    def receive(self, event: FundingEvent) -> str:
        """Register a new event.  Returns 'new' | 'duplicate' | 'conflict'.

        - 'new': persisted as received, no accounting effect yet.
        - 'duplicate': same id + same payload (amount + time + source_ref)
          AND already applied → no-op.
        - 'conflict': same id but different payload → quarantined.
        """
        existing = self.events.get(event.event_id)
        if existing is not None:
            # Same payload already applied → harmless duplicate.
            if existing.status == "applied":
                if (existing.signed_amount == event.signed_amount
                        and existing.event_time == event.event_time
                        and existing.source_ref == event.source_ref):
                    return "duplicate"
                existing.status = "conflict"
                logger.error(
                    f"Funding conflict: {event.event_id} — "
                    f"applied payload differs from incoming"
                )
                return "conflict"
            # Same event still received → resume application.
            if existing.status == "received":
                if (existing.signed_amount == event.signed_amount
                        and existing.event_time == event.event_time
                        and existing.source_ref == event.source_ref):
                    return "duplicate"
                existing.status = "conflict"
                logger.error(f"Funding conflict: {event.event_id} — received payload differs")
                return "conflict"
            # Already quarantined or conflict.
            return existing.status

        event.status = "received"
        self.events[event.event_id] = event
        self._order.append(event.event_id)
        return "new"

    def mark_applied(self, event_id: str) -> None:
        event = self.events.get(event_id)
        if event and event.status == "received":
            event.status = "applied"
            event.applied_at = time.time()

    def mark_quarantined(self, event_id: str, reason: str = "late_event") -> None:
        event = self.events.get(event_id)
        if event:
            event.status = "quarantined"
            logger.warning(f"Funding event {event_id} quarantined: {reason}")

    def get_received(self) -> List[FundingEvent]:
        """Return un-applied received events in insertion order."""
        return [self.events[eid] for eid in self._order
                if self.events[eid].status == "received"]

    def latest_applied_time(self) -> float:
        latest = 0.0
        for eid in self._order:
            e = self.events[eid]
            if e.status == "applied" and (e.applied_at or 0) > latest:
                latest = e.applied_at or 0
        return latest


# ── save / reload (durable boundary) ─────────────────────────────────────────

def save_funding_state(journal: FundingJournal, halt: FundingHalt,
                        state: Dict, wallet: Optional[Dict] = None) -> Dict:
    """Serialize funding state into a dict suitable for JSON persistence."""
    state["funding_journal"] = journal.to_list()
    state["funding_halt"] = halt.to_dict()
    return state


def load_funding_state(state: Dict) -> Tuple[FundingJournal, FundingHalt]:
    """Restore journal + halt from persisted state dict (idempotent)."""
    journal = FundingJournal.from_list(state.get("funding_journal"))
    halt = FundingHalt.from_dict(state.get("funding_halt"))
    return journal, halt


# ── funding application ──────────────────────────────────────────────────────

WALLET_PRECISION = Decimal("0.0001")


def apply_funding_paper(signed_amount: Decimal, paper_wallet: Dict,
                         strategy_state: Dict) -> Dict:
    """Apply one funding event to paper wallet + strategy state.

    Both wallet and V are quantised to WALLET_PRECISION together to avoid
    rounding a CASH drift.  The remainder is tracked in a rounding buffer.
    """
    amount = Decimal(str(signed_amount))

    # Accumulate raw values first, then quantise both in lock-step.
    wallet_raw = Decimal(str(paper_wallet.get("balance", 0)))
    v_raw = Decimal(str(strategy_state.get("virt_debt", 0)))
    buf_raw = Decimal(str(strategy_state.get("funding_rounding_buf", 0)))

    wallet_raw += amount
    v_raw += amount

    wallet_q = wallet_raw.quantize(WALLET_PRECISION)
    v_q = v_raw.quantize(WALLET_PRECISION)
    # Rounding remainder: must conserve total TPV.
    rounding = (wallet_raw - wallet_q) + (v_raw - v_q)

    paper_wallet["balance"] = float(wallet_q)
    strategy_state["virt_debt"] = float(v_q)
    strategy_state["funding_rounding_buf"] = float(buf_raw + rounding)

    prev_paid = Decimal(str(strategy_state.get("funding_paid", 0)))
    strategy_state["funding_paid"] = float(prev_paid - amount)

    v_after = Decimal(str(strategy_state["virt_debt"]))
    shortfall = max(Decimal("0"), -v_after)

    return {
        "v_before": float(Decimal(str(strategy_state.get("virt_debt", 0)))),
        "v_after": float(v_after),
        "shortfall": float(shortfall),
        "cash_change": 0.0,
    }


def apply_funding_backtest(signed_amount: Decimal, state) -> Dict:
    """Apply one funding event to BacktestState (V-only).

    Backtest val_cash is separate from virt_debt — only virt_debt changes.
    """
    amount = Decimal(str(signed_amount))
    v_before = state.virt_debt
    state.virt_debt += amount
    v_after = state.virt_debt
    shortfall = max(Decimal("0"), -v_after)
    state.funding_paid -= amount
    return {
        "v_before": float(v_before), "v_after": float(v_after),
        "shortfall": float(shortfall),
        "cash_change": 0.0,
    }


# ── legacy CASH-first funding (baseline reproduction only) ──────────────────

def pay_funding_legacy(state, amount: Decimal) -> Decimal:
    """Old CASH-first waterfall — for legacy_constant compatibility only.

    Do NOT mix with signed_actual events.
    """
    amount = Decimal(str(amount))
    if amount > 0 and amount > state.val_cash:
        state.funding_from_virtual += state.release_virtual(amount - state.val_cash)
    state.val_cash -= amount
    state.funding_paid += amount
    return amount


# ── funding halt + close intents ─────────────────────────────────────────────

def commit_funding_halt(
    halt: FundingHalt,
    event_id: str,
    shortfall: Decimal,
    strategy_state: Optional[Dict] = None,
) -> FundingHalt:
    """Atomically commit a funding shortfall halt before any close actions."""
    halt.active = True
    halt.reason = "funding_shortfall"
    halt.event_id = event_id
    halt.halted_at = time.time()
    halt.shortfall_amount = shortfall
    halt.increases_blocked = True
    halt.close_state = {}

    if strategy_state is not None:
        strategy_state["funding_halt"] = halt.to_dict()
        strategy_state["funding_shortfall"] = float(shortfall)
    return halt


def begin_close_leg(halt: FundingHalt, leg: str, close_event_id: str,
                     qty_total: Decimal = Decimal("0")) -> CloseLegState:
    """Record a durable close intent for one leg.

    Returns existing filled/partial state (idempotent) or creates a new pending intent.
    """
    existing = halt.close_state.get(leg)
    if existing and existing.status in ("filled", "partial"):
        return existing
    intent = CloseLegState(
        leg=leg, close_event_id=close_event_id,
        status="pending", qty_remaining=qty_total,
    )
    halt.close_state[leg] = intent
    return intent


def record_close_result(
    halt: FundingHalt, leg: str,
    status: str, qty: Decimal, price: Decimal,
    commission: Decimal, realized_pnl: Decimal,
    qty_remaining: Decimal = Decimal("0"),
) -> None:
    """Persist the result of one leg close action (may be partial)."""
    cs = halt.close_state.get(leg)
    if cs is None:
        cs = CloseLegState(leg=leg, close_event_id=f"close_{leg}_{int(time.time())}")
        halt.close_state[leg] = cs
    cs.status = status
    cs.qty_closed += qty  # accumulate across partial fills
    cs.qty_remaining = qty_remaining
    cs.fill_price = price
    cs.commission += commission
    cs.realized_pnl += realized_pnl
    cs.filled_at = time.time()


def all_legs_closed(halt: FundingHalt) -> bool:
    """True when all expected legs (default 2: LONG + SHORT) are durably filled."""
    expected = 2  # hedged portfolio always has exactly two legs
    if not halt.close_state:
        return False
    if len(halt.close_state) < expected:
        return False  # not all legs registered yet
    return all(cs.status == "filled" for cs in halt.close_state.values())


# ── aggregate: compute funding from rates ────────────────────────────────────

def compute_funding_from_rates(
    qty_long: Decimal, qty_short: Decimal,
    mark_price: Decimal, funding_rate: Decimal,
) -> Decimal:
    """Compute net signed funding from positions and rate.

    long_funding  = -q_long  * p * r
    short_funding = +q_short * p * r
    funding       = long_funding + short_funding
    """
    long_funding = -qty_long * mark_price * funding_rate
    short_funding = qty_short * mark_price * funding_rate
    return long_funding + short_funding