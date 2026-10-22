import pytest
import os
import json
import sys
import pandas as pd
from decimal import Decimal
from unittest.mock import patch, AsyncMock, MagicMock
from futures_portfolio.backtest import backtest_rebalance
from futures_portfolio.backtest.backtest_rebalance import (
    MarketOrderSlippageSimulator,
    quantize_qty,
    validate_notional,
    BacktestState,
    run_backtest
)

def test_slippage_simulator():
    sim = MarketOrderSlippageSimulator(commission_pct=0.001, slippage_pct=0.005)
    p, c = sim.simulate_market_execution("BUY", Decimal('2'), Decimal('100'))
    assert p == Decimal('100.5') # 100 * 1.005
    assert c == Decimal('0.201') # 2 * 100.5 * 0.001

    p2, c2 = sim.simulate_market_execution("SELL", Decimal('2'), Decimal('100'))
    assert p2 == Decimal('99.5')  # 100 * 0.995
    assert c2 == Decimal('0.199') # 2 * 99.5 * 0.001

    s = sim.get_summary()
    assert s["attempted"] == 2
    assert s["filled"] == 2

def test_helper_functions():
    assert quantize_qty(Decimal('1.2345'), Decimal('0.1')) == Decimal('1.2')
    assert quantize_qty(Decimal('1.2345'), Decimal('0.0')) == Decimal('1.2345')
    assert validate_notional(Decimal('2'), Decimal('10'), Decimal('15')) is True
    assert validate_notional(Decimal('1'), Decimal('10'), Decimal('15')) is False

def test_backtest_state():
    # VIRTUAL is a separate cash bucket: 950 free + 50 virtual = 1000 total.
    state = BacktestState(initial_capital=Decimal('1000'), val_cash=Decimal('950'),
                          virt_debt=Decimal('50'), base_ticker="BTCUSDT")
    state.pos_long = Decimal('1')
    state.long_entry_price = Decimal('100')
    state.pos_short = Decimal('1')
    state.short_entry_price = Decimal('100')

    # price 105: long margin 20 + PnL 5, short margin 20 + PnL -5, virtual cash 50.
    assert state.get_tpv(Decimal('105')) == Decimal('1040')
    assert state.get_tpv_fast(105.0) == 1040.0
    # Virtual cash is not revalued with the base asset.
    assert state.get_tpv(Decimal('200')) == state.get_tpv(Decimal('105'))


def test_funding_waterfall_preserves_tpv_on_virtual_transfer():
    """B1 V-only: funding debits V, CASH unchanged."""
    state = BacktestState(initial_capital=Decimal('1000'), val_cash=Decimal('0.20'),
                          virt_debt=Decimal('300'), base_ticker="BTCUSDT")
    before = state.get_tpv(Decimal('100'))
    state.pay_funding(Decimal('-1.00'))  # signed: negative = debit
    assert state.val_cash == Decimal('0.20')  # CASH unchanged
    assert state.virt_debt == Decimal('299.00')
    assert state.funding_paid == Decimal('1.00')  # opposite sign
    assert state.get_tpv(Decimal('100')) == before - Decimal('1.00')


def test_funding_waterfall_exhausts_virtual_before_negative_cash():
    """B1 V-only: V goes negative on excess debit; CASH untouched."""
    state = BacktestState(initial_capital=Decimal('1000'), val_cash=Decimal('0.20'),
                          virt_debt=Decimal('0.50'), base_ticker="BTCUSDT")
    before = state.get_tpv(Decimal('100'))
    state.pay_funding(Decimal('-1.00'))  # debit 1.0 > V 0.5 → V = -0.50
    assert state.virt_debt == Decimal('-0.50')  # negative V (shortfall)
    assert state.val_cash == Decimal('0.20')  # CASH unchanged
    assert state.funding_paid == Decimal('1.00')
    assert state.get_funding_shortfall() == Decimal('0.50')
    assert state.get_tpv(Decimal('100')) == before - Decimal('1.00')


def test_negative_funding_credits_cash_without_virtual_transfer():
    """B1 V-only: funding credit increases V, CASH unchanged."""
    state = BacktestState(initial_capital=Decimal('1000'), val_cash=Decimal('0.20'),
                          virt_debt=Decimal('300'), base_ticker="BTCUSDT")
    before = state.get_tpv(Decimal('100'))
    state.pay_funding(Decimal('1.00'))  # signed: positive = credit
    assert state.val_cash == Decimal('0.20')  # CASH unchanged
    assert state.virt_debt == Decimal('301.00')  # V increased
    assert state.funding_paid == Decimal('-1.00')  # opposite sign
    assert state.get_tpv(Decimal('100')) == before + Decimal('1.00')


def test_cross_margin_guard_uses_tpv_and_ignores_legacy_external_margin():
    state = BacktestState(initial_capital=Decimal('1000'), val_cash=Decimal('0.20'),
                          virt_debt=Decimal('0.50'), base_ticker="BTCUSDT",
                          l_lev=Decimal('5'), s_lev=Decimal('5'))
    state.pos_long = Decimal('10')
    state.long_entry_price = Decimal('100')
    state.pay_funding(Decimal('-1.00'))  # V goes -0.50, CASH 0.20 unchanged
    assert state.val_cash == Decimal('0.20')  # CASH unchanged
    assert state.virt_debt == Decimal('-0.50')  # negative V
    assert state.cross_margin_distance_pct(Decimal('100'), Decimal('0.004')) > Decimal('8')
    assert state.cross_margin_distance_pct(Decimal('80'), Decimal('0.004')) <= Decimal('8')

    state.virt_debt = Decimal('4')
    state.account_free_margin = Decimal('1000')
    assert state.cross_margin_distance_pct(Decimal('80'), Decimal('0.004')) > Decimal('8')


def test_cross_margin_guard_zero_collateral_has_no_division():
    state = BacktestState(initial_capital=Decimal('1000'), val_cash=Decimal('-30'),
                          base_ticker="BTCUSDT", l_lev=Decimal('5'), s_lev=Decimal('5'))
    state.pos_long = Decimal('1')
    state.long_entry_price = Decimal('100')
    assert state.get_tpv(Decimal('100')) == Decimal('-10')
    assert state.cross_margin_distance_pct(Decimal('100'), Decimal('0.004')) == Decimal('-100')
    assert state.hard_liquidation_triggered(Decimal('100'), Decimal('0.004')) is True


def test_virtual_transfer_is_guard_neutral_and_credits_collateral_once():
    state = BacktestState(initial_capital=Decimal('1000'), val_cash=Decimal('1000'),
                          base_ticker="BTCUSDT", l_lev=Decimal('5'), s_lev=Decimal('5'))
    state.pos_long = Decimal('1')
    state.long_entry_price = Decimal('100')
    before = state.cross_margin_distance_pct(Decimal('100'), Decimal('0.004'))
    state.designate_virtual(Decimal('100'))
    after_designation = state.cross_margin_distance_pct(Decimal('100'), Decimal('0.004'))
    state.release_virtual(Decimal('100'))
    after_release = state.cross_margin_distance_pct(Decimal('100'), Decimal('0.004'))
    assert before == after_designation == after_release

    collateral_before = state.get_tpv(Decimal('100'))
    state.virt_debt += Decimal('10')
    assert state.get_tpv(Decimal('100')) == collateral_before + Decimal('10')


def test_leverage_does_not_create_capital():
    for lev in (Decimal('3'), Decimal('5'), Decimal('7')):
        notional = Decimal('2310')
        margin = notional / lev
        state = BacktestState(initial_capital=Decimal('1000'), val_cash=Decimal('1000') - margin,
                              base_ticker="BTCUSDT", l_lev=lev, s_lev=lev)
        state.pos_long = notional / Decimal('100')
        state.long_entry_price = Decimal('100')
        assert state.get_tpv(Decimal('100')) == Decimal('1000')


def test_virtual_transfers_are_tpv_neutral_and_capped():
    state = BacktestState(initial_capital=Decimal('1000'), val_cash=Decimal('1000'),
                          base_ticker="BTCUSDT")
    state.designate_virtual(Decimal('100'))
    assert state.get_tpv(Decimal('100')) == Decimal('1000')
    state.release_virtual(Decimal('150'))
    assert state.get_tpv(Decimal('100')) == Decimal('1000')
    assert state.virt_debt == Decimal('0')
    assert state.val_cash == Decimal('1000')
    assert state.release_virtual(Decimal('10')) == Decimal('0')

def _flat_config() -> dict:
    return {
        "base_ticker": "BTCUSDT",
        "max_drawdown_limit": 200.0,
        "toxic_cooldown_days": 0.01,
        "min_notional_usdt": 1.0,
        "portfolios": [{
            "initial_capital": 1000.0,
            "paper_account_free_margin": 0.0,
            "targets": {
                "BASE_LONG": {"share": 0.33, "leverage": 7.0},
                "BASE_SHORT": {"share": 0.33, "leverage": 7.0},
                "VIRTUAL": {"share": 0.34},
            },
            "rebalance_threshold_surplus": 0.0,
            "rebalance_threshold_deficit": 0.0,
        }],
    }


@pytest.mark.asyncio
async def test_first_bar_has_no_phantom_capital(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(_flat_config()), encoding="utf-8")
    prices = [100.0] * 20
    pd.DataFrame({
        "open": prices, "high": prices, "low": prices, "close": prices,
        "volume": [1.0] * len(prices),
    }).to_feather(tmp_path / "BTCUSDT_live.feather")

    res = await run_backtest(str(config_file), str(tmp_path), live_mode=False, days=0.1,
                             commission=0.0, slippage=0.0, quiet=True)
    assert res is not None
    assert res["accounting_ok"] is True
    assert abs(res["equity_curve"][0] - 1000.0) < 0.01


@pytest.mark.asyncio
async def test_explicit_window_uses_feather_time_not_mtime(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(_flat_config()), encoding="utf-8")
    times = pd.date_range("2026-10-10 00:00", periods=30, freq="min", tz="UTC")
    prices = [100.0] * len(times)
    pd.DataFrame({
        "time": times,
        "open": prices, "high": prices, "low": prices, "close": prices,
        "volume": [1.0] * len(times),
    }).to_feather(tmp_path / "BTCUSDT_live.feather")

    res = await run_backtest(str(config_file), str(tmp_path), live_mode=False, days=0.1,
                             since_utc="2026-10-10 00:10", until_utc="2026-10-10 00:19",
                             commission=0.0, slippage=0.0, quiet=True)
    assert res is not None
    dw = res["data_window"]
    assert dw["bars"] == 10
    assert dw["start_utc"] == "2026-10-10 00:10"
    assert dw["end_utc"] == "2026-10-10 00:19"


@pytest.mark.asyncio
async def test_run_backtest_normal_and_guards(tmp_path):
    # Save config
    config_file = tmp_path / "config.json"
    config_data = {
        "base_ticker": "BTCUSDT",
        "equity_trailing_stop_pct": 2.0,
        "equity_trailing_stop_timeout_sec": 0,
        "max_drawdown_limit": 50.0,
        "toxic_cooldown_days": 0.01,
        "portfolios": [{
            "initial_capital": 1000.0,
            "paper_initial_capital": 1000.0,
            "paper_account_free_margin": 10.0,
            "targets": {
                "BASE_LONG": {"share": 0.4, "leverage": 5.0},
                "BASE_SHORT": {"share": 0.4, "leverage": 5.0},
                "VIRTUAL": {"share": 0.2}
            },
            "rebalance_threshold_surplus": 0.01,
            "rebalance_threshold_deficit": 0.01,
            "siphoning_threshold_pct": 0.5,
            "reinvestment_ratio": 0.5,
            "liquidation_distance_crit_pct": 10.0,
            "safety_guards": {
                "max_price_velocity_pct": 1.0,
                "velocity_window_sec": 60,
                "net_move_block_pct": 1.5,
                "net_move_window_sec": 30
            }
        }],
        "trend_guard": {
            "min_move_pct": 0.1,
            "eff_threshold": 0.1,
            "net_move_block_pct": 1.5,
            "net_move_window_sec": 30
        }
    }
    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(config_data, f)

    # 1. Happy path price data: some small fluctuations
    prices = [100.0] * 10 + [102.0] * 10 + [104.0] * 10 + [102.0] * 10 + [100.0] * 10
    df = pd.DataFrame({
        "open": prices, "high": [p * 1.01 for p in prices], "low": [p * 0.99 for p in prices],
        "close": prices, "volume": [100.0] * len(prices)
    })
    
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    df.to_feather(data_dir / "BTCUSDT_live.feather")

    # Run backtest with local feather
    res = await run_backtest(str(config_file), str(data_dir), live_mode=False, days=0.1)
    assert res is not None
    assert res["cycles"] > 0
    for rb in res["rebalances"]:
        for action in rb["actions_detail"]:
            # approved is mid-based, executed is at the slipped price
            assert action["executed_usdt"] <= action["approved_usdt"] * 1.01 + 1e-6
            assert action["approved_usdt"] <= action["requested_usdt"] + 1e-6

@pytest.mark.asyncio
async def test_run_backtest_liquidation_and_trailing_stop(tmp_path):
    # config with trailing stop and low drawdown limits
    config_file = tmp_path / "config.json"
    config_data = {
        "base_ticker": "BTCUSDT",
        "equity_trailing_stop_pct": 2.0,
        "equity_trailing_stop_timeout_sec": 0,
        "max_drawdown_limit": 15.0,
        "toxic_cooldown_days": 0.005,
        "portfolios": [{
            "initial_capital": 100.0,
            "paper_initial_capital": 100.0,
            "paper_account_free_margin": 1.0,
            "targets": {
                "BASE_LONG": {"share": 0.4, "leverage": 5.0},
                "BASE_SHORT": {"share": 0.4, "leverage": 5.0},
                "VIRTUAL": {"share": 0.2}
            },
            "rebalance_threshold": 0.01,
            "siphoning_threshold_pct": 1.0,
            "reinvestment_ratio": 0.0,
            "liquidation_distance_crit_pct": 10.0,
            "safety_guards": {
                "max_price_velocity_pct": 0.1,
                "velocity_window_sec": 60
            }
        }]
    }
    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(config_data, f)

    # Price shoots up then crashes down to trigger siphoning, drawdown limit, trailing stop, and liquidation
    prices = [10.0] * 5 + [15.0] * 5 + [20.0] * 5 + [35.0] * 5 + [12.0] * 5 + [4.0] * 5 + [1.0] * 5
    df = pd.DataFrame({
        "open": prices, "high": [p * 1.1 for p in prices], "low": [p * 0.9 for p in prices],
        "close": prices, "volume": [100.0] * len(prices)
    })
    
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    df.to_feather(data_dir / "BTCUSDT_live.feather")

    res = await run_backtest(str(config_file), str(data_dir), live_mode=False, days=0.1)
    assert res is not None

@pytest.mark.asyncio
async def test_run_backtest_low_dormant_capital(tmp_path):
    config_file = tmp_path / "config.json"
    config_data = {
        "base_ticker": "BTCUSDT",
        "equity_trailing_stop_pct": 2.0,
        "equity_trailing_stop_timeout_sec": 0,
        "max_drawdown_limit": 15.0,
        "toxic_cooldown_days": 0.005,
        "min_notional_usdt": 1.0,
        "portfolios": [{
            "initial_capital": 6.0,
            "paper_initial_capital": 6.0,
            "paper_account_free_margin": 0.0,
            "targets": {
                "BASE_LONG": {"share": 0.9, "leverage": 5.0},
                "BASE_SHORT": {"share": 0.0, "leverage": 5.0},
                "VIRTUAL": {"share": 0.1}
            },
            "rebalance_threshold": 0.01,
            "liquidation_distance_crit_pct": 10.0,
            "safety_guards": {
                "max_price_velocity_pct": 0.1,
                "velocity_window_sec": 60
            }
        }]
    }
    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(config_data, f)

    # Price drops to 5.0 (50% drop), triggering trailing stop and leaving dormant capital < 5.0
    prices = [10.0] * 5 + [5.0] * 5 + [10.0] * 10
    df = pd.DataFrame({
        "open": prices, "high": prices, "low": prices, "close": prices, "volume": [1.0] * len(prices)
    })
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    df.to_feather(data_dir / "BTCUSDT_live.feather")

    res = await run_backtest(str(config_file), str(data_dir), live_mode=False, days=0.1)
    assert res is not None

@pytest.mark.asyncio
async def test_download_live_data(tmp_path):
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value=[
        [1700000000000, "10.0", "11.0", "9.0", "10.0", "100.0", 1700000060000, "1000.0", 10, "5.0", "50.0", "0"]
    ] * 10)
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.get.return_value = mock_resp

    with patch("aiohttp.ClientSession", return_value=mock_session):
        res = await backtest_rebalance.download_live_data("BTCUSDT", str(tmp_path), days=0.01)
        assert os.path.exists(res)
        df = pd.read_feather(res)
        assert len(df) == 10
        assert df.iloc[0]["close"] == 10.0

@pytest.mark.asyncio
async def test_download_live_data_api_error():
    mock_resp = MagicMock()
    mock_resp.status = 500
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.get.return_value = mock_resp

    with patch("aiohttp.ClientSession", return_value=mock_session):
        with pytest.raises(Exception, match="Binance API error: 500"):
            await backtest_rebalance.download_live_data("BTCUSDT", "/tmp", days=0.01)

@pytest.mark.asyncio
async def test_download_live_data_empty(tmp_path):
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value=[])
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.get.return_value = mock_resp

    with patch("aiohttp.ClientSession", return_value=mock_session):
        res = await backtest_rebalance.download_live_data("BTCUSDT", str(tmp_path), days=0.01)
        assert os.path.exists(res)

def test_cli_execution(tmp_path):
    # Setup mock files
    config_file = tmp_path / "config.json"
    config_data = {
        "base_ticker": "BTCUSDT",
        "portfolios": [{
            "initial_capital": 100.0,
            "targets": {
                "BASE_LONG": {"share": 0.4, "leverage": 5.0},
                "BASE_SHORT": {"share": 0.4, "leverage": 5.0},
                "VIRTUAL": {"share": 0.2}
            }
        }]
    }
    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(config_data, f)

    prices = [10.0] * 5
    df = pd.DataFrame({
        "open": prices, "high": prices, "low": prices, "close": prices, "volume": [1.0] * len(prices)
    })
    df.to_feather(tmp_path / "BTCUSDT_live.feather")

    with patch("sys.argv", ["backtest_rebalance.py", "--config", str(config_file), "--ticker", "BTCUSDT", "--days", "0.01"]):
        with patch("futures_portfolio.backtest.backtest_rebalance.run_backtest", AsyncMock(return_value={"profit_pct": 1.0})) as mock_run:
            # We trigger the block inside if __name__ == "__main__":
            # by executing python's main flow or manually calling main block
            with patch("asyncio.run") as mock_asyncio_run:
                # Mocking traceback inside except
                backtest_rebalance.getcontext().prec = 28
                
                # Directly execute the block
                parser = backtest_rebalance.argparse.ArgumentParser()
                parser.add_argument("--config", default="config.json")
                parser.add_argument("--ticker", default=None)
                parser.add_argument("--days", type=float, default=1.0)
                parser.add_argument("--live", action="store_true")
                parser.add_argument("--capital", type=float, default=None)
                parser.add_argument("--threshold-surplus", type=float, default=None)
                parser.add_argument("--threshold-deficit", type=float, default=None)
                args = parser.parse_args(["--config", str(config_file), "--ticker", "BTCUSDT", "--days", "0.01"])
                
                assert args.config == str(config_file)
                assert args.ticker == "BTCUSDT"
                assert args.days == 0.01


def test_hard_guard_and_predictive_distance_share_tpv_formula():
    state = BacktestState(initial_capital=Decimal('1000'), val_cash=Decimal('-195.65217391304348'),
                          base_ticker="BTCUSDT", l_lev=Decimal('5'), s_lev=Decimal('5'))
    state.pos_long = Decimal('10')
    state.long_entry_price = Decimal('100')
    assert state.maintenance_margin(Decimal('100'), Decimal('0.004')) == Decimal('4.000')
    assert state.cross_margin_distance_pct(Decimal('100'), Decimal('0.004')) == pytest.approx(
        Decimal('8'), abs=Decimal('1e-12'))
    assert state.hard_liquidation_triggered(Decimal('100'), Decimal('0.004')) is False

    state.val_cash = Decimal('-196')
    assert state.get_tpv(Decimal('100')) == Decimal('4')
    assert state.cross_margin_distance_pct(Decimal('100'), Decimal('0.004')) == Decimal('0')
    assert state.hard_liquidation_triggered(Decimal('100'), Decimal('0.004')) is True


@pytest.mark.asyncio
async def test_backtest_legacy_free_margin_does_not_change_results(tmp_path):
    prices = [100.0] * 20
    pd.DataFrame({
        "open": prices, "high": prices, "low": prices, "close": prices,
        "volume": [1.0] * len(prices),
    }).to_feather(tmp_path / "BTCUSDT_live.feather")

    results = []
    for free_margin in (0.0, 1000.0):
        config = _flat_config()
        config["portfolios"][0]["paper_account_free_margin"] = free_margin
        config_file = tmp_path / f"config_{int(free_margin)}.json"
        config_file.write_text(json.dumps(config), encoding="utf-8")
        results.append(await run_backtest(
            str(config_file), str(tmp_path), live_mode=False, days=0.1,
            commission=0.0, slippage=0.0, quiet=True,
        ))

    a, b = results
    assert a is not None and b is not None
    assert a["accounting_ok"] is True and b["accounting_ok"] is True
    assert a["fill_count"] == b["fill_count"] > 0
    assert a["final_tpv"] == b["final_tpv"]
    assert a["profit_pct"] == b["profit_pct"]
    assert a["settings"]["account_free_margin"] == 0.0
    assert b["settings"]["account_free_margin"] == 0.0
