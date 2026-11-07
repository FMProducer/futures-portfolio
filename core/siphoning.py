"""VIRTUAL-only profit withdrawals with an immutable capital basis."""

from decimal import Decimal


def validate_config(threshold_pct, virtual_share, reinvestment_ratio=0, siphoning_pct=None):
    threshold, share, ratio = map(Decimal, map(str, (threshold_pct, virtual_share, reinvestment_ratio or 0)))
    if (not threshold.is_finite() or not share.is_finite()
            or not 0 <= share <= 1 or not 0 <= threshold <= share * 100):
        raise ValueError("siphoning_threshold_pct must be finite, nonnegative and <= VIRTUAL share * 100")
    if not ratio.is_finite() or not 0 <= ratio <= 1:
        raise ValueError("reinvestment_ratio must be finite and between 0 and 1")
    if siphoning_pct is not None:
        pct = Decimal(str(siphoning_pct))
        if not pct.is_finite() or pct < 0:
            raise ValueError("siphoning_pct must be finite and nonnegative")


def calculate_amount(active_equity, initial_capital, virtual_available, threshold_pct, reinvestment_ratio=0,
                     siphoning_pct=None):
    """Positive pct sets a fixed withdrawal; zero or None uses reinvestment_ratio."""
    equity, base, virtual, threshold, ratio = map(Decimal, map(str, (
        active_equity, initial_capital, virtual_available, threshold_pct, reinvestment_ratio or 0)))
    if not all(v.is_finite() for v in (equity, base, virtual, threshold, ratio)) or base <= 0:
        raise ValueError("Siphoning amounts must be finite and initial capital positive")
    if threshold == 0:
        return Decimal(0)
    profit = equity - base
    if profit < max(Decimal('0.1'), base * threshold / 100):
        return Decimal(0)
    amount = profit * (1-ratio)
    if siphoning_pct is not None:
        pct = Decimal(str(siphoning_pct))
        if not pct.is_finite() or pct < 0:
            raise ValueError("siphoning_pct must be finite and nonnegative")
        if pct > 0:
            amount = min(profit, base * pct / 100)
    return min(max(Decimal(0), amount), max(Decimal(0), virtual))


def credit_reserve(config, mode, ticker, state):
    """Credit a closed session once; persist config and receipt atomically together."""
    if mode not in ("paper", "real") or state.get("stop_reason") != "siphon":
        raise ValueError("Invalid siphon credit mode or reason")
    amount = Decimal(str(state["siphon_amount"]))
    available = Decimal(str(state.get("siphoning_reserve", 0)))
    if (not amount.is_finite() or not available.is_finite() or not 0 < amount <= available
            or not state.get("started_at")):
        raise ValueError("Invalid siphon credit amount or session")
    identity = f"{mode}:{ticker}:{state['started_at']}:{state['rebalance_cycles']}"
    credits = config.get("siphoning_credits", {})
    if identity in credits:
        if Decimal(str(credits[identity])) != amount:
            raise ValueError("Siphon credit receipt mismatch")
        return False
    portfolio = config["portfolios"][0]
    key = "paper_account_free_margin" if mode == "paper" else "cash_reserve_usdt"
    reserve = Decimal(str(portfolio.get(key, 0)))
    if not reserve.is_finite() or reserve < 0:
        raise ValueError("Invalid external margin reserve")
    from core.funding import json_decimal
    portfolio[key] = json_decimal(reserve + amount)
    config.setdefault("siphoning_credits", {})[identity] = str(amount)
    return True


def funded_reserve_change(previous, current):
    """Accept reserve reloads only when new REAL credit receipts fund the increase."""
    old, new = previous.get("siphoning_credits", {}), current.get("siphoning_credits", {})
    if any(new.get(key) != value for key, value in old.items()):
        return False
    amounts = [Decimal(str(value)) for key,value in new.items() if key.startswith("real:") and key not in old]
    old_funding = previous.get("funding_reserve_receipts", {})
    new_funding = current.get("funding_reserve_receipts", {})
    if "funding_reserve_totals" not in current and any(new_funding.get(key) != value for key, value in old_funding.items()):
        return False
    funding_delta = sum((Decimal(value["settled"]) for key, value in new_funding.items()
                         if key not in old_funding and value["reserve_key"] == "cash_reserve_usdt"), Decimal(0))
    if "funding_reserve_totals" in current:
        previous_total = Decimal(previous.get("funding_reserve_totals", {}).get("cash_reserve_usdt", "0"))
        if old_funding:
            previous_total = sum((Decimal(r["settled"]) for r in old_funding.values()
                                  if r["reserve_key"] == "cash_reserve_usdt"), Decimal(0))
        funding_delta = Decimal(current["funding_reserve_totals"].get("cash_reserve_usdt", "0")) - previous_total
    before = Decimal(str(previous["portfolios"][0].get("cash_reserve_usdt", 0)))
    after = Decimal(str(current["portfolios"][0].get("cash_reserve_usdt", 0)))
    return (all(v.is_finite() and v > 0 for v in amounts) and before.is_finite() and after.is_finite()
            and after >= 0 and funding_delta.is_finite()
            and abs(after-before-sum(amounts)-funding_delta) <= Decimal('1e-8'))
