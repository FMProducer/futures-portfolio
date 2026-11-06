"""Runtime decision rules matching the 01e4df4 minute-bar backtest contract."""

import math
from decimal import Decimal


def filter_actions(actions, prices, config, portfolio, memory, initial_build=False, flat=False):
    i = len(prices) - 1
    safety = portfolio.get("safety_guards", {})
    trend = config.get("trend_guard", {})
    window = int(safety.get("trend_lookback_bars", 30))
    if window <= 0 or not prices or any(not math.isfinite(p) or p <= 0 for p in prices):
        raise ValueError("Guard history requires positive finite prices and lookback")
    if actions and i >= window:
        sample = prices[max(0, i - window):i + 1]
        move = abs(sample[-1] - sample[0])
        path = sum(abs(b - a) for a, b in zip(sample, sample[1:]))
        efficiency = move / path if path else 0
        minimum = trend.get("min_move_pct", safety.get("trend_min_move_pct", .5)) / 100
        limit = trend.get("eff_threshold", safety.get("trend_eff_threshold", .85))
        if move / sample[0] > minimum and efficiency > limit:
            actions = []
        net_window = max(1, trend.get("net_move_window_sec", safety.get("net_move_window_sec", 30)) // 60)
        net_limit = trend.get("net_move_block_pct", safety.get("net_move_block_pct", 1.5)) / 100
        if actions and i >= net_window and net_limit > 0:
            if abs(prices[i] - prices[i - int(net_window)]) / prices[i - int(net_window)] > net_limit:
                actions = []
    cooldown = memory.get("velocity_cooldown_bars", 0)
    velocity_window = max(1, safety.get("velocity_window_sec", 60) // 60)
    if actions and cooldown > 0:
        memory["velocity_cooldown_bars"] = cooldown - 1
        actions = []
    elif actions and i >= velocity_window:
        old = prices[i - int(velocity_window)]
        if abs(prices[i] - old) / old > safety.get("max_price_velocity_pct", 2.) / 100:
            actions = []
            memory["velocity_cooldown_bars"] = velocity_window
    addition = portfolio.get("trend_add_block") or {}
    threshold, window = addition.get("pct"), int(addition.get("window_bars", 120))
    if actions and threshold and i >= window:
        move = (prices[i] - prices[i - window]) / prices[i - window]
        if abs(move) >= float(threshold):
            opposite = "BASE_SHORT" if move > 0 else "BASE_LONG"
            blocked = [a for a in actions if a.get("key") == opposite and not a.get("is_reduction", False)]
            if blocked:
                actions = [a for a in actions if a not in blocked]
                if initial_build and flat:
                    actions = [a for a in actions if a["key"] == "VIRTUAL"]
    return actions


def maxdd_reached(equity, peak, initial_capital, limit):
    from core.capital import drawdown_threshold
    if not math.isfinite(equity):
        raise ValueError("Drawdown equity must be finite")
    peak = max(peak, equity)
    return equity <= drawdown_threshold(peak, initial_capital, limit), peak


def closed_minutes(rows, now_ms):
    boundary = int(now_ms) // 60000 * 60000
    bars = [(int(row[0]), float(row[4])) for row in rows if int(row[0]) < boundary]
    if (not bars or bars != sorted(bars) or len({t for t, _ in bars}) != len(bars)
            or any(t % 60000 or not math.isfinite(p) or p <= 0 for t, p in bars)
            or any(b[0] - a[0] != 60000 for a, b in zip(bars, bars[1:]))
            or bars[-1][0] != boundary - 60000):
        raise ValueError("Strategy requires contiguous, current, closed minute candles")
    return bars


async def strategy_minutes(connector, ticker, state, config, portfolio, now_ms, wallet=None, paper_mode=True):
    safety, addition = portfolio.get('safety_guards', {}), portfolio.get('trend_add_block') or {}
    trend = config.get('trend_guard', {})
    lookback = max(int(safety.get('trend_lookback_bars', 30)), int(addition.get('window_bars', 120)),
                   int(trend.get('net_move_window_sec', safety.get('net_move_window_sec', 30)) // 60),
                   int(safety.get('velocity_window_sec', 60) // 60)) + 2
    if not 2 <= lookback <= 1499:
        raise ValueError('Strategy lookback exceeds the closed-candle request bound')
    bars = closed_minutes(await connector.get_futures_klines(ticker, '1m', lookback + 1), now_ms)
    previous = state.get('strategy_bar_ms')
    if previous is not None and bars[-1][0] <= previous:
        return []
    if previous is not None and bars[-1][0] != previous + 60000:
        clean = (wallet is not None and isinstance(wallet.get('positions'), dict)
                 and {f'{ticker}_LONG', f'{ticker}_SHORT'}.issubset(wallet['positions'])
                 and state.get('rebalance_cycles') == 0
                 and Decimal(str(state.get('virt_debt', 0))) == 0
                 and not state.get('initial_build_pending') and not state.get('rebalance_inflight')
                 and not state.get('startup_blocked') and not state.get('trailing_stop_triggered')
                 and not (state.get('funding_halt') or {}).get('active')
                 and all(Decimal(str(q)) == 0 for q in wallet.get('positions', {}).values())
                 and all(Decimal(str(wallet.get(f'{leg}_entry_price', 0))) == 0 for leg in ('long', 'short')))
        if clean and not paper_mode:
            actual = await connector.get_positions()
            clean = all(Decimal(str(p.get('qty', 'NaN'))) == 0
                        for key, p in actual.items() if key.split('_')[0] == ticker)
        if not clean:
            raise ValueError('Missed strategy bars require reconciliation before trading')
    origin = state.get('strategy_origin_ms', bars[-1][0])
    return [(t, p) for t, p in bars if t >= origin]


def paper_legacy_funding(wallet, state, targets, ticker, price, bar_ms):
    if state.get('legacy_funding_bar_ms', -1) >= bar_ms:
        return Decimal('0')
    value = Decimal(str(price))
    notional = Decimal('0')
    for leg in ('LONG', 'SHORT'):
        qty = abs(Decimal(str(wallet['positions'].get(f'{ticker}_{leg}', 0))))
        notional += qty * value
    amount = notional * Decimal('.0002') / 1440
    state['legacy_funding_bar_ms'] = bar_ms
    return amount
