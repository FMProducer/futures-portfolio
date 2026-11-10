"""Unit tests for Delta Limit gate and Account MaxDD normalization."""
from decimal import Decimal
import pytest

from core.calculator import PortfolioCalculator, select_tick_actions
from backtest.backtest_rebalance import run_backtest


def test_delta_imbalance_calculation():
    """Verify calculation of physical delta imbalance ratio."""
    # Equal positions -> 0% imbalance
    calc_eq = PortfolioCalculator(
        positions={"BTCUSDT_LONG": 1.0, "BTCUSDT_SHORT": 1.0},
        spot_price=100.0,
        real_equity=1000.0,
        long_entry_price=100.0,
        short_entry_price=100.0,
        virt_qty=0.0,
        base_ticker="BTCUSDT"
    )
    assert calc_eq.delta_imbalance == Decimal("0")

    # Q_long = 1.0, Q_short = 0.5 -> |1.0 - 0.5| / 1.0 = 0.5 (50%)
    calc_imb = PortfolioCalculator(
        positions={"BTCUSDT_LONG": 1.0, "BTCUSDT_SHORT": 0.5},
        spot_price=100.0,
        real_equity=1000.0,
        long_entry_price=100.0,
        short_entry_price=100.0,
        virt_qty=0.0,
        base_ticker="BTCUSDT"
    )
    assert calc_imb.delta_imbalance == Decimal("0.5")


def test_delta_limit_gate_bypasses_trailing_wait_for_excess_leg():
    """When delta_imbalance > delta_limit_threshold, trailing wait for reducing excess leg is bypassed."""
    targets = {
        "BASE_LONG": {"share": 0.25, "leverage": 5},
        "BASE_SHORT": {"share": 0.25, "leverage": 5},
        "VIRTUAL": {"share": 0.50, "leverage": 1}
    }

    # Positions: LONG has 20.0 qty, SHORT has 10.0 qty -> delta imbalance = 50%
    # Long NAV share = 40% > target 25% + 4% surplus threshold
    calc = PortfolioCalculator(
        positions={"BTCUSDT_LONG": 20.0, "BTCUSDT_SHORT": 10.0},
        spot_price=100.0,
        real_equity=1000.0,
        long_entry_price=100.0,
        short_entry_price=100.0,
        virt_qty=0.0,
        targets=targets,
        base_ticker="BTCUSDT"
    )

    # Trailing state where LONG peak is 0.41, current share is 0.40 -> pullback is only 0.01 < distance 0.05
    trail = {
        "BASE_LONG": {"peak": "0.41", "trough": None},
        "BASE_SHORT": {"peak": None, "trough": None}
    }

    # Without delta limit (threshold=0.0): reduce_ready is False because peak-sh < distance
    res_no_gate = calc.calculate_rebalance(
        targets,
        threshold_surplus=0.04,
        threshold_deficit=0.04,
        current_equity=calc.tpv,
        fuse_enabled=False,
        share_trail=trail.copy(),
        trail_distance=0.05,
        delta_limit_threshold=0.0
    )
    assert not res_no_gate["delta_limit_triggered"]
    assert not any(a["key"] == "BASE_LONG" and a.get("is_reduction") for a in res_no_gate["actions"])

    # With delta limit threshold = 0.25 (25% < 50% imbalance):
    # Trail pullback wait is bypassed for BASE_LONG reduction
    res_with_gate = calc.calculate_rebalance(
        targets,
        threshold_surplus=0.04,
        threshold_deficit=0.04,
        current_equity=calc.tpv,
        fuse_enabled=False,
        share_trail=trail.copy(),
        trail_distance=0.05,
        delta_limit_threshold=0.25
    )
    assert res_with_gate["delta_limit_triggered"]
    long_red = [a for a in res_with_gate["actions"] if a["key"] == "BASE_LONG" and a.get("is_reduction")]
    assert len(long_red) == 1


def test_select_tick_actions_prioritizes_excess_reduction():
    """select_tick_actions prioritizes the excess leg when preferred_reduction is specified."""
    actions = [
        {"key": "BASE_SHORT", "is_reduction": True, "diff_usdt": -50.0},
        {"key": "BASE_LONG", "is_reduction": True, "diff_usdt": -100.0},
    ]
    # Without preferred_reduction, first reduction in list is picked
    sel_default = select_tick_actions(actions, initial_build=False)
    assert sel_default[0]["key"] == "BASE_SHORT"

    # With preferred_reduction="BASE_LONG", BASE_LONG is chosen
    sel_pref = select_tick_actions(actions, initial_build=False, preferred_reduction="BASE_LONG")
    assert sel_pref[0]["key"] == "BASE_LONG"


@pytest.mark.asyncio
async def test_account_max_dd_in_backtest():
    """Verify that run_backtest produces account_max_dd_pct < max_dd_pct when cash reserve exists."""
    res = await run_backtest(
        config_path="config.json",
        data_dir="data",
        ticker_override="ZECUSDT",
        since_utc="2025-10-20 00:00",
        until_utc="2025-10-21 00:00",
        quiet=True
    )
    assert res is not None
    assert "account_max_dd_pct" in res
    assert "max_dd_pct" in res
    assert res["account_max_dd_pct"] <= res["max_dd_pct"]
    assert res["total_account_initial"] == 6000.0
