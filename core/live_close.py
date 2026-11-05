"""Durable REAL close intents and confirmed exchange accounting."""

from copy import deepcopy
from decimal import Decimal
from uuid import uuid4

from core import profit_budget, share_trail
from core.executor import PortfolioExecutor
from core import funding


def reconcile_positions(wallet, actual, ticker):
    for leg in ('LONG', 'SHORT'):
        key = f'{ticker}_{leg}'
        shadow = Decimal(str(wallet['positions'].get(key, 0)))
        exchange = abs(Decimal(str(actual.get(key, {}).get('qty', 0))))
        if not shadow.is_finite() or shadow < 0 or not exchange.is_finite() or shadow != exchange:
            raise ValueError('REAL positions differ from confirmed wallet; reconciliation required')


async def close_live_wallet(connector, state, wallet, ticker, targets, step_sizes,
                            strategy_path, wallet_path, reason):
    if state.get("live_close", {}).get("status") == "pending":
        raise ValueError("Pending REAL close requires exchange reconciliation before resubmission")
    actual = await connector.get_positions()
    reconcile_positions(wallet, actual, ticker)
    orders = []
    for leg in ("LONG", "SHORT"):
        symbol = f"{ticker}_{leg}"
        qty = Decimal(str(wallet["positions"].get(symbol, 0)))
        exchange_qty = abs(Decimal(str(actual.get(symbol, {}).get("qty", 0))))
        if not qty.is_finite() or qty < 0 or not exchange_qty.is_finite() or qty != exchange_qty:
            raise ValueError("REAL close requires matching exchange and shadow quantities")
        if qty:
            orders.append({"type": "ORDER", "key": f"BASE_{leg}", "symbol": symbol,
                           "base_symbol": ticker, "position_side": leg, "is_reduction": True,
                           "qty": str(qty), "client_order_id": "close-" + uuid4().hex[:26]})
    if not orders:
        state.update(live_close={'status': 'complete', 'reason': reason, 'orders': []},
                     trailing_stop_triggered=True,
                     last_tpv=float(wallet['balance']) + float(state.get('siphoning_reserve', 0)))
        state['last_profit'] = state['last_tpv'] - float(state['initial_tpv'])
        state['total_pnl_pct'] = state['last_profit'] / float(state['initial_tpv']) * 100
        journal, halt = funding.load_funding_state(state)
        funding.save_funding_boundary(strategy_path, wallet_path, state, wallet, journal, halt)
        return
    state["startup_blocked"] = True
    state['trailing_stop_triggered'] = True
    state["live_close"] = {"status": "pending", "reason": reason, "orders": orders}

    def commit():
        journal, halt = funding.load_funding_state(state)
        funding.save_funding_boundary(strategy_path, wallet_path, state, wallet, journal, halt)

    commit()
    prices = await connector.get_mark_prices([ticker])
    price = Decimal(str(prices.get(ticker, 0)))
    if not price.is_finite() or price <= 0:
        raise ValueError("REAL close requires a valid current price")
    executor = PortfolioExecutor(connector)
    config = {"targets": targets, "limit_order_enabled": False, "min_notional_usdt": 0}
    for order in orders:
        action = dict(order, diff_usdt=-float(Decimal(order["qty"]) * price))
        result = await executor._execute_single_action(action, float(price), False, config,
                                                        step_sizes, wallet, {ticker: price})
        order["result"] = deepcopy(result)
        commit()
        if (result.get("status") not in ("SUCCESS", "SUCCESS_LIMIT", "SUCCESS_FALLBACK")
                or result.get("financials_confirmed") is not True or result.get("execution_pending")):
            raise ValueError("REAL close outcome is unconfirmed; reconciliation required")
        qty, pnl, fee = (Decimal(str(result.get(key, "NaN")))
                         for key in ("qty", "trade_pnl", "commission"))
        symbol, leg = order["symbol"], order["position_side"]
        expected = Decimal(order["qty"])
        if (not all(value.is_finite() for value in (qty, pnl, fee)) or not 0 < qty <= expected or fee < 0
                or result.get("symbol") != symbol or result.get("type") != leg
                or result.get("side") != ("SELL" if leg == "LONG" else "BUY")
                or result.get("reduce_only") is not True):
            raise ValueError("Invalid confirmed REAL close facts")
        updated = deepcopy(wallet)
        if not share_trail.fill_receipt(updated, result):
            raise ValueError("REAL close receipt already applied; reconcile before continuing")
        profit_budget.record_fill(updated, result, targets[f"BASE_{leg}"]["leverage"], require_confirmed=True)
        updated["balance"] = float(Decimal(str(updated["balance"])) + pnl - fee)
        updated["positions"][symbol] = float(expected - qty)
        if qty == expected:
            updated[f"{leg.lower()}_entry_price"] = 0.0
        share_trail.fill_receipt(updated, result, record=True)
        wallet.clear()
        wallet.update(updated)
        order["confirmed_qty"] = str(qty)
        commit()
        if qty != expected:
            raise ValueError("Partial REAL close recorded; residual position requires reconciliation")
    remaining = await connector.get_positions()
    if any(abs(Decimal(str(remaining.get(f"{ticker}_{leg}", {}).get("qty", 0)))) for leg in ("LONG", "SHORT")):
        raise ValueError("Exchange still reports a position after REAL close")
    state["live_close"]["status"] = "complete"
    state["startup_blocked"] = False
    state["last_tpv"] = float(wallet["balance"]) + float(state.get("siphoning_reserve", 0))
    state["last_profit"] = state["last_tpv"] - float(state["initial_tpv"])
    state['total_pnl_pct'] = state['last_profit'] / float(state['initial_tpv']) * 100
    commit()
