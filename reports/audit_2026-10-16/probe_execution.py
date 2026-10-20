"""Reproduce order-lifecycle defects with fake accepted orders; never use a broker."""
import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import requests

OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(OUT / "_sandbox" / "futures_portfolio"))
from core.connector import BinanceConnector
from core.executor import PortfolioExecutor


async def main():
    calls = []
    def accept_then_lose_ack(**kw):
        calls.append(kw)
        if len(calls) == 1:
            raise requests.exceptions.ReadTimeout("fixture: order accepted, response lost")
        return dict(orderId=2)
    connector = BinanceConnector("fixture-key", "fixture-secret")
    connector.futures_client = SimpleNamespace(futures_create_order=accept_then_lose_ack)
    with patch("asyncio.sleep", new=AsyncMock()):
        result = await connector.place_limit_order("BTCUSDT", "BUY", 1, 100, position_side="LONG")
    out = dict(limit_ack_loss=dict(result=result, accepted_order_calls=list(calls),
                                   stable_client_id_present=any("newClientOrderId" in x for x in calls)))
    fake = SimpleNamespace(get_order_book=AsyncMock(return_value=dict(bids=[["99", "1"]], asks=[["101", "1"]])),
        place_limit_maker_order=AsyncMock(return_value=dict(orderId=1)),
        get_order_status=AsyncMock(return_value=dict(status="PARTIALLY_FILLED", executedQty="0.2", avgPrice="100")),
        cancel_order=AsyncMock(return_value=dict(status="FILLED", executedQty="1", avgPrice="100")),
        get_order_trades=AsyncMock(return_value=[]))
    executor = PortfolioExecutor(fake)
    executor.execute_market_order = AsyncMock(return_value=dict(status="SUCCESS", executed_qty=Decimal("0.8"), realized_pnl="0", commission="0"))
    with patch("asyncio.sleep", new=AsyncMock()):
        result = await executor.execute_limit_with_fallback("BTCUSDT", "1", "BUY", position_side="LONG", timeout_sec=1, price="100")
    out["cancel_fill_race"] = dict(result=result, actual_final_limit_fill="1", requested_market_qty=str(executor.execute_market_order.call_args.kwargs["qty"]), actual_total="1.8")
    calls.clear()
    connector.futures_client = SimpleNamespace(futures_create_order=lambda **kw: (_ for _ in ()).throw(requests.exceptions.ReadTimeout("fixture: uncertain")))
    result = await PortfolioExecutor(connector).execute_market_order("BTCUSDT", "1", "BUY", price="100")
    out["market_ack_loss"] = result
    (OUT / "execution_results.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(json.dumps(out, indent=2, default=str))


asyncio.run(main())
