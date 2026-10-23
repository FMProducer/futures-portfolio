"""Two-sided share trailing: reduce on peak pullback, add on trough rebound."""
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional, Tuple

FUTURES_LEGS: Tuple[str, ...] = ("BASE_LONG", "BASE_SHORT")


def _dec(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        out = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return out if out.is_finite() else None


def fresh_state() -> Dict[str, Dict[str, Any]]:
    return {key: {"peak": None, "trough": None}
            for key in FUTURES_LEGS}


def load_state(raw: Any) -> Dict[str, Dict[str, Any]]:
    """Safe load: missing/legacy/corrupt rows fall back to unarmed state."""
    state = fresh_state()
    if not isinstance(raw, dict):
        return state
    for key in FUTURES_LEGS:
        row = raw.get(key)
        if not isinstance(row, dict):
            continue
        for field in ("peak", "trough"):
            value = _dec(row.get(field))
            state[key][field] = str(value) if value is not None else None
    return state


def step(state: Dict[str, Dict[str, Any]], key: str, share: Any, target: Any,
         upper: Any, lower: Any, distance: Any, flat: bool = False) -> Tuple[bool, bool, Optional[str]]:
    """Observe extrema after band activation; wait for a pullback or rebound."""
    if key not in FUTURES_LEGS:
        raise ValueError(f"trailing only covers futures legs, got {key}")
    if flat:
        reset_leg(state, key)
        return False, False, "FLAT"
    sh, tgt, up, lo, dist = map(lambda x: Decimal(str(x)), (share, target, upper, lower, distance))
    if not all(x.is_finite() for x in (sh, tgt, up, lo, dist)) or dist < 0:
        raise ValueError("invalid share trailing observation")
    row = state[key]
    peak, trough = _dec(row.get("peak")), _dec(row.get("trough"))
    if sh >= up:
        peak = sh if peak is None else max(peak, sh)
    if sh <= lo:
        trough = sh if trough is None else min(trough, sh)
    row["peak"] = str(peak) if peak is not None else None
    row["trough"] = str(trough) if trough is not None else None
    return (peak is not None and sh > tgt and peak - sh >= dist,
            trough is not None and sh < tgt and sh - trough >= dist, None)


def consume(state: Dict[str, Dict[str, Any]], key: str, direction: str) -> None:
    """A confirmed fill clears only its own signal."""
    if direction not in ("REDUCE", "ADD"):
        raise ValueError(f"invalid trailing direction {direction}")
    state[key]["peak" if direction == "REDUCE" else "trough"] = None


def reset_leg(state: Dict[str, Dict[str, Any]], key: str) -> None:
    """Full close of a leg: both of its extrema are reset."""
    state[key] = fresh_state()[key]


def reset_all(state: Dict[str, Dict[str, Any]]) -> None:
    """Portfolio close / new session / spec change: all extrema are reset."""
    for key in FUTURES_LEGS:
        reset_leg(state, key)


def spec_fingerprint(distance: Any, targets: Dict[str, Dict], threshold_surplus: Any,
                    threshold_deficit: Any) -> Dict[str, Any]:
    return {
        "model": "share-pullback-rebound-v2",
        "distance": float(Decimal(str(distance))),
        "target_long": float(Decimal(str(targets["BASE_LONG"]["share"]))),
        "target_short": float(Decimal(str(targets["BASE_SHORT"]["share"]))),
        "surplus": float(Decimal(str(threshold_surplus))),
        "deficit": float(Decimal(str(threshold_deficit))),
    }


def spec_mismatch(stored: Any, current: Dict[str, Any]) -> bool:
    """True when a persisted spec differs from the live one (incompatible observations)."""
    if not isinstance(stored, dict):
        return False
    return any(stored.get(field) != value for field, value in current.items())


def fill_receipt(book: Dict, result: Dict, *, record: bool = False) -> bool:
    """Reject conflicting cumulative fills; deduplicate before economic mutation."""
    identity = result.get("execution_id")
    if not identity:
        raise ValueError("confirmed fill has no stable execution identity; reconcile before continuing")
    payload = {key: str(result.get(key)) for key in
               ("symbol", "type", "side", "qty", "price", "commission", "trade_pnl", "reduce_only")}
    journal = book.setdefault("share_fill_receipts", {})
    if not isinstance(journal, dict):
        raise ValueError("invalid fill receipt journal")
    old = journal.get(identity)
    if old is not None:
        if old != payload:
            raise ValueError("changed cumulative fill requires reconciliation")
        return False
    if record:
        journal[identity] = payload
    return True


def restore_checkpoint(state: Dict, book: Dict) -> bool:
    """Recover consumed signals from the same atomic file as position/cash effects."""
    checkpoint = book.get("share_trail_checkpoint")
    if not isinstance(checkpoint, dict):
        return False
    identity = checkpoint.get("execution_id")
    if not identity or identity == state.get("share_trail_last_fill"):
        return False
    if identity not in book.get("share_fill_receipts", {}):
        raise ValueError("share trail checkpoint has no confirmed receipt")
    if checkpoint.get("spec") == state.get("share_trail_spec"):
        state["share_trail"] = load_state(checkpoint.get("trail"))
    state["share_trail_last_fill"] = identity
    return True
