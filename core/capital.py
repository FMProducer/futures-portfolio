"""Keep external account margin outside trading capital and portfolio shares."""
import copy
import math


def capital_with_reserve(portfolio, trading_capital):
    reserve = portfolio.get("cash_reserve_usdt", 0)
    if (type(reserve) not in (int, float) or not math.isfinite(reserve) or reserve < 0
            or type(trading_capital) not in (int, float) or not math.isfinite(trading_capital)
            or trading_capital <= 0):
        raise ValueError("Trading capital must be positive and CASH reserve finite and nonnegative")
    targets = copy.deepcopy(portfolio["targets"])
    total = trading_capital
    if not math.isfinite(total):
        raise ValueError("Total capital must be finite")
    if reserve:
        for target in targets.values():
            share = target["share"]
            if type(share) not in (int, float) or not math.isfinite(share) or share < 0:
                raise ValueError("Target shares must be finite and nonnegative")
        if sum(t["share"] for t in portfolio["targets"].values()) > 1 + 1e-12:
            raise ValueError("Trading shares must not exceed one with a CASH reserve")
    return total, targets
