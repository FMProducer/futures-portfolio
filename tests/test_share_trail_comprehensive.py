"""Comprehensive unit tests for core/share_trail.py module."""
import pytest
from decimal import Decimal
from core import share_trail


def test_fresh_state():
    """Verify fresh state creates unarmed structures for all futures legs."""
    state = share_trail.fresh_state()
    assert set(state.keys()) == set(share_trail.FUTURES_LEGS)
    for leg in share_trail.FUTURES_LEGS:
        assert state[leg]["peak"] is None
        assert state[leg]["trough"] is None


def test_load_state_safe_fallback():
    """Verify load_state handles missing, corrupted, or partial dictionaries."""
    assert share_trail.load_state(None) == share_trail.fresh_state()
    assert share_trail.load_state("invalid") == share_trail.fresh_state()

    partial_raw = {
        "BASE_LONG": {"peak": "0.35", "trough": "invalid"},
        "BASE_SHORT": None
    }
    loaded = share_trail.load_state(partial_raw)
    assert loaded["BASE_LONG"]["peak"] == "0.35"
    assert loaded["BASE_LONG"]["trough"] is None
    assert loaded["BASE_SHORT"]["peak"] is None
    assert loaded["BASE_SHORT"]["trough"] is None


def test_step_extrema_tracking_and_signals():
    """Verify step tracks peaks and troughs and generates reduce/add signals correctly."""
    state = share_trail.fresh_state()
    key = "BASE_LONG"
    target = Decimal("0.25")
    upper = Decimal("0.29")
    lower = Decimal("0.21")
    distance = Decimal("0.05")

    # 1. Share below upper threshold: no peak recorded, no signal
    red, add, res = share_trail.step(state, key, Decimal("0.27"), target, upper, lower, distance)
    assert not red and not add and res is None
    assert state[key]["peak"] is None

    # 2. Share breaches upper threshold: peak recorded
    red, add, res = share_trail.step(state, key, Decimal("0.35"), target, upper, lower, distance)
    assert not red and not add and res is None
    assert state[key]["peak"] == "0.35"

    # 3. New higher peak: peak updated
    red, add, res = share_trail.step(state, key, Decimal("0.38"), target, upper, lower, distance)
    assert not red and not add and res is None
    assert state[key]["peak"] == "0.38"

    # 4. Partial pullback (less than distance 0.05): no signal
    red, add, res = share_trail.step(state, key, Decimal("0.34"), target, upper, lower, distance)
    assert not red and not add and res is None

    # 5. Full pullback >= distance (0.38 - 0.32 = 0.06 >= 0.05) and share > target (0.32 > 0.25)
    red, add, res = share_trail.step(state, key, Decimal("0.32"), target, upper, lower, distance)
    assert red is True
    assert add is False

    # 6. Consume clears peak
    share_trail.consume(state, key, "REDUCE")
    assert state[key]["peak"] is None

    # 7. Mirror test for deficit/add signal:
    # Breach lower threshold
    red, add, res = share_trail.step(state, key, Decimal("0.15"), target, upper, lower, distance)
    assert not red and not add
    assert state[key]["trough"] == "0.15"

    # Rebound >= distance (0.22 - 0.15 = 0.07 >= 0.05) and share < target (0.22 < 0.25)
    red, add, res = share_trail.step(state, key, Decimal("0.22"), target, upper, lower, distance)
    assert red is False
    assert add is True

    # Consume clears trough
    share_trail.consume(state, key, "ADD")
    assert state[key]["trough"] is None


def test_step_flat_position_resets_leg():
    """Verify flat=True resets extrema and returns FLAT."""
    state = share_trail.fresh_state()
    state["BASE_LONG"]["peak"] = "0.40"
    state["BASE_LONG"]["trough"] = "0.10"

    red, add, res = share_trail.step(
        state, "BASE_LONG", Decimal("0.25"), Decimal("0.25"),
        Decimal("0.29"), Decimal("0.21"), Decimal("0.05"), flat=True
    )
    assert not red and not add
    assert res == "FLAT"
    assert state["BASE_LONG"]["peak"] is None
    assert state["BASE_LONG"]["trough"] is None


def test_step_invalid_inputs():
    """Verify step raises ValueError on invalid keys or non-finite inputs."""
    state = share_trail.fresh_state()
    with pytest.raises(ValueError, match="trailing only covers futures legs"):
        share_trail.step(state, "VIRTUAL", 0.5, 0.5, 0.6, 0.4, 0.05)

    with pytest.raises(ValueError, match="invalid share trailing observation"):
        share_trail.step(state, "BASE_LONG", float("nan"), 0.25, 0.29, 0.21, 0.05)

    with pytest.raises(ValueError, match="invalid share trailing observation"):
        share_trail.step(state, "BASE_LONG", 0.30, 0.25, 0.29, 0.21, -0.01)


def test_reset_functions():
    """Verify reset_leg and reset_all clear stored extrema."""
    state = share_trail.fresh_state()
    for leg in share_trail.FUTURES_LEGS:
        state[leg]["peak"] = "0.45"
        state[leg]["trough"] = "0.15"

    share_trail.reset_leg(state, "BASE_LONG")
    assert state["BASE_LONG"]["peak"] is None
    assert state["BASE_SHORT"]["peak"] == "0.45"

    share_trail.reset_all(state)
    assert state["BASE_SHORT"]["peak"] is None
    assert state["BASE_SHORT"]["trough"] is None


def test_spec_fingerprint_and_mismatch():
    """Verify fingerprint generation and mismatch detection."""
    targets = {
        "BASE_LONG": {"share": 0.25},
        "BASE_SHORT": {"share": 0.25}
    }
    fp = share_trail.spec_fingerprint(0.05, targets, 0.04, 0.04)
    assert fp["model"] == "share-pullback-rebound-v2"
    assert fp["distance"] == 0.05
    assert fp["target_long"] == 0.25
    assert fp["target_short"] == 0.25

    # Match check
    assert not share_trail.spec_mismatch(fp, fp)

    # Mismatch check
    changed_fp = dict(fp, distance=0.06)
    assert share_trail.spec_mismatch(fp, changed_fp)
    assert not share_trail.spec_mismatch(None, fp)


def test_fill_receipt_and_restore_checkpoint():
    """Verify journal deduplication and atomic checkpoint recovery."""
    book = {}
    fill1 = {
        "execution_id": "binance:ZECUSDT:1001",
        "symbol": "ZECUSDT",
        "type": "LIMIT",
        "side": "BUY",
        "qty": "1.5",
        "price": "235.5",
        "commission": "0.14",
        "trade_pnl": "0.0",
        "reduce_only": False
    }

    # Record first fill
    is_new = share_trail.fill_receipt(book, fill1, record=True)
    assert is_new is True
    assert "binance:ZECUSDT:1001" in book["share_fill_receipts"]

    # Duplicate fill with same payload returns False (deduplicated)
    is_dup = share_trail.fill_receipt(book, fill1, record=True)
    assert is_dup is False

    # Conflicting payload with same ID raises ValueError
    conflicting = dict(fill1, qty="2.0")
    with pytest.raises(ValueError, match="changed cumulative fill requires reconciliation"):
        share_trail.fill_receipt(book, conflicting, record=True)

    # Restore checkpoint
    state = {
        "share_trail": share_trail.fresh_state(),
        "share_trail_spec": "spec-v1",
        "share_trail_last_fill": None
    }
    book["share_trail_checkpoint"] = {
        "execution_id": "binance:ZECUSDT:1001",
        "spec": "spec-v1",
        "trail": {"BASE_LONG": {"peak": "0.36", "trough": None}}
    }
    restored = share_trail.restore_checkpoint(state, book)
    assert restored is True
    assert state["share_trail"]["BASE_LONG"]["peak"] == "0.36"
    assert state["share_trail_last_fill"] == "binance:ZECUSDT:1001"
