"""Branch coverage for core/executor.py: paper/real single-action paths,
explicit-quantity validation, order-book fallback and executed-zero handling."""
import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.executor import PortfolioExecutor, validate_paper_positions


@pytest.fixture
def executor():
    connector = MagicMock()
    return PortfolioExecutor(connector, "BTCUSDT")


def test_validate_paper_positions_rejects_non_dict(executor):
    with pytest.raises(ValueError, match="Invalid PAPER positions"):
        validate_paper_positions({"positions": "nope"})
    with pytest.raises(ValueError, match="hedge quantity"):
        validate_paper_positions({"positions": {"BTCUSDT_LONG": "abc"}})
    validate_paper_positions({"positions": {"BTCUSDT_LONG": "1.5", "BTCUSDT_SHORT": "0"}})


def _paper_action(**overrides):
    action = {"type": "MARKET_ORDER", "symbol": "BTCUSDT_LONG", "diff_usdt": "100",
              "base_symbol": "BTCUSDT", "position_side": "LONG"}
    action.update(overrides)
    return action


@pytest.mark.asyncio
async def test_paper_invalid_order_rejected(executor):
    with pytest.raises(ValueError, match="Invalid PAPER order"):
        await executor._execute_single_action(
            _paper_action(position_side="BOTH"), 100.0, True, {}, {})
    with pytest.raises(ValueError, match="Invalid PAPER order"):
        await executor._execute_single_action(_paper_action(), 0.0, True, {}, {})


@pytest.mark.asyncio
async def test_paper_reductions(executor):
    # reduce-only without a wallet
    with pytest.raises(ValueError, match="without wallet"):
        await executor._execute_single_action(
            _paper_action(diff_usdt="-50", position_side="SHORT"), 100.0, True, {}, {})
    # a non-dict/invalid hedge quantity is rejected up front by the validator
    with pytest.raises(ValueError, match="Invalid PAPER hedge quantity"):
        await executor._execute_single_action(
            _paper_action(diff_usdt="-50"), 100.0, True, {}, {},
            paper_state={"positions": {"BTCUSDT_LONG": "-1"}})
    # valid reduction with a bad entry price
    with pytest.raises(ValueError, match="Invalid PAPER reduction entry"):
        await executor._execute_single_action(
            _paper_action(diff_usdt="-50", position_side="SHORT"), 100.0, True, {}, {},
            paper_state={"positions": {"BTCUSDT_SHORT": 1}, "short_entry_price": 0})


@pytest.mark.asyncio
async def test_paper_skips_too_small_and_zero(executor):
    result = await executor._execute_single_action(
        _paper_action(diff_usdt="0.0001"), 1000.0, True, {"min_notional_usdt": "6"}, {})
    assert result["status"] == "SKIPPED" and "too small" in result["message"].lower()
    # Zero min-notional lets a step-rounded-to-zero order reach the second skip.
    result = await executor._execute_single_action(
        _paper_action(diff_usdt="1"), 1000000.0, True, {"min_notional_usdt": "0"},
        {"BTCUSDT": "0.000001"})
    assert result["status"] == "SKIPPED" and "Округлено до нуля" in result["message"]


@pytest.mark.asyncio
async def test_paper_success_with_mid_price(executor):
    result = await executor._execute_single_action(
        _paper_action(diff_usdt="100"), 100.0, True, {}, {},
        mid_prices={"BTCUSDT": Decimal("100")})
    assert result["status"] == "SUCCESS" and result["reduce_only"] is False
    assert result["execution_id"].startswith("paper:")


@pytest.mark.asyncio
async def test_explicit_quantity_requires_reduction(executor):
    with pytest.raises(ValueError, match="positive reduction"):
        await executor._execute_single_action(
            _paper_action(diff_usdt="100", qty="1"), 100.0, True, {}, {})
    result = await executor._execute_single_action(
        _paper_action(diff_usdt="-100", qty="2"), 100.0, True, {}, {},
        paper_state={"positions": {"BTCUSDT_LONG": 5}})
    assert result["qty"] == 2.0 and result["reduce_only"] is True


@pytest.mark.asyncio
async def test_real_mode_executed_zero(executor):
    ex = executor
    ex.connector.client = MagicMock()
    ex.connector.futures_client = MagicMock()
    ex.connector.futures_client.futures_create_order = lambda **params: {"orderId": 7, "executedQty": "0", "avgPrice": "0"}
    ex.connector.get_order_status = AsyncMock(return_value={"status": "NEW", "executedQty": "0", "avgPrice": "0"})
    ex.connector.get_order_trades = AsyncMock(return_value=[])
    ex.get_limit_order_params = lambda cfg: (False, Decimal("0.2"), 30)
    result = await ex._execute_single_action(
        _paper_action(diff_usdt="100", symbol="BTCUSDT_LONG"), 100.0, False,
        {"min_notional_usdt": "6"}, {})
    assert result["status"] == "ERROR"


@pytest.mark.asyncio
async def test_real_mode_success(executor):
    ex = executor
    ex.connector.client = MagicMock()
    ex.connector.futures_client = MagicMock()
    ex.connector.futures_client.futures_create_order = lambda **params: {"orderId": 9, "executedQty": "1", "avgPrice": "0"}
    ex.connector.get_order_trades = AsyncMock(return_value=[
        {"qty": "1", "commission": "0.04", "realizedPnl": "5", "commissionAsset": "USDT"}])
    ex.get_limit_order_params = lambda cfg: (False, Decimal("0.2"), 30)
    result = await ex._execute_single_action(
        _paper_action(diff_usdt="100"), 100.0, False, {"min_notional_usdt": "6"}, {})
    # avgPrice 0 -> falls back to the passed price (line 146-147)
    assert result["status"] == "SUCCESS"
    assert result["execution_id"] == "binance:BTCUSDT:9"
    assert result["price"] == 100.0
    assert result["financials_confirmed"] is True


@pytest.mark.asyncio
async def test_execute_rebalance_book_fallback_and_limit(executor, monkeypatch):
    ex = executor
    # Book call fails -> mark-price fallback (lines 394-396), then a real market fill.
    ex.connector.get_order_book = AsyncMock(side_effect=OSError("no book"))
    ex.connector.futures_client = MagicMock()
    ex.connector.futures_client.futures_create_order = (
        lambda **params: {"orderId": 3, "executedQty": "1", "avgPrice": "100"})
    ex.connector.get_order_trades = AsyncMock(return_value=[])
    ok = await ex.execute_rebalance(
        {"symbol": "BTCUSDT_LONG", "diff_usdt": "100"}, 100.0, 0.001, limit_order=False,
        portfolio_cfg={"min_notional_usdt": "6"})
    assert ok is True
    # Successful book path + explicit quantity + limit order (patched limit executor).
    ex.connector.get_order_book = AsyncMock(return_value={
        "bids": [["99.9", "1"]], "asks": [["100.1", "1"]], "_fetched_at": 0})
    ex.execute_limit_with_fallback = AsyncMock(
        return_value={"status": "SUCCESS_FALLBACK", "executed_qty": 1, "avg_price": "100",
                      "realized_pnl": 0, "commission": 0, "result": {"orderId": 5}})
    ok = await ex.execute_rebalance(
        {"symbol": "BTCUSDT_LONG", "diff_usdt": "-100", "qty": "1"}, 100.0, 0.001,
        limit_order=True, portfolio_cfg={"min_notional_usdt": "6"})
    assert ok is True
    # Explicit qty that is not a positive reduction is rejected.
    with pytest.raises(ValueError, match="positive reduction"):
        await ex.execute_rebalance(
            {"symbol": "BTCUSDT_LONG", "diff_usdt": "100", "qty": "1"}, 100.0, 0.001,
            limit_order=False, portfolio_cfg={"min_notional_usdt": "6"})
