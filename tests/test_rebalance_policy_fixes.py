from decimal import Decimal
import json

import pandas as pd
import pytest

from backtest.backtest_rebalance import run_backtest
from core.calculator import PortfolioCalculator


@pytest.mark.parametrize("price", [80, 100, 120])
def test_virtual_surplus_bypasses_asset_fuse(price):
    targets = {
        "BASE_LONG": {"share": 0.35, "leverage": 1},
        "BASE_SHORT": {"share": 0.35, "leverage": 1},
        "VIRTUAL": {"share": 0.30, "leverage": 1},
    }
    calc = PortfolioCalculator(
        positions={}, spot_price=price, real_equity=500, virt_qty=0,
        virt_debt=500, long_entry_price=200, last_rebalance_price=150,
        targets=targets,
    )
    result = calc.calculate_rebalance(targets, 0.02, 0.04, calc.tpv)
    action = next(a for a in result["actions"] if a["key"] == "VIRTUAL")
    assert action["is_reduction"]
    assert action["diff_usdt"] == pytest.approx(-200)


@pytest.mark.parametrize("leg", ["LONG", "SHORT"])
@pytest.mark.parametrize("cash", [100, 500])
def test_futures_deficit_gets_cash_before_virtual(leg, cash):
    targets = {
        "BASE_LONG": {"share": 0.35, "leverage": 1},
        "BASE_SHORT": {"share": 0.35, "leverage": 1},
        "VIRTUAL": {"share": 0.30, "leverage": 1},
    }
    calc = PortfolioCalculator(
        positions={"BTCUSDT_LONG": 1 if leg == "LONG" else 7,
                   "BTCUSDT_SHORT": 1 if leg == "SHORT" else 7},
        spot_price=100, real_equity=800 + cash, virt_qty=0, virt_debt=100,
        long_entry_price=100, short_entry_price=100,
        last_rebalance_price=150, targets=targets,
    )
    actions = calc.calculate_rebalance(targets, 0.02, 0.04, calc.tpv)["actions"]
    assert not any(a["is_reduction"] for a in actions)
    assert actions[0]["key"] == f"BASE_{leg}"
    required = Decimal("0.35") * calc.tpv - 100
    assert actions[0]["approved_usdt"] == pytest.approx(float(min(required, cash)))
    virtual = [a for a in actions if a["key"] == "VIRTUAL"]
    if cash > required:
        assert virtual[0]["approved_usdt"] == pytest.approx(float(Decimal(cash) - required))
    else:
        assert not virtual


@pytest.mark.asyncio
async def test_backtest_uses_configured_trend_window(tmp_path):
    prices = [100.0] * 10 + [100 + i * 0.5 for i in range(1, 36)]
    pd.DataFrame({k: prices for k in ("open", "high", "low", "close", "volume")}).to_feather(
        tmp_path / "BTCUSDT_live.feather"
    )
    config = {
        "portfolios": [{
            "initial_capital": 1000,
            "targets": {
                "BASE_LONG": {"share": 0.35, "leverage": 7},
                "BASE_SHORT": {"share": 0.35, "leverage": 7},
                "VIRTUAL": {"share": 0.30, "leverage": 1},
            },
            "rebalance_threshold_surplus": 0.001,
            "rebalance_threshold_deficit": 0.001,
            "safety_guards": {
                "trend_min_move_pct": 0.1,
                "trend_eff_threshold": 0.55,
                "net_move_block_pct": 100,
                "max_price_velocity_pct": 100,
            },
        }],
    }
    path = tmp_path / "config.json"
    guards = config["portfolios"][0]["safety_guards"]
    results = {}
    for window in (None, 30, 10):
        if window is not None:
            guards["trend_lookback_bars"] = window
        path.write_text(json.dumps(config), encoding="utf-8")
        results[window] = await run_backtest(str(path), str(tmp_path), days=1)
        assert results[window] is not None
    assert results[None]["equity_curve"] == results[30]["equity_curve"]
    blocks = lambda result: [e["bar"] for e in result["events"] if e["kind"] == "trend_guard_block"]
    assert blocks(results[30])
    assert blocks(results[10]) != blocks(results[30])
    for window in (0, -1):
        guards["trend_lookback_bars"] = window
        path.write_text(json.dumps(config), encoding="utf-8")
        assert await run_backtest(str(path), str(tmp_path), days=1) is None
