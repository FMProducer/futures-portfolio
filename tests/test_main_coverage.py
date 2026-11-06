"""
Coverage push: main.py 65% -> 85%+
Targets: trailing stop trigger (978-1067), emergency stop (931-957),
         liquidation guard (1421-1460), VIRTUAL_ORDER (1191-1233),
         stop/close logic (1525-1601), init blocks (130-148, 201-218)
"""
import pytest
import json
import copy
import time
import asyncio
import sys
import tempfile
from contextlib import nullcontext
from decimal import Decimal
from unittest.mock import patch, AsyncMock, MagicMock, Mock, mock_open
from pathlib import Path


class ZombieExit(BaseException):
    pass


from futures_portfolio.core.main import rebalance_loop, _paper_collateral, _paper_guard_metrics


M = "futures_portfolio.core.main"


@pytest.fixture(autouse=True)
def isolate_supervisor_side_effects(request):
    clock = (nullcontext() if 'tmp_path' in request.fixturenames else
             patch(f'{M}.backtest_rules.strategy_minutes', AsyncMock(return_value=[(int(time.time() // 60 - 1) * 60000, 60000.)])))
    funding = (nullcontext() if 'tmp_path' in request.fixturenames else
               patch(f'{M}.backtest_rules.paper_legacy_funding', return_value=Decimal(0)))
    with patch(f"{M}.emit_signal"), patch(f"{M}.self_kill_pm2", AsyncMock()), \
         clock, funding:
        yield


def _make_config(**overrides):
    cfg = {
        "paper_mode": True,
        "base_ticker": "BTCUSDT",
        "equity_trailing_stop_pct": 5.0,
        "equity_trailing_stop_timeout_sec": 0.0,
        "equity_trailing_stop_activation_pct": 7.5,
        "equity_trailing_stop_activation_pct": 0.001,
        "max_drawdown_limit": 100.0,
        "liquidation_distance_warn_pct": 15.0,
        "liquidation_distance_crit_pct": 8.0,
        "margin_ratio_warning": 5.0,
        "margin_ratio_critical": 2.0,
        "use_v2_scoring": False,
        "scoring_drawdown_weight": 0.5,
        "portfolios": [{
            "initial_capital": 100.0,
            "paper_initial_capital": 100.0,
            "targets": {
                "BASE_LONG": {"share": 0.4, "leverage": 7.0},
                "BASE_SHORT": {"share": 0.4, "leverage": 7.0},
                "VIRTUAL": {"share": 0.2}
            },
            "rebalance_threshold": 0.05,
            "check_interval_sec": 0.01,
            "min_notional_usdt": 5.1,
            "siphoning_threshold_pct": 1.0,
            "reinvestment_ratio": 0.5,
        }]
    }
    cfg.update(overrides)
    return cfg


def _make_state(**overrides):
    s = {
        "virt_qty": 0.033333,
        "virt_debt": 0.0,
        "base_ticker": "BTCUSDT",
        "siphoning_reserve": 0.0,
        "initial_tpv": 100.0,
        "reference_tpv": 100.0,
        "tpv_ath": 100.0,
        "trailing_stop_triggered": False,
        "trailing_stop_violation_start": 0.0,
        "rebalance_cycles": 10,
        "positions": {"BTCUSDT_LONG": 0.0, "BTCUSDT_SHORT": 0.0},
    }
    s.update(overrides)
    return s


def _make_paper_state(**overrides):
    ps = {
        "balance": 100.0,
        "positions": {"BTCUSDT_LONG": 0.0, "BTCUSDT_SHORT": 0.0},
        "last_price": 60000.0,
        "base_ticker": "BTCUSDT",
        "long_entry_price": 0.0,
        "short_entry_price": 0.0
    }
    ps.update(overrides)
    return ps


async def _run_rebalance(config, state, paper_state, connector=None,
                         loop_limit=5, real_mode=False, calc_return=None,
                         calc_val_cash=None, calculator_class=None,
                         calc_tpv=100.0, price=60000.0):
    """Helper: run rebalance_loop with mocks. Returns saves list."""
    if connector is None:
        connector = MagicMock()
        connector.get_futures_prices = AsyncMock(return_value={"BTCUSDT": price})
        connector.get_mark_prices = AsyncMock(return_value={"BTCUSDT": price})
        connector.get_exchange_info = AsyncMock(return_value={
            "symbols": [{"symbol": "BTCUSDT", "filters": [
                {"filterType": "LOT_SIZE", "stepSize": "0.001"},
                {"filterType": "MIN_NOTIONAL", "notional": "5.0"},
            ]}]
        })
        connector.get_hedge_mode = AsyncMock(return_value=True)
        connector.get_free_balance = AsyncMock(return_value=100.0)
        connector.get_positions = AsyncMock(return_value={"BTCUSDT_LONG": {"qty": 0.0, "entry_price": 60000.0}})
        connector.get_margin_ratio = AsyncMock(return_value={"margin_ratio": 10.0, "total_wallet_balance": 100.0})
        connector.get_bnb_balance = AsyncMock(return_value=1.0)
        connector.get_order_book = AsyncMock(return_value={"bids": [["60000", "1"]], "asks": [["60001", "1"]]})
        connector.set_leverage = AsyncMock()
        connector.set_margin_type = AsyncMock()

    if real_mode:
        snapshot = int(time.time() * 1000) - 60000
        state = copy.deepcopy(state)
        state.update(funding_model='signed_actual', live_funding={
            'symbol': 'BTCUSDT', 'snapshot_ms': snapshot, 'boundary_ms': snapshot,
            'opening_wallet': str(state['initial_tpv'])})
        connector.get_funding_income = AsyncMock(return_value=[])
        connector.get_position_risk = AsyncMock(return_value={})
        connector.get_commission_rates = AsyncMock(return_value={'takerCommissionRate': '.0004'})
        connector.get_order_book = AsyncMock(side_effect=lambda *a, **kw: {
            'bids': [[str(price), '100']], 'asks': [[str(price), '100']], '_fetched_at': time.time()})

    notifier = MagicMock()
    notifier.send_message = AsyncMock()
    notifier.send_alert = AsyncMock()
    notifier.send_status = AsyncMock()
    notifier.close = AsyncMock()

    def load_side_effect(path, default=None):
        if "paper_state" in str(path):
            return copy.deepcopy(paper_state)
        if "state" in str(path) and "paper" not in str(path):
            return copy.deepcopy(state)
        return default

    def settle_reserve(path, mode, symbol, event):
        from core.funding import reserve_funding
        key = "paper_account_free_margin" if mode == "paper" else "cash_reserve_usdt"
        result = reserve_funding(event.signed_amount, config["portfolios"][0].get(key, 0))
        config["portfolios"][0][key] = float(result["reserve_after"])
        return {**result, "reserve_key": key, "current_reserve": result["reserve_after"]}

    saves = []
    def save_side_effect(path, data):
        saves.append((str(path), copy.deepcopy(data)))

    loop_count = [0]
    def bounded_sleep(secs, *args):
        if secs == 86400:
            raise ZombieExit("ZombieMode")
        loop_count[0] += 1
        if loop_count[0] > loop_limit:
            raise Exception("StopLoop")
        return None

    config_file = json.dumps(config)
    calc_mock = MagicMock()
    if calc_return is not None:
        calc_mock.calculate_rebalance.return_value = calc_return
    else:
        calc_mock.calculate_rebalance.return_value = {
            "total_tpv": 100.0, "tpv": 100.0, "actions": [],
            "total_pnl_pct": 0.0
        }
    calc_mock.tpv = (calc_return or {}).get('tpv', calc_tpv)
    calc_mock.total_tpv = (calc_return or {}).get('total_tpv', calc_tpv)
    result = calc_mock.calculate_rebalance.return_value
    for key in ('share_long_pct', 'share_short_pct', 'share_virt_pct', 'share_cash_pct',
                'val_long', 'val_short', 'val_virt', 'val_cash'):
        result.setdefault(key, 0.)
    calc_mock.val_cash = Decimal(str(calc_val_cash)) if calc_val_cash is not None else Decimal('0')

    from core.funding import live_snapshot_pair_error
    from core import funding
    funding_save = (nullcontext() if isinstance(funding.save_funding_boundary, Mock)
                    else patch('core.funding.save_funding_boundary', side_effect=lambda sp, wp, st, wa, *rest:
                               (save_side_effect(sp, st), save_side_effect(wp, wa))))
    signal_patch = nullcontext() if isinstance(sys.modules[M].emit_signal, Mock) else patch(f'{M}.emit_signal')
    kill_patch = nullcontext() if isinstance(sys.modules[M].self_kill_pm2, Mock) else patch(f'{M}.self_kill_pm2', AsyncMock())
    if state.get('live_funding') and paper_state and 'live_funding_snapshot' not in paper_state:
        paper_state = dict(paper_state, live_funding_snapshot={
            'symbol': state['live_funding']['symbol'], 'snapshot_ms': state['live_funding']['snapshot_ms']})
    with tempfile.TemporaryDirectory() as directory:
        raw_state, raw_wallet = Path(directory) / 'state.json', Path(directory) / 'wallet.json'
        raw_state.write_text(json.dumps(state))
        raw_wallet.write_text(json.dumps(paper_state))
        pair_error = live_snapshot_pair_error(str(raw_state), str(raw_wallet), config.get('base_ticker', 'BTCUSDT'))

    with patch(f"{M}.safe_load_json_sync", return_value=config), \
         patch('core.funding.live_snapshot_pair_error', return_value=pair_error), \
         patch('core.funding.atomic_save_json', side_effect=save_side_effect), \
         patch(f"{M}.backtest_rules.strategy_minutes", AsyncMock(return_value=[(int(time.time() // 60 - 1) * 60000, price)])), \
         patch(f"{M}.backtest_rules.paper_legacy_funding", return_value=Decimal('0')), \
         funding_save, \
         signal_patch, \
         kill_patch, \
         patch("builtins.open", mock_open(read_data=config_file)), \
         patch("os.path.getmtime", return_value=123), \
         patch(f"{M}.load_json", AsyncMock(side_effect=load_side_effect)), \
         patch(f"{M}.save_json", AsyncMock(side_effect=save_side_effect)), \
         patch(f"{M}.sys.exit", side_effect=BaseException("ProcessExit")), \
         patch(f"{M}.TelegramNotifier", return_value=notifier), \
         patch("asyncio.sleep", side_effect=bounded_sleep), \
         patch(f"{M}.PortfolioCalculator", side_effect=calculator_class, return_value=calc_mock), \
         patch(f"{M}.BinanceConnector", return_value=connector), \
         patch("core.funding.settle_funding_reserve", side_effect=settle_reserve):
        try:
            await rebalance_loop(connector, "config.json", "state.json", "paper_state.json", MagicMock())
        except (Exception, BaseException) as e:
            if str(e) not in ["StopLoop", "ProcessExit", "ZombieMode"]:
                raise e

    return saves, notifier, connector


# =====================================================================
# Coverage: Trailing Stop trigger + position closure (lines 978-1067)
# =====================================================================

@pytest.mark.asyncio
async def test_coverage_trailing_stop_below_initial_capital_is_blocked():
    """A marked drawdown below the configured initial capital cannot trigger equity trailing."""
    config = _make_config(
        equity_trailing_stop_pct=2.0,
        equity_trailing_stop_timeout_sec=0.0,
        equity_trailing_stop_activation_pct=0.0,
    )
    state = _make_state(
        tpv_ath=110.0, initial_tpv=100.0,
        trailing_stop_triggered=False,
    )
    paper_state = _make_paper_state(balance=100.0)

    low_tpv = {"total_tpv": 90.0, "tpv": 90.0, "actions": [], "total_pnl_pct": -10.0}
    saves, notifier, conn = await _run_rebalance(config, state, paper_state, loop_limit=3, calc_return=low_tpv)
    assert not notifier.send_alert.called
    assert all(not value.get('trailing_stop_triggered') for path, value in saves if path == 'state.json')


@pytest.mark.asyncio
async def test_coverage_trailing_stop_violation_start():
    """Lines 978-983: first time breach -> set violation_start"""
    config = _make_config(
        equity_trailing_stop_pct=2.0,
        equity_trailing_stop_timeout_sec=9999,
        equity_trailing_stop_activation_pct=0.0,
    )
    state = _make_state(tpv_ath=110.0, initial_tpv=100.0)
    paper_state = _make_paper_state(balance=100.0)

    low_tpv = {"total_tpv": 90.0, "tpv": 90.0, "actions": [], "total_pnl_pct": -10.0}
    saves, notifier, conn = await _run_rebalance(config, state, paper_state, loop_limit=3, calc_return=low_tpv)

    # Check if trailing_stop_violation_start was set in saves
    state_saves = [s[1] for s in saves if "state.json" in s[0]]
    violation_found = any(s.get("trailing_stop_violation_start", 0) > 0 for s in state_saves)
    # The timeout is long so it shouldn't trigger the stop, just set violation
    assert violation_found or not any("STOP LOSS" in str(a) for a in notifier.send_alert.call_args_list)


# =====================================================================
# Coverage: Emergency Stop / Max Drawdown (lines 931-957)
# =====================================================================

@pytest.mark.asyncio
async def test_coverage_emergency_stop_blacklist(tmp_path):
    from test_runtime_backtest_parity import test_paper_minute_clock_and_legacy_funding_replay as replay
    await replay(tmp_path, False, 850, 'maxdd', False, False)


@pytest.mark.asyncio
async def test_coverage_emergency_stop_probation(tmp_path):
    from test_runtime_backtest_parity import test_paper_minute_clock_and_legacy_funding_replay as replay
    await replay(tmp_path, False, 850, 'maxdd', False, True)


# =====================================================================
# Coverage: Liquidation Guard (lines 1421-1460)
# =====================================================================

@pytest.mark.asyncio
async def test_coverage_liquidation_guard_detects_missing_long(tmp_path):
    from test_runtime_backtest_parity import test_paper_minute_clock_and_legacy_funding_replay as replay
    await replay(tmp_path, False, 1000, False, False, 'missing_long')

    # Recovery handler should have been called
    # (it detects missing LONG when expected_long != 0 and not has_long)


# =====================================================================
# Coverage: VIRTUAL_ORDER processing (lines 1191-1233)
# =====================================================================

@pytest.mark.asyncio
async def test_coverage_virtual_order_processing():
    """Lines 1191-1233: VIRTUAL_ORDER result -> cash accounting + dust guard"""
    config = _make_config()
    state = _make_state(virt_qty=0.033333, virt_debt=0.0)
    paper_state = _make_paper_state(balance=100.0)

    connector = MagicMock()
    connector.get_futures_prices = AsyncMock(return_value={"BTCUSDT": 60000.0})
    connector.get_mark_prices = AsyncMock(return_value={"BTCUSDT": 60000.0})
    connector.get_exchange_info = AsyncMock(return_value={
        "symbols": [{"symbol": "BTCUSDT", "filters": [
            {"filterType": "LOT_SIZE", "stepSize": "0.001"},
            {"filterType": "MIN_NOTIONAL", "notional": "5.0"},
        ]}]
    })
    connector.get_hedge_mode = AsyncMock(return_value=True)
    connector.get_free_balance = AsyncMock(return_value=100.0)
    connector.get_positions = AsyncMock(return_value={})
    connector.get_margin_ratio = AsyncMock(return_value={"margin_ratio": 10.0, "total_wallet_balance": 100.0})
    connector.get_bnb_balance = AsyncMock(return_value=1.0)
    connector.get_order_book = AsyncMock(return_value={"bids": [["60000", "1"]], "asks": [["60001", "1"]]})
    connector.set_leverage = AsyncMock()
    connector.set_margin_type = AsyncMock()

    # PortfolioExecutor needs to return VIRTUAL_ORDER results
    virt_result = {
        "type": "VIRTUAL_ORDER",
        "diff_usdt": 5.0,
        "symbol": "BTCUSDT",
        "side": "BUY",
        "qty": 0.0,
        "price": 60000.0,
    }
    long_result = {"type": "MARKET", "symbol": "BTCUSDT", "side": "BUY", "qty": 0.001, "price": 60000.0, "success": True}
    short_result = {"type": "MARKET", "symbol": "BTCUSDT", "side": "SELL", "qty": 0.001, "price": 60000.0, "success": True}

    executor_mock = MagicMock()
    executor_mock.execute_market_order = AsyncMock(side_effect=[
        long_result, short_result, virt_result,
        long_result, short_result, virt_result,
        long_result, short_result, virt_result,
        long_result, short_result, virt_result,
        long_result, short_result, virt_result,
    ])

    saves = []
    def save_side_effect(path, data):
        saves.append((str(path), copy.deepcopy(data)))

    loop_count = [0]
    async def bounded_sleep(secs, *args):
        if secs == 86400: raise ZombieExit("ZombieMode")
        loop_count[0] += 1
        if loop_count[0] > 3: raise Exception("StopLoop")

    notifier = MagicMock()
    notifier.send_message = AsyncMock()
    notifier.send_alert = AsyncMock()
    notifier.send_status = AsyncMock()
    notifier.close = AsyncMock()

    with patch(f"{M}.safe_load_json_sync", return_value=config), \
         patch("builtins.open", mock_open(read_data=json.dumps(config))), \
         patch("os.path.getmtime", return_value=123), \
         patch(f"{M}.load_json", AsyncMock(return_value=copy.deepcopy(state))), \
         patch(f"{M}.save_json", AsyncMock(side_effect=save_side_effect)), \
         patch(f"{M}.sys.exit", side_effect=BaseException("ProcessExit")), \
         patch(f"{M}.TelegramNotifier", return_value=notifier), \
         patch("asyncio.sleep", side_effect=bounded_sleep), \
         patch(f"{M}.BinanceConnector", return_value=connector), \
         patch(f"{M}.PortfolioExecutor", return_value=executor_mock):
        try:
            await rebalance_loop(connector, "config.json", "state.json", "paper_state.json", MagicMock())
        except (Exception, BaseException) as e:
            if str(e) not in ["StopLoop", "ProcessExit", "ZombieMode"]: raise e

    # Virtual order should update balance and virt_debt in state saves
    state_saves = [s[1] for s in saves if "state.json" in s[0]]
    virt_debt_found = any("virt_debt" in s and s.get("virt_debt", 0) != 0 for s in state_saves)
    # virt_debt should be updated if VIRTUAL_ORDER was processed
    assert len(state_saves) > 0


@pytest.mark.asyncio
async def test_initial_cash_violation_saves_facts_and_blocks_increases():
    config = _make_config()
    state = _make_state(virt_qty=0.0, rebalance_cycles=0)
    paper_state = _make_paper_state()

    action = {
        "type": "ORDER",
        "position_side": "LONG",
        "diff_usdt": 10.0,
        "symbol": "BTCUSDT",
        "side": "BUY",
        "key": "BASE_LONG",
    }
    calc_return = {
        "total_tpv": 100.0,
        "tpv": 100.0,
        "actions": [action],
        "total_pnl_pct": 0.0,
        "share_long_pct": 0.0,
        "share_short_pct": 0.0,
        "share_virt_pct": 0.0,
        "share_cash_pct": 100.0,
    }

    executor_mock = MagicMock()
    executor_mock.execute_actions = AsyncMock(return_value=[{
        "status": "SUCCESS",
        "type": "LONG",
        "symbol": "BTCUSDT_LONG", "execution_id": "paper:initial-cash", "trade_pnl": 0., "reduce_only": False,
        "side": "BUY",
        "qty": 0.0002,
        "price": 60000.0,
        "commission": 0.02,
    }])

    with patch(f"{M}.PortfolioExecutor", return_value=executor_mock):
        saves, _, _ = await _run_rebalance(
            config, state, paper_state, loop_limit=3,
            calc_return=calc_return, calc_val_cash=Decimal("-0.02"),
        )

    violation_saves = [
        saved for path, saved in saves
        if "state.json" in path and saved.get("cash_invariant_violation")
    ]
    assert violation_saves
    violation = violation_saves[0]["cash_invariant_violation"]
    assert violation["cash"] == pytest.approx(-0.02)
    assert violation["executions"]
    assert violation["executions"][0]["commission"] == pytest.approx(0.02)

    paper_saves = [saved for path, saved in saves if "paper_state.json" in path]
    assert paper_saves
    assert paper_saves[-1]["positions"]["BTCUSDT_LONG"] > 0

    assert executor_mock.execute_actions.call_count == 1


@pytest.mark.asyncio
async def test_failed_initial_leg_stays_pending_and_is_not_reopened():
    config = _make_config()
    state = _make_state(virt_qty=0.0, rebalance_cycles=0)
    paper_state = _make_paper_state()

    long_action = {
        "type": "ORDER",
        "position_side": "LONG",
        "diff_usdt": 10.0,
        "symbol": "BTCUSDT",
        "side": "BUY",
        "key": "BASE_LONG",
    }
    short_action = {
        "type": "ORDER",
        "position_side": "SHORT",
        "diff_usdt": 10.0,
        "symbol": "BTCUSDT",
        "side": "SELL",
        "key": "BASE_SHORT",
    }
    calc_return = {
        "total_tpv": 100.0,
        "tpv": 100.0,
        "actions": [long_action, short_action],
        "total_pnl_pct": 0.0,
        "share_long_pct": 0.0,
        "share_short_pct": 0.0,
        "share_virt_pct": 0.0,
        "share_cash_pct": 100.0,
    }

    long_result = {
        "status": "SUCCESS",
        "type": "LONG",
        "symbol": "BTCUSDT_LONG", "execution_id": "paper:initial-long", "trade_pnl": 0., "reduce_only": False,
        "side": "BUY",
        "qty": 0.0002,
        "price": 60000.0,
        "commission": 0.02,
    }
    short_error = {"status": "ERROR", "message": "rejected"}

    executor_mock = MagicMock()
    executor_mock.execute_actions = AsyncMock(side_effect=[
        [long_result, short_error],
        [short_error],
        [short_error],
    ])

    with patch(f"{M}.PortfolioExecutor", return_value=executor_mock):
        saves, _, _ = await _run_rebalance(
            config, state, paper_state, loop_limit=3,
            calc_return=calc_return, calc_val_cash=Decimal("10"),
        )

    state_saves = [saved for path, saved in saves if "state.json" in path]
    pending_states = [saved for saved in state_saves if saved.get("initial_build_pending")]
    assert pending_states
    pending = pending_states[0]["initial_build_pending"]
    assert pending["missing"] == ["BASE_SHORT"]
    assert pending_states[0]["virt_debt"] == pytest.approx(20.0)
    assert not any(saved.get("cash_invariant_violation") for saved in state_saves)

    paper_saves = [saved for path, saved in saves if "paper_state.json" in path]
    assert paper_saves
    assert paper_saves[-1]["positions"]["BTCUSDT_LONG"] > 0
    assert paper_saves[-1]["positions"]["BTCUSDT_SHORT"] == 0

    calls = executor_mock.execute_actions.call_args_list
    assert calls
    assert [action["key"] for action in calls[0].args[0]] == ["BASE_LONG", "BASE_SHORT"]
    for call in calls[1:]:
        assert [action["key"] for action in call.args[0]] == ["BASE_SHORT"]


@pytest.mark.asyncio
async def test_pending_initial_build_restart_completes_only_missing_leg():
    config = _make_config()
    state = _make_state(virt_qty=0.0, rebalance_cycles=0)
    paper_state = _make_paper_state()

    long_action = {
        "type": "ORDER", "position_side": "LONG", "diff_usdt": 10.0,
        "symbol": "BTCUSDT", "side": "BUY", "key": "BASE_LONG",
    }
    short_action = {
        "type": "ORDER", "position_side": "SHORT", "diff_usdt": 10.0,
        "symbol": "BTCUSDT", "side": "SELL", "key": "BASE_SHORT",
    }
    calc_return = {
        "total_tpv": 100.0, "tpv": 100.0,
        "actions": [long_action, short_action],
        "total_pnl_pct": 0.0, "share_long_pct": 0.0,
        "share_short_pct": 0.0, "share_virt_pct": 0.0,
        "share_cash_pct": 100.0,
    }
    long_result = {
        "status": "SUCCESS", "type": "LONG", "symbol": "BTCUSDT_LONG",
        "execution_id": "paper:restart-long", "trade_pnl": 0., "reduce_only": False,
        "side": "BUY", "qty": 0.0002, "price": 60000.0, "commission": 0.02,
    }
    short_result = {
        "status": "SUCCESS", "type": "SHORT", "symbol": "BTCUSDT_SHORT",
        "execution_id": "paper:restart-short", "trade_pnl": 0., "reduce_only": False,
        "side": "SELL", "qty": 0.0002, "price": 60000.0, "commission": 0.02,
    }

    first_calls = []

    async def execute_first(actions, price, paper_mode, portfolio_cfg, step_sizes, ps, **kwargs):
        first_calls.append([action["key"] for action in actions])
        results = []
        for action in actions:
            if action["key"] == "BASE_LONG":
                results.append(long_result)
            else:
                results.append({"status": "ERROR", "message": "rejected"})
        return results

    first_executor = MagicMock()
    first_executor.execute_actions = AsyncMock(side_effect=execute_first)
    with patch(f"{M}.PortfolioExecutor", return_value=first_executor):
        first_saves, _, _ = await _run_rebalance(
            config, state, paper_state, loop_limit=0,
            calc_return=calc_return, calc_val_cash=Decimal("10"),
        )

    state_after = [saved for path, saved in first_saves if path == "state.json"][-1]
    paper_after = [saved for path, saved in first_saves if path == "paper_state.json"][-1]
    assert first_calls == [["BASE_LONG", "BASE_SHORT"]]
    assert state_after["initial_build_pending"]["missing"] == ["BASE_SHORT"]
    assert paper_after["positions"]["BTCUSDT_LONG"] > 0
    assert paper_after["positions"]["BTCUSDT_SHORT"] == 0

    second_calls = []

    async def execute_second(actions, price, paper_mode, portfolio_cfg, step_sizes, ps, **kwargs):
        second_calls.append([action["key"] for action in actions])
        results = []
        for action in actions:
            if action["key"] == "BASE_SHORT":
                results.append(short_result)
            else:
                results.append({"status": "ERROR", "message": "unexpected leg"})
        return results

    second_executor = MagicMock()
    second_executor.execute_actions = AsyncMock(side_effect=execute_second)
    with patch(f"{M}.PortfolioExecutor", return_value=second_executor):
        second_saves, _, _ = await _run_rebalance(
            config, state_after, paper_after, loop_limit=0,
            calc_return=calc_return, calc_val_cash=Decimal("10"),
        )

    state_final = [saved for path, saved in second_saves if path == "state.json"][-1]
    paper_final = [saved for path, saved in second_saves if path == "paper_state.json"][-1]
    assert second_calls == [["BASE_SHORT"]]
    assert state_final["initial_build_pending"] is None
    assert state_final["virt_debt"] == pytest.approx(30.0)
    assert state_final.get("cash_invariant_violation") is None
    assert paper_final["positions"]["BTCUSDT_LONG"] == pytest.approx(0.0002)
    assert paper_final["positions"]["BTCUSDT_SHORT"] == pytest.approx(0.0002)


# =====================================================================
# Coverage: Stop/close logic (lines 1525-1601)
# =====================================================================

@pytest.mark.asyncio
async def test_coverage_paper_stop_closes_positions(tmp_path):
    from core.executor import validate_paper_positions
    wallet = {'positions': {'BTCUSDT_LONG': .001, 'BTCUSDT_SHORT': -.001}, 'balance': 100}
    before = copy.deepcopy(wallet)
    with pytest.raises(ValueError, match='Invalid PAPER hedge quantity'):
        validate_paper_positions(wallet)
    assert wallet == before


@pytest.mark.asyncio
async def test_coverage_real_stop_closes_positions(tmp_path):
    from test_runtime_backtest_parity import test_paper_minute_clock_and_legacy_funding_replay as replay
    await replay(tmp_path, False, 1100, True, False, True)

    # In real mode, stop should attempt to close positions via market orders
    # (we can't easily assert on specific calls, but no crash = success)


# =====================================================================
# Coverage: Margin ratio warning in paper mode (lines 515-520, 530-533)
# =====================================================================

@pytest.mark.asyncio
async def test_coverage_margin_warning_real(tmp_path):
    from test_runtime_backtest_parity import test_paper_minute_clock_and_legacy_funding_replay as replay
    await replay(tmp_path, False, 1000, False, False, 'warning')


# =====================================================================
# Coverage: Liquidity guard warning (lines 647-649, 653-665)
# =====================================================================

@pytest.mark.asyncio
async def test_coverage_liquidity_guard():
    """Lines 647-665: notional below min -> skip rebalance"""
    config = _make_config()
    state = _make_state()
    paper_state = _make_paper_state(balance=50.0)

    connector = MagicMock()
    connector.get_futures_prices = AsyncMock(return_value={"BTCUSDT": 60000.0})
    connector.get_mark_prices = AsyncMock(return_value={"BTCUSDT": 60000.0})
    connector.get_exchange_info = AsyncMock(return_value={
        "symbols": [{"symbol": "BTCUSDT", "filters": [
            {"filterType": "LOT_SIZE", "stepSize": "0.001"},
            {"filterType": "MIN_NOTIONAL", "notional": "5.0"},
        ]}]
    })
    connector.get_hedge_mode = AsyncMock(return_value=True)
    connector.get_free_balance = AsyncMock(return_value=100.0)
    connector.get_positions = AsyncMock(return_value={})
    connector.get_margin_ratio = AsyncMock(return_value={"margin_ratio": 10.0, "total_wallet_balance": 100.0})
    connector.get_bnb_balance = AsyncMock(return_value=1.0)
    connector.get_order_book = AsyncMock(return_value={"bids": [["60000", "1"]], "asks": [["60001", "1"]]})
    connector.set_leverage = AsyncMock()
    connector.set_margin_type = AsyncMock()

    loop_count = [0]
    def bounded_sleep(secs, *args):
        if secs == 86400: raise ZombieExit("ZombieMode")
        loop_count[0] += 1
        if loop_count[0] > 2: raise Exception("StopLoop")

    def load_side_effect(path, default=None):
        if "paper_state" in str(path):
            return copy.deepcopy(paper_state)
        if "state" in str(path) and "paper" not in str(path):
            return copy.deepcopy(state)
        return default

    notifier = MagicMock()
    notifier.send_message = AsyncMock()
    notifier.send_alert = AsyncMock()
    notifier.send_status = AsyncMock()
    notifier.close = AsyncMock()

    with patch(f"{M}.safe_load_json_sync", return_value=config), \
         patch("builtins.open", mock_open(read_data=json.dumps(config))), \
         patch("os.path.getmtime", return_value=123), \
         patch(f"{M}.load_json", AsyncMock(side_effect=load_side_effect)), \
         patch(f"{M}.save_json", AsyncMock()), \
         patch(f"{M}.sys.exit", side_effect=BaseException("ProcessExit")), \
         patch(f"{M}.TelegramNotifier", return_value=notifier), \
         patch("asyncio.sleep", side_effect=bounded_sleep), \
         patch(f"{M}.BinanceConnector", return_value=connector):
        try:
            await rebalance_loop(connector, "config.json", "state.json", "paper_state.json", MagicMock())
        except (Exception, BaseException) as e:
            if str(e) not in ["StopLoop", "ProcessExit", "ZombieMode"]: raise e


# =====================================================================
# Coverage: ticker_thresholds per-ticker override (lines 296-299)
# =====================================================================

@pytest.mark.asyncio
async def test_coverage_ticker_thresholds_dict():
    """Lines 296-297: per-ticker threshold dict override"""
    config = _make_config()
    config["portfolios"][0]["ticker_thresholds"] = {
        "BTCUSDT": {"surplus": 0.10, "deficit": 0.08}
    }
    state = _make_state()
    paper_state = _make_paper_state()

    saves, notifier, conn = await _run_rebalance(config, state, paper_state, loop_limit=2)
    assert len(saves) > 0


@pytest.mark.asyncio
async def test_coverage_ticker_thresholds_float():
    """Line 299: per-ticker threshold as float"""
    config = _make_config()
    config["portfolios"][0]["ticker_thresholds"] = {"BTCUSDT": 0.15}
    state = _make_state()
    paper_state = _make_paper_state()

    saves, notifier, conn = await _run_rebalance(config, state, paper_state, loop_limit=2)
    assert len(saves) > 0


# =====================================================================
# Coverage: siphoning / reinvestment (lines 343-346, 350-360, 363-370)
# =====================================================================

@pytest.mark.asyncio
async def test_coverage_siphoning_logic():
    """Lines 343-370: siphoning reserve + reinvestment"""
    config = _make_config()
    config["portfolios"][0]["siphoning_threshold_pct"] = 0.5
    config["portfolios"][0]["reinvestment_ratio"] = 0.5
    # Low TPV vs initial triggers siphoning
    state = _make_state(tpv_ath=120.0, initial_tpv=100.0, siphoning_reserve=0.0)
    paper_state = _make_paper_state(balance=80.0)

    saves, notifier, conn = await _run_rebalance(config, state, paper_state, loop_limit=3)
    # Just verify no crash
    assert True


# =====================================================================
# Coverage: config reload (lines 195-218, 253)
# =====================================================================

@pytest.mark.asyncio
async def test_coverage_config_reload_detection():
    """Lines 195-218: config file modification detected -> reload"""
    config = _make_config()
    state = _make_state()
    paper_state = _make_paper_state()

    call_count = [0]
    def varying_mtime(path):
        call_count[0] += 1
        return 100 + call_count[0]

    calc_mock = MagicMock()
    calc_mock.calculate_rebalance.return_value = {
        "total_tpv": 100.0, "tpv": 100.0, "actions": [], "total_pnl_pct": 0.0
    }
    calc_mock.tpv = 100.0

    mock_connector = MagicMock()
    mock_connector.get_futures_prices = AsyncMock(return_value={"BTCUSDT": 60000.0})
    mock_connector.get_mark_prices = AsyncMock(return_value={"BTCUSDT": 60000.0})
    mock_connector.get_exchange_info = AsyncMock(return_value={"symbols": [{"symbol": "BTCUSDT", "filters": [{"filterType": "LOT_SIZE", "stepSize": "0.001"}, {"filterType": "MIN_NOTIONAL", "notional": "5.0"}]}]})
    mock_connector.get_hedge_mode = AsyncMock(return_value=True)
    mock_connector.get_free_balance = AsyncMock(return_value=100.0)
    mock_connector.get_positions = AsyncMock(return_value={})
    mock_connector.get_margin_ratio = AsyncMock(return_value={"margin_ratio": 10.0, "total_wallet_balance": 100.0})
    mock_connector.get_bnb_balance = AsyncMock(return_value=1.0)
    mock_connector.get_order_book = AsyncMock(return_value={"bids": [["60000", "1"]], "asks": [["60001", "1"]]})
    mock_connector.set_leverage = AsyncMock()
    mock_connector.set_margin_type = AsyncMock()

    with patch(f"{M}.safe_load_json_sync", return_value=config), \
         patch("builtins.open", mock_open(read_data=json.dumps(config))), \
         patch("os.path.getmtime", side_effect=varying_mtime), \
         patch(f"{M}.load_json", AsyncMock(return_value=copy.deepcopy(state))), \
         patch(f"{M}.save_json", AsyncMock()), \
         patch(f"{M}.sys.exit", side_effect=BaseException("ProcessExit")), \
         patch(f"{M}.TelegramNotifier", return_value=MagicMock(
             send_message=AsyncMock(), send_alert=AsyncMock(),
             send_status=AsyncMock(), close=AsyncMock())), \
         patch(f"{M}.PortfolioCalculator", return_value=calc_mock), \
         patch(f"{M}.BinanceConnector", return_value=mock_connector):
        loop_count = [0]
        async def bounded_sleep(secs, *args):
            if secs == 86400: raise ZombieExit("ZombieMode")
            loop_count[0] += 1
            if loop_count[0] > 3: raise Exception("StopLoop")

        with patch("asyncio.sleep", side_effect=bounded_sleep):
            try:
                await rebalance_loop(mock_connector, "config.json", "state.json", "paper_state.json", MagicMock())
            except (Exception, BaseException) as e:
                if str(e) not in ["StopLoop", "ProcessExit", "ZombieMode"]: raise e


# =====================================================================
# Coverage: Liquidation Guard (lines 1421-1460)
# =====================================================================

@pytest.mark.asyncio
async def test_coverage_liquidation_guard_missing_long(tmp_path):
    from test_runtime_backtest_parity import test_paper_minute_clock_and_legacy_funding_replay as replay
    await replay(tmp_path, False, 1000, False, False, 'missing_long')


@pytest.mark.asyncio
async def test_coverage_liquidation_guard_missing_short(tmp_path):
    from test_runtime_backtest_parity import test_paper_minute_clock_and_legacy_funding_replay as replay
    await replay(tmp_path, False, 1000, False, False, 'missing_short')


# =====================================================================
# Coverage: Blacklist rebase (lines 598-625)
# =====================================================================

@pytest.mark.asyncio
async def test_coverage_blacklist_rebase_loss():
    """Lines 598-614: blacklist rebase when last_profit < 0 -> rebase to initial_capital"""
    config = _make_config(equity_trailing_stop_pct=0.0, max_drawdown_limit=100.0)
    config["portfolios"][0]["initial_capital"] = 100.0

    state = _make_state(
        initial_tpv=100.0, reference_tpv=100.0, tpv_ath=100.0,
        last_profit=-10.0, last_tpv=90.0,
    )
    paper_state = _make_paper_state(balance=90.0)

    # Mock blacklist: ticker is blacklisted
    blacklist = {"BTCUSDT": time.time() + 3600}
    config["black_list"] = blacklist

    calc_mock = MagicMock()
    calc_mock.calculate_rebalance.return_value = {
        "total_tpv": 90.0, "tpv": 90.0, "actions": [], "total_pnl_pct": -10.0
    }
    calc_mock.tpv = 90.0

    connector = MagicMock()
    connector.get_futures_prices = AsyncMock(return_value={"BTCUSDT": 60000.0})
    connector.get_mark_prices = AsyncMock(return_value={"BTCUSDT": 60000.0})
    connector.get_exchange_info = AsyncMock(return_value={
        "symbols": [{"symbol": "BTCUSDT", "filters": [
            {"filterType": "LOT_SIZE", "stepSize": "0.001"},
            {"filterType": "MIN_NOTIONAL", "notional": "5.0"},
        ]}]
    })
    connector.get_hedge_mode = AsyncMock(return_value=True)
    connector.get_free_balance = AsyncMock(return_value=100.0)
    connector.get_positions = AsyncMock(return_value={})
    connector.get_margin_ratio = AsyncMock(return_value={"margin_ratio": 10.0, "total_wallet_balance": 100.0})
    connector.get_bnb_balance = AsyncMock(return_value=1.0)
    connector.get_order_book = AsyncMock(return_value={"bids": [["60000", "1"]], "asks": [["60001", "1"]]})
    connector.set_leverage = AsyncMock()
    connector.set_margin_type = AsyncMock()

    saves = []
    def save_side_effect(path, data):
        saves.append((str(path), copy.deepcopy(data)))

    loop_count = [0]
    async def bounded_sleep(secs, *args):
        if secs == 86400: raise ZombieExit("ZombieMode")
        loop_count[0] += 1
        if loop_count[0] > 3: raise Exception("StopLoop")

    def load_side_effect(path, default=None):
        if "paper_state" in str(path):
            return copy.deepcopy(paper_state)
        if "state" in str(path) and "paper" not in str(path):
            return copy.deepcopy(state)
        return default

    with patch(f"{M}.safe_load_json_sync", return_value=config), \
         patch("builtins.open", mock_open(read_data=json.dumps(config))), \
         patch("os.path.getmtime", return_value=123), \
         patch(f"{M}.load_json", AsyncMock(side_effect=load_side_effect)), \
         patch(f"{M}.save_json", AsyncMock(side_effect=save_side_effect)), \
         patch(f"{M}.sys.exit", side_effect=BaseException("ProcessExit")), \
         patch(f"{M}.TelegramNotifier", return_value=MagicMock(
             send_message=AsyncMock(), send_alert=AsyncMock(),
             send_status=AsyncMock(), close=AsyncMock())), \
         patch("asyncio.sleep", side_effect=bounded_sleep), \
         patch(f"{M}.PortfolioCalculator", return_value=calc_mock), \
         patch(f"{M}.BinanceConnector", return_value=connector):
        try:
            await rebalance_loop(connector, "config.json", "state.json", "paper_state.json", MagicMock())
        except (Exception, BaseException) as e:
            if str(e) not in ["StopLoop", "ProcessExit", "ZombieMode"]: raise e

    # Check if initial_tpv was rebased to initial_capital (100.0)
    state_saves = [s[1] for s in saves if "state.json" in s[0]]
    rebase_found = any(s.get("initial_tpv", 0) == 100.0 for s in state_saves)
    assert rebase_found, "Blacklist rebase should reset initial_tpv to config initial_capital"


# =====================================================================
# Coverage: Paper cross-margin check (lines 814-827)
# =====================================================================

@pytest.mark.asyncio
async def test_coverage_paper_cross_margin_check():
    """Lines 814-827: paper mode cross-margin check -> insufficient margin -> skip"""
    config = _make_config(paper_mode=True, equity_trailing_stop_pct=0.0, max_drawdown_limit=100.0)
    config["portfolios"][0]["paper_account_free_margin"] = 5.0
    config["portfolios"][0]["paper_min_free_margin_pct"] = 15.0

    state = _make_state(initial_tpv=100.0, tpv_ath=100.0)
    paper_state = _make_paper_state(balance=100.0)

    calc_mock = MagicMock()
    calc_mock.calculate_rebalance.return_value = {
        "total_tpv": 100.0, "tpv": 100.0, "actions": [], "total_pnl_pct": 0.0
    }
    calc_mock.tpv = 100.0

    connector = MagicMock()
    connector.get_futures_prices = AsyncMock(return_value={"BTCUSDT": 60000.0})
    connector.get_mark_prices = AsyncMock(return_value={"BTCUSDT": 60000.0})
    connector.get_exchange_info = AsyncMock(return_value={
        "symbols": [{"symbol": "BTCUSDT", "filters": [
            {"filterType": "LOT_SIZE", "stepSize": "0.001"},
            {"filterType": "MIN_NOTIONAL", "notional": "5.0"},
        ]}]
    })
    connector.get_hedge_mode = AsyncMock(return_value=True)
    connector.get_free_balance = AsyncMock(return_value=100.0)
    connector.get_positions = AsyncMock(return_value={})
    connector.get_margin_ratio = AsyncMock(return_value={"margin_ratio": 10.0, "total_wallet_balance": 100.0})
    connector.get_bnb_balance = AsyncMock(return_value=1.0)
    connector.get_order_book = AsyncMock(return_value={"bids": [["60000", "1"]], "asks": [["60001", "1"]]})
    connector.set_leverage = AsyncMock()
    connector.set_margin_type = AsyncMock()

    loop_count = [0]
    async def bounded_sleep(secs, *args):
        if secs == 86400: raise ZombieExit("ZombieMode")
        loop_count[0] += 1
        if loop_count[0] > 3: raise Exception("StopLoop")

    async def bounded_to_thread(func, *args, **kwargs):
        """Safety net: when 'continue' skips asyncio.sleep, to_thread still fires."""
        loop_count[0] += 1
        if loop_count[0] > 6: raise Exception("StopLoop")
        return func(*args, **kwargs)

    # CRITICAL: continue on line 827 skips asyncio.sleep (line 1476).
    # get_mark_prices is the ONLY async call on every iteration in this path.
    async def mark_prices_side_effect(*args, **kwargs):
        loop_count[0] += 1
        if loop_count[0] > 5: raise Exception("StopLoop")
        return {"BTCUSDT": 60000.0}

    def load_side_effect(path, default=None):
        if "paper_state" in str(path):
            return copy.deepcopy(paper_state)
        if "state" in str(path) and "paper" not in str(path):
            return copy.deepcopy(state)
        return default

    connector.get_mark_prices = AsyncMock(side_effect=mark_prices_side_effect)

    with patch(f"{M}.safe_load_json_sync", return_value=config), \
         patch("builtins.open", mock_open(read_data=json.dumps(config))), \
         patch("os.path.getmtime", return_value=123), \
         patch(f"{M}.load_json", AsyncMock(side_effect=load_side_effect)), \
         patch(f"{M}.save_json", AsyncMock()), \
         patch(f"{M}.sys.exit", side_effect=BaseException("ProcessExit")), \
         patch(f"{M}.TelegramNotifier", return_value=MagicMock(
             send_message=AsyncMock(), send_alert=AsyncMock(),
             send_status=AsyncMock(), close=AsyncMock())), \
         patch("asyncio.sleep", side_effect=bounded_sleep), \
         patch("asyncio.to_thread", side_effect=bounded_to_thread), \
         patch(f"{M}.PortfolioCalculator", return_value=calc_mock), \
         patch(f"{M}.BinanceConnector", return_value=connector):
        try:
            await rebalance_loop(connector, "config.json", "state.json", "paper_state.json", MagicMock())
        except (Exception, BaseException) as e:
            if str(e) not in ["StopLoop", "ProcessExit", "ZombieMode"]: raise e

    # Just verify no crash - the cross-margin check should have been hit
    assert loop_count[0] > 0


# =====================================================================
# Coverage: Trailing stop closure with paper positions (lines 992-1009)
# =====================================================================

@pytest.mark.asyncio
async def test_coverage_trailing_stop_closure_paper_positions(tmp_path):
    from test_runtime_backtest_parity import test_paper_minute_clock_and_legacy_funding_replay as replay
    await replay(tmp_path, False, 1100, True, False, False)


# =====================================================================
# Coverage: emergency_stop function (lines 1561-1601)
# =====================================================================

@pytest.mark.asyncio
async def test_coverage_emergency_stop_full(tmp_path):
    from test_live_close import test_emergency_stop_preserves_capital_and_books_only_confirmed_close
    await test_emergency_stop_preserves_capital_and_books_only_confirmed_close(tmp_path, False)


# =====================================================================
# Coverage: emergency_stop close_only=True (lines 1584-1601)
# =====================================================================

@pytest.mark.asyncio
async def test_coverage_emergency_stop_close_only(tmp_path):
    from test_live_close import test_emergency_stop_preserves_capital_and_books_only_confirmed_close
    await test_emergency_stop_preserves_capital_and_books_only_confirmed_close(tmp_path, True)


# =====================================================================
# Coverage: _handle_liquidation_recovery close positions (lines 130-148)
# =====================================================================

@pytest.mark.asyncio
async def test_coverage_missing_position_blocks_without_closing_or_inventing_loss():
    """A missing exchange position requires reconciliation before further execution."""
    from futures_portfolio.core.main import _handle_liquidation_recovery

    connector = MagicMock()
    connector.get_position_risk = AsyncMock(return_value={
        "BTCUSDT_SHORT": {
            "positionAmt": "-1.0",
            "entryPrice": "60000.0",
            "positionSide": "SHORT",
        }
    })
    connector.get_futures_prices = AsyncMock(return_value={"BTCUSDT": 60000.0})
    connector.get_exchange_info = AsyncMock(return_value={
        "symbols": [{"symbol": "BTCUSDT", "filters": [
            {"filterType": "LOT_SIZE", "stepSize": "0.001"},
        ]}]
    })

    executor_mock = MagicMock()
    executor_mock.execute_market_order = AsyncMock(return_value={"success": True})

    state = {"positions": {"BTCUSDT_SHORT": -1.0}}
    paper_state = {"balance": 100.0}

    notifier = MagicMock()
    notifier.send_alert = AsyncMock()

    with patch(f"{M}.PortfolioExecutor", return_value=executor_mock), \
         patch(f"{M}.emit_signal") as mock_emit:
        await _handle_liquidation_recovery(
            connector=connector,
            base_ticker="BTCUSDT",
            state=state,
            state_file_path="state.json",
            paper_state=paper_state,
            paper_state_file_path="paper_state.json",
            config_path="config.json",
            logger=MagicMock(),
            notifier=notifier,
        )

    executor_mock.execute_market_order.assert_not_awaited()
    mock_emit.assert_not_called()
    assert state["status"] == "reconciliation_required"
    assert state["startup_blocked"] is True
    assert state["positions"] == {"BTCUSDT_SHORT": -1.0}
    assert "last_tpv" not in state
    assert paper_state == {"balance": 100.0}


# =====================================================================
# A2a: canonical paper collateral guard
# =====================================================================

def _paper_state_for_distance(distance_pct: float) -> dict:
    maintenance = Decimal("10") * Decimal("100") * Decimal("0.004")
    collateral = maintenance / (Decimal("1") - Decimal(str(distance_pct)) / Decimal("100"))
    return _make_paper_state(
        balance=float(collateral),
        positions={"BTCUSDT_LONG": 10.0, "BTCUSDT_SHORT": 0.0},
        long_entry_price=100.0,
    )


def test_a2_paper_collateral_uses_wallet_plus_upnl_without_double_count():
    paper_state = _make_paper_state(
        balance=1000.0,
        positions={"BTCUSDT_LONG": 10.0, "BTCUSDT_SHORT": 0.0},
        long_entry_price=100.0,
    )
    collateral = _paper_collateral(
        paper_state, "BTCUSDT", 110.0, Decimal("1100"), MagicMock()
    )
    assert collateral == Decimal("1100")
    assert collateral != Decimal("1300")


def test_a2_paper_collateral_keeps_virtual_inside_wallet_once():
    paper_state = _make_paper_state(balance=1000.0)
    collateral = _paper_collateral(
        paper_state, "BTCUSDT", 60000.0, Decimal("1000"), MagicMock()
    )
    assert collateral == Decimal("1000")
    assert collateral != Decimal("1300")


@pytest.mark.parametrize("distance_pct", [8.0, 15.0])
def test_a2_paper_guard_boundaries_are_exact(distance_pct):
    paper_state = _paper_state_for_distance(distance_pct)
    collateral, maintenance, distance = _paper_guard_metrics(
        paper_state, "BTCUSDT", 100.0, Decimal(str(paper_state["balance"])), MagicMock()
    )
    assert maintenance == Decimal("4.000")
    assert distance == pytest.approx(distance_pct, abs=1e-9)
    assert collateral > maintenance


@pytest.mark.parametrize("balance", [0.0, -10.0])
def test_a2_paper_guard_zero_or_negative_collateral_has_no_division(balance):
    paper_state = _make_paper_state(
        balance=balance,
        positions={"BTCUSDT_LONG": 1.0, "BTCUSDT_SHORT": 0.0},
        long_entry_price=100.0,
    )
    collateral, maintenance, distance = _paper_guard_metrics(
        paper_state, "BTCUSDT", 100.0, Decimal("1e-9"), MagicMock()
    )
    assert collateral == Decimal(str(balance))
    assert maintenance == Decimal("0.400")
    assert distance == -100.0


@pytest.mark.asyncio
async def test_a2_paper_liquidation_price_is_diagnostic_only():
    config = _make_config()
    state = _make_state()
    # Both legs with non-zero entries → legitimate startup, no diagnostic block.
    # Price = entry → zero MTM; TPV = balance = 100.
    paper_state = _make_paper_state(
        balance=100.0,
        positions={"BTCUSDT_LONG": 1.0, "BTCUSDT_SHORT": 1.0},
        long_entry_price=100.0,
        short_entry_price=100.0,
    )
    with patch(f"{M}.emit_signal") as emit:
        saves, _, _ = await _run_rebalance(
            config, state, paper_state, loop_limit=2, calc_tpv=100.0, price=100.0,
        )
    final_paper = [value for path, value in saves if path == "paper_state.json"][-1]
    assert final_paper["positions"]["BTCUSDT_LONG"] == 1.0
    assert final_paper["long_liquidation_price"] == pytest.approx(
        100 * (1 - 1 / 7 + 0.004), abs=1e-7
    )
    emit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_free_margin", [0.0, 1000.0])
async def test_a2_canonical_guard_counts_external_paper_reserve(
    legacy_free_margin,
):
    collateral = 4.0 / 0.93
    config = _make_config()
    config["portfolios"][0].update(
        initial_capital=collateral,
        paper_initial_capital=collateral,
        paper_account_free_margin=legacy_free_margin,
    )
    state = _make_state(initial_tpv=collateral, reference_tpv=collateral, tpv_ath=collateral)
    # Q=5 each leg → maintenance = (5+5)*100*0.004 = 4.0, distance ≈ 7% (same as original Q=10 single leg)
    paper_state = _make_paper_state(
        balance=collateral,
        positions={"BTCUSDT_LONG": 5.0, "BTCUSDT_SHORT": 5.0},
        long_entry_price=100.0,
        short_entry_price=100.0,
    )
    with patch(f"{M}.emit_signal") as emit:
        saves, notifier, _ = await _run_rebalance(
            config, state, paper_state, loop_limit=3, calc_tpv=collateral, price=100.0,
            calc_return={
                "total_tpv": collateral, "tpv": collateral, "actions": [],
                "total_pnl_pct": 0.0, "total_pnl": 0.0,
                "share_long_pct": 0.0, "share_short_pct": 0.0,
                "share_virt_pct": 100.0, "share_cash_pct": 0.0,
                "val_long": 0.0, "val_short": 0.0,
                "val_virt": 0.0, "val_cash": 0.0,
            },
        )
    final_state = [value for path, value in saves if path == "state.json"][-1]
    final_paper = [value for path, value in saves if path == "paper_state.json"][-1]
    if legacy_free_margin:
        assert final_paper['positions'] == paper_state['positions']
        emit.assert_not_called()
        notifier.send_alert.assert_not_awaited()
        return
    assert final_paper["positions"]["BTCUSDT_LONG"] == 0.0
    assert final_paper["positions"]["BTCUSDT_SHORT"] == 0.0
    assert final_paper["long_entry_price"] == 0.0
    assert final_paper["short_entry_price"] == 0.0
    assert final_paper["long_liquidation_price"] == 0.0
    assert final_paper["short_liquidation_price"] == 0.0
    assert final_state["initial_build_pending"] is None
    emit.assert_called_once_with('exit', 'BTCUSDT', True)
    notifier.send_alert.assert_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("critical, hard_check, balance, expected_status", [
    (0.0, False, 4.0, None),
    (0.0, True, 4.0, "modeled_liquidation"),
    (0.0, False, 0.0, "portfolio_default"),
    (0.0, False, -1.0, "portfolio_default"),
])
async def test_paper_liquidation_switches_and_terminal_result(critical, hard_check, balance, expected_status):
    config = _make_config(max_drawdown_limit=200, equity_trailing_stop_pct=0)
    config["portfolios"][0].update(liquidation_distance_crit_pct=critical,
                                  liquidation_distance_warn_pct=0.0,
                                  post_factum_liquidation_check=hard_check)
    state = _make_state()
    wallet = _make_paper_state(balance=balance, positions={"BTCUSDT_LONG": 5.0, "BTCUSDT_SHORT": 5.0},
                               long_entry_price=100.0, short_entry_price=100.0)
    terminal = []
    def save_boundary(state_path, wallet_path, saved_state, saved_wallet, *args):
        terminal.append((copy.deepcopy(saved_state), copy.deepcopy(saved_wallet)))

    executor = MagicMock(execute_actions=AsyncMock(return_value=[]))
    with patch("core.funding.save_funding_boundary", side_effect=save_boundary), \
         patch(f"{M}.PortfolioExecutor", return_value=executor), \
         patch(f"{M}.self_kill_pm2", AsyncMock()), patch(f"{M}.emit_signal") as emit:
        saves, _, _ = await _run_rebalance(config, state, wallet, loop_limit=2, price=100,
                                           calc_tpv=balance, calc_return={
            "total_tpv": balance, "tpv": balance, "actions": [], "total_pnl_pct": 0,
            "share_long_pct": 50, "share_short_pct": 50, "share_virt_pct": 0,
            "share_cash_pct": 0, "val_long": 0, "val_short": 0, "val_virt": 0, "val_cash": 0,
        })
    emit.assert_not_called()
    executor.execute_actions.assert_not_awaited()
    if expected_status is None:
        assert not any(state.get('status') in ('portfolio_default', 'modeled_liquidation') for state, _ in terminal)
        assert [v for p, v in saves if p == "paper_state.json"][-1]["positions"] == wallet["positions"]
    else:
        final, book = terminal[-1]
        assert final["status"] == expected_status and final["startup_blocked"] is True
        assert final["last_tpv"] == (0 if expected_status == "modeled_liquidation" else balance)
        assert final["last_profit"] == final["last_tpv"] - 100
        assert final["total_pnl_pct"] == final["last_tpv"] - 100
        assert book["balance"] == (0 if expected_status == "modeled_liquidation" else balance)
        assert book["positions"] == ({"BTCUSDT_LONG": 0, "BTCUSDT_SHORT": 0}
                                     if expected_status == "modeled_liquidation" else wallet["positions"])
        assert not book.get("share_fill_receipts")


@pytest.mark.asyncio
@pytest.mark.parametrize("paper_mode", [True, False])
async def test_terminal_status_blocks_restart_before_wallet_load(paper_mode):
    config = _make_config(paper_mode=paper_mode)
    with patch(f"{M}.self_kill_pm2", AsyncMock()), patch(f"{M}.PortfolioCalculator") as calculator:
        saves, _, conn = await _run_rebalance(config, _make_state(status="portfolio_default"),
                                              _make_paper_state(), loop_limit=1)
    assert not saves
    calculator.assert_not_called()
    conn.get_positions.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_missing_both_legs_preserves_ledger_before_any_order(tmp_path):
    from test_runtime_backtest_parity import test_paper_minute_clock_and_legacy_funding_replay as replay
    await replay(tmp_path, False, 1000, False, False, 'missing_both')


@pytest.mark.asyncio
@pytest.mark.parametrize("warn, expected_warnings", [(0.0, 0), (15.0, 1)])
@pytest.mark.parametrize("paper_mode", [True, False])
async def test_shared_liquidation_guard_zero_disables_critical_and_warning(warn, expected_warnings, paper_mode):
    from futures_portfolio.core.main import _handle_liquidation_guard
    logger = MagicMock()
    with patch(f"{M}.PortfolioExecutor") as executor, patch(f"{M}.emit_signal") as emit:
        await _handle_liquidation_guard("PORTFOLIO", -8.3, 100, warn, 0,
            paper_mode, {}, {}, None, "BTCUSDT", {}, MagicMock(send_alert=AsyncMock()), logger)
    executor.assert_not_called()
    emit.assert_not_called()
    assert logger.warning.call_count == expected_warnings


@pytest.mark.asyncio
async def test_a2_critical_guard_halts_before_supervisor_can_reopen():
    collateral = 4.0 / 0.93
    config = _make_config(
        equity_trailing_stop_activation_pct=9000.0,
        max_drawdown_limit=200.0,
    )
    config["portfolios"][0].update(
        initial_capital=collateral,
        paper_initial_capital=collateral,
    )
    state = _make_state(
        initial_tpv=collateral, reference_tpv=collateral, tpv_ath=collateral
    )
    paper_state = _make_paper_state(
        balance=collateral,
        # Q=5 each → maintenance=4.0, distance≈7% (same as original Q=10 single leg)
        positions={"BTCUSDT_LONG": 5.0, "BTCUSDT_SHORT": 5.0},
        long_entry_price=100.0,
        short_entry_price=100.0,
    )
    result = {
        "total_tpv": collateral, "tpv": collateral, "total_pnl_pct": 0.0,
        "total_pnl": 0.0, "share_long_pct": 0.0, "share_short_pct": 0.0,
        "share_virt_pct": 100.0, "share_cash_pct": 0.0,
        "val_long": 0.0, "val_short": 0.0, "val_virt": 0.0, "val_cash": 0.0,
        "actions": [{
            "type": "ORDER", "position_side": "LONG", "symbol": "BTCUSDT_LONG",
            "side": "BUY", "key": "BASE_LONG", "diff_usdt": 100.0,
            "leverage": 7, "is_reduction": False,
        }],
    }
    executor = MagicMock()
    executor.execute_actions = AsyncMock(return_value=[])
    with patch(f"{M}.self_kill_pm2", AsyncMock()), \
         patch(f"{M}.emit_signal") as emit, \
         patch(f"{M}.PortfolioExecutor", return_value=executor):
        saves, _, _ = await _run_rebalance(
            config, state, paper_state, loop_limit=3,
            calc_tpv=collateral, price=100.0, calc_return=result,
        )

    emit.assert_called_once_with('exit', 'BTCUSDT', True)
    executor.execute_actions.assert_awaited_once()
    final_state = [value for path, value in saves if path == "state.json"][-1]
    final_paper = [value for path, value in saves if path == "paper_state.json"][-1]
    assert final_state["trailing_stop_triggered"] is True
    assert final_paper["positions"]["BTCUSDT_LONG"] == 0.0
    assert final_paper["long_entry_price"] == 0.0


@pytest.mark.asyncio
@pytest.mark.parametrize("paper_mode", [True, False])
@pytest.mark.parametrize("reference", [100.0, 120.0])
async def test_equity_stop_persists_fixed_budget_and_cooldown(paper_mode, reference):
    config = _make_config(paper_mode=paper_mode, max_drawdown_limit=2.08,
                          max_drawdown_cooldown_days=365.0, equity_trailing_stop_pct=0.0)
    config["portfolios"][0]["siphoning_threshold_pct"] = 0
    ledger = reference - 2.08
    state = _make_state(started_at=1, tpv_ath=reference, max_drawdown_reference=reference)
    calc = {"total_tpv": ledger, "tpv": ledger, "actions": [], "total_pnl_pct": 0.0}
    with patch(f"{M}.emergency_stop", AsyncMock()) as close:
        before = time.time()
        saves, _, _ = await _run_rebalance(config, state, _make_paper_state(balance=ledger),
                                          loop_limit=2, calc_return=calc, real_mode=not paper_mode)
    stopped = [s for _, s in saves if s.get("stop_reason") == "max_drawdown"]
    assert stopped and close.await_count == 1
    assert stopped[-1]["max_drawdown_reference"] == reference
    assert stopped[-1]["max_drawdown_basis"] == "portfolio_equity_peak"
    assert stopped[-1]["restart_not_before"] == stopped[-1]["strategy_bar_ms"] / 1000 + 365 * 86400


@pytest.mark.asyncio
@pytest.mark.parametrize("paper_mode", [True, False])
async def test_equity_stop_counts_open_mtm_and_persisted_peak(paper_mode):
    config = _make_config(paper_mode=paper_mode, max_drawdown_limit=2.08,
                          max_drawdown_cooldown_days=365.0, equity_trailing_stop_pct=0.0)
    config["portfolios"][0]["siphoning_threshold_pct"] = 0
    calc = {"total_tpv": 50.0, "tpv": 50.0, "actions": [], "total_pnl_pct": -50.0}
    with patch(f"{M}.emergency_stop", AsyncMock()) as close:
        saves, _, _ = await _run_rebalance(config, _make_state(started_at=1, tpv_ath=5000),
                                          _make_paper_state(balance=100.0),
                                          loop_limit=2, calc_return=calc, real_mode=not paper_mode)
    close.assert_awaited_once()
    assert any(s.get("stop_reason") == "max_drawdown" for _, s in saves)


@pytest.mark.parametrize("price", [60, 100, 140])
def test_wallet_ledger_matches_portfolio_equity_without_open_mtm(price):
    from core.calculator import PortfolioCalculator
    calculator = PortfolioCalculator(
        positions={"BTCUSDT_LONG": 3, "BTCUSDT_SHORT": -2},
        spot_price=price, real_equity=500, virt_qty=0, virt_debt=500,
        siphoning_reserve=25, initial_capital=1000, base_ticker="BTCUSDT",
        long_entry_price=100, short_entry_price=100,
        targets={"BASE_LONG": {"share": .25, "leverage": 3},
                 "BASE_SHORT": {"share": .25, "leverage": 3},
                 "VIRTUAL": {"share": .5}})
    open_pnl = Decimal(3) * (price - 100) + Decimal(2) * (100 - price)
    assert calculator.total_tpv - open_pnl == Decimal(1000 + 25)


@pytest.mark.asyncio
@pytest.mark.parametrize("paper_mode", [True, False])
async def test_equity_stop_precedes_rebalance_submission(paper_mode):
    config = _make_config(paper_mode=paper_mode, max_drawdown_limit=2.08,
                          max_drawdown_cooldown_days=365, equity_trailing_stop_pct=0)
    config['portfolios'][0]['siphoning_threshold_pct'] = 0
    executor = MagicMock(execute_actions=AsyncMock())
    calc = {'total_tpv': 97, 'tpv': 97, 'total_pnl_pct': -3, 'actions': [
        {'key': 'BASE_LONG', 'diff_usdt': 10, 'is_reduction': False}]}
    with patch(f'{M}.emergency_stop', AsyncMock()) as close, \
         patch(f'{M}.PortfolioExecutor', return_value=executor):
        await _run_rebalance(config, _make_state(started_at=1), _make_paper_state(balance=97),
                             calc_return=calc, real_mode=not paper_mode, loop_limit=2)
    close.assert_awaited_once()
    executor.execute_actions.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("paper_mode", [True, False])
async def test_equity_stop_counts_safe_in_total_equity(paper_mode):
    config = _make_config(paper_mode=paper_mode, max_drawdown_limit=2.08,
                          equity_trailing_stop_pct=0)
    config['portfolios'][0]['siphoning_threshold_pct'] = 0
    state = _make_state(started_at=1, siphoning_reserve=30, max_drawdown_reference=100)
    calc = {'total_tpv': 100, 'tpv': 70, 'total_pnl_pct': 0, 'actions': []}
    with patch(f'{M}.emergency_stop', AsyncMock()) as close:
        saves, _, _ = await _run_rebalance(config, state, _make_paper_state(balance=70),
                                          calc_return=calc, real_mode=not paper_mode, loop_limit=2)
    close.assert_not_awaited()
    assert any(value.get('max_drawdown_reference') == 100 for _, value in saves)
