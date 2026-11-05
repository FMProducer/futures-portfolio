"""Deterministic PAPER closing receipts; no exchange calls."""

from copy import deepcopy
from decimal import Decimal


def close_wallet(wallet, ticker, price, event_id, fee_rate, slippage_rate=0):
    price, rate, slippage = map(lambda value: Decimal(str(value)), (price, fee_rate, slippage_rate))
    if (not price.is_finite() or price <= 0 or not rate.is_finite() or rate < 0
            or not slippage.is_finite() or not 0 <= slippage < 1):
        raise ValueError("Invalid PAPER close price or fee")
    result = deepcopy(wallet)
    balance = Decimal(str(result["balance"]))
    if not balance.is_finite():
        raise ValueError("Invalid PAPER wallet")
    receipts = result.setdefault("share_fill_receipts", {})
    for leg in ("LONG", "SHORT"):
        symbol = f"{ticker}_{leg}"
        qty = Decimal(str(result.get("positions", {}).get(symbol, 0)))
        if not qty.is_finite() or qty < 0:
            raise ValueError("Invalid PAPER hedge quantity")
        if not qty:
            continue
        key = f"paper-close:{event_id}:{leg}"
        if key in receipts:
            raise ValueError("Close receipt exists but position is still open")
        entry = Decimal(str(result.get(f"{leg.lower()}_entry_price", 0)))
        if not entry.is_finite() or entry <= 0:
            raise ValueError("PAPER close requires a positive entry")
        fill_price = price * (1 - slippage if leg == "LONG" else 1 + slippage)
        pnl = qty * (fill_price - entry) * (1 if leg == "LONG" else -1)
        fee = qty * fill_price * rate
        balance = balance + pnl - fee
        receipts[key] = {"symbol": symbol, "type": leg,
                         "side": "SELL" if leg == "LONG" else "BUY",
                         "qty": str(qty), "price": str(fill_price), "commission": str(fee),
                         "trade_pnl": str(pnl), "reduce_only": "True", "event_id": event_id}
        result["positions"][symbol] = 0.0
        result[f"{leg.lower()}_entry_price"] = 0.0
        result[f"{leg.lower()}_liquidation_price"] = 0.0
    result["balance"] = float(balance)
    result["last_price"] = float(price)
    return result
