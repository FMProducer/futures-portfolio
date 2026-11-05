"""Live engine trend_add_block guard: blocks adds opposite a one-way window move."""
import json
import copy
import time
import sys
from decimal import Decimal
from unittest.mock import patch, AsyncMock, MagicMock, mock_open

from futures_portfolio.core.main import rebalance_loop

M = "futures_portfolio.core.main"


def _make_config():
    return {
        "paper_mode": True,
        "base_ticker": "BTCUSDT",
        "equity_trailing_stop_pct": 5.0,
        "equity_trailing_stop_timeout_sec": 0.0,
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
            "trend_add_block": {"pct": 0.02, "window_bars": 120},
            # test ticks are milliseconds apart; relax velocity/net-move guards so only
            # the trend_add_block guard can act (its 7200s window falls back to the oldest tick)
            "safety_guards": {
                "max_price_velocity_pct": 500.0, "velocity_window_sec": 3600,
                "net_move_block_pct": 5.0, "net_move_window_sec": 3600,
            },
        }]
    }


def _make_state():
    return {
        "virt_qty": 0.0, "virt_debt": 0.0,
        "base_ticker": "BTCUSDT",
        "siphoning_reserve": 0.0,
        "initial_tpv": 100.0, "reference_tpv": 100.0, "tpv_ath": 100.0,
        "trailing_stop_triggered": False, "trailing_stop_violation_start": 0.0,
        "rebalance_cycles": 10,
        "positions": {"BTCUSDT_LONG": 0.0, "BTCUSDT_SHORT": 0.0},
        "funding_journal": None, "funding_halt": None, "initial_build_pending": None,
    }


def _make_paper_state():
    return {
        "balance": 100.0,
        "positions": {"BTCUSDT_LONG": 0.0, "BTCUSDT_SHORT": 0.0},
        "last_price": 60000.0,
        "base_ticker": "BTCUSDT",
        "long_entry_price": 0.0,
        "short_entry_price": 0.0,
    }


# add (deficit) on both futures legs + a short reduction: only the adds are direction-sensitive
_ACTIONS = [
    {"type": "ORDER", "key": "BASE_SHORT", "position_side": "SHORT", "symbol": "BTCUSDT",
     "diff_usdt": 10.0, "approved_usdt": 10.0, "is_reduction": False, "leverage": 7.0},
    {"type": "ORDER", "key": "BASE_LONG", "position_side": "LONG", "symbol": "BTCUSDT",
     "diff_usdt": 10.0, "approved_usdt": 10.0, "is_reduction": False, "leverage": 7.0},
    {"type": "ORDER", "key": "BASE_SHORT", "position_side": "SHORT", "symbol": "BTCUSDT",
     "diff_usdt": -10.0, "approved_usdt": 10.0, "is_reduction": True, "leverage": 7.0},
]


async def _run_with_prices(prices, directory, loop_limit=4):
    config, state, wallet = _make_config(), _make_state(), _make_paper_state()
    config['portfolios'][0]['safety_guards']['trend_eff_threshold'] = 2
    directory.mkdir(parents=True, exist_ok=True)
    state.update(started_at=1767225720-121*60, strategy_origin_ms=(1767225720-121*60)*1000)
    for name, value in [('config.json', config), ('state.json', state), ('wallet.json', wallet)]:
        (directory/name).write_text(json.dumps(value))
    tick = [0]
    now = lambda: 1767225720 + tick[0]*60
    connector = MagicMock()
    connector.get_exchange_info = AsyncMock(return_value={'symbols': [{'symbol': 'BTCUSDT', 'filters': [
        {'filterType': 'LOT_SIZE', 'stepSize': '.001'}, {'filterType': 'MIN_NOTIONAL', 'notional': '5'}]}]})
    connector.get_hedge_mode = AsyncMock(return_value=True)
    connector.get_positions = AsyncMock(return_value={})
    connector.get_margin_ratio = AsyncMock(return_value={'margin_ratio': 10, 'total_wallet_balance': 100})
    connector.get_mark_prices = AsyncMock(return_value={'BTCUSDT': prices[0]})
    connector.get_order_book = AsyncMock(return_value={'bids': [['60000', '100']], 'asks': [['60000', '100']]})
    connector.set_leverage, connector.set_margin_type = AsyncMock(), AsyncMock()

    async def candles(symbol, interval, limit):
        end = int(now()//60)*60000
        current = min(tick[0], len(prices)-1)
        return [[t, (value := prices[max(0, current-int((end-t)//60000)+1)]), value, value, value, 1]
                for t in range(end-limit*60000, end+1, 60000)]
    connector.get_futures_klines = AsyncMock(side_effect=candles)
    calculator = MagicMock(tpv=100., total_tpv=100., val_cash=Decimal(0))
    calculator.calculate_rebalance.return_value = {'total_tpv': 100., 'tpv': 100.,
        'actions': copy.deepcopy(_ACTIONS), 'total_pnl_pct': 0,
        'share_long_pct': 40, 'share_short_pct': 40, 'share_virt_pct': 20, 'share_cash_pct': 0,
        'val_long': 40, 'val_short': 40, 'val_virt': 20, 'val_cash': 0}
    executor = MagicMock(execute_actions=AsyncMock(return_value=[{'status': 'SKIPPED', 'qty': 0}]))
    notifier = MagicMock(send_message=AsyncMock(), send_alert=AsyncMock(), close=AsyncMock())
    filters = sys.modules[M].backtest_rules.filter_actions
    planned = []
    def observe(*args, **kwargs):
        result = filters(*args, **kwargs)
        if result:
            planned.append(copy.deepcopy(result))
        return result
    async def sleep(seconds):
        tick[0] += 1
        if seconds >= 86400 or tick[0] > loop_limit:
            raise EndRun()
    class EndRun(BaseException):
        pass
    with patch(f'{M}.time.time', side_effect=now), patch('asyncio.sleep', side_effect=sleep), \
         patch(f'{M}.PortfolioCalculator', return_value=calculator), \
         patch(f'{M}.PortfolioExecutor', return_value=executor), \
         patch(f'{M}.backtest_rules.filter_actions', side_effect=observe), \
         patch(f'{M}.TelegramNotifier', return_value=notifier), \
         patch(f'{M}.self_kill_pm2', AsyncMock()), patch(f'{M}.emit_signal'):
        try:
            await rebalance_loop(connector, str(directory/'config.json'), str(directory/'state.json'),
                                 str(directory/'wallet.json'), MagicMock(), paper_mode_override=True)
        except EndRun:
            pass
    for call in executor.execute_actions.call_args_list:
        assert len({a['key'] for a in call.args[0] if a.get('key') in ('BASE_LONG', 'BASE_SHORT')}) <= 1
    return planned


def _adds(actions):
    return [a for a in actions if not a.get("is_reduction", False)]


def _reductions(actions):
    return [a for a in actions if a.get("is_reduction", False)]


async def test_zero_net_move_threshold_keeps_live_rebalances_enabled(tmp_path):
    config = _make_config()
    config["portfolios"][0]["trend_add_block"]["pct"] = 0
    counts = {}
    for threshold in (0, 0.1):
        config["portfolios"][0]["safety_guards"]["net_move_block_pct"] = threshold
        with patch(f"{__name__}._make_config", return_value=copy.deepcopy(config)):
            calls = await _run_with_prices([60000, 61400, 61400, 61400], tmp_path/str(threshold))
        counts[threshold] = len(calls)
    assert counts[0] >= 2
    assert counts[0.1] == 1


async def test_up_move_blocks_short_adds_keeps_reductions(tmp_path):
    # First cycle (no move yet) executes everything; after +2.33% the short add is blocked.
    per_call = await _run_with_prices([60000, 61400, 61400, 61400], tmp_path)
    assert len(per_call) >= 2, f"expected several executed cycles, got {len(per_call)}"
    assert any(a["key"] == "BASE_SHORT" for a in _adds(per_call[0])), "short add must pass before the move"
    last = per_call[-1]
    assert all(a["key"] != "BASE_SHORT" for a in _adds(last)), "short adds must be blocked on an up move"
    assert any(a["key"] == "BASE_LONG" for a in _adds(last)), "long adds stay allowed on an up move"
    assert any(a["key"] == "BASE_SHORT" for a in _reductions(last)), "short reductions stay allowed on an up move"


async def test_down_move_blocks_long_adds_keeps_shorts(tmp_path):
    # After -2.33% the long add is blocked; short adds and reductions pass.
    per_call = await _run_with_prices([60000, 58600, 58600, 58600], tmp_path)
    assert len(per_call) >= 2, f"expected several executed cycles, got {len(per_call)}"
    assert all(a["key"] != "BASE_LONG" for a in _adds(per_call[-1])), "long adds must be blocked on a down move"
    assert any(a["key"] == "BASE_SHORT" for a in _adds(per_call[-1])), "short adds stay allowed on a down move"
    assert any(a["key"] == "BASE_SHORT" for a in _reductions(per_call[-1])), "short reductions stay allowed on a down move"
