"""E2E market-only partial fill: real PortfolioExecutor + mock connector -> runtime ->
durable save -> restart. No pre-injected results: the executor's market path produces
the partial fill, and the runtime must persist identity/volume/price/fees exactly once
and reconcile with the exchange after restart."""
import asyncio
import copy
import json
import time
from unittest.mock import AsyncMock, MagicMock, Mock, mock_open, patch

import pytest

from core import share_trail
from futures_portfolio.core import main


class EndTick(BaseException):
    pass


EID = "binance:ZECUSDT:77"

CONFIG = {
    "paper_mode": False,
    "base_ticker": "ZECUSDT",
    "equity_trailing_stop_activation_pct": 9000.0,
    "max_drawdown_limit": 200.0,
    "rebalance_share_trail_distance": 0.035,
    "funding_model": "legacy_constant",
    "portfolios": [{
        "name": "E2E",
        "initial_capital": 1000.0,
        "paper_initial_capital": 1000.0,
        "max_capital_usdt": 1000.0,
        "siphoning_threshold_pct": 1000.0,
        "reinvestment_ratio": 0.0,
        "liquidation_distance_warn_pct": 0.0,
        "liquidation_distance_crit_pct": 0.0,
        "post_factum_liquidation_check": False,
        "fuse_enabled": False,
        "targets": {
            "BASE_LONG": {"share": 0.35, "leverage": 7},
            "BASE_SHORT": {"share": 0.35, "leverage": 7},
            "VIRTUAL": {"share": 0.30, "leverage": 1},
        },
        "rebalance_threshold_surplus": 0.01,
        "rebalance_threshold_deficit": 0.04,
        "min_notional_usdt": 6.0,
        "check_interval_sec": 0.01,
        "safety_guards": {
            "max_spread_pct": 0.15, "max_price_velocity_pct": 2.0, "velocity_window_sec": 60,
            "trend_min_move_pct": 0.5, "trend_eff_threshold": 0.55,
            "net_move_block_pct": 2.14, "net_move_window_sec": 72, "trend_lookback_bars": 30,
        },
    }],
}


def _stamped_book(*args, **kwargs):
    return {"bids": [["200", "100"]], "asks": [["200.1", "100"]], "_fetched_at": time.time()}


def _make_connector(exchange):
    c = MagicMock()
    c.client = MagicMock()
    c.get_exchange_info = AsyncMock(return_value={"symbols": [{"symbol": "ZECUSDT", "filters": [
        {"filterType": "LOT_SIZE", "stepSize": "0.1"},
        {"filterType": "MIN_NOTIONAL", "notional": "5.0"}]}]})
    c.get_hedge_mode = AsyncMock(return_value=True)
    c.set_leverage = AsyncMock()
    c.set_margin_type = AsyncMock()
    c.get_futures_prices = AsyncMock(return_value={"ZECUSDT": 200.0})
    c.get_mark_prices = AsyncMock(return_value={"ZECUSDT": 200.0})
    c.get_order_book = AsyncMock(side_effect=_stamped_book)
    c.get_commission_rates = AsyncMock(return_value={
        "makerCommissionRate": "0.0002", "takerCommissionRate": "0.0004"})
    c.get_positions = AsyncMock(side_effect=lambda: {
        k: {"qty": v, "entry_price": 180.0 if "LONG" in k else 220.0}
        for k, v in exchange.items()})
    c.get_position_risk = AsyncMock(side_effect=lambda: {
        k: {"positionAmt": str(v), "positionSide": k.split("_")[1]}
        for k, v in exchange.items() if v})
    c.get_margin_ratio = AsyncMock(return_value={"margin_ratio": 10.0, "total_wallet_balance": 1000.0})
    c.get_order_trades = AsyncMock(return_value=[
        {"realizedPnl": "18.5", "commission": "0.45"}])
    c.futures_client = MagicMock()
    # the market order is partially filled by the "exchange"
    c.futures_client.futures_create_order = Mock(return_value={
        "orderId": 77, "status": "NEW", "executedQty": "1.0", "avgPrice": "200.05"})
    return c


def _make_notifier():
    notifier = MagicMock()
    notifier.send_message = AsyncMock()
    notifier.send_alert = AsyncMock()
    notifier.send_status = AsyncMock()
    notifier.close = AsyncMock()
    return notifier


def _seed_files():
    return {
        "state.json": {
            "base_ticker": "ZECUSDT", "initial_tpv": 1000.0, "reference_tpv": 1000.0,
            "tpv_ath": 1000.0, "virt_qty": 0.0, "virt_debt": 0.0,
            "rebalance_cycles": 1, "last_rebalance_price": 200.0,
            "share_trail": {"BASE_LONG": {"peak": "0.55", "trough": None},
                            "BASE_SHORT": {"peak": None, "trough": None}},
        },
        "paper_state.json": {
            "base_ticker": "ZECUSDT", "balance": 1000.0,
            "positions": {"ZECUSDT_LONG": 14.0, "ZECUSDT_SHORT": 2.0},
            "long_entry_price": 180.0, "short_entry_price": 220.0,
            "last_price": 200.0,
        },
    }


async def _run_once(files, connector, stop_after_receipt=False, stop_sleep_count=0):
    original_sleep = asyncio.sleep
    stop = {"receipt_saved": False, "seen": 0}

    async def sleep(seconds, *args, **kwargs):
        if seconds >= 86400:
            raise EndTick()
        stop["seen"] += 1
        if (stop_after_receipt and stop["receipt_saved"]) \
                or (stop_sleep_count and stop["seen"] >= stop_sleep_count):
            raise EndTick()
        return await original_sleep(0.0)

    def load(path, default=None):
        return copy.deepcopy(files.get(str(path), default))

    def save(path, value):
        files[str(path)] = json.loads(json.dumps(value))
        if value.get("share_fill_receipts"):
            stop["receipt_saved"] = True

    with patch.object(main, "load_json", side_effect=load), \
         patch.object(main, "save_json", side_effect=save), \
         patch.object(main, "safe_load_json_sync", return_value=CONFIG), \
         patch.object(main, "TelegramNotifier", return_value=_make_notifier()), \
         patch("builtins.open", mock_open(read_data=json.dumps(CONFIG))), \
         patch("os.path.getmtime", return_value=123), \
         patch("asyncio.sleep", side_effect=sleep):
        with pytest.raises(EndTick):
            await main.rebalance_loop(connector, "config.json", "state.json",
                                      "paper_state.json", MagicMock())
    return stop


@pytest.mark.asyncio
async def test_market_only_partial_fill_survives_restart():
    files = _seed_files()
    exchange = {"ZECUSDT_LONG": 14.0, "ZECUSDT_SHORT": 2.0}
    connector = _make_connector(exchange)

    # run 1: the reduction triggers, the market order fills partially, the durable
    # save happens, then the process dies right after that save
    await _run_once(files, connector, stop_after_receipt=True)
    paper = files["paper_state.json"]
    state = files["state.json"]
    assert len(paper["share_fill_receipts"]) == 1
    receipt = paper["share_fill_receipts"][EID]
    assert receipt["qty"] == "1.0" and receipt["price"] == "200.05"
    assert receipt["commission"] == "0.45" and receipt["trade_pnl"] == "18.5"
    assert receipt["reduce_only"] == "True"
    # the shadow ledger reflects exactly the executed part, with fees applied once
    assert paper["positions"]["ZECUSDT_LONG"] == 13.0
    assert paper["balance"] == 1018.05
    assert state["share_trail_last_fill"] == EID
    assert paper["share_trail_checkpoint"]["execution_id"] == EID
    # the trailing signal of that leg was consumed exactly once
    assert state["share_trail"]["BASE_LONG"]["peak"] is None
    orders_placed = connector.futures_client.futures_create_order.call_count
    assert orders_placed == 1
    # the exchange side applied the fill
    exchange["ZECUSDT_LONG"] = 13.0

    # run 2: restart from disk. The consumed signal must not be re-ordered; the
    # ledger must stay consistent with the exchange.
    await _run_once(files, connector, stop_sleep_count=6)
    paper = files["paper_state.json"]
    state = files["state.json"]
    assert paper["positions"]["ZECUSDT_LONG"] == 13.0 == exchange["ZECUSDT_LONG"]
    assert paper["positions"]["ZECUSDT_SHORT"] == 2.0 == exchange["ZECUSDT_SHORT"]
    assert paper["balance"] == 1018.05
    assert len(paper["share_fill_receipts"]) == 1
    assert connector.futures_client.futures_create_order.call_count == orders_placed

    # re-delivery of the same confirmed fill is rejected by the receipt journal
    payload = paper["share_fill_receipts"][EID]
    duplicate = {
        "execution_id": EID, "symbol": "ZECUSDT_LONG", "type": "LONG", "side": "SELL",
        "qty": payload["qty"], "price": payload["price"],
        "commission": payload["commission"], "trade_pnl": payload["trade_pnl"],
        "reduce_only": payload["reduce_only"],
    }
    paper_copy = copy.deepcopy(paper)
    assert share_trail.fill_receipt(paper_copy, duplicate, record=True) is False
    assert paper_copy["share_fill_receipts"] == paper["share_fill_receipts"]
