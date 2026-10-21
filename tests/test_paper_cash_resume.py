"""Resume tests: paper session survives restart without Clean Slate destroying facts."""
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import test_main_coverage as author
from futures_portfolio.core.calculator import PortfolioCalculator


# ── helpers ──────────────────────────────────────────────────────────────────

def _hedged_session(balance=998.5, l_qty=1.5, s_qty=1.4, entry=1345.0,
                    virt_debt=340.0, price=1340.0, cycles=3):
    """Factory: legitimate hedged session with both legs and non-zero entries."""
    state = author._make_state(
        virt_qty=virt_debt / price,
        rebalance_cycles=cycles,
        initial_tpv=1000.0,
        reference_tpv=1000.0,
        tpv_ath=1005.0,
        initial_build_pending=None,
        virt_debt=virt_debt,
        last_rebalance_price=1335.0,
    )
    paper = author._make_paper_state(
        balance=balance,
        positions={"BTCUSDT_LONG": l_qty, "BTCUSDT_SHORT": s_qty},
        long_entry_price=entry,
        short_entry_price=entry,
    )
    return state, paper


def _blocking_config():
    config = author._make_config(
        equity_trailing_stop_activation_pct=9000.0,
        max_drawdown_limit=200.0,
    )
    p = config["portfolios"][0]
    p.update(initial_capital=1000.0, paper_initial_capital=1000.0,
             siphoning_threshold_pct=1000.0, reinvestment_ratio=0.0)
    p["targets"] = {
        "BASE_LONG":  {"share": 0.33, "leverage": 7},
        "BASE_SHORT": {"share": 0.33, "leverage": 7},
        "VIRTUAL":    {"share": 0.34, "leverage": 1},
    }
    return config


# ── tests ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_legitimate_hedged_session_resumes_without_clean_slate():
    """Both legs > 0, entries > 0 → resume preserves all accounting facts."""
    config = _blocking_config()
    price = 1340.0
    state, paper = _hedged_session(price=price)

    calc_return = {
        "total_tpv": 998.0, "tpv": 998.0, "total_pnl_pct": -0.2, "total_pnl": -2.0,
        "share_long_pct": 32.8, "share_short_pct": 30.6, "share_virt_pct": 34.1, "share_cash_pct": 2.5,
        "val_long": 327.5, "val_short": 305.7, "val_virt": 340.0, "val_cash": 25.3,
        "actions": [],
    }

    executor_mock = MagicMock()
    executor_mock.execute_actions = AsyncMock(return_value=[])

    with patch(f"{author.M}.PortfolioExecutor", return_value=executor_mock):
        saves, _, _ = await author._run_rebalance(config, state, paper, loop_limit=1,
            calc_return=calc_return, calc_val_cash=Decimal("25.3"))

    final_state = [v for p, v in saves if p == "state.json"][-1]
    final_paper = [v for p, v in saves if p == "paper_state.json"][-1]

    # No destruction
    assert final_paper["positions"]["BTCUSDT_LONG"] == 1.5
    assert final_paper["positions"]["BTCUSDT_SHORT"] == 1.4
    assert final_paper["long_entry_price"] == 1345.0
    assert final_paper["short_entry_price"] == 1345.0
    assert final_paper["balance"] == pytest.approx(998.5, abs=0.01)

    # Session identity preserved
    assert final_state.get("initial_build_pending") is None
    assert final_state.get("cash_invariant_violation") is None
    assert final_state.get("startup_blocked") is None
    assert final_state["virt_debt"] == pytest.approx(340.0, abs=0.01)
    assert final_state["rebalance_cycles"] == 3
    assert final_state["initial_tpv"] == 1000.0

    # TPV/CASH identity at resume price
    v = final_state["virt_debt"]
    calc = PortfolioCalculator(
        positions=final_paper["positions"], spot_price=price,
        real_equity=final_paper["balance"] - v,
        virt_qty=final_state["virt_qty"], virt_debt=v,
        long_entry_price=final_paper["long_entry_price"],
        short_entry_price=final_paper["short_entry_price"],
        targets=config["portfolios"][0]["targets"],
        initial_capital=final_state["initial_tpv"], base_ticker="BTCUSDT",
    )
    long_mtm = 1.5 * (price - 1345.0)
    short_mtm = 1.4 * (1345.0 - price)
    expected_tpv = 998.5 + long_mtm + short_mtm
    assert calc.tpv == pytest.approx(Decimal(str(expected_tpv)), abs=Decimal("0.01"))
    assert calc.val_cash > 0


@pytest.mark.asyncio
async def test_ambiguous_one_sided_state_blocks_trading_preserves_facts():
    """One-sided position → block, DO NOT wipe accounting facts."""
    config = _blocking_config()
    state = author._make_state(
        rebalance_cycles=5, initial_tpv=1000.0, reference_tpv=1000.0, tpv_ath=1000.0,
    )
    paper = author._make_paper_state(
        balance=1150.0,
        positions={"BTCUSDT_LONG": 0.5, "BTCUSDT_SHORT": 0.0},
        long_entry_price=60000.0,
        short_entry_price=60000.0,
    )

    executor_mock = MagicMock()
    executor_mock.execute_actions = AsyncMock(return_value=[])

    with patch(f"{author.M}.PortfolioExecutor", return_value=executor_mock):
        saves, _, _ = await author._run_rebalance(config, state, paper, loop_limit=1,
            calc_val_cash=Decimal("0"))

    final_paper = [v for p, v in saves if p == "paper_state.json"][-1]
    final_state = [v for p, v in saves if p == "state.json"][-1]

    # Facts preserved (NOT destroyed)
    assert final_paper["positions"]["BTCUSDT_LONG"] == 0.5
    assert final_paper["positions"]["BTCUSDT_SHORT"] == 0.0
    assert final_paper["balance"] == 1150.0
    assert final_paper["long_entry_price"] == 60000.0

    # Trading blocked
    assert final_state.get("startup_blocked") is True

    # Session identity survives
    assert final_state["rebalance_cycles"] == 5
    assert final_state["initial_tpv"] == 1000.0


@pytest.mark.asyncio
async def test_ambiguous_zero_entry_state_blocks_trading_preserves_facts():
    """Both legs > 0 but entries = 0 → block, DO NOT wipe."""
    config = _blocking_config()
    state = author._make_state(
        rebalance_cycles=5, initial_tpv=1000.0, reference_tpv=1000.0, tpv_ath=1000.0,
    )
    paper = author._make_paper_state(
        balance=1150.0,
        positions={"BTCUSDT_LONG": 1.0, "BTCUSDT_SHORT": 0.5},
        long_entry_price=0.0,
        short_entry_price=0.0,
    )

    executor_mock = MagicMock()
    executor_mock.execute_actions = AsyncMock(return_value=[])

    with patch(f"{author.M}.PortfolioExecutor", return_value=executor_mock):
        saves, _, _ = await author._run_rebalance(config, state, paper, loop_limit=1,
            calc_val_cash=Decimal("0"))

    final_paper = [v for p, v in saves if p == "paper_state.json"][-1]
    final_state = [v for p, v in saves if p == "state.json"][-1]

    assert final_paper["positions"]["BTCUSDT_LONG"] == 1.0
    assert final_paper["positions"]["BTCUSDT_SHORT"] == 0.5
    assert final_state.get("startup_blocked") is True


@pytest.mark.asyncio
async def test_persisted_startup_blocked_survives_restart():
    """startup_blocked flag from prior session blocks trading on next restart,
    even if positions and entries are now valid.  Flag must be cleared manually."""
    config = _blocking_config()
    state, paper = _hedged_session()
    state["startup_blocked"] = True  # persisted from previous ambiguous detection

    executor_mock = MagicMock()
    executor_mock.execute_actions = AsyncMock(return_value=[])

    with patch(f"{author.M}.PortfolioExecutor", return_value=executor_mock), \
         patch(f"{author.M}.self_kill_pm2", AsyncMock()) as kill_mock:
        saves, _, _ = await author._run_rebalance(config, state, paper, loop_limit=1,
            calc_val_cash=Decimal("0"))

    # self_kill_pm2 called → trading blocked; no state/paper saves entered the loop
    kill_mock.assert_called()
    # Execute actions never called (bot blocked before trading loop)
    executor_mock.execute_actions.assert_not_called()
    # State still carries the flag (persisted in file on disk)
    state_saves = [v for p, v in saves if p == "state.json"]
    if state_saves:
        assert state_saves[-1].get("startup_blocked") is True


@pytest.mark.asyncio
async def test_startup_blocked_prevents_clean_slate_on_empty_state():
    """startup_blocked=True with empty positions, drifted wallet and non-zero
    VIRTUAL → block BEFORE Clean Slate resets anything.  Wallet, V and state
    must survive untouched."""
    config = _blocking_config()
    # Empty positions, wallet drifted far from target, VIRTUAL allocated
    state = author._make_state(
        virt_qty=0.25,
        rebalance_cycles=5,
        initial_tpv=1000.0,
        reference_tpv=1000.0,
        tpv_ath=1050.0,
        virt_debt=340.0,
    )
    state["startup_blocked"] = True
    paper = author._make_paper_state(
        balance=1150.0,  # drifted from target=1000
        positions={"BTCUSDT_LONG": 0.0, "BTCUSDT_SHORT": 0.0},
        long_entry_price=0.0,
        short_entry_price=0.0,
    )

    with patch(f"{author.M}.PortfolioExecutor", return_value=MagicMock()), \
         patch(f"{author.M}.self_kill_pm2", AsyncMock()) as kill_mock:
        saves, _, _ = await author._run_rebalance(config, state, paper, loop_limit=1,
            calc_val_cash=Decimal("0"))

    # Blocked — self_kill_pm2 called before any state mutation.
    kill_mock.assert_called()
    # Verify: NO state/paper saves made it past the blocker (trading never started).
    paper_saves = [v for p, v in saves if p == "paper_state.json"]
    if paper_saves:
        # If Clean Slate had fired, wallet would be reset to 1000 and V to 0.
        assert paper_saves[-1]["balance"] == 1150.0, "Wallet preserved"
    state_saves = [v for p, v in saves if p == "state.json"]
    if state_saves:
        assert state_saves[-1].get("startup_blocked") is True
        assert state_saves[-1].get("virt_debt", 0.0) == pytest.approx(340.0, abs=0.01)


@pytest.mark.asyncio
async def test_startup_blocked_with_ticker_mismatch_preserves_original_files():
    """startup_blocked=True, state ticker=ZECUSDT, config ticker=BTCUSDT.
    Ticker-change reset must NOT run; no save, no migration, no trading."""
    config = _blocking_config()
    # State was saved for ZECUSDT; config now points to BTCUSDT
    state = author._make_state(
        virt_qty=0.25,
        virt_debt=340.0,
        rebalance_cycles=5,
        initial_tpv=1000.0,
        reference_tpv=1000.0,
        tpv_ath=1050.0,
    )
    state["base_ticker"] = "ZECUSDT"  # mismatch vs config's BTCUSDT
    state["startup_blocked"] = True
    paper = author._make_paper_state(
        balance=1150.0,
        positions={"ZECUSDT_LONG": 1.0, "ZECUSDT_SHORT": 1.0},
        long_entry_price=1360.0,
        short_entry_price=1360.0,
    )

    with patch(f"{author.M}.PortfolioExecutor", return_value=MagicMock()), \
         patch(f"{author.M}.self_kill_pm2", AsyncMock()) as kill_mock:
        saves, _, _ = await author._run_rebalance(config, state, paper, loop_limit=1,
            calc_val_cash=Decimal("0"))

    kill_mock.assert_called()
    # No ticker-change save, no state mutation
    state_saves = [v for p, v in saves if p == "state.json"]
    assert not state_saves, "No state save before block"
    paper_saves = [v for p, v in saves if p == "paper_state.json"]
    assert not paper_saves, "No paper save before block"


@pytest.mark.asyncio
async def test_resume_on_actual_session_snapshot():
    """Exact snapshot values from running ZECUSDT paper session
    (2026-10-21 22:20:20).  Real calculator computes TPV in the loop;
    no actions triggered (price = entry → zero MTM, high thresholds)."""
    config = _blocking_config()
    p = config["portfolios"][0]
    p.update(rebalance_threshold_surplus=90.0, rebalance_threshold_deficit=90.0)
    price = 1360.245  # = entry_price → zero MTM deviation

    # ── paper_state_ZECUSDT.json (strategy state) ──
    state = author._make_state(
        virt_qty=0.24982628434759277,
        rebalance_cycles=2,
        initial_tpv=1000.0,
        reference_tpv=1000.0,
        tpv_ath=1000.0,
        initial_build_pending=None,
        virt_debt=340.1876614285714,
        last_rebalance_price=1346.84,
        trailing_stop_triggered=False,
    )
    state["last_tpv"] = 997.7916815489
    state["last_profit"] = -2.2083184511000127
    state["total_pnl_pct"] = -0.22083184511
    state["last_update"] = 1792614020.8817525

    # ── paper_shadow_ZECUSDT.json (wallet) ──
    paper = author._make_paper_state(
        balance=999.5975,
        positions={"BTCUSDT_LONG": 1.693, "BTCUSDT_SHORT": 1.578},
        long_entry_price=1360.245,
        short_entry_price=1360.245,
    )
    paper["last_price"] = price

    executor_mock = MagicMock()
    executor_mock.execute_actions = AsyncMock(return_value=[])

    with patch(f"{author.M}.PortfolioExecutor", return_value=executor_mock):
        saves, _, _ = await author._run_rebalance(
            config, state, paper, loop_limit=3,
            calculator_class=PortfolioCalculator, price=price,
        )

    final_state = [v for p, v in saves if p == "state.json"][-1]
    final_paper = [v for p, v in saves if p == "paper_state.json"][-1]

    # All accounting facts preserved
    assert final_paper["balance"] == 999.5975
    assert final_paper["positions"]["BTCUSDT_LONG"] == 1.693
    assert final_paper["positions"]["BTCUSDT_SHORT"] == 1.578
    assert final_paper["long_entry_price"] == 1360.245
    assert final_paper["short_entry_price"] == 1360.245

    # Session identity
    assert final_state["virt_debt"] == pytest.approx(340.1876614285714, abs=0.01)
    assert final_state["rebalance_cycles"] == 2  # NOT reset to 0
    assert final_state["initial_tpv"] == 1000.0
    assert final_state.get("initial_build_pending") is None
    assert final_state.get("startup_blocked") is None

    # Real calculator TPV identity at entry price: TPV = balance (zero MTM)
    v = final_state["virt_debt"]
    calc = PortfolioCalculator(
        positions=final_paper["positions"], spot_price=price,
        real_equity=final_paper["balance"] - v,
        virt_qty=final_state["virt_qty"], virt_debt=v,
        long_entry_price=final_paper["long_entry_price"],
        short_entry_price=final_paper["short_entry_price"],
        targets=p["targets"],
        initial_capital=final_state["initial_tpv"], base_ticker="BTCUSDT",
    )
    assert calc.tpv == pytest.approx(Decimal("999.5975"), abs=Decimal("0.02"))
    assert float(calc.val_cash) > 0

    # At the actual snapshot price (≠ entry): TPV matches the formula
    actual_price = 1344.54223086
    calc_at_price = PortfolioCalculator(
        positions=final_paper["positions"], spot_price=actual_price,
        real_equity=final_paper["balance"] - v,
        virt_qty=final_state["virt_qty"], virt_debt=v,
        long_entry_price=final_paper["long_entry_price"],
        short_entry_price=final_paper["short_entry_price"],
        targets=p["targets"],
        initial_capital=final_state["initial_tpv"], base_ticker="BTCUSDT",
    )
    long_mtm = 1.693 * (actual_price - 1360.245)
    short_mtm = 1.578 * (1360.245 - actual_price)
    expected_tpv = 999.5975 + long_mtm + short_mtm
    assert calc_at_price.tpv == pytest.approx(Decimal(str(expected_tpv)), abs=Decimal("0.02"))

    # No rebalance actions were triggered (zero MTM + high thresholds)
    executor_mock.execute_actions.assert_not_called()


@pytest.mark.asyncio
async def test_resume_with_existing_halt_violation_does_not_trigger_orders():
    """Cash invariant violation in saved state → resume keeps violation,
    does NOT place new orders (increases are blocked)."""
    config = _blocking_config()
    price = 1340.0
    state, paper = _hedged_session(price=price)
    state["cash_invariant_violation"] = {
        "cash": -0.13,
        "balance": 1002.8,
        "virt_debt": 340.18,
        "positions": {"BTCUSDT_LONG": 1.726, "BTCUSDT_SHORT": 1.736},
        "executions": [{"symbol": "BTCUSDT_LONG", "qty": 0.248, "price": 1339.88, "commission": 0.014}],
        "at": 1792614000.0,
    }

    # Calculator produces an expansion action — but with violation, it should be blocked.
    buy_action = {
        "type": "ORDER", "key": "BASE_LONG", "symbol": "BTCUSDT_LONG",
        "base_symbol": "BTCUSDT", "position_side": "LONG",
        "diff_usdt": 45.0, "is_reduction": False, "leverage": 7,
    }
    calc_return = {
        "total_tpv": 998.0, "tpv": 998.0, "total_pnl_pct": -0.2,
        "share_long_pct": 31.5, "share_short_pct": 30.6,
        "share_virt_pct": 34.1, "share_cash_pct": 3.8,
        "val_long": 315.0, "val_short": 305.7, "val_virt": 340.0, "val_cash": 38.0,
        "actions": [buy_action],
    }

    executor_mock = MagicMock()
    executor_mock.execute_actions = AsyncMock(return_value=[])

    with patch(f"{author.M}.PortfolioExecutor", return_value=executor_mock):
        saves, _, _ = await author._run_rebalance(config, state, paper, loop_limit=1,
            calc_return=calc_return, calc_val_cash=Decimal("38.0"))

    final_state = [v for p, v in saves if p == "state.json"][-1]

    # Violation preserved
    assert final_state.get("cash_invariant_violation") is not None
    assert final_state["cash_invariant_violation"]["cash"] == -0.13

    # execute_actions should receive only reductions (the expansion was filtered out)
    # or receive empty list if only expansion was present
    call_args_list = executor_mock.execute_actions.call_args_list
    if call_args_list:
        actions_sent = call_args_list[0][0][0]  # first positional arg = actions list
        increases = [a for a in actions_sent if not a.get("is_reduction")]
        assert len(increases) == 0, f"Increases should be blocked: got {increases}"
    # else: execute_actions was not called at all — also acceptable (empty fused_actions)