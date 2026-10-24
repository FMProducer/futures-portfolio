"""Live engine trend_add_block guard: blocks adds opposite a one-way window move."""
import json
import copy
import time
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


async def _run_with_prices(prices, loop_limit=4):
    config = _make_config()
    state = _make_state()
    paper_state = _make_paper_state()

    connector = MagicMock()
    price_seq = [{"BTCUSDT": px} for px in prices] + [{"BTCUSDT": prices[-1]}] * 20
    connector.get_futures_prices = AsyncMock(side_effect=price_seq)
    connector.get_mark_prices = AsyncMock(side_effect=price_seq)
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
    connector.get_order_book = AsyncMock(
        return_value={"bids": [["60000", "1"]], "asks": [["60001", "1"]], "_fetched_at": time.time()})
    connector.set_leverage = AsyncMock()
    connector.set_margin_type = AsyncMock()

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

    calc_mock = MagicMock()
    calc_mock.calculate_rebalance.return_value = {
        "total_tpv": 100.0, "tpv": 100.0,
        "actions": [copy.deepcopy(a) for a in _ACTIONS],
        "total_pnl_pct": 0.0,
        "share_long_pct": 40.0, "share_short_pct": 40.0, "share_virt_pct": 20.0, "share_cash_pct": 0.0,
        "val_long": 40.0, "val_short": 40.0, "val_virt": 20.0, "val_cash": 0.0,
    }
    calc_mock.tpv = 100.0
    calc_mock.val_cash = Decimal("0")

    executor_mock = MagicMock()
    executor_mock.execute_actions = AsyncMock(return_value=[
        {"status": "SUCCESS", "type": "ORDER", "symbol": "BTCUSDT"}
    ])

    class ZombieExit(BaseException):
        pass

    loop_count = [0]

    def bounded_sleep(secs, *args):
        if secs == 86400:
            raise ZombieExit("ZombieMode")
        loop_count[0] += 1
        if loop_count[0] > loop_limit:
            raise Exception("StopLoop")
        return None

    with patch(f"{M}.safe_load_json_sync", return_value=config), \
         patch("builtins.open", mock_open(read_data=json.dumps(config))), \
         patch("os.path.getmtime", return_value=123), \
         patch(f"{M}.load_json", AsyncMock(side_effect=load_side_effect)), \
         patch(f"{M}.save_json", AsyncMock()), \
         patch(f"{M}.sys.exit", side_effect=BaseException("ProcessExit")), \
         patch(f"{M}.TelegramNotifier", return_value=notifier), \
         patch("asyncio.sleep", side_effect=bounded_sleep), \
         patch(f"{M}.PortfolioCalculator", return_value=calc_mock), \
         patch(f"{M}.PortfolioExecutor", return_value=executor_mock), \
         patch(f"{M}.BinanceConnector", return_value=connector):
        try:
            await rebalance_loop(connector, "config.json", "state.json", "paper_state.json", MagicMock())
        except (Exception, BaseException) as e:
            if str(e) not in ["StopLoop", "ProcessExit", "ZombieMode"]:
                raise
    return [call.args[0] for call in executor_mock.execute_actions.call_args_list]


def _adds(actions):
    return [a for a in actions if not a.get("is_reduction", False)]


def _reductions(actions):
    return [a for a in actions if a.get("is_reduction", False)]


async def test_up_move_blocks_short_adds_keeps_reductions():
    # First cycle (no move yet) executes everything; after +2.33% the short add is blocked.
    per_call = await _run_with_prices([60000, 61400, 61400, 61400])
    assert len(per_call) >= 2, f"expected several executed cycles, got {len(per_call)}"
    assert any(a["key"] == "BASE_SHORT" for a in _adds(per_call[0])), "short add must pass before the move"
    last = per_call[-1]
    assert all(a["key"] != "BASE_SHORT" for a in _adds(last)), "short adds must be blocked on an up move"
    assert any(a["key"] == "BASE_LONG" for a in _adds(last)), "long adds stay allowed on an up move"
    assert any(a["key"] == "BASE_SHORT" for a in _reductions(last)), "short reductions stay allowed on an up move"


async def test_down_move_blocks_long_adds_keeps_shorts():
    # After -2.33% the long add is blocked; short adds and reductions pass.
    per_call = await _run_with_prices([60000, 58600, 58600, 58600])
    assert len(per_call) >= 2, f"expected several executed cycles, got {len(per_call)}"
    assert all(a["key"] != "BASE_LONG" for a in _adds(per_call[-1])), "long adds must be blocked on a down move"
    assert any(a["key"] == "BASE_SHORT" for a in _adds(per_call[-1])), "short adds stay allowed on a down move"
    assert any(a["key"] == "BASE_SHORT" for a in _reductions(per_call[-1])), "short reductions stay allowed on a down move"
