import copy
import json

import pandas as pd
import pytest

from core.capital import capital_with_reserve
from core.calculator import PortfolioCalculator
from core import share_trail
from decimal import Decimal
from backtest.backtest_rebalance import run_backtest


def portfolio(reserve=461):
    return {"initial_capital": 200, "cash_reserve_usdt": reserve, "targets": {
        "BASE_LONG": {"share": .5, "leverage": 3},
        "BASE_SHORT": {"share": .5, "leverage": 3},
        "VIRTUAL": {"share": 0, "leverage": 1}}}


def test_cash_reserve_keeps_initial_exposure_and_does_not_mutate():
    cfg = portfolio()
    original = copy.deepcopy(cfg)
    total, targets = capital_with_reserve(cfg, 200)
    assert total == 661
    assert targets["BASE_LONG"]["share"] * total == pytest.approx(100)
    assert targets["BASE_SHORT"]["share"] * total == pytest.approx(100)
    assert cfg == original
    assert capital_with_reserve(portfolio(0), 200) == (200, original["targets"])


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf"), True, "461"])
def test_cash_reserve_rejects_invalid_values(bad):
    with pytest.raises(ValueError):
        capital_with_reserve(portfolio(bad), 200)


@pytest.mark.asyncio
async def test_backtest_keeps_reserve_in_cash_and_counts_it_once(tmp_path):
    cfg = {"base_ticker": "TESTUSDT", "min_notional_usdt": 1, "portfolios": [portfolio()]}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg))
    pd.DataFrame({k: [100.] * 5 for k in ["open", "high", "low", "close", "volume"]}).to_feather(tmp_path / "TESTUSDT_live.feather")
    result = await run_backtest(str(path), str(tmp_path), days=5/1440, commission=0, slippage=0, quiet=True)
    assert result and result["accounting_ok"] and not result["cash_invariant_violation"]
    assert result["initial_capital"] == 661
    assert result["trading_initial_capital"] == 200
    assert result["cash_reserve_usdt"] == 461
    formation = result["initial_formation"]
    assert formation["long_margin"] == pytest.approx(100)
    assert formation["short_margin"] == pytest.approx(100)
    assert formation["cash"] == pytest.approx(461)
    assert formation["virtual"] == 0
    assert json.loads(path.read_text()) == cfg


@pytest.mark.parametrize("distance", [0, .05800887342949769])
def test_reserve_preserves_trade_band_and_trailing_response(distance):
    responses = []
    for reserve in (0, 8900):
        total, targets = capital_with_reserve(portfolio(reserve), 1000)
        trail = share_trail.fresh_state()
        sequence = []
        for price in (100, 104, 108, 112, 108):
            calc = PortfolioCalculator({"TESTUSDT_LONG": 15, "TESTUSDT_SHORT": 15},
                price, total, 0, long_entry_price=100, short_entry_price=100,
                base_ticker="TESTUSDT", targets=targets, initial_capital=total, last_rebalance_price=100)
            result = calc.calculate_rebalance(targets, .04, .04, calc.tpv, fuse_enabled=False,
                share_trail=trail, trail_distance=distance, share_basis_scale=1000/total)
            sequence.append({a['key']: a['diff_usdt'] for a in result['actions']})
        responses.append(sequence)
    assert responses[0][-1]
    for expected, actual in zip(*responses):
        assert actual == pytest.approx(expected)


def test_reserve_does_not_lower_fuse_price_threshold():
    total, targets = capital_with_reserve(portfolio(8900), 1000)
    calc = PortfolioCalculator({"TESTUSDT_LONG": 15, "TESTUSDT_SHORT": 15},
        103, total, 0, long_entry_price=100, short_entry_price=100,
        base_ticker="TESTUSDT", targets=targets, initial_capital=total, last_rebalance_price=100)
    result = calc.calculate_rebalance(targets, .04, .04, calc.tpv, fuse_enabled=True, share_basis_scale=1000/total)
    assert not any(a['key']=='BASE_LONG' and a['is_reduction'] for a in result['actions'])
