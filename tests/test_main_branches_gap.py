"""Targeted branch coverage for core/main.py helpers: self-kill retries,
paper collateral/guard metrics, FUSE fill inputs, funding leg reconciliation,
and the live funding close driver."""
import asyncio
import logging
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

LOGGER = logging.getLogger("test-main-branch")


def _paper_state():
    return {"positions": {"BTC_LONG": 2.0, "BTC_SHORT": 0.0},
            "long_entry_price": 100.0, "short_entry_price": 0.0,
            "balance": 1000.0}


def test_self_kill_pm2_exhausts_retries(monkeypatch):
    from futures_portfolio.core.main import self_kill_pm2
    attempts = []

    async def fake_shell(cmd, **kwargs):
        attempts.append(cmd)
        raise OSError("pm2 gone")

    async def noop_sleep(seconds):
        pass

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_shell)
    monkeypatch.setattr(asyncio, "sleep", noop_sleep)
    asyncio.run(self_kill_pm2("BTCUSDT", True))  # final error is logged, not raised
    assert len(attempts) == 3


def test_paper_guard_metrics_rejects_bad_reserve():
    from futures_portfolio.core.main import _paper_guard_metrics
    with pytest.raises(ValueError, match="finite and nonnegative"):
        _paper_guard_metrics(_paper_state(), "BTCUSDT", 110, Decimal("1010"), LOGGER,
                             account_free_margin=Decimal("-1"))
    with pytest.raises(ValueError, match="finite and nonnegative"):
        _paper_guard_metrics(_paper_state(), "BTCUSDT", 110, Decimal("1010"), LOGGER,
                             account_free_margin=float("inf"))


def test_paper_guard_metrics_zero_collateral_distance():
    from futures_portfolio.core.main import _paper_guard_metrics
    state = {"positions": {}, "balance": -100.0}
    collateral, maintenance, distance = _paper_guard_metrics(state, "BTCUSDT", 100,
                                                              Decimal("-100"), LOGGER, 0)
    assert distance == -100.0


@pytest.mark.asyncio
async def test_live_fuse_fill_taker_out_of_range():
    from futures_portfolio.core.main import _live_fuse_fill
    connector = AsyncMock()
    connector.get_commission_rates = AsyncMock(return_value={"takerCommissionRate": "1.5"})
    result = await _live_fuse_fill(connector, "BTC", LOGGER)
    assert result == {"bids": [], "asks": [], "taker": None}


@pytest.mark.asyncio
async def test_live_fuse_fill_stale_book_keeps_taker_only():
    import time
    from futures_portfolio.core.main import _live_fuse_fill
    connector = AsyncMock()
    connector.get_commission_rates = AsyncMock(return_value={"takerCommissionRate": "0.001"})
    connector.get_order_book = AsyncMock(
        return_value={"bids": [[100, 5]], "asks": [[101, 5]], "_fetched_at": time.time() - 99})
    result = await _live_fuse_fill(connector, "BTC", LOGGER)
    assert result["taker"] == Decimal("0.001")
    assert result["bids"] == []


@pytest.mark.asyncio
async def test_live_fuse_fill_success():
    import time
    from futures_portfolio.core.main import _live_fuse_fill
    connector = AsyncMock()
    connector.get_commission_rates = AsyncMock(return_value={"takerCommissionRate": "0.001"})
    connector.get_order_book = AsyncMock(
        return_value={"bids": [[100, 5]], "asks": [[101, 5]], "_fetched_at": time.time()})
    result = await _live_fuse_fill(connector, "BTC", LOGGER)
    assert result["bids"] == [(Decimal("100"), Decimal("5"))]
    assert result["asks"] == [(Decimal("101"), Decimal("5"))]


def _halt(cs):
    from core.funding import FundingHalt
    halt = FundingHalt(active=True)
    halt.close_state["BTC_LONG"] = cs
    return halt


def _cs(**kwargs):
    from core.funding import CloseLegState
    base = dict(leg="BTC_LONG", close_event_id="e1")
    base.update(kwargs)
    return CloseLegState(**base)


@pytest.mark.asyncio
async def test_reconcile_funding_leg_paper_mode_reissues():
    from futures_portfolio.core.main import _reconcile_funding_leg
    halt = _halt(_cs())
    connector = AsyncMock()
    verdict = await _reconcile_funding_leg(connector, halt, "BTC_LONG", _paper_state(),
                                           "BTC", True, LOGGER)
    assert verdict == "reissue"


@pytest.mark.asyncio
async def test_reconcile_funding_leg_missing_state_raises():
    from core.funding import FundingHalt
    from futures_portfolio.core.main import _reconcile_funding_leg
    with pytest.raises(ValueError, match="Missing durable funding close intent"):
        await _reconcile_funding_leg(AsyncMock(), FundingHalt(active=True), "BTC_LONG",
                                     _paper_state(), "BTC", False, LOGGER)


@pytest.mark.asyncio
async def test_reconcile_funding_leg_quarantined_returns_handled():
    from futures_portfolio.core.main import _reconcile_funding_leg
    halt = _halt(_cs(status="quarantined"))
    verdict = await _reconcile_funding_leg(AsyncMock(), halt, "BTC_LONG", _paper_state(),
                                           "BTC", False, LOGGER)
    assert verdict == "handled"


def _filled_connector(order_overrides=None, trades=None):
    connector = AsyncMock()
    order = {"symbol": "BTC", "orderId": 555, "side": "SELL", "positionSide": "LONG",
             "executedQty": "2", "origQty": "2", "status": "FILLED", "avgPrice": "100"}
    if order_overrides:
        order.update(order_overrides)
    connector.get_order_status = AsyncMock(return_value=order)
    connector.get_order_trades = AsyncMock(return_value=trades if trades is not None else [
        {"id": "t1", "orderId": 555, "symbol": "BTC", "side": "SELL", "positionSide": "LONG",
         "qty": "2", "commission": "0.1", "realizedPnl": "5", "price": "100",
         "commissionAsset": "USDT"}])
    return connector


@pytest.mark.asyncio
async def test_reconcile_funding_leg_full_fill():
    from futures_portfolio.core.main import _reconcile_funding_leg
    cs = _cs(qty_closed=Decimal("0"), qty_remaining=Decimal("2"),
              execution_id="binance:BTC:555")
    halt = _halt(cs)
    paper = _paper_state()
    verdict = await _reconcile_funding_leg(_filled_connector(), halt, "BTC_LONG", paper,
                                           "BTC", False, LOGGER)
    assert verdict == "handled"
    assert cs.status == "filled"
    assert cs.qty_closed == Decimal("2")
    assert paper["positions"]["BTC_LONG"] == 0.0
    assert paper["balance"] == 1005 - 0.1
    assert paper["long_entry_price"] == 0.0


@pytest.mark.asyncio
async def test_reconcile_funding_leg_partial_fill():
    from futures_portfolio.core.main import _reconcile_funding_leg
    cs = _cs(qty_closed=Decimal("0"), qty_remaining=Decimal("2"),
             execution_id="binance:BTC:555")
    halt = _halt(cs)
    connector = _filled_connector(
        {"executedQty": "1", "status": "PARTIALLY_FILLED"},
        trades=[{"id": "t1", "orderId": 555, "symbol": "BTC", "side": "SELL",
                 "positionSide": "LONG", "qty": "1", "commission": "0.05",
                 "realizedPnl": "2.5", "price": "100", "commissionAsset": "USDT"}])
    verdict = await _reconcile_funding_leg(connector, halt, "BTC_LONG", _paper_state(),
                                           "BTC", False, LOGGER)
    assert verdict == "handled"
    assert cs.status == "partial"
    assert cs.qty_remaining == Decimal("1")


@pytest.mark.asyncio
async def test_reconcile_funding_leg_provider_mismatch_quarantines():
    from futures_portfolio.core.main import _reconcile_funding_leg
    cs = _cs(qty_remaining=Decimal("2"), execution_id="kraken:BTC:555")
    halt = _halt(cs)
    verdict = await _reconcile_funding_leg(_filled_connector(), halt, "BTC_LONG",
                                           _paper_state(), "BTC", False, LOGGER)
    assert verdict == "handled"
    assert cs.status == "quarantined"
    assert "identity" in cs.reconciliation_error.lower() or "does not match" in cs.reconciliation_error


@pytest.mark.asyncio
async def test_reconcile_funding_leg_unknown_status_quarantines():
    from futures_portfolio.core.main import _reconcile_funding_leg
    cs = _cs(qty_remaining=Decimal("2"), execution_id="binance:BTC:555")
    halt = _halt(cs)
    connector = _filled_connector({"status": "WEIRD"})
    verdict = await _reconcile_funding_leg(connector, halt, "BTC_LONG", _paper_state(),
                                           "BTC", False, LOGGER)
    assert cs.status == "quarantined"
    assert verdict == "handled"


@pytest.mark.asyncio
async def test_reconcile_funding_leg_trade_side_mismatch_quarantines():
    from futures_portfolio.core.main import _reconcile_funding_leg
    cs = _cs(qty_remaining=Decimal("2"), execution_id="binance:BTC:555")
    halt = _halt(cs)
    connector = _filled_connector(
        trades=[{"id": "t1", "orderId": 555, "symbol": "BTC", "side": "BUY",
                 "positionSide": "SHORT", "qty": "2", "commission": "0.1",
                 "realizedPnl": "5", "price": "100", "commissionAsset": "USDT"}])
    verdict = await _reconcile_funding_leg(connector, halt, "BTC_LONG", _paper_state(),
                                           "BTC", False, LOGGER)
    assert cs.status == "quarantined"
    assert verdict == "handled"


@pytest.mark.asyncio
async def test_reconcile_funding_leg_non_usdt_fee_quarantines():
    from futures_portfolio.core.main import _reconcile_funding_leg
    cs = _cs(qty_remaining=Decimal("2"), execution_id="binance:BTC:555")
    halt = _halt(cs)
    connector = _filled_connector(
        trades=[{"id": "t1", "orderId": 555, "symbol": "BTC", "side": "SELL",
                 "positionSide": "LONG", "qty": "2", "commission": "0.1",
                 "realizedPnl": "5", "price": "100", "commissionAsset": "BTC"}])
    verdict = await _reconcile_funding_leg(connector, halt, "BTC_LONG", _paper_state(),
                                           "BTC", False, LOGGER)
    assert cs.status == "quarantined"
    assert "Non-USDT fees" in cs.reconciliation_error
    assert verdict == "handled"


@pytest.mark.asyncio
async def test_reconcile_funding_leg_terminal_residual_quarantines():
    from futures_portfolio.core.main import _reconcile_funding_leg
    cs = _cs(qty_remaining=Decimal("2"), execution_id="binance:BTC:555")
    halt = _halt(cs)
    connector = _filled_connector(
        {"executedQty": "1", "status": "CANCELED"},
        trades=[{"id": "t1", "orderId": 555, "symbol": "BTC", "side": "SELL",
                 "positionSide": "LONG", "qty": "1", "commission": "0.05",
                 "realizedPnl": "2.5", "price": "100", "commissionAsset": "USDT"}])
    verdict = await _reconcile_funding_leg(connector, halt, "BTC_LONG", _paper_state(),
                                           "BTC", False, LOGGER)
    assert cs.status == "quarantined"
    assert "residual" in cs.reconciliation_error
    assert verdict == "handled"


@pytest.mark.asyncio
async def test_reconcile_funding_leg_ready_submission_mismatch_quarantines():
    from futures_portfolio.core.main import _reconcile_funding_leg
    cs = _cs(qty_remaining=Decimal("2"), client_order_id="c1", submission_state="ready")
    halt = _halt(cs)
    connector = _filled_connector()
    connector.get_positions = AsyncMock(return_value={"BTC_LONG": {"qty": "1"}})
    verdict = await _reconcile_funding_leg(connector, halt, "BTC_LONG", _paper_state(),
                                           "BTC", False, LOGGER)
    assert cs.status == "quarantined"
    assert verdict == "handled"


@pytest.mark.asyncio
async def test_reconcile_funding_leg_ready_submission_reissues():
    from futures_portfolio.core.main import _reconcile_funding_leg
    cs = _cs(qty_remaining=Decimal("2"), client_order_id="c1", submission_state="ready")
    halt = _halt(cs)
    connector = _filled_connector()
    connector.get_positions = AsyncMock(return_value={"BTC_LONG": {"qty": "2"}})
    verdict = await _reconcile_funding_leg(connector, halt, "BTC_LONG", _paper_state(),
                                           "BTC", False, LOGGER)
    assert verdict == "reissue"


def test_apply_clean_slate_resets_state():
    from futures_portfolio.core.main import _apply_clean_slate
    state = {"tpv_ath": 5, "virt_qty": 1, "virt_debt": 2, "rebalance_cycles": 9,
             "initial_tpv": 1, "reference_tpv": 1, "share_trail_receipts": ["x"]}
    paper = {"balance": 123.4, "positions": {"BTC_LONG": 2.0}, "profit_budget": 1}
    _apply_clean_slate(state, paper, "BTC", 250.0, LOGGER)
    assert paper["balance"] == 250.0
    assert paper["positions"] == {"BTC_LONG": 0.0, "BTC_SHORT": 0.0}
    assert state["rebalance_cycles"] == 0
    assert state["virt_debt"] == 0.0
    assert paper.get("profit_budget") is None
