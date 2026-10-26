"""Offline regressions for bounded PAPER hedge reductions."""
from copy import deepcopy
from decimal import Decimal as D
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from futures_portfolio.core.executor import PortfolioExecutor, validate_paper_positions
from futures_portfolio.core import main, share_trail
from futures_portfolio.core.storage import safe_save_json, safe_load_json_sync
import test_main_coverage as author


PRICE = D("0.004088")
CFG = {"min_notional_usdt": 6, "targets": {
    "BASE_LONG": {"share": .25, "leverage": 7},
    "BASE_SHORT": {"share": .25, "leverage": 7},
    "VIRTUAL": {"share": .5, "leverage": 1}}}


def wallet():
    return {"balance": 1030.5124, "positions": {"JCTUSDT_LONG": 319884.0, "JCTUSDT_SHORT": 171096.0},
            "base_ticker": "JCTUSDT", "last_price": float(PRICE),
            "long_entry_price": .00621353, "short_entry_price": .00622257}


def action(leg="SHORT", quantity=568196, reduction=True):
    return {"type": "ORDER", "key": f"BASE_{leg}", "symbol": f"JCTUSDT_{leg}",
            "base_symbol": "JCTUSDT", "position_side": leg, "leverage": 7,
            "diff_usdt": float(D(str(quantity)) * PRICE * (-1 if reduction else 1)),
            "is_reduction": reduction}


def connector():
    conn = MagicMock()
    conn.get_order_book = AsyncMock(return_value={"bids": [[str(PRICE), "9999999"]], "asks": [[str(PRICE), "9999999"]]})
    conn.get_mark_prices = AsyncMock(return_value={"JCTUSDT": float(PRICE)})
    conn.get_exchange_info = AsyncMock(return_value={"symbols": [{"symbol": "JCTUSDT", "filters": [
        {"filterType": "LOT_SIZE", "stepSize": "1"}, {"filterType": "MIN_NOTIONAL", "notional": "6"}]}]})
    conn.get_positions = AsyncMock(return_value={})
    conn.get_bnb_balance = AsyncMock(return_value=1)
    conn.get_margin_ratio = AsyncMock(return_value={"margin_ratio": 10, "total_wallet_balance": 1030.5124})
    return conn


@pytest.mark.asyncio
@pytest.mark.parametrize("leg", ["LONG", "SHORT"])
@pytest.mark.parametrize("requested", [100000, 568196])
async def test_actual_quantity_drives_fees_pnl_and_preserves_input(leg, requested):
    book = wallet()
    before = deepcopy(book)
    executor = PortfolioExecutor(connector())
    result = await executor._execute_single_action(action(leg, requested), float(PRICE), True, CFG,
                                                   {"JCTUSDT": 1}, book)
    expected = min(D(str(requested)), D(str(book["positions"][f"JCTUSDT_{leg}"])))
    entry = D(str(book[f"{leg.lower()}_entry_price"]))
    assert D(str(result["qty"])) == expected
    assert result["side"] == ("SELL" if leg == "LONG" else "BUY")
    assert result["reduce_only"] is True
    assert D(str(result["commission"])) == expected * PRICE * D("0.0004")
    assert result["trade_pnl"] == pytest.approx(float(expected * (PRICE - entry) * (1 if leg == "LONG" else -1)))
    assert book == before


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", [None, D("0")])
@pytest.mark.parametrize("leg", ["LONG", "SHORT"])
async def test_two_reductions_share_remaining_quantity(leg, budget):
    book = wallet()
    executor = PortfolioExecutor(connector())
    fills = await executor.execute_actions([action(leg, 120000), action(leg, 568196)], float(PRICE), True,
        deepcopy(CFG), {"JCTUSDT": 1}, book, cash_budget=budget)
    assert all(f["status"] == "SUCCESS" for f in fills)
    assert sum(D(str(f["qty"])) for f in fills) == D(str(book["positions"][f"JCTUSDT_{leg}"]))
    assert book == wallet()


@pytest.mark.asyncio
async def test_expansion_receives_only_real_reduction_budget():
    book = wallet()
    fills = await PortfolioExecutor(connector()).execute_actions(
        [action(), action("LONG", 2000000, False)], float(PRICE), True,
        deepcopy(CFG), {"JCTUSDT": 1}, book, cash_budget=D("0"))
    closed, opened = fills
    assert closed["qty"] == 171096
    proceeds = D(str(closed["qty"])) * D(str(book["short_entry_price"])) / 7
    proceeds += D(str(closed["trade_pnl"])) - D(str(closed["commission"]))
    expense = D(str(opened["qty"])) * PRICE / 7 + D(str(opened["commission"]))
    assert expense <= proceeds
    assert opened["qty"] < 2000000


@pytest.mark.asyncio
@pytest.mark.parametrize("remaining", [0, .0004])
async def test_flat_or_dust_reduction_does_not_create_fill(remaining):
    book = wallet()
    book["positions"]["JCTUSDT_SHORT"] = remaining
    result = await PortfolioExecutor(connector())._execute_single_action(action(), float(PRICE), True, CFG,
        {"JCTUSDT": .001}, book)
    assert result["status"] == "SKIPPED" and result["qty"] == 0
    assert not result.get("execution_id") and not result.get("commission")


@pytest.mark.asyncio
@pytest.mark.parametrize("quantity", [-397100, "NaN", "Infinity", "bad"])
async def test_corrupt_positions_block_execution(quantity):
    book = wallet()
    book["positions"]["JCTUSDT_SHORT"] = quantity
    with pytest.raises(ValueError, match="Invalid PAPER hedge quantity"):
        await PortfolioExecutor(connector()).execute_actions([action()], float(PRICE), True, CFG,
            {"JCTUSDT": 1}, book)


@pytest.mark.asyncio
async def test_step_rounding_never_exceeds_remaining():
    book = wallet()
    book["positions"]["JCTUSDT_SHORT"] = 171096.7
    result = await PortfolioExecutor(connector())._execute_single_action(action(), float(PRICE), True, CFG,
        {"JCTUSDT": 1}, book)
    assert result["qty"] == 171096


def calc_result():
    return {"total_tpv": 1000, "tpv": 1000, "total_pnl_pct": 0, "total_pnl": 0,
        "share_long_pct": 25, "share_short_pct": 25, "share_virt_pct": 50, "share_cash_pct": 0,
        "val_long": 250, "val_short": 250, "val_virt": 500, "val_cash": 100,
        "actions": [{"type": "ORDER", "key": "BASE_SHORT", "symbol": "BTCUSDT_SHORT",
            "base_symbol": "BTCUSDT", "position_side": "SHORT", "leverage": 7,
            "diff_usdt": -50, "is_reduction": True}]}


@pytest.mark.asyncio
@pytest.mark.parametrize("trail_distance", [0, .04])
async def test_duplicate_receipt_applied_once_in_main(trail_distance):
    config = author._make_config(equity_trailing_stop_activation_pct=9000, max_drawdown_limit=200,
                                 rebalance_share_trail_distance=trail_distance)
    state = author._make_state(initial_tpv=1000, reference_tpv=1000, tpv_ath=1000)
    book = author._make_paper_state(balance=1000, positions={"BTCUSDT_LONG": 1, "BTCUSDT_SHORT": 1},
                                   long_entry_price=100, short_entry_price=100)
    fill = {"status": "SUCCESS", "type": "SHORT", "symbol": "BTCUSDT_SHORT", "side": "BUY",
            "qty": .5, "price": 100, "commission": .02, "trade_pnl": 0, "reduce_only": True,
            "execution_id": "paper:duplicate-fixture"}
    executor = MagicMock()
    executor.execute_actions = AsyncMock(return_value=[fill, deepcopy(fill)])
    with patch(f"{author.M}.PortfolioExecutor", return_value=executor):
        saves, _, _ = await author._run_rebalance(config, state, book, loop_limit=1,
            calc_return=calc_result(), calc_val_cash=100, calc_tpv=1000, price=100)
    final = [value for path, value in saves if path == "paper_state.json"][-1]
    assert final["positions"]["BTCUSDT_SHORT"] == .5
    assert final["balance"] == 999.98
    assert len(final["share_fill_receipts"]) == 1


@pytest.mark.asyncio
async def test_overfilled_result_blocks_before_wallet_mutation():
    config = author._make_config(equity_trailing_stop_activation_pct=9000, max_drawdown_limit=200)
    state = author._make_state(initial_tpv=1000, reference_tpv=1000, tpv_ath=1000)
    book = author._make_paper_state(balance=1000, positions={"BTCUSDT_LONG": 1, "BTCUSDT_SHORT": 1},
                                   long_entry_price=100, short_entry_price=100)
    fill = {"status": "SUCCESS", "type": "SHORT", "symbol": "BTCUSDT_SHORT", "side": "BUY",
            "qty": 2, "price": 100, "commission": .08, "trade_pnl": 100, "reduce_only": True,
            "execution_id": "paper:overfilled-fixture"}
    executor = MagicMock()
    executor.execute_actions = AsyncMock(return_value=[fill])
    with patch(f"{author.M}.PortfolioExecutor", return_value=executor):
        saves, _, _ = await author._run_rebalance(config, state, book, loop_limit=2,
            calc_return=calc_result(), calc_val_cash=100, calc_tpv=1000, price=100)
    final = [value for path, value in saves if path == "state.json"][-1]
    assert final["startup_blocked"] is True
    assert executor.execute_actions.await_count == 1
    assert not [value for path, value in saves if path == "paper_state.json"]


@pytest.mark.asyncio
async def test_corrupt_resume_blocks_before_calculator():
    config = author._make_config()
    state = author._make_state()
    book = author._make_paper_state(positions={"BTCUSDT_LONG": 1, "BTCUSDT_SHORT": -397100})
    with patch(f"{author.M}.save_json", new_callable=AsyncMock) as save:
        with pytest.raises(ValueError, match="Invalid PAPER hedge quantity"):
            await author._run_rebalance(config, state, book)


class EndCycle(BaseException):
    pass


@pytest.mark.asyncio
async def test_real_executor_main_storage_reload_and_receipt_retry(tmp_path):
    config = author._make_config(base_ticker="JCTUSDT", equity_trailing_stop_activation_pct=9000,
        max_drawdown_limit=200, rebalance_share_trail_distance=0)
    config["portfolios"][0].update(deepcopy(CFG), initial_capital=1000, paper_initial_capital=1000,
        siphoning_threshold_pct=1000.05, reinvestment_ratio=0, liquidation_distance_crit_pct=0,
        liquidation_distance_warn_pct=0)
    config_path, state_path, wallet_path = [tmp_path / name for name in ("config.json", "state.json", "wallet.json")]
    state = author._make_state(base_ticker="JCTUSDT", initial_tpv=1000, reference_tpv=1000,
        tpv_ath=1000, virt_debt=353.65875489, rebalance_cycles=7, started_at=1792970671.6828263,
        last_rebalance_price=float(PRICE), initial_build_pending=None)
    book = wallet()
    for path, data in ((config_path, config), (state_path, state), (wallet_path, book)):
        await safe_save_json(str(path), data)
    notifier = MagicMock()
    for name in ("send_message", "send_alert", "send_status", "close"):
        setattr(notifier, name, AsyncMock())
    original = main.PortfolioCalculator
    fills = []
    real = main.PortfolioExecutor(connector())

    async def execute(*args, **kwargs):
        result = await real.execute_actions(*args, **kwargs)
        fills.extend(deepcopy(result))
        return result

    executor = MagicMock()
    executor.execute_actions = AsyncMock(side_effect=execute)
    injected = []
    def calculator(*args, **kwargs):
        instance = original(*args, **kwargs)
        calc = instance.calculate_rebalance
        def calculate(*values, **options):
            result = calc(*values, **options)
            result["actions"] = [] if injected else [action(quantity=120000)]
            if not injected:
                injected.append(True)
            return result
        instance.calculate_rebalance = calculate
        return instance
    with patch.object(main, "PortfolioExecutor", return_value=executor), \
         patch.object(main, "PortfolioCalculator", side_effect=calculator), \
         patch.object(main, "TelegramNotifier", return_value=notifier), \
         patch("asyncio.sleep", side_effect=EndCycle):
        with pytest.raises(EndCycle):
            await main.rebalance_loop(connector(), str(config_path), str(state_path), str(wallet_path),
                logging.getLogger("isolated-paper-test"), paper_mode_override=True)
    saved = safe_load_json_sync(str(wallet_path), {})
    saved_state = safe_load_json_sync(str(state_path), {})
    assert len(fills) == 1 and fills[0]["qty"] == 120000
    assert saved["positions"]["JCTUSDT_SHORT"] == 51096
    expected_wallet = (D(str(book["balance"])) + D(str(fills[0]["trade_pnl"])) - D(str(fills[0]["commission"]))).quantize(D("0.0001"))
    assert D(str(saved["balance"])) == expected_wallet
    assert saved_state["siphoning_reserve"] == 0
    validate_paper_positions(saved)
    assert share_trail.fill_receipt(saved, fills[0]) is False
    before = deepcopy(saved)
    assert share_trail.fill_receipt(saved, fills[0]) is False
    assert saved == before
    expected_tpv = expected_wallet + D(str(saved["positions"]["JCTUSDT_LONG"])) * (PRICE - D(str(saved["long_entry_price"])))
    expected_tpv += D(str(saved["positions"]["JCTUSDT_SHORT"])) * (D(str(saved["short_entry_price"])) - PRICE)
    assert saved_state["last_tpv"] == pytest.approx(float(expected_tpv), abs=.0001)
