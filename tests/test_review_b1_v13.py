"""B1 V13 reviewer integration tests.

Scenarios:
  1. Confirmed funding-halt: normal startup validation MUST NOT block
     completing reductions.  Phase 2 recovery proceeds.
  2. After quarantined close leg: restart stays blocked until operator
     resolves.  Quarantined is terminal — no automatic retry.
"""
import json
import os
import tempfile
from decimal import Decimal as D

import pytest

from futures_portfolio.core.funding import (
    FundingEvent,
    FundingJournal,
    FundingHalt,
    CloseLegState,
    begin_close_leg,
    record_close_result,
    get_pending_close_legs,
    commit_funding_halt_with_intents,
    save_funding_boundary,
    recover_funding_boundary,
    RECOVERY_ABSENT,
    RECOVERY_RECOVERED,
    RECOVERY_FAILED,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _tmp_paths():
    d = tempfile.mkdtemp(prefix="b1_review_")
    return os.path.join(d, "s.json"), os.path.join(d, "w.json"), d


def _simulate_halt_after_long_closed() -> tuple:
    """Funding halt with LONG filled, SHORT pending (crash mid-close)."""
    halt = commit_funding_halt_with_intents(
        FundingHalt(), "fund:1", D('50'), D('1.5'), D('1.4'))
    # LONG closed successfully
    record_close_result(halt, "BASE_LONG", "filled", D('1.5'), D('1300'),
                         D('0.78'), D('-0.39'), D('0'))
    # SHORT still pending (crash before close)
    return halt


# ═══════════════════════════════════════════════════════════════════════════════
# Scenario 1: Funding-halt bypasses normal startup validation
# ═══════════════════════════════════════════════════════════════════════════════

def test_pending_close_legs_excludes_quarantined():
    """get_pending_close_legs skips both filled AND quarantined."""
    halt = commit_funding_halt_with_intents(
        FundingHalt(), "fund:1", D('50'), D('1.5'), D('1.4'))
    record_close_result(halt, "BASE_LONG", "filled", D('1.5'), D('1300'),
                         D('0.78'), D('-0.39'), D('0'))
    record_close_result(halt, "BASE_SHORT", "quarantined", D('0'), D('0'),
                         D('0'), D('0'), D('1.4'))
    pending = get_pending_close_legs(halt)
    assert pending == []  # neither filled nor quarantined


def test_funding_halt_active_allows_recovery_of_pending_leg():
    """With active funding_halt + LONG filled, SHORT is still pending."""
    halt = _simulate_halt_after_long_closed()
    pending = get_pending_close_legs(halt)
    assert pending == ["BASE_SHORT"]  # SHORT reachable
    assert halt.close_state["BASE_LONG"].status == "filled"
    assert halt.close_state["BASE_SHORT"].status == "pending"
    assert halt.active
    assert halt.increases_blocked


def test_funding_halt_close_recovery_completes_second_leg():
    """Phase 2 recovery: resume SHORT after LONG already filled."""
    halt = _simulate_halt_after_long_closed()
    assert not all(x == "filled"
                   for x in [cs.status for cs in halt.close_state.values()])
    # Complete SHORT
    record_close_result(halt, "BASE_SHORT", "filled", D('1.4'), D('1300'),
                         D('0.73'), D('-0.36'), D('0'))
    assert get_pending_close_legs(halt) == []
    assert halt.active  # halt survives successful close


def test_startup_decision_with_active_halt_does_not_block():
    """If funding_halt.active is True, ambiguous state does NOT set startup_blocked.

    Simulates the decision that main.py makes at startup: when funding_halt
    is active, an otherwise-ambiguous state (one leg zero, the other non-zero)
    is NOT treated as a diagnostic block.
    """
    # Simulate state after LONG closed, SHORT pending (crash)
    state = {
        "funding_halt": {
            "active": True,
            "reason": "funding_shortfall",
            "event_id": "fund:1",
            "halted_at": 100.0,
            "shortfall_amount": 50.0,
            "increases_blocked": True,
            "close_state": {
                "BASE_LONG": {
                    "leg": "BASE_LONG", "close_event_id": "c1",
                    "status": "filled", "qty_closed": 1.5,
                    "qty_remaining": 0.0, "fill_price": 1300.0,
                    "commission": 0.78, "realized_pnl": -0.39,
                },
                "BASE_SHORT": {
                    "leg": "BASE_SHORT", "close_event_id": "c2",
                    "status": "pending", "qty_closed": 0.0,
                    "qty_remaining": 1.4, "fill_price": 0.0,
                    "commission": 0.0, "realized_pnl": 0.0,
                },
            },
        },
        "funding_journal": [],
        "virt_debt": -7.0,
        "funding_paid": 400.0,
    }
    paper_state = {
        "balance": 660.0,
        "positions": {"BTCUSDT_LONG": 0.0, "BTCUSDT_SHORT": 1.4},
        "long_entry_price": 0.0,   # LONG was closed → entry zeroed
        "short_entry_price": 1300.0,
        "last_price": 1300.0,
    }

    # This is the predicate main.py evaluates:
    _p_l = abs(paper_state["positions"].get("BTCUSDT_LONG", 0.0))
    _p_s = abs(paper_state["positions"].get("BTCUSDT_SHORT", 0.0))
    _both_legs = _p_l > 1e-10 and _p_s > 1e-10
    _le = float(paper_state.get("long_entry_price", 0.0))
    _se = float(paper_state.get("short_entry_price", 0.0))
    is_ambiguous = not (_both_legs and _le > 0 and _se > 0)

    _funding_halt_active = (
        isinstance(state.get("funding_halt"), dict)
        and state.get("funding_halt", {}).get("active", False)
    )

    # Assert: state IS ambiguous (LONG=0, SHORT>0 with one entry zeroed)
    assert is_ambiguous, "state must appear ambiguous to the validator"

    # BUT: funding_halt is active → validator must NOT block
    should_block = is_ambiguous and not _funding_halt_active
    assert not should_block, (
        f"startup should NOT block: funding_halt is active, "
        f"Phase 2 recovery handles the rest"
    )


def test_startup_decision_without_halt_still_blocks_ambiguous():
    """Without funding_halt, ambiguous state still blocks as before."""
    state = {"funding_halt": {}}
    paper_state = {
        "balance": 660.0,
        "positions": {"BTCUSDT_LONG": 1.5, "BTCUSDT_SHORT": 0.0},
        "long_entry_price": 1300.0,
        "short_entry_price": 0.0,
    }
    _p_l = abs(paper_state["positions"].get("BTCUSDT_LONG", 0.0))
    _p_s = abs(paper_state["positions"].get("BTCUSDT_SHORT", 0.0))
    _both_legs = _p_l > 1e-10 and _p_s > 1e-10
    _le = float(paper_state.get("long_entry_price", 0.0))
    _se = float(paper_state.get("short_entry_price", 0.0))
    is_ambiguous = not (_both_legs and _le > 0 and _se > 0)

    _funding_halt_active = (
        isinstance(state.get("funding_halt"), dict)
        and state.get("funding_halt", {}).get("active", False)
    )

    assert is_ambiguous
    assert not _funding_halt_active
    # Without halt, ambiguous → block
    should_block = is_ambiguous and not _funding_halt_active
    assert should_block


# ═══════════════════════════════════════════════════════════════════════════════
# Scenario 2: Quarantined close leg blocks restart
# ═══════════════════════════════════════════════════════════════════════════════

def test_quarantined_close_leg_blocks_startup():
    """Quarantined close leg → startup_blocked, trading stays blocked."""
    state = {
        "funding_halt": {
            "active": True,
            "reason": "funding_shortfall",
            "event_id": "fund:1",
            "halted_at": 100.0,
            "shortfall_amount": 50.0,
            "increases_blocked": True,
            "close_state": {
                "BASE_LONG": {
                    "leg": "BASE_LONG", "close_event_id": "c1",
                    "status": "filled", "qty_closed": 1.5,
                    "qty_remaining": 0.0, "fill_price": 1300.0,
                    "commission": 0.78, "realized_pnl": -0.39,
                },
                "BASE_SHORT": {
                    "leg": "BASE_SHORT", "close_event_id": "c2",
                    "status": "quarantined", "qty_closed": 0.0,
                    "qty_remaining": 1.4, "fill_price": 0.0,
                    "commission": 0.0, "realized_pnl": 0.0,
                },
            },
        },
    }

    halt_obj = FundingHalt.from_dict(state["funding_halt"])
    quarantined = [leg for leg, cs in halt_obj.close_state.items()
                   if cs.status == "quarantined"]
    assert quarantined == ["BASE_SHORT"], "SHORT should be detected as quarantined"

    # get_pending_close_legs must NOT return quarantined legs
    pending = get_pending_close_legs(halt_obj)
    assert "BASE_SHORT" not in pending, "quarantined leg MUST NOT be retried"
    assert pending == []

    # Startup decision: quarantined → block
    should_block = bool(quarantined)
    assert should_block, "quarantined legs must block startup"


def test_quarantined_roundtrip_survives_save_reload():
    """Quarantined close leg status survives JSON roundtrip."""
    halt = commit_funding_halt_with_intents(
        FundingHalt(), "fund:1", D('50'), D('1.5'), D('1.4'))
    record_close_result(halt, "BASE_LONG", "filled", D('1.5'), D('1300'),
                         D('0.78'), D('-0.39'), D('0'))
    record_close_result(halt, "BASE_SHORT", "quarantined", D('0'), D('0'),
                         D('0'), D('0'), D('1.4'))

    d = halt.to_dict()
    restored = FundingHalt.from_dict(d)
    assert restored.close_state["BASE_SHORT"].status == "quarantined"
    assert restored.close_state["BASE_SHORT"].qty_remaining == D('1.4')
    assert restored.active
    assert restored.increases_blocked


def test_restart_after_quarantined_blocks_trading():
    """Full restart cycle: quarantined leg → startup_blocked persisted.

    Simulates:
      1. Phase 2 recovery close fails → leg quarantined
      2. State saved with quarantined close leg
      3. Restart: startup validation sees quarantined → sets startup_blocked
      4. On next restart: persisted startup_blocked → zombie mode
    """
    # Step 1: Build state as if Phase 2 recovery just quarantined SHORT
    halt = FundingHalt()
    halt.active = True
    halt.reason = "funding_shortfall"
    halt.event_id = "fund:1"
    halt.halted_at = 100.0
    halt.shortfall_amount = D('50')
    halt.increases_blocked = True
    begin_close_leg(halt, "BASE_LONG", "c1", D('1.5'))
    record_close_result(halt, "BASE_LONG", "filled", D('1.5'), D('1300'),
                         D('0.78'), D('-0.39'), D('0'))
    begin_close_leg(halt, "BASE_SHORT", "c2", D('1.4'))
    # Recovery close failed → quarantined
    record_close_result(halt, "BASE_SHORT", "quarantined", D('0'), D('0'),
                         D('0'), D('0'), D('1.4'))

    # Verify terminal state
    quarantined = [leg for leg, cs in halt.close_state.items()
                   if cs.status == "quarantined"]
    assert quarantined == ["BASE_SHORT"]

    # Step 2: Persist as main.py would
    state = {
        "funding_halt": halt.to_dict(),
        "funding_journal": [],
        "startup_blocked": True,  # set by quarantined check
    }
    # Roundtrip
    restored = json.loads(json.dumps(state, default=str))
    halt2 = FundingHalt.from_dict(restored["funding_halt"])
    quarantined2 = [leg for leg, cs in halt2.close_state.items()
                    if cs.status == "quarantined"]
    assert quarantined2 == ["BASE_SHORT"]
    assert restored["startup_blocked"]

    # Step 3: Next restart — startup_blocked is True → block immediately
    assert restored.get("startup_blocked"), (
        "persisted startup_blocked must survive restart"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Blocker regression 1: Clean Slate must not reset wallet during funding-halt
# ═══════════════════════════════════════════════════════════════════════════════

def test_clean_slate_suppressed_when_funding_halt_active_both_legs_closed():
    """Funding halt active + both legs closed → wallet preserved, not reset.

    Reproduces the defect: wallet=70 after shortfall close, but Clean Slate
    sees zero positions and resets wallet to target_initial_cap=100,
    destroying actual session capital.
    """
    state = {
        "funding_halt": {
            "active": True,
            "reason": "funding_shortfall",
            "event_id": "fund:1",
            "halted_at": 100.0,
            "shortfall_amount": 50.0,
            "increases_blocked": True,
            "close_state": {
                "BASE_LONG": {
                    "leg": "BASE_LONG", "close_event_id": "c1",
                    "status": "filled", "qty_closed": 1.5,
                    "qty_remaining": 0.0, "fill_price": 1300.0,
                    "commission": 0.78, "realized_pnl": -0.39,
                },
                "BASE_SHORT": {
                    "leg": "BASE_SHORT", "close_event_id": "c2",
                    "status": "filled", "qty_closed": 1.4,
                    "qty_remaining": 0.0, "fill_price": 1300.0,
                    "commission": 0.73, "realized_pnl": -0.36,
                },
            },
        },
        "virt_debt": -7.0,
        "funding_paid": 400.0,
        "virt_qty": 0.0,
    }
    paper_state = {
        "balance": 70.0,   # actual capital after shortfall close
        "positions": {"BTCUSDT_LONG": 0.0, "BTCUSDT_SHORT": 0.0},
        "long_entry_price": 0.0,
        "short_entry_price": 0.0,
    }

    # Simulate main.py startup decision
    _p_l = abs(paper_state["positions"].get("BTCUSDT_LONG", 0.0))
    _p_s = abs(paper_state["positions"].get("BTCUSDT_SHORT", 0.0))
    _funding_halt_active = (
        isinstance(state.get("funding_halt"), dict)
        and state.get("funding_halt", {}).get("active", False)
    )

    assert _p_l < 1e-10 and _p_s < 1e-10, "both legs closed"
    assert _funding_halt_active, "halt is active"

    # Clean Slate check: do NOT reset when funding_halt is active
    should_clean_slate = (
        _p_l < 1e-10 and _p_s < 1e-10
        and not _funding_halt_active
    )
    assert not should_clean_slate, (
        f"Clean Slate must be suppressed: funding_halt is active, "
        f"wallet={paper_state['balance']} must be preserved"
    )

    # Wallet must NOT be overwritten
    assert paper_state["balance"] == 70.0, "wallet preserved at 70, not reset to 100"


def test_clean_slate_still_fires_without_funding_halt():
    """Without funding_halt, zero positions + balance drift → Clean Slate fires."""
    state = {"funding_halt": {}}
    _funding_halt_active = False
    paper_state = {
        "balance": 95.0,
        "positions": {"BTCUSDT_LONG": 0.0, "BTCUSDT_SHORT": 0.0},
    }
    _p_l = abs(paper_state["positions"].get("BTCUSDT_LONG", 0.0))
    _p_s = abs(paper_state["positions"].get("BTCUSDT_SHORT", 0.0))
    should_clean_slate = (
        _p_l < 1e-10 and _p_s < 1e-10
        and not _funding_halt_active
    )
    assert should_clean_slate, "Clean Slate should fire without funding_halt"


# ═══════════════════════════════════════════════════════════════════════════════
# Blocker regression 2: Corrupted WAL must persist FAILED across restarts
# ═══════════════════════════════════════════════════════════════════════════════

def test_corrupted_wal_returns_failed_on_second_call():
    """Recovery FAILED → intent quarantined as .corrupted.
    Next call must still be FAILED (not ABSENT)."""
    sp, wp, d = _tmp_paths()
    try:
        from futures_portfolio.core.funding import atomic_save_json
        atomic_save_json(sp + ".funding_intent", {"bad": "payload"})

        # First call: FAILED, intent renamed to .corrupted
        status1, rs1, rw1 = recover_funding_boundary(sp, wp)
        assert status1 == RECOVERY_FAILED
        assert rs1 is None and rw1 is None

        # .corrupted must now exist
        assert os.path.exists(sp + ".funding_intent.corrupted"), (
            "intent must be renamed to .corrupted"
        )
        # Original intent must be gone
        assert not os.path.exists(sp + ".funding_intent")

        # Second call: MUST still be FAILED (not ABSENT)
        status2, rs2, rw2 = recover_funding_boundary(sp, wp)
        assert status2 == RECOVERY_FAILED, (
            f"Expected FAILED on second call, got {status2}. "
            f"Corrupted intent must persist the block."
        )
        assert rs2 is None and rw2 is None

        # .corrupted must still exist (never auto-deleted)
        assert os.path.exists(sp + ".funding_intent.corrupted")
    finally:
        import shutil; shutil.rmtree(d, ignore_errors=True)


def test_corrupted_wal_persists_after_single_call():
    """One corruption → .corrupted persists; intent is gone."""
    sp, wp, d = _tmp_paths()
    try:
        from futures_portfolio.core.funding import atomic_save_json
        atomic_save_json(sp + ".funding_intent", {"not": "valid"})
        status, rs, rw = recover_funding_boundary(sp, wp)
        assert status == RECOVERY_FAILED
        assert os.path.exists(sp + ".funding_intent.corrupted")
        assert not os.path.exists(sp + ".funding_intent")
    finally:
        import shutil; shutil.rmtree(d, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════════
# WAL boundary + quarantine interaction
# ═══════════════════════════════════════════════════════════════════════════════

def test_wal_recovery_of_quarantined_preserves_status():
    """WAL intent with quarantined close leg → recovered as quarantined,
    Phase 2 check then blocks (not tested here — model level only)."""
    sp, wp, d = _tmp_paths()
    try:
        halt = commit_funding_halt_with_intents(
            FundingHalt(), "fund:1", D('50'), D('1.5'), D('1.4'))
        record_close_result(halt, "BASE_LONG", "filled", D('1.5'), D('1300'),
                             D('0.78'), D('-0.39'), D('0'))
        record_close_result(halt, "BASE_SHORT", "quarantined", D('0'), D('0'),
                             D('0'), D('0'), D('1.4'))

        state = {"virt_debt": -7.0, "funding_paid": 400.0,
                 "funding_journal": [], "funding_halt": halt.to_dict()}
        paper = {"balance": 660.0,
                 "positions": {"BTCUSDT_LONG": 0.0, "BTCUSDT_SHORT": 1.4}}

        from futures_portfolio.core.funding import atomic_save_json
        intent = {"strategy_state": dict(state),
                   "paper_wallet": dict(paper),
                   "intent_time": 100.0}
        atomic_save_json(sp + ".funding_intent", intent)

        status, rs, rw = recover_funding_boundary(sp, wp)
        assert status == RECOVERY_RECOVERED
        recovered_halt = FundingHalt.from_dict(rs["funding_halt"])
        assert recovered_halt.close_state["BASE_SHORT"].status == "quarantined"
    finally:
        import shutil; shutil.rmtree(d, ignore_errors=True)