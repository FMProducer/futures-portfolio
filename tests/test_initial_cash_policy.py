import json

import pandas as pd
import pytest

from futures_portfolio.backtest.backtest_rebalance import run_backtest
from futures_portfolio.core.calculator import PortfolioCalculator, initial_notional_after_fee


def _targets():
    return {
        "BASE_LONG": {"share": 0.33, "leverage": 7.0},
        "BASE_SHORT": {"share": 0.33, "leverage": 7.0},
        "VIRTUAL": {"share": 0.34, "leverage": 1.0},
    }


def test_initial_notional_reserves_fee():
    target_notional = 2310.0
    adjusted = initial_notional_after_fee(target_notional, leverage=7.0, fee_rate=0.0004)
    assert adjusted < target_notional
    assert adjusted / 7.0 + adjusted * 0.0004 == pytest.approx(330.0)


def test_calculator_does_not_zero_small_negative_cash():
    calc = PortfolioCalculator(
        positions={"BTCUSDT_LONG": 0.001},
        spot_price=100.0,
        real_equity=0.0,
        virt_qty=0.0,
        virt_debt=0.0,
        long_entry_price=100.0,
        base_ticker="BTCUSDT",
        targets=_targets(),
        initial_capital=100.0,
    )
    assert calc.val_cash < 0


@pytest.mark.asyncio
async def test_initial_cash_is_zero_and_residual_moves_to_virtual(tmp_path):
    config = {
        "base_ticker": "BTCUSDT",
        "min_notional_usdt": 1.0,
        "portfolios": [{
            "initial_capital": 1000.0,
            "paper_initial_capital": 1000.0,
            "targets": _targets(),
            "rebalance_threshold_surplus": 0.0,
            "rebalance_threshold_deficit": 0.0,
        }],
    }
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(config), encoding="utf-8")

    prices = [100.0] * 20
    pd.DataFrame({
        "open": prices,
        "high": prices,
        "low": prices,
        "close": prices,
        "volume": [1.0] * len(prices),
    }).to_feather(tmp_path / "BTCUSDT_live.feather")

    res = await run_backtest(
        str(config_file), str(tmp_path), live_mode=False, days=0.1,
        commission=0.0004, slippage=0.0, quiet=True,
    )

    assert res is not None
    assert res["accounting_ok"] is True
    assert res["cash_invariant_violation"] is None
    initial = res["initial_formation"]
    assert initial is not None
    assert initial["cash"] == pytest.approx(0.0, abs=1e-9)
    assert initial["virtual"] >= 340.0
    assert initial["commission_paid"] > 0
    assert len(initial["fills"]) >= 2
    assert sum(fill["commission"] for fill in initial["fills"]) == pytest.approx(initial["commission_paid"])


@pytest.mark.asyncio
async def test_partial_initial_build_keeps_both_legs_and_closes_cash(tmp_path):
    config = {
        "base_ticker": "BTCUSDT",
        "min_notional_usdt": 1.0,
        "portfolios": [{
            "initial_capital": 1000.0,
            "paper_initial_capital": 1000.0,
            "targets": {
                "BASE_LONG": {"share": 0.25, "leverage": 7.0},
                "BASE_SHORT": {"share": 0.25, "leverage": 7.0},
                "VIRTUAL": {"share": 0.5, "leverage": 1.0},
            },
            "rebalance_threshold_surplus": 0.0,
            "rebalance_threshold_deficit": 0.0,
        }],
    }
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(config), encoding="utf-8")

    prices = [100.0] * 5
    pd.DataFrame({
        "open": prices,
        "high": prices,
        "low": prices,
        "close": prices,
        "volume": [1.0] * len(prices),
    }).to_feather(tmp_path / "BTCUSDT_live.feather")

    res = await run_backtest(
        str(config_file), str(tmp_path), live_mode=False, days=0.1,
        commission=0.0004, slippage=0.0, quiet=True,
    )

    assert res is not None
    assert res["accounting_ok"] is True
    assert res["cash_invariant_violation"] is None
    initial = res["initial_formation"]
    assert initial["cash"] == pytest.approx(0.0, abs=1e-9)
    assert initial["virtual"] > 500.0
    assert initial["long_position"] > 0
    assert initial["short_position"] > 0
    first_actions = res["rebalances"][0]["actions_detail"]
    assert any(action["status"] == "partial" for action in first_actions)
    assert initial["commission_paid"] > 0


@pytest.mark.asyncio
async def test_failed_initial_leg_is_not_treated_as_complete(tmp_path):
    config = {
        "base_ticker": "BTCUSDT",
        "min_notional_usdt": 1.0,
        "portfolios": [{
            "initial_capital": 1000.0,
            "paper_initial_capital": 1000.0,
            "targets": {
                "BASE_LONG": {"share": 0.33, "leverage": 7.0},
                "BASE_SHORT": {"share": 0.0001, "leverage": 7.0},
                "VIRTUAL": {"share": 0.5, "leverage": 1.0},
            },
            "rebalance_threshold_surplus": 0.0,
            "rebalance_threshold_deficit": 0.0,
        }],
    }
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(config), encoding="utf-8")

    prices = [100.0] * 5
    pd.DataFrame({
        "open": prices,
        "high": prices,
        "low": prices,
        "close": prices,
        "volume": [1.0] * len(prices),
    }).to_feather(tmp_path / "BTCUSDT_live.feather")

    res = await run_backtest(
        str(config_file), str(tmp_path), live_mode=False, days=0.1,
        commission=0.0004, slippage=0.0, quiet=True,
    )

    assert res is not None
    initial = res["initial_formation"]
    assert initial["complete"] is False
    assert initial["cash"] > 0
    assert initial["virtual"] == pytest.approx(500.0, abs=1e-9)
    assert res["initial_build_pending"]["missing"] == ["BASE_SHORT"]

    long_fills = [fill for fill in res["fills"] if fill["leg"] == "BASE_LONG"]
    short_fills = [fill for fill in res["fills"] if fill["leg"] == "BASE_SHORT"]
    assert len(long_fills) == 1
    assert short_fills == []
