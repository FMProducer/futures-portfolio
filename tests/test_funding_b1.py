"""B1 funding tests — V-only accounting, shortfall halt, close recovery."""
import json
from decimal import Decimal as D
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from futures_portfolio.core.funding import (
    FundingEvent, FundingJournal, FundingHalt, CloseLegState,
    apply_funding_paper, apply_funding_backtest,
    commit_funding_halt, begin_close_leg, record_close_result,
    all_legs_closed, compute_funding_from_rates,
    save_funding_state, load_funding_state,
)
from futures_portfolio.backtest.backtest_rebalance import BacktestState


# ═══════════════════════════════════════════════════════════════════════════════
# F1–F4: basic funding application
# ═══════════════════════════════════════════════════════════════════════════════

def test_f1_funding_paid_less_than_v_decreases_v_tpv_cash_unchanged():
    """Paid funding < V: V and TPV decrease; CASH unchanged."""
    state = BacktestState(initial_capital=D('1000'), val_cash=D('660'),
                          virt_debt=D('340'), base_ticker="BTCUSDT")
    tpv_before = state.get_tpv(D('100'))
    cash_before = state.val_cash
    result = apply_funding_backtest(D('-3.00'), state)
    assert result["v_after"] == pytest.approx(337.0)
    assert result["shortfall"] == 0.0
    assert result["cash_change"] == 0.0
    assert state.val_cash == cash_before  # CASH unchanged
    assert state.virt_debt == D('337')
    assert state.funding_paid == D('3')  # opposite sign: paid 3
    assert state.get_tpv(D('100')) == tpv_before - D('3')
    assert state.accounting_residual(D('100')) == D('0')


def test_f2_funding_paid_equal_to_v_leaves_v_zero():
    """Paid funding = V: V=0, no shortfall, no CASH transfer."""
    state = BacktestState(initial_capital=D('1000'), val_cash=D('660'),
                          virt_debt=D('5'), base_ticker="BTCUSDT")
    result = apply_funding_backtest(D('-5.00'), state)
    assert state.virt_debt == D('0')
    assert result["shortfall"] == 0.0
    assert state.val_cash == D('660')  # CASH unchanged
    assert state.funding_paid == D('5')


def test_f3_funding_paid_greater_than_v_preserves_negative_v():
    """Paid > V: full debit, negative V, shortfall > 0, CASH unchanged."""
    state = BacktestState(initial_capital=D('1000'), val_cash=D('660'),
                          virt_debt=D('3'), base_ticker="BTCUSDT")
    tpv_before = state.get_tpv(D('100'))
    result = apply_funding_backtest(D('-10.00'), state)
    assert state.virt_debt == D('-7')  # full debit, negative V
    assert result["shortfall"] == 7.0
    assert state.val_cash == D('660')  # CASH unchanged
    assert state.funding_paid == D('10')
    assert state.get_funding_shortfall() == D('7')
    assert state.get_tpv(D('100')) == tpv_before - D('10')


def test_f4_funding_received_increases_v_tpv():
    """Credit: V and TPV increase, CASH unchanged."""
    state = BacktestState(initial_capital=D('1000'), val_cash=D('660'),
                          virt_debt=D('340'), base_ticker="BTCUSDT")
    tpv_before = state.get_tpv(D('100'))
    result = apply_funding_backtest(D('3.00'), state)
    assert state.virt_debt == D('343')
    assert result["shortfall"] == 0.0
    assert state.val_cash == D('660')
    assert state.funding_paid == D('-3')  # received 3 → funding_paid = -3
    assert state.get_tpv(D('100')) == tpv_before + D('3')


# ═══════════════════════════════════════════════════════════════════════════════
# F5–F9: event journal idempotency
# ═══════════════════════════════════════════════════════════════════════════════

def test_f5_duplicate_event_is_noop():
    journal = FundingJournal()
    e1 = FundingEvent(event_id="fund:1", signed_amount=D('-3'),
                       event_time=100.0)
    assert journal.receive(e1) == "new"
    journal.mark_applied("fund:1")
    # Same event again
    e2 = FundingEvent(event_id="fund:1", signed_amount=D('-3'),
                       event_time=100.0)
    assert journal.receive(e2) == "duplicate"
    assert len(journal._order) == 1  # not added again


def test_f6_same_id_different_payload_is_conflict():
    journal = FundingJournal()
    journal.receive(FundingEvent(event_id="fund:1", signed_amount=D('-3'),
                                  event_time=100.0))
    journal.mark_applied("fund:1")
    # Same id, different amount
    result = journal.receive(FundingEvent(
        event_id="fund:1", signed_amount=D('-5'), event_time=100.0))
    assert result == "conflict"
    assert journal.events["fund:1"].status == "conflict"


def test_f7_crash_before_commit_keeps_received_state_unchanged():
    """Event remains 'received'; recovery applies once."""
    state = BacktestState(initial_capital=D('1000'), val_cash=D('660'),
                          virt_debt=D('340'), base_ticker="BTCUSDT")
    journal = FundingJournal()
    journal.receive(FundingEvent(event_id="fund:1", signed_amount=D('-3'),
                                  event_time=100.0))
    # crash before marking applied → event still received, state unchanged
    v_before = state.virt_debt
    assert journal.events["fund:1"].status == "received"
    assert state.virt_debt == v_before  # no accounting effect yet

    # recovery: apply and mark
    apply_funding_backtest(D('-3'), state)
    journal.mark_applied("fund:1")
    assert journal.events["fund:1"].status == "applied"


def test_f8_crash_after_commit_is_noop_on_retry():
    journal = FundingJournal()
    journal.receive(FundingEvent(event_id="fund:1", signed_amount=D('-3'),
                                  event_time=100.0))
    journal.mark_applied("fund:1")
    result = journal.receive(FundingEvent(
        event_id="fund:1", signed_amount=D('-3'), event_time=100.0))
    assert result == "duplicate"


def test_f9_late_event_is_quarantined():
    journal = FundingJournal()
    e = FundingEvent(event_id="fund:late", signed_amount=D('-3'),
                      event_time=50.0)
    journal.receive(e)
    # Simulate: last applied event at t=100, this is at t=50 → late
    # (The check is done by the caller comparing event_time with latest_applied)
    journal.mark_quarantined("fund:late", "late_event")
    assert journal.events["fund:late"].status == "quarantined"


# ═══════════════════════════════════════════════════════════════════════════════
# F10: paper funding
# ═══════════════════════════════════════════════════════════════════════════════

def test_f10_paper_funding_updates_wallet_and_v_together_cash_unchanged():
    paper_wallet = {"balance": 1000.0}
    strategy = {"virt_debt": 340.0, "funding_paid": 0.0}
    result = apply_funding_paper(D('-5.00'), paper_wallet, strategy)
    assert paper_wallet["balance"] == pytest.approx(995.0)
    assert strategy["virt_debt"] == pytest.approx(335.0)
    assert result["cash_change"] == 0.0
    assert strategy["funding_paid"] == pytest.approx(5.0)  # paid 5


def test_f10b_paper_funding_credit():
    paper_wallet = {"balance": 1000.0}
    strategy = {"virt_debt": 340.0, "funding_paid": 0.0}
    apply_funding_paper(D('3.00'), paper_wallet, strategy)
    assert paper_wallet["balance"] == pytest.approx(1003.0)
    assert strategy["virt_debt"] == pytest.approx(343.0)
    assert strategy["funding_paid"] == pytest.approx(-3.0)


# ═══════════════════════════════════════════════════════════════════════════════
# F11: funding shortfall close (backtest)
# ═══════════════════════════════════════════════════════════════════════════════

class _FakeSim:
    commission_pct = D('0.0004')
    slippage_pct = D('0.0002')
    def execution_price(self, side, mid):
        if side.upper() == "BUY":
            return mid * (D('1') + self.slippage_pct)
        return mid * (D('1') - self.slippage_pct)
    def simulate_market_execution(self, side, qty, mid):
        ep = self.execution_price(side, mid)
        comm = abs(D(str(qty))) * ep * self.commission_pct
        return ep, comm


def test_f11_funding_shortfall_closes_both_legs_with_fees_and_halt():
    """F3 + close: both legs closed with commission, halt active."""
    state = BacktestState(initial_capital=D('1000'), val_cash=D('100'),
                          virt_debt=D('340'), base_ticker="BTCUSDT",
                          l_lev=D('7'), s_lev=D('7'))
    state.pos_long = D('1.5')
    state.long_entry_price = D('1300')
    state.pos_short = D('1.4')
    state.short_entry_price = D('1300')
    sim = _FakeSim()
    price = D('1300')

    # Apply funding debit that exceeds V
    apply_funding_backtest(D('-400'), state)
    assert state.virt_debt < 0
    shortfall = state.get_funding_shortfall()
    assert shortfall > 0

    # Close legs
    state.funding_halt_active = True
    if state.pos_long > 0:
        state.close_position_for_funding_shortfall(
            "BASE_LONG", state.pos_long, price, sim, 0)
    if state.pos_short > 0:
        state.close_position_for_funding_shortfall(
            "BASE_SHORT", state.pos_short, price, sim, 0)

    assert state.pos_long == 0
    assert state.pos_short == 0
    assert len(state.fills) == 2
    for f in state.fills:
        assert f["trigger"] == "funding_shortfall"
        assert f["commission"] > 0
    assert state.funding_halt_active


# ═══════════════════════════════════════════════════════════════════════════════
# F12: halt survives close and credit
# ═══════════════════════════════════════════════════════════════════════════════

def test_f12_halt_survives_close_proceeds():
    halt = FundingHalt()
    commit_funding_halt(halt, "fund:1", D('50'), {})
    assert halt.active
    # Close both legs successfully
    begin_close_leg(halt, "BASE_LONG", "close:1")
    record_close_result(halt, "BASE_LONG", "filled", D('1'), D('1300'), D('0.5'), D('0'))
    begin_close_leg(halt, "BASE_SHORT", "close:2")
    record_close_result(halt, "BASE_SHORT", "filled", D('1'), D('1300'), D('0.5'), D('0'))
    assert all_legs_closed(halt)
    # Halt still active after successful close
    assert halt.active
    assert halt.increases_blocked


# ═══════════════════════════════════════════════════════════════════════════════
# F15: funding_paid sign
# ═══════════════════════════════════════════════════════════════════════════════

def test_f15_funding_paid_sign_credit_3():
    """Credit +3: V/TPV +3, funding_paid -3, equity +3."""
    state = BacktestState(initial_capital=D('1000'), val_cash=D('660'),
                          virt_debt=D('340'), base_ticker="BTCUSDT",
                          ledger_base=D('1000'))
    tpv_before = state.get_tpv(D('100'))
    equity_before = state.accounting_equity(D('100'))
    apply_funding_backtest(D('3'), state)
    assert state.virt_debt == D('343')  # +3
    assert state.funding_paid == D('-3')
    assert state.get_tpv(D('100')) == tpv_before + D('3')
    assert state.accounting_equity(D('100')) == equity_before + D('3')
    assert state.accounting_residual(D('100')) == D('0')


def test_f15b_opposite_payments_long_short_same_rate():
    """LONG pays, SHORT receives equal amounts → net zero if same notional."""
    funding = compute_funding_from_rates(
        D('1'), D('1'), D('1300'), D('0.0001'))
    assert funding == D('0')  # -1*1300*0.0001 + 1*1300*0.0001 = 0


def test_f15c_long_pays_short_gets_at_positive_rate():
    """Positive rate: LONG pays, SHORT receives.  Net from position imbalance."""
    # LONG=2, SHORT=1: net = -2*1300*r + 1*1300*r = -1300*r
    funding = compute_funding_from_rates(
        D('2'), D('1'), D('1300'), D('0.0001'))
    assert funding == D('-0.13')  # net debit


# ═══════════════════════════════════════════════════════════════════════════════
# F16–F18: close recovery
# ═══════════════════════════════════════════════════════════════════════════════

def test_f16_crash_after_first_close_resumes_second_leg_only():
    """Crash after LONG closed: LONG not re-closed, SHORT resumes."""
    halt = FundingHalt()
    commit_funding_halt(halt, "fund:1", D('50'), {})
    # First leg filled
    begin_close_leg(halt, "BASE_LONG", "close:L1")
    record_close_result(halt, "BASE_LONG", "filled", D('1'), D('1300'), D('0.5'), D('10'))
    # Second leg NOT yet closed (crash)
    begin_close_leg(halt, "BASE_SHORT", "close:S1")
    # Recovery: only SHORT is pending
    assert not all_legs_closed(halt)
    cs_long = halt.close_state["BASE_LONG"]
    assert cs_long.status == "filled"
    cs_short = halt.close_state["BASE_SHORT"]
    assert cs_short.status == "pending"
    # Resume SHORT
    record_close_result(halt, "BASE_SHORT", "filled", D('1'), D('1300'), D('0.5'), D('-5'))
    assert all_legs_closed(halt)


def test_f18_crash_before_any_close_resumes_both_legs():
    halt = FundingHalt()
    commit_funding_halt(halt, "fund:1", D('50'), {})
    # No legs closed yet → recovery needs both
    assert not all_legs_closed(halt)
    begin_close_leg(halt, "BASE_LONG", "close:L1")
    record_close_result(halt, "BASE_LONG", "filled", D('1'), D('1300'), D('0.5'), D('0'))
    begin_close_leg(halt, "BASE_SHORT", "close:S1")
    record_close_result(halt, "BASE_SHORT", "filled", D('1'), D('1300'), D('0.5'), D('0'))
    assert all_legs_closed(halt)


def test_f17_second_leg_error_keeps_halt():
    halt = FundingHalt()
    commit_funding_halt(halt, "fund:1", D('50'), {})
    begin_close_leg(halt, "BASE_LONG", "close:L1")
    record_close_result(halt, "BASE_LONG", "filled", D('1'), D('1300'), D('0.5'), D('10'))
    begin_close_leg(halt, "BASE_SHORT", "close:S1")
    record_close_result(halt, "BASE_SHORT", "error", D('0'), D('0'), D('0'), D('0'))
    assert not all_legs_closed(halt)
    assert halt.active  # halt remains
    assert halt.close_state["BASE_SHORT"].status == "error"


def test_f19_duplicate_close_retry_is_noop():
    halt = FundingHalt()
    commit_funding_halt(halt, "fund:1", D('50'), {})
    cs = begin_close_leg(halt, "BASE_LONG", "close:L1")
    record_close_result(halt, "BASE_LONG", "filled", D('1'), D('1300'), D('0.5'), D('10'))
    # Retry: begin_close_leg returns existing filled state
    cs2 = begin_close_leg(halt, "BASE_LONG", "close:L1_retry")
    assert cs2.status == "filled"
    assert cs2.qty_closed == D('1')


# ═══════════════════════════════════════════════════════════════════════════════
# Paper: funding halt survives restart (persisted state check)
# ═══════════════════════════════════════════════════════════════════════════════

def test_funding_halt_serialization_roundtrip():
    halt = FundingHalt()
    commit_funding_halt(halt, "fund:1", D('50'), {})
    begin_close_leg(halt, "BASE_LONG", "close:L1")
    record_close_result(halt, "BASE_LONG", "filled", D('1'), D('1300'), D('0.5'), D('10'))

    d = halt.to_dict()
    restored = FundingHalt.from_dict(d)
    assert restored.active
    assert restored.reason == "funding_shortfall"
    assert restored.shortfall_amount == D('50')
    assert restored.close_state["BASE_LONG"].status == "filled"


def test_funding_journal_roundtrip():
    journal = FundingJournal()
    journal.receive(FundingEvent(event_id="fund:1", signed_amount=D('-3'),
                                  event_time=100.0))
    journal.mark_applied("fund:1")
    journal.receive(FundingEvent(event_id="fund:2", signed_amount=D('2'),
                                  event_time=200.0))
    lst = journal.to_list()
    restored = FundingJournal.from_list(lst)
    assert len(restored._order) == 2
    assert restored.events["fund:1"].status == "applied"
    assert restored.events["fund:2"].status == "received"


# ═══════════════════════════════════════════════════════════════════════════════
# Defect-specific regression tests
# ═══════════════════════════════════════════════════════════════════════════════

def test_defect_applied_with_different_timestamp_is_conflict():
    """Same ID + amount, different timestamp → conflict, NOT duplicate."""
    journal = FundingJournal()
    journal.receive(FundingEvent(event_id="fund:1", signed_amount=D('-3'),
                                  event_time=100.0))
    journal.mark_applied("fund:1")
    # Same ID + amount, different time → different payload → conflict
    result = journal.receive(FundingEvent(
        event_id="fund:1", signed_amount=D('-3'), event_time=200.0))
    assert result == "conflict"


def test_defect_all_legs_closed_false_when_second_not_registered():
    """Empty close_state → False; one leg filled + second not tracked → False."""
    halt = FundingHalt()
    commit_funding_halt(halt, "fund:1", D('50'), {})
    assert not all_legs_closed(halt)
    begin_close_leg(halt, "BASE_LONG", "close:L1")
    record_close_result(halt, "BASE_LONG", "filled", D('1'), D('1300'), D('0.5'), D('0'))
    # SHORT not yet registered → not all closed
    assert not all_legs_closed(halt)
    # Register + fill SHORT
    begin_close_leg(halt, "BASE_SHORT", "close:S1")
    record_close_result(halt, "BASE_SHORT", "filled", D('1'), D('1300'), D('0.5'), D('0'))
    assert all_legs_closed(halt)


def test_defect_partial_close_preserves_remainder():
    """Closing 1 of 2 contracts leaves 1 remaining, not zero."""
    state = BacktestState(initial_capital=D('1000'), val_cash=D('660'),
                          virt_debt=D('340'), base_ticker="BTCUSDT",
                          l_lev=D('7'), s_lev=D('7'))
    state.pos_long = D('2')
    state.long_entry_price = D('1300')
    sim = _FakeSim()
    result = state.close_position_for_funding_shortfall(
        "BASE_LONG", D('1'), D('1300'), sim, 0)
    assert result["status"] == "partial"
    assert result["qty_remaining"] == 1.0
    assert state.pos_long == D('1')  # remainder preserved
    assert state.long_entry_price > 0  # entry not zeroed

    # Close remaining
    result2 = state.close_position_for_funding_shortfall(
        "BASE_LONG", D('1'), D('1300'), sim, 0)
    assert result2["status"] == "filled"
    assert state.pos_long == 0
    assert state.long_entry_price == 0


def test_defect_and_f4b_funding_rounding_preserves_cash_zero():
    """Funding -0.00006 on paper: wallet and V both quantised; CASH stays 0."""
    paper = {"balance": 1000.0}
    strategy = {"virt_debt": 340.0, "funding_paid": 0.0, "funding_rounding_buf": 0.0}
    result = apply_funding_paper(D('-0.00006'), paper, strategy)
    # Both wallet and V quantised to 1e-4; rounding diff captured in buf.
    assert result["cash_change"] == 0.0
    # Wallet change = V change (both to 1e-4)
    wallet_delta = abs(D(str(paper["balance"])) - D('1000'))
    v_delta = abs(D(str(strategy["virt_debt"])) - D('340'))
    assert wallet_delta == v_delta
    # Rounding buffer absorbs the micro-difference
    buf = abs(D(str(strategy.get("funding_rounding_buf", 0))))
    assert buf < D('0.0002')  # small


# ═══════════════════════════════════════════════════════════════════════════════
# Durable crash-recovery: save → "crash" → reload
# ═══════════════════════════════════════════════════════════════════════════════

def _roundtrip_via_dict(journal, halt):
    """Simulate JSON save → process restart → reload."""
    data = {"funding_journal": journal.to_list(), "funding_halt": halt.to_dict()}
    j2, h2 = load_funding_state(data)
    return j2, h2


def test_crash_recovery_funding_applied_once_after_reload():
    """Crash after applying one event: reload→event is applied, no re-apply."""
    journal = FundingJournal()
    journal.receive(FundingEvent(event_id="fund:1", signed_amount=D('-3'),
                                  event_time=100.0))
    journal.mark_applied("fund:1")
    halt = FundingHalt()

    # Save → "crash" → reload
    j2, h2 = _roundtrip_via_dict(journal, halt)
    assert j2.events["fund:1"].status == "applied"
    # Re-receive same event → duplicate
    result = j2.receive(FundingEvent(
        event_id="fund:1", signed_amount=D('-3'), event_time=100.0))
    assert result == "duplicate"


def test_crash_recovery_halt_and_close_state_survive_reload():
    """Halt + partial close intents survive save→reload."""
    halt = FundingHalt()
    commit_funding_halt(halt, "fund:1", D('50'), {})
    begin_close_leg(halt, "BASE_LONG", "close:L1", D('1.5'))
    record_close_result(halt, "BASE_LONG", "filled", D('1.5'), D('1300'),
                         D('0.78'), D('-0.39'), D('0'))
    begin_close_leg(halt, "BASE_SHORT", "close:S1", D('1.4'))

    journal = FundingJournal()
    journal.receive(FundingEvent(event_id="fund:1", signed_amount=D('-400'),
                                  event_time=100.0))
    journal.mark_applied("fund:1")

    # Reload
    j2, h2 = _roundtrip_via_dict(journal, halt)
    assert h2.active
    assert h2.reason == "funding_shortfall"
    assert h2.close_state["BASE_LONG"].status == "filled"
    assert h2.close_state["BASE_SHORT"].status == "pending"
    assert h2.close_state["BASE_SHORT"].qty_remaining == D('1.4')
    assert not all_legs_closed(h2)

    # Resume SHORT
    record_close_result(h2, "BASE_SHORT", "filled", D('1.4'), D('1300'),
                         D('0.73'), D('-0.36'), D('0'))
    assert all_legs_closed(h2)
    # Halt survives successful close
    assert h2.active


def test_crash_recovery_unapplied_event_applied_once_after_reload():
    """Event received but not applied (crash before commit): reload, apply once."""
    journal = FundingJournal()
    journal.receive(FundingEvent(event_id="fund:1", signed_amount=D('-3'),
                                  event_time=100.0))
    assert journal.events["fund:1"].status == "received"

    j2, _ = _roundtrip_via_dict(journal, FundingHalt())
    assert j2.events["fund:1"].status == "received"
    # Apply after reload
    state = BacktestState(initial_capital=D('1000'), val_cash=D('660'),
                          virt_debt=D('340'), base_ticker="BTCUSDT")
    apply_funding_backtest(D('-3'), state)
    j2.mark_applied("fund:1")
    assert j2.events["fund:1"].status == "applied"
    assert state.virt_debt == D('337')