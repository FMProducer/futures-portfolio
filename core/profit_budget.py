"""Confirmed net sale profits fund additions; collateral and returned margin do not."""
from decimal import Decimal


def _amount(value):
    out = Decimal(str(value))
    if not out.is_finite():
        raise ValueError("Profit budget amounts must be finite")
    return out


def ledger(book):
    row = book.setdefault("profit_budget", {"balance": "0", "receipts": {}, "blocked": False})
    if (not isinstance(row, dict) or not isinstance(row.get("receipts"), dict)
            or not isinstance(row.get("blocked"), bool)):
        raise ValueError("Invalid persisted profit budget")
    _amount(row["balance"])
    return row


def balance(book):
    return _amount(ledger(book)["balance"])


def available(book):
    return Decimal('0') if ledger(book)["blocked"] else max(Decimal('0'), balance(book))


def charge(book, expense):
    expense = _amount(expense)
    if expense < 0:
        raise ValueError("Profit budget expense must be nonnegative")
    ledger(book)["balance"] = str(balance(book) - expense)


def record_fill(book, fill, leverage=1, initial_build=False, require_confirmed=False):
    if fill.get("status") not in ("SUCCESS", "SUCCESS_LIMIT", "SUCCESS_FALLBACK"):
        return False
    virtual = fill.get("type") == "VIRTUAL_ORDER"
    if not virtual and _amount(fill.get("qty", 0)) <= 0:
        return False
    identity = fill.get("execution_id")
    if not identity:
        raise ValueError("Confirmed profit-budget fill has no execution identity")
    fields = ("type", "symbol", "side", "qty", "price", "commission", "trade_pnl", "reduce_only", "diff_usdt")
    payload = {key: str(fill.get(key)) for key in fields}
    payload.update(leverage=str(leverage), initial_build=str(initial_build))
    row = ledger(book)
    old = row["receipts"].get(identity)
    if old is not None:
        if old != payload:
            raise ValueError("Changed confirmed fill requires profit-budget reconciliation")
        return False
    if fill.get("execution_pending") or (require_confirmed and fill.get("financials_confirmed") is not True):
        row["blocked"] = True
        row["reason"] = "unconfirmed_execution_financials"
        row["receipts"][identity] = payload
        return True
    if virtual:
        expense = max(Decimal('0'), _amount(fill["diff_usdt"])) if not initial_build else Decimal('0')
        change = -expense
    else:
        qty, price, fee, pnl, lev = map(_amount, (
            fill["qty"], fill["price"], fill["commission"], fill["trade_pnl"], leverage))
        if qty <= 0 or price <= 0 or fee < 0 or lev <= 0:
            raise ValueError("Invalid confirmed profit-budget fill")
        if fill.get("reduce_only"):
            expense = Decimal('0')
            change = pnl - fee
        else:
            expense = qty * price / lev + fee if not initial_build else Decimal('0')
            change = -expense if not initial_build else -fee
    if expense > available(book):
        row["blocked"] = True
        row["reason"] = "confirmed_cost_exceeded_profit_budget"
    row["balance"] = str(balance(book) + change)
    row["receipts"][identity] = payload
    return True
