"""
Virtual 5m Portfolio Indicator for HTF Share Bias Experiment.

This module implements a separate virtual 5m portfolio that:
- Starts with 25/25/50 allocation (LONG/SHORT/VIRTUAL)
- Revalues LONG/SHORT legs every 5 minutes using 1m bar closes
- VIRTUAL leg maintains fixed USDT value (no revaluation)
- Measures actual shares at each 5m boundary BEFORE reset
- Applies step-changes to 1m bot targets based on share skew
- Resets to 25/25/50 after measurement (preserving total value)

The indicator runs entirely offline inside the backtest, with no connection
to the main 1m bot's trades, commissions, funding, guards, or trailing.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_EVEN, getcontext
from typing import Dict, List, Optional, Tuple

# Local copy of apply_share_bias to avoid circular import
def apply_share_bias(targets: Dict[str, Dict], bias_pp: float) -> Dict[str, Dict]:
    """Shift target weight between the futures legs by bias_pp percentage points.

    bias_pp == 0 returns the same object so the baseline path is unchanged.
    LONG + SHORT is preserved (clamped into [0, LONG+SHORT]); VIRTUAL and the
    residual CASH weight are untouched — this moves targets, never positions.
    """
    if bias_pp == 0:
        return targets
    out = copy.deepcopy(targets)
    delta = Decimal(str(bias_pp)) / Decimal('100')
    long_share = Decimal(str(out["BASE_LONG"]["share"]))
    short_share = Decimal(str(out["BASE_SHORT"]["share"]))
    total = long_share + short_share
    long_new = long_share + delta
    if long_new < 0:
        long_new = Decimal('0')
    elif long_new > total:
        long_new = total
    out["BASE_LONG"]["share"] = float(long_new)
    out["BASE_SHORT"]["share"] = float(total - long_new)
    return out


# 5m Virtual Portfolio initial targets (LONG, SHORT, VIRTUAL)
# Keys match main bot targets for compatibility with apply_share_bias
INITIAL_5M_TARGETS = {
    "BASE_LONG": {"share": Decimal('0.25'), "leverage": Decimal('3')},
    "BASE_SHORT": {"share": Decimal('0.25'), "leverage": Decimal('3')},
    "VIRTUAL": {"share": Decimal('0.50'), "leverage": Decimal('1')},
}


@dataclass
class Virtual5MPortfolio:
    """
    Virtual 5m portfolio that revalues independently of the 1m bot.

    Capital allocation:
    - Total capital is fixed at initialization (not reset to initial each period)
    - LONG/SHORT: sized by target share * total_capital / leverage
    - VIRTUAL: fixed USDT value = target share * total_capital
    - Position entry prices updated on each reset
    """
    total_capital: Decimal
    long_entry_price: Decimal = Decimal('0')
    short_entry_price: Decimal = Decimal('0')
    long_qty: Decimal = Decimal('0')
    short_qty: Decimal = Decimal('0')
    virtual_value: Decimal = Decimal('0')

    # History for diagnostic logging
    events: List[Dict] = field(default_factory=list)

    def __post_init__(self):
        if self.virtual_value == Decimal('0'):
            self.virtual_value = INITIAL_5M_TARGETS["VIRTUAL"]["share"] * self.total_capital

    def initialize(self, price: Decimal) -> None:
        """Initialize positions at given price (first 5m boundary)."""
        self.long_entry_price = price
        self.short_entry_price = price
        long_margin = INITIAL_5M_TARGETS["BASE_LONG"]["share"] * self.total_capital
        short_margin = INITIAL_5M_TARGETS["BASE_SHORT"]["share"] * self.total_capital
        self.long_qty = long_margin * INITIAL_5M_TARGETS["BASE_LONG"]["leverage"] / price
        self.short_qty = short_margin * INITIAL_5M_TARGETS["BASE_SHORT"]["leverage"] / price

    def revalue(self, price: Decimal) -> Dict[str, Decimal]:
        """
        Revalue LONG/SHORT legs at current price.
        VIRTUAL value stays fixed in USDT.
        Returns dict with leg values and total.
        """
        # LONG leg value = margin + unrealized PnL
        long_margin = self.long_qty * self.long_entry_price / INITIAL_5M_TARGETS["BASE_LONG"]["leverage"]
        long_upnl = self.long_qty * (price - self.long_entry_price)
        long_value = long_margin + long_upnl

        # SHORT leg value = margin + unrealized PnL
        short_margin = self.short_qty * self.short_entry_price / INITIAL_5M_TARGETS["BASE_SHORT"]["leverage"]
        short_upnl = self.short_qty * (self.short_entry_price - price)
        short_value = short_margin + short_upnl

        # VIRTUAL value is fixed USDT
        virtual_value = self.virtual_value

        total_value = long_value + short_value + virtual_value

        return {
            "long": long_value,
            "short": short_value,
            "virtual": virtual_value,
            "total": total_value,
            "long_upnl": long_upnl,
            "short_upnl": short_upnl,
        }

    def get_shares(self, price: Decimal) -> Tuple[Decimal, Decimal, Decimal]:
        """Get actual shares (w_long, w_short, w_virtual) at current price."""
        vals = self.revalue(price)
        total = vals["total"]
        if total <= Decimal('0'):
            return Decimal('0'), Decimal('0'), Decimal('0')
        w_long = vals["long"] / total
        w_short = vals["short"] / total
        w_virtual = vals["virtual"] / total
        return w_long, w_short, w_virtual

    def measure_and_reset(self, price: Decimal, interval_idx: int, interval_start: int, interval_end: int) -> Dict:
        """
        Measure actual shares BEFORE reset, then reset to 25/25/50.

        Returns diagnostic dict with:
        - shares before reset
        - excess in percentage points
        - signal (LONG/SHORT/NEUTRAL) for each threshold variant
        - targets after applying signal
        - state after reset
        """
        # Measure BEFORE reset
        w_long, w_short, w_virtual = self.get_shares(price)
        vals = self.revalue(price)

        excess_long_pp = (w_long - Decimal('0.25')) * Decimal('100')
        excess_short_pp = (w_short - Decimal('0.25')) * Decimal('100')

        # Calculate signals for each variant
        signals = {}
        targets_by_variant = {}

        for threshold_pp, variant_name in [(Decimal('1'), "1pp"), (Decimal('2'), "2pp")]:
            if excess_long_pp >= threshold_pp:
                signal = "LONG"
                bias_pp = float(threshold_pp)
            elif excess_short_pp >= threshold_pp:
                signal = "SHORT"
                bias_pp = float(-threshold_pp)
            else:
                signal = "NEUTRAL"
                bias_pp = 0.0

            signals[variant_name] = signal
            # Apply bias to base 25/25/50 targets
            biased = apply_share_bias(INITIAL_5M_TARGETS, bias_pp)
            targets_by_variant[variant_name] = {
                "LONG": float(biased["BASE_LONG"]["share"]),
                "SHORT": float(biased["BASE_SHORT"]["share"]),
                "VIRTUAL": float(biased["VIRTUAL"]["share"]),
            }

        # Baseline (no bias)
        targets_by_variant["baseline"] = {
            "LONG": float(INITIAL_5M_TARGETS["BASE_LONG"]["share"]),
            "SHORT": float(INITIAL_5M_TARGETS["BASE_SHORT"]["share"]),
            "VIRTUAL": float(INITIAL_5M_TARGETS["VIRTUAL"]["share"]),
        }

        # Record event
        event = {
            "interval_idx": interval_idx,
            "interval_start_bar": interval_start,
            "interval_end_bar": interval_end,
            "price": float(price),
            "long_value": float(vals["long"]),
            "short_value": float(vals["short"]),
            "virtual_value": float(vals["virtual"]),
            "total_value": float(vals["total"]),
            "w_long": float(w_long),
            "w_short": float(w_short),
            "w_virtual": float(w_virtual),
            "excess_long_pp": float(excess_long_pp),
            "excess_short_pp": float(excess_short_pp),
            "signals": signals,
            "targets": targets_by_variant,
        }
        self.events.append(event)

        # Reset to 25/25/50 preserving total value
        total = vals["total"]
        self.virtual_value = INITIAL_5M_TARGETS["VIRTUAL"]["share"] * total
        long_margin = INITIAL_5M_TARGETS["BASE_LONG"]["share"] * total
        short_margin = INITIAL_5M_TARGETS["BASE_SHORT"]["share"] * total
        self.long_entry_price = price
        self.short_entry_price = price
        self.long_qty = long_margin * INITIAL_5M_TARGETS["BASE_LONG"]["leverage"] / price
        self.short_qty = short_margin * INITIAL_5M_TARGETS["BASE_SHORT"]["leverage"] / price

        event["after_reset"] = {
            "long_qty": float(self.long_qty),
            "short_qty": float(self.short_qty),
            "long_entry": float(self.long_entry_price),
            "short_entry": float(self.short_entry_price),
            "virtual_value": float(self.virtual_value),
            "total_value": float(total),
        }

        return event

    def get_current_targets(self, variant: str) -> Dict[str, float]:
        """Get current targets for a variant (baseline, 1pp, 2pp)."""
        baseline = {
            "LONG": float(INITIAL_5M_TARGETS["BASE_LONG"]["share"]),
            "SHORT": float(INITIAL_5M_TARGETS["BASE_SHORT"]["share"]),
            "VIRTUAL": float(INITIAL_5M_TARGETS["VIRTUAL"]["share"]),
        }
        if variant == "baseline":
            return baseline
        # The latest event has the current targets
        if self.events:
            return self.events[-1]["targets"].get(variant, baseline)
        return baseline


class HTF5MIndicator:
    """
    Higher Timeframe 5m Share Bias Indicator.

    Manages the virtual 5m portfolio and produces target adjustments
    for the 1m bot at each 5m boundary.
    """

    def __init__(self, total_capital: float = 1000.0):
        self.portfolio = Virtual5MPortfolio(Decimal(str(total_capital)))
        self.initialized = False
        self.bars_since_5m_boundary = 0
        self.current_5m_interval = 0

    def initialize(self, price: float) -> None:
        """Initialize at first bar (before any 5m interval completes)."""
        self.portfolio.initialize(Decimal(str(price)))
        self.initialized = True
        self.bars_since_5m_boundary = 0
        self.current_5m_interval = 0

    def on_bar(self, price: float, bar_idx: int, timestamp_ms: int) -> Optional[Dict]:
        """
        Process a 1m bar. Returns measurement event if 5m boundary reached.

        Bar timestamps are OPENS. A 5m interval [10:00, 10:05) contains
        bars 10:00, 10:01, 10:02, 10:03, 10:04. The boundary is at 10:05
        (timestamp % 300000 == 0), and the last price in the interval is
        the close of bar 10:04.

        Signal becomes available AT the boundary (after 10:04 close, at 10:05).
        It can be used for decisions starting from the NEXT bar (10:05).
        """
        if not self.initialized:
            self.initialize(price)
            return None

        self.bars_since_5m_boundary += 1

        # Check if this bar closes a 5m interval
        # timestamp is the OPEN of this bar. The interval ends at the next 5m boundary.
        # So we measure when (timestamp + 60000) % 300000 == 0
        # i.e., when this bar's close is at a 5m boundary
        next_boundary_ts = timestamp_ms + 60000
        is_5m_boundary = (next_boundary_ts % 300000) == 0

        if is_5m_boundary and self.bars_since_5m_boundary >= 5:
            # We have a complete 5m interval
            interval_start = bar_idx - self.bars_since_5m_boundary + 1
            interval_end = bar_idx

            event = self.portfolio.measure_and_reset(
                Decimal(str(price)),
                self.current_5m_interval,
                interval_start,
                interval_end
            )
            event["signal_available_at_bar"] = bar_idx + 1  # next bar
            event["signal_available_at_ts"] = next_boundary_ts

            self.current_5m_interval += 1
            self.bars_since_5m_boundary = 0
            return event

        return None

    def get_targets_for_variant(self, variant: str) -> Dict[str, float]:
        """Get current targets for 1m bot (baseline, 1pp, 2pp)."""
        return self.portfolio.get_current_targets(variant)

    def get_all_targets(self) -> Dict[str, Dict[str, float]]:
        """Get targets for all variants."""
        return {
            "baseline": self.get_targets_for_variant("baseline"),
            "1pp": self.get_targets_for_variant("1pp"),
            "2pp": self.get_targets_for_variant("2pp"),
        }


def run_htf_5m_experiment(
    close_prices: List[float],
    timestamps_ms: List[int],
    start_idx: int,
    end_idx: int,
    initial_capital: float = 1000.0,
) -> Dict:
    """
    Run the HTF 5m indicator over a range of bars.

    Returns diagnostic data for all variants.
    """
    indicator = HTF5MIndicator(initial_capital)

    # Initialize with first price
    indicator.initialize(close_prices[start_idx])

    events = []

    for i in range(start_idx, end_idx):
        event = indicator.on_bar(close_prices[i], i, timestamps_ms[i])
        if event:
            events.append(event)

    # Compile results per variant
    results = {"baseline": [], "1pp": [], "2pp": []}

    for event in events:
        for variant in results:
            targets = event["targets"].get(variant, event["targets"]["baseline"])
            results[variant].append({
                "bar": event["signal_available_at_bar"],
                "ts": event["signal_available_at_ts"],
                "targets": targets,
                "signal": event["signals"].get(variant, "NEUTRAL") if variant != "baseline" else "NEUTRAL",
            })

    return {
        "events": events,
        "targets_by_variant": results,
        "portfolio_final_state": {
            "long_qty": float(indicator.portfolio.long_qty),
            "short_qty": float(indicator.portfolio.short_qty),
            "virtual_value": float(indicator.portfolio.virtual_value),
        },
    }


def apply_variant_targets(base_targets: Dict[str, Dict], variant: str, indicator: HTF5MIndicator) -> Dict[str, Dict]:
    """
    Apply variant targets to base targets dict.

    base_targets is the main bot's targets (e.g., from config).
    variant is "baseline", "1pp", or "2pp".

    Returns new targets dict with LONG/SHORT adjusted, VIRTUAL unchanged.
    """
    if variant == "baseline":
        return copy.deepcopy(base_targets)

    variant_targets = indicator.get_targets_for_variant(variant)
    out = copy.deepcopy(base_targets)

    # Map 5m variant targets (LONG/SHORT/VIRTUAL) to main bot keys (BASE_LONG/BASE_SHORT/VIRTUAL)
    if "BASE_LONG" in out and "LONG" in variant_targets:
        out["BASE_LONG"]["share"] = variant_targets["LONG"]
    if "BASE_SHORT" in out and "SHORT" in variant_targets:
        out["BASE_SHORT"]["share"] = variant_targets["SHORT"]
    # VIRTUAL unchanged

    return out