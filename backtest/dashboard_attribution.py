"""Reconstruct per-bar PnL attribution from the backtest fill log.

The engine records execution PnL (entry/exit prices already include slippage).
The dashboard reconciliation separates mark-to-mid PnL, slippage, commissions,
funding and siphoning, so the same fills are replayed here without touching the
trading logic. Observation only.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Set


def reconstruct_attribution(
    closes: Sequence[float],
    equity: Sequence[float],
    fills: Iterable[Dict[str, Any]],
    *,
    initial_capital: float,
    final_equity: Optional[float] = None,
    funding_rate_per_bar: float = 0.0,
    funding_model: str = "legacy_constant",
    events: Optional[Iterable[Dict[str, Any]]] = None,
    siphoning_log: Optional[Iterable[Dict[str, Any]]] = None,
    sample_indices: Optional[Set[int]] = None,
) -> Dict[str, Any]:
    closes = list(closes)
    equity = list(equity)
    n = min(len(closes), len(equity))
    final_equity = float(equity[-1]) if final_equity is None and n else float(final_equity or 0.0)

    fill_bars: Dict[int, List[Dict[str, Any]]] = {}
    for fill in fills:
        bar = int(fill.get("bar", 0))
        if 0 <= bar < n:
            fill_bars.setdefault(bar, []).append(fill)

    cooldown_starts: Dict[int, Dict[str, Any]] = {}
    cooldown_ends: Dict[int, Dict[str, Any]] = {}
    for event in events or []:
        bar = int(event.get("bar", -1))
        kind = event.get("kind")
        if kind == "cooldown_start":
            cooldown_starts[bar] = event
        elif kind == "cooldown_end":
            cooldown_ends[bar] = event

    siphon_bars: Dict[int, float] = {}
    for entry in siphoning_log or []:
        bar = int(entry.get("bar", -1))
        siphon_bars[bar] = siphon_bars.get(bar, 0.0) + float(entry.get("amount", 0.0))

    # B1: use actual funding events from fills (trigger="funding_shortfall").
    # Also consume "funding" log-events for per-bar breakdown.
    funding_by_bar: Dict[int, float] = {}
    for event in events or []:
        if event.get("kind") == "funding":
            bar = int(event.get("bar", -1))
            # Prefer numeric funding_amount (full precision); fall back to detail text.
            if "funding_amount" in event:
                amt = float(event["funding_amount"])
            else:
                detail = event.get("detail", "")
                if "amount=" in detail:
                    try:
                        amt = float(detail.split("amount=")[1].split()[0])
                    except (ValueError, IndexError):
                        continue
                else:
                    continue
            # signed_amount: negative = debit (paid).  funding_paid = -signed_amount.
            # Attribution tracks cost: cost = funding_paid = -signed_amount.
            funding_by_bar[bar] = funding_by_bar.get(bar, 0.0) + (-amt)

    pos_long = pos_short = 0.0
    entry_mid_long = entry_mid_short = 0.0
    realized = commissions = funding = slippage = reserve = 0.0
    unmatched_close_qty = 0.0
    open_slippage = 0.0

    series = {
        "idx": [],
        "equity_pnl": [],
        "realized": [],
        "unrealized": [],
        "commissions": [],
        "funding": [],
        "slippage": [],
        "reserve": [],
        "net": [],
        "residual": [],
    }
    sample_indices = set(int(i) for i in (sample_indices or set(range(n))))
    cooldown_until = 0

    def _open_long(qty: float, mid: float) -> None:
        nonlocal pos_long, entry_mid_long
        total = pos_long + qty
        entry_mid_long = ((entry_mid_long * pos_long) + (mid * qty)) / total if total > 0 else mid
        pos_long = total

    def _close_long(qty: float, mid: float) -> float:
        nonlocal pos_long, realized, unmatched_close_qty
        original = qty
        qty = min(qty, pos_long)
        unmatched_close_qty += max(0.0, original - pos_long)
        if qty <= 0:
            return 0.0
        pnl = qty * (mid - entry_mid_long)
        pos_long -= qty
        realized += pnl
        return pnl

    def _open_short(qty: float, mid: float) -> None:
        nonlocal pos_short, entry_mid_short
        total = pos_short + qty
        entry_mid_short = ((entry_mid_short * pos_short) + (mid * qty)) / total if total > 0 else mid
        pos_short = total

    def _close_short(qty: float, mid: float) -> float:
        nonlocal pos_short, realized, unmatched_close_qty
        original = qty
        qty = min(qty, pos_short)
        unmatched_close_qty += max(0.0, original - pos_short)
        if qty <= 0:
            return 0.0
        pnl = qty * (entry_mid_short - mid)
        pos_short -= qty
        realized += pnl
        return pnl

    for i in range(n):
        in_cooldown = cooldown_until > i
        if cooldown_until and i >= cooldown_until:
            in_cooldown = False
            cooldown_until = 0

        if not in_cooldown:
            for fill in fill_bars.get(i, []):
                qty = abs(float(fill.get("qty", 0.0) or 0.0))
                price = float(fill.get("price", 0.0) or 0.0)
                mid = float(fill.get("mid", price) or price)
                leg = str(fill.get("leg", ""))
                side = str(fill.get("side", "")).upper()
                commissions += abs(float(fill.get("commission", 0.0) or 0.0))
                if qty > 0:
                    slip = qty * abs(price - mid)
                    slippage += slip
                    open_slippage += slip
                if leg == "BASE_LONG":
                    if side == "BUY":
                        _open_long(qty, mid)
                    elif side == "SELL":
                        _close_long(qty, mid)
                elif leg == "BASE_SHORT":
                    if side == "SELL":
                        _open_short(qty, mid)
                    elif side == "BUY":
                        _close_short(qty, mid)

        reserve += siphon_bars.get(i, 0.0)

        if funding_model == "signed_actual":
            if i in funding_by_bar:
                funding += funding_by_bar[i]
        elif not in_cooldown and funding_rate_per_bar > 0 and (pos_long > 0 or pos_short > 0):
            funding += (pos_long + pos_short) * closes[i] * funding_rate_per_bar

        if i in cooldown_starts:
            event = cooldown_starts[i]
            # The engine freezes TPV at the cooldown start; mark abandoned
            # positions to the same close so attribution matches that equity.
            if pos_long > 0:
                realized += pos_long * (closes[i] - entry_mid_long)
                pos_long = 0.0
                entry_mid_long = 0.0
            if pos_short > 0:
                realized += pos_short * (entry_mid_short - closes[i])
                pos_short = 0.0
                entry_mid_short = 0.0
            cooldown_until = int(event.get("until_bar", n + 1) or (n + 1))

        if i in cooldown_ends:
            reserve -= float(cooldown_ends[i].get("reserve_reset", 0.0) or 0.0)

        unrealized = pos_long * (closes[i] - entry_mid_long) + pos_short * (entry_mid_short - closes[i])
        net = realized + unrealized - commissions - funding - slippage - reserve
        if i in sample_indices:
            equity_pnl = equity[i] - initial_capital
            series["idx"].append(i)
            series["equity_pnl"].append(equity_pnl)
            series["realized"].append(realized)
            series["unrealized"].append(unrealized)
            series["commissions"].append(commissions)
            series["funding"].append(funding)
            series["slippage"].append(slippage)
            series["reserve"].append(reserve)
            series["net"].append(net)
            series["residual"].append(equity_pnl - net)

    final_unrealized = pos_long * (closes[n - 1] - entry_mid_long) + pos_short * (entry_mid_short - closes[n - 1]) if n else 0.0
    net_equity_pnl = final_equity - initial_capital
    residual = net_equity_pnl - (realized + final_unrealized - commissions - funding - slippage - reserve)
    return {
        "totals": {
            "initial_capital": float(initial_capital),
            "final_equity": float(final_equity),
            "net_equity_pnl": float(net_equity_pnl),
            "realized": float(realized),
            "unrealized": float(final_unrealized),
            "commissions": float(commissions),
            "funding": float(funding),
            "slippage": float(slippage),
            "reserve": float(reserve),
            "residual": float(residual),
        },
        "series": series,
        "checks": {
            "open_long": float(pos_long),
            "open_short": float(pos_short),
            "entry_mid_long": float(entry_mid_long),
            "entry_mid_short": float(entry_mid_short),
            "unmatched_close_qty": float(unmatched_close_qty),
            "fill_slippage": float(open_slippage),
        },
    }
