import pandas as pd
import numpy as np
import numpy.typing as npt
from typing import Dict, List, Optional, Tuple, Any, Union
from dataclasses import dataclass, field
import asyncio
import aiohttp
import time
import json
import os
import sys
import math
import logging
import argparse
import traceback
from decimal import Decimal, ROUND_HALF_EVEN, InvalidOperation, getcontext

try:
    from core.calculator import PortfolioCalculator
    from core.funding import FundingJournal, apply_funding_backtest
    from core import share_trail
except ImportError:
    import sys
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from core.calculator import PortfolioCalculator
    from core.funding import FundingJournal, apply_funding_backtest
    from core import share_trail

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("Backtest")

getcontext().prec = 28
getcontext().rounding = ROUND_HALF_EVEN

class MarketOrderSlippageSimulator:
    """Симулятор рыночных ордеров с учетом проскальзывания и Taker-комиссии."""
    def __init__(self, commission_pct: float = 0.0004, slippage_pct: float = 0.0002):
        self.commission_pct = Decimal(str(commission_pct))
        self.slippage_pct = Decimal(str(slippage_pct))
        self.stats: Dict[str, int] = {"attempted": 0, "filled": 0}

    def execution_price(self, side: str, mid_price: Decimal) -> Decimal:
        dec_mid = Decimal(str(mid_price))
        if side.upper() == "BUY":
            return dec_mid * (Decimal('1') + self.slippage_pct)
        return dec_mid * (Decimal('1') - self.slippage_pct)

    def simulate_market_execution(self, side: str, qty: Decimal, mid_price: Decimal) -> Tuple[Decimal, Decimal]:
        self.stats["attempted"] += 1
        dec_qty = abs(Decimal(str(qty)))
        exec_price = self.execution_price(side, mid_price)
        commission = dec_qty * exec_price * self.commission_pct
        self.stats["filled"] += 1
        return exec_price, commission

    def get_summary(self) -> Dict[str, Any]:
        return self.stats

def quantize_qty(qty: Decimal, step_size: Decimal) -> Decimal:
    if step_size <= Decimal('0'):
        return qty
    try:
        remainder = qty % step_size
    except InvalidOperation:
        # runaway qty not representable at fixed precision: skip expansion instead of crashing the backtest
        return Decimal('0')
    return qty - remainder

def validate_notional(qty: Decimal, price: Decimal, min_notional: Decimal) -> bool:
    return abs(qty * price) >= min_notional

def action_side(key: str, is_reduction: bool) -> str:
    if key == "BASE_LONG":
        return "SELL" if is_reduction else "BUY"
    if key == "BASE_SHORT":
        return "BUY" if is_reduction else "SELL"
    return "RELEASE" if is_reduction else "DESIGNATE"

class AccountingMismatch(AssertionError):
    def __init__(self, details: Dict[str, Any]):
        self.details = details
        super().__init__("TPV accounting mismatch: " + json.dumps(details, sort_keys=True))

@dataclass
class BacktestState:
    """Управление состоянием портфеля и кросс-маржинального аккаунта."""
    initial_capital: Decimal
    base_ticker: str
    val_cash: Decimal
    account_free_margin: Decimal = Decimal('0')  # legacy zero; external collateral is excluded from A2a guard
    pos_long: Decimal = Decimal('0')
    pos_short: Decimal = Decimal('0')
    virt_qty: Decimal = Decimal('0')
    virt_debt: Decimal = Decimal('0')
    long_entry_price: Decimal = Decimal('0')
    short_entry_price: Decimal = Decimal('0')
    siphoning_reserve: Decimal = Decimal('0')
    cycles: int = 0
    skipped_expansions_counter: int = 0
    liquidations_counter: int = 0
    stops_counter: int = 0
    cooldown_until_bar: int = 0
    dormant_capital: Decimal = Decimal('0')
    
    tpv_ath: float = 0.0
    trailing_stop_violation_start: float = 0.0
    trailing_stop_triggered: bool = False
    last_rebalance_price: Decimal = Decimal('0')
    history: List[float] = field(default_factory=list)
    rebalance_log: List[Dict[str, Any]] = field(default_factory=list)
    fills: List[Dict[str, Any]] = field(default_factory=list)
    events: List[Dict[str, Any]] = field(default_factory=list)
    siphoning_log: List[Dict[str, Any]] = field(default_factory=list)
    fill_count: int = 0
    internal_transfer_count: int = 0
    commission_paid: Decimal = Decimal('0')
    realized_total: Decimal = Decimal('0')
    funding_paid: Decimal = Decimal('0')
    funding_from_virtual: Decimal = Decimal('0')
    slippage_paid: Decimal = Decimal('0')
    liquidation_loss_total: Decimal = Decimal('0')
    ledger_base: Optional[Decimal] = None
    accounting_error: Optional[Dict[str, Any]] = None
    cash_invariant_violation: Optional[Dict[str, Any]] = None
    initial_formation: Optional[Dict[str, Any]] = None
    initial_build_pending: Optional[Dict[str, Any]] = None

    # Leverage values from config (added to fix hardcoded 5x in TPV calc)
    l_lev: Decimal = Decimal('5')
    s_lev: Decimal = Decimal('5')

    # B1: funding model and halt state
    funding_model: str = "legacy_constant"  # signed_actual | legacy_constant
    funding_shortfall: Decimal = Decimal('0')
    funding_halt_active: bool = False
    funding_halt_event_id: str = ""
    funding_halt_at: float = 0.0

    # Two-sided share trailing (contract 2026-10-23); None when distance <= 0
    share_trail: Optional[Dict[str, Any]] = None
    share_trail_spec: Optional[Dict[str, Any]] = None
    share_trail_signals: int = 0
    share_trail_consumes: int = 0

    def margin_long(self) -> Decimal:
        return (self.pos_long * self.long_entry_price) / self.l_lev if self.pos_long > 0 else Decimal('0')

    def margin_short(self) -> Decimal:
        return (self.pos_short * self.short_entry_price) / self.s_lev if self.pos_short > 0 else Decimal('0')

    def unrealized(self, price: Decimal) -> Decimal:
        upnl_l = self.pos_long * (price - self.long_entry_price) if self.pos_long > 0 else Decimal('0')
        upnl_s = self.pos_short * (self.short_entry_price - price) if self.pos_short > 0 else Decimal('0')
        return upnl_l + upnl_s

    def get_tpv(self, price: Decimal) -> Decimal:
        # VIRTUAL is a separate cash bucket, so its value is added back and never revalued.
        return self.val_cash + self.margin_long() + self.margin_short() + self.unrealized(price) + self.virt_debt

    def get_tpv_fast(self, price: float) -> float:
        pos_l = float(self.pos_long)
        pos_s = float(self.pos_short)
        p = float(price)
        l_entry = float(self.long_entry_price)
        s_entry = float(self.short_entry_price)
        cash = float(self.val_cash)

        upnl_l = pos_l * (p - l_entry) if pos_l > 0 else 0.0
        upnl_s = pos_s * (s_entry - p) if pos_s > 0 else 0.0
        m_l = (pos_l * l_entry) / float(self.l_lev) if pos_l > 0 else 0.0
        m_s = (pos_s * s_entry) / float(self.s_lev) if pos_s > 0 else 0.0
        return cash + m_l + upnl_l + m_s + upnl_s + float(self.virt_debt)

    def accounting_equity(self, price: Decimal) -> Decimal:
        base = self.initial_capital if self.ledger_base is None else self.ledger_base
        return (base + self.realized_total + self.unrealized(price)
                - self.commission_paid - self.funding_paid - self.liquidation_loss_total)

    def accounting_residual(self, price: Decimal) -> Decimal:
        return (self.get_tpv(price) + self.siphoning_reserve) - self.accounting_equity(price)

    def check_accounting(self, price: Decimal, bar: int, context: str = "") -> Decimal:
        expected = self.accounting_equity(price)
        actual = self.get_tpv(price) + self.siphoning_reserve
        residual = actual - expected
        tolerance = max(Decimal('1e-6'), abs(expected) * Decimal('1e-9'))
        if abs(residual) > tolerance:
            details = {
                "bar": int(bar),
                "context": context,
                "price": float(price),
                "positions_before_after": {"long": float(self.pos_long), "short": float(self.pos_short)},
                "entry_prices": {"long": float(self.long_entry_price), "short": float(self.short_entry_price)},
                "cash": float(self.val_cash),
                "virtual": float(self.virt_debt),
                "margin": {"long": float(self.margin_long()), "short": float(self.margin_short())},
                "realized": float(self.realized_total),
                "unrealized": float(self.unrealized(price)),
                "commissions": float(self.commission_paid),
                "funding": float(self.funding_paid),
                "liquidation_loss": float(self.liquidation_loss_total),
                "reserve": float(self.siphoning_reserve),
                "expected_tpv": float(expected),
                "actual_tpv": float(actual),
                "residual": float(residual),
                "tolerance": float(tolerance),
            }
            self.accounting_error = details
            self.log_event(bar, "accounting_mismatch", float(actual), json.dumps(details, sort_keys=True))
            raise AccountingMismatch(details)
        return residual

    def designate_virtual(self, amount: Decimal) -> Decimal:
        amount = min(abs(Decimal(str(amount))), max(Decimal('0'), self.val_cash))
        self.val_cash -= amount
        self.virt_debt += amount
        return amount

    def release_virtual(self, amount: Decimal) -> Decimal:
        amount = min(abs(Decimal(str(amount))), self.virt_debt)
        self.virt_debt -= amount
        self.val_cash += amount
        return amount

    # B1: V-only funding (spec §4).  CASH-first legacy kept for baseline reproduction.
    def pay_funding(self, amount: Decimal) -> Decimal:
        """Apply signed funding to V (virt_debt) only.  CASH unchanged.

        Returns the signed amount applied.  If V becomes negative, the caller
        must handle the shortfall via the funding-shortfall close/halt path.
        """
        amount = Decimal(str(amount))
        self.virt_debt += amount
        self.funding_paid -= amount  # opposite sign
        return amount

    # Legacy CASH-first funding — for baseline reproduction only (legacy_constant mode).
    def pay_funding_legacy(self, amount: Decimal) -> Decimal:
        """Old CASH-first waterfall.  val_cash decreases; V only absorbs remainder."""
        amount = Decimal(str(amount))
        if amount > 0 and amount > self.val_cash:
            self.funding_from_virtual += self.release_virtual(amount - self.val_cash)
        self.val_cash -= amount
        self.funding_paid += amount
        return amount

    def get_funding_shortfall(self) -> Decimal:
        """Non-negative shortfall from negative V.  0 when V >= 0."""
        return max(Decimal('0'), -self.virt_debt)

    def close_position_for_funding_shortfall(
        self, leg: str, qty: Decimal, mid_price: Decimal,
        sim, bar: int, trigger: str = "funding_shortfall",
    ) -> Dict:
        """Close a leg (possibly partial); returns fill dict.  Preserves remainder."""
        if leg == "BASE_LONG":
            side = "SELL"
            entry_price = self.long_entry_price
            lev = self.l_lev
            current_qty = self.pos_long
        else:
            side = "BUY"
            entry_price = self.short_entry_price
            lev = self.s_lev
            current_qty = self.pos_short

        close_qty = min(abs(qty), abs(current_qty))
        if close_qty <= 0:
            return {"status": "skipped", "leg": leg, "qty": 0, "qty_remaining": float(abs(current_qty))}

        exec_p, comm = sim.simulate_market_execution(side, close_qty, mid_price)
        if leg == "BASE_LONG":
            realized_pnl = close_qty * (exec_p - entry_price)
            released_margin = close_qty * entry_price / lev
            self.val_cash += released_margin + realized_pnl - comm
            self.pos_long -= close_qty
            if self.pos_long <= 0:
                self.pos_long = Decimal('0')
                self.long_entry_price = Decimal('0')
            remaining = self.pos_long
        else:
            realized_pnl = close_qty * (entry_price - exec_p)
            released_margin = close_qty * entry_price / lev
            self.val_cash += released_margin + realized_pnl - comm
            self.pos_short -= close_qty
            if self.pos_short <= 0:
                self.pos_short = Decimal('0')
                self.short_entry_price = Decimal('0')
            remaining = self.pos_short

        self.log_fill(bar, side, leg, close_qty, exec_p, comm, realized_pnl,
                      float(self.get_tpv_fast(float(mid_price))),
                      mid_price=mid_price, leverage=lev,
                      trigger=trigger, cycle_id=self.cycles)
        status = "filled" if remaining <= 0 else "partial"
        return {"status": status, "leg": leg, "qty": float(close_qty),
                "qty_remaining": float(remaining),
                "price": float(exec_p), "commission": float(comm),
                "realized_pnl": float(realized_pnl)}

    def maintenance_margin(self, price: Decimal, mmr: Decimal) -> Decimal:
        return (abs(self.pos_long) + abs(self.pos_short)) * price * mmr

    def hard_liquidation_triggered(self, price: Decimal, mmr: Decimal) -> bool:
        return (self.pos_long > 0 or self.pos_short > 0) and self.get_tpv(price) <= self.maintenance_margin(price, mmr)

    def cross_margin_distance_pct(self, price: Decimal, mmr: Decimal) -> Decimal:
        total_margin_balance = self.get_tpv(price)
        if total_margin_balance <= 0:
            return Decimal('-100')
        total_mm = self.maintenance_margin(price, mmr)
        return ((total_margin_balance - total_mm) / total_margin_balance) * Decimal('100')

    # cap per-run fill log so a runaway run cannot grow the JSON unboundedly
    MAX_FILLS = 200_000

    def log_fill(self, bar: int, side: str, leg: str, qty: Decimal, price: Decimal,
                  commission: Decimal, realized_pnl: Decimal, tpv: float,
                  mid_price: Optional[Decimal] = None, leverage: Optional[Decimal] = None,
                  trigger: str = "rebalance", cycle_id: Optional[int] = None) -> None:
        self.fill_count += 1
        if leg == "VIRTUAL":
            self.internal_transfer_count += 1
        self.commission_paid += Decimal(str(commission))
        self.realized_total += Decimal(str(realized_pnl))
        if mid_price is not None:
            self.slippage_paid += abs(Decimal(str(qty))) * abs(Decimal(str(price)) - Decimal(str(mid_price)))
        if len(self.fills) >= self.MAX_FILLS:
            return
        self.fills.append({
            "bar": int(bar), "side": side, "leg": leg,
            "qty": float(qty), "price": float(price),
            "mid": float(mid_price) if mid_price is not None else float(price),
            "notional": float(abs(Decimal(str(qty))) * Decimal(str(price))),
            "leverage": float(leverage) if leverage is not None else None,
            "commission": float(commission), "realized_pnl": float(realized_pnl),
            "commission_cum": float(self.commission_paid),
            "realized_cum": float(self.realized_total),
            "funding_cum": float(self.funding_paid),
            "trigger": trigger,
            "rebalance_id": int(cycle_id) if cycle_id is not None else int(self.cycles),
            "tpv": float(tpv),
        })

    def log_event(self, bar: int, kind: str, tpv: float, detail: str = "", **extra: Any) -> None:
        if len(self.events) < self.MAX_FILLS:
            event = {"bar": int(bar), "kind": kind, "tpv": float(tpv), "detail": detail}
            event.update(extra)
            self.events.append(event)

async def download_live_data(symbol: str, data_dir: str, days: float = 2.0) -> str:
    endpoint = "https://fapi.binance.com/fapi/v1/klines"
    logger.info(f"Downloading live data for {symbol} ({days} days)...")
    total_minutes = int(days * 24 * 60)
    limit_per_request = 1500
    num_requests = math.ceil(total_minutes / limit_per_request)
    all_data = []
    end_time_ms = int(time.time() * 1000)

    async with aiohttp.ClientSession() as session:
        for i in range(num_requests):
            batch = min(limit_per_request, total_minutes - len(all_data))
            if batch <= 0:
                break
            params = {"symbol": symbol, "interval": "1m", "limit": batch}
            if i > 0:
                params["endTime"] = end_time_ms

            async with session.get(endpoint, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if not data:
                        break
                    all_data = data + all_data
                    end_time_ms = data[0][0] - 1
                else:
                    raise Exception(f"Binance API error: {resp.status}")
            if i < num_requests - 1:
                await asyncio.sleep(0.25)

    df = pd.DataFrame(all_data, columns=['time', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'q_vol', 'trades', 't_base', 't_quote', 'ignore'])
    df = df[['open', 'high', 'low', 'close', 'volume']]
    for col in df.columns:
        df[col] = df[col].astype(float)

    if not os.path.exists(data_dir):
        os.makedirs(data_dir)
    file_path = os.path.join(data_dir, f"{symbol}_live.feather")
    df.to_feather(file_path)
    return file_path

async def run_backtest(config_path: str, data_dir: str, live_mode: bool = False, ticker_override: Optional[str] = None,
                        days: float = 2.0, commission: float = 0.0004, slippage: float = 0.0005,
                        quiet: bool = False,
                        threshold_surplus_override: Optional[float] = None,
                        threshold_deficit_override: Optional[float] = None,
                        capital_override: Optional[float] = None,
                        since_utc: Optional[str] = None,
                        until_utc: Optional[str] = None) -> Optional[Dict[str, Any]]:
    try:
        if quiet:
            logger.setLevel(logging.WARNING)

        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        
        base_ticker: str = ticker_override if ticker_override else config.get("base_ticker", "BTCUSDT")

        if live_mode:
            # explicit --live / live_mode=True: refresh from the exchange
            file_path = await download_live_data(base_ticker, data_dir, days)
        else:
            # offline backtest: use only already-downloaded feather data.
            # Pick the longest available history; iloc[-max_bars:] below trims it to the requested window,
            # so a 365d file also serves a 1d/30d run deterministically.
            import glob
            candidates = {
                p for p in glob.glob(os.path.join(data_dir, f"{base_ticker}*.feather"))
            }
            candidates |= {
                os.path.join(data_dir, suffix) for suffix in
                (f"{base_ticker}_live.feather", f"{base_ticker}_live_10d.feather", f"{base_ticker}_live_48h.feather")
            }
            candidates = {p for p in candidates if os.path.exists(p)}
            if not candidates:
                raise FileNotFoundError(
                    f"No local data for {base_ticker} in {data_dir} "
                    f"(expected {base_ticker}_live.feather or {base_ticker}*.feather). "
                    f"Run once with --live to download, or drop a feather into data/."
                )
            file_path = max(candidates, key=lambda p: (os.path.getsize(p), p))
            logger.info(f"Offline backtest using local {os.path.basename(file_path)}")

        df: pd.DataFrame = pd.read_feather(file_path)
        time_values = None
        if "time" in df.columns:
            # feather timestamps are the only real time axis; keep them for the dashboard
            time_values = (pd.to_datetime(df["time"], utc=True).dt.as_unit("ms").astype("int64").to_numpy())
        df = df[['open', 'high', 'low', 'close', 'volume']]
        FULL_FILE_LEN = len(df)  # anchor for --until index math (pre-trim)

        portfolio_cfg: Dict[str, Any] = config["portfolios"][0]
        fuse_enabled = portfolio_cfg.get("fuse_enabled", True)
        if not isinstance(fuse_enabled, bool):
            raise ValueError("fuse_enabled must be a boolean")
        
        bars_per_day = 1440
        max_bars = int(days * bars_per_day)
        head_dropped = 0
        if time_values is not None and len(time_values) >= len(df):
            # Prefer the real feather time axis over mtime so --since/--until replay the same bars.
            start_idx = 0
            end_idx = len(df)
            if since_utc is not None:
                since_ts = pd.Timestamp(since_utc, tz="UTC").floor("min")
                start_idx = int(np.searchsorted(time_values, int(since_ts.timestamp() * 1000), side="left"))
                if start_idx >= len(df):
                    data_end = pd.Timestamp(int(time_values[-1]), unit="ms", tz="UTC")
                    raise ValueError(f"--since {since_utc} is after the end of the local data ({data_end})")
            elif len(df) > max_bars:
                start_idx = len(df) - max_bars

            if until_utc is not None:
                until_ts = pd.Timestamp(until_utc, tz="UTC").floor("min")
                end_idx = int(np.searchsorted(time_values, int(until_ts.timestamp() * 1000), side="right"))
                if end_idx <= start_idx:
                    raise ValueError(f"--until {until_utc} leaves an empty window")
                if since_utc is None:
                    # --days limits the tail that ends at --until
                    start_idx = max(start_idx, end_idx - max_bars)

            head_dropped = start_idx
            window_len = end_idx - start_idx
            logger.info(
                f"Window: {pd.Timestamp(int(time_values[start_idx]), unit='ms', tz='UTC')} -> "
                f"{pd.Timestamp(int(time_values[end_idx - 1]), unit='ms', tz='UTC')} ({window_len} bars)"
            )
            if start_idx > 0 or end_idx < len(df):
                df = df.iloc[start_idx:end_idx]
        else:
            if since_utc is not None:
                # no time axis: data ends at the download time (mtime), 1 bar per minute.
                end_ts = pd.Timestamp.fromtimestamp(os.path.getmtime(file_path), tz="UTC").floor("min")
                since_ts = pd.Timestamp(since_utc, tz="UTC").floor("min")
                if since_ts > end_ts:
                    raise ValueError(f"--since {since_utc} is after the end of the local data ({end_ts})")
                min_bars_needed = int((end_ts - since_ts).total_seconds() // 60)
                keep = min(len(df), min_bars_needed + 1)
                if keep < len(df):
                    head_dropped = len(df) - keep
                    df = df.iloc[-keep:]
                logger.info(f"Window: since {since_ts} -> {end_ts} ({len(df)} bars; --days ignored in since mode)")
            elif len(df) > max_bars:
                head_dropped = len(df) - max_bars
                df = df.iloc[-max_bars:]

            if until_utc is not None:
                # hard stop: trim everything after the until boundary (UTC)
                full_len = FULL_FILE_LEN
                end_ts = pd.Timestamp.fromtimestamp(os.path.getmtime(file_path), tz="UTC").floor("min")
                until_ts = pd.Timestamp(until_utc, tz="UTC").floor("min")
                if until_ts > end_ts:
                    logger.warning(f"--until {until_utc} is after the end of the local data ({end_ts}); ignoring")
                else:
                    # full-file index of the last bar inside [.., until_ts]:
                    # file bar i starts at end_ts - (full_len - 1 - i) minutes
                    idx_last = full_len - 1 - int((end_ts - until_ts).total_seconds() // 60)
                    # df may already be head-trimmed by --since: full index of df row 0
                    keep_upto = max(0, min(len(df), idx_last + 1 - head_dropped))
                    if keep_upto < len(df):
                        df = df.iloc[:keep_upto]
                        logger.info(f"--until {until_ts}: frame trimmed to {len(df)} bars")

        time_window = (
            time_values[head_dropped:head_dropped + len(df)]
            if time_values is not None and len(time_values) >= head_dropped + len(df)
            else None
        )
        # metadata for the dashboard: where in the full feather file this window lives
        data_window = {
            "feather": os.path.basename(file_path),
            "offset": int(head_dropped),   # bars skipped before df row 0
            "bars": int(len(df)),
            "input_bars": int(FULL_FILE_LEN),
            "since_utc": since_utc,
            "until_utc": until_utc,
            "bars_per_day": 1440,
            "time_source": "feather_time" if time_window is not None else "mtime",
            "start_utc": pd.Timestamp(int(time_window[0]), unit="ms", tz="UTC").strftime("%Y-%m-%d %H:%M") if time_window is not None and len(time_window) else None,
            "end_utc": pd.Timestamp(int(time_window[-1]), unit="ms", tz="UTC").strftime("%Y-%m-%d %H:%M") if time_window is not None and len(time_window) else None,
        }

        initial_capital: float = capital_override if capital_override is not None else portfolio_cfg.get("initial_capital", 1000.0)
        
        # Инъекция параметров Кросс-Маржи из конфигурации
        paper_initial_capital: float = portfolio_cfg.get("paper_initial_capital", initial_capital)
        
        targets: Dict[str, Any] = portfolio_cfg["targets"]
        ticker_thresholds = portfolio_cfg.get("ticker_thresholds", {})
        
        if threshold_surplus_override is not None:
            t_surplus = threshold_surplus_override
            t_deficit = threshold_deficit_override if threshold_deficit_override is not None else threshold_surplus_override
        else:
            t_surplus = portfolio_cfg.get("rebalance_threshold_surplus", portfolio_cfg.get("rebalance_threshold", 0.02))
            t_deficit = portfolio_cfg.get("rebalance_threshold_deficit", t_surplus)
        
        t_cfg = ticker_thresholds.get(base_ticker)
        if isinstance(t_cfg, dict):
            threshold_surplus = float(t_cfg.get("surplus", t_surplus))
            threshold_deficit = float(t_cfg.get("deficit", t_deficit))
        elif isinstance(t_cfg, (float, int)):
            threshold_surplus = threshold_deficit = float(t_cfg)
        else:
            threshold_surplus = float(t_surplus)
            threshold_deficit = float(t_deficit)
            
        siphoning_threshold_pct: float = portfolio_cfg.get("siphoning_threshold_pct", 0.0)
        reinvestment_ratio: float = portfolio_cfg.get("reinvestment_ratio", 0.0)
        trail_distance: float = float(config.get("rebalance_share_trail_distance", 0.0))
        if not math.isfinite(trail_distance) or trail_distance < 0:
            raise ValueError("rebalance_share_trail_distance must be a finite non-negative share fraction")
        trailing_stop_pct: float = config.get("equity_trailing_stop_pct", 0.0)
        trailing_stop_activation_pct: float = config.get("equity_trailing_stop_activation_pct", 0.0)
        trailing_stop_timeout_sec: int = int(config.get("equity_trailing_stop_timeout_sec", 0))
        min_notional_usdt: Decimal = Decimal(str(config.get("min_notional_usdt", 6.0)))
        toxic_cooldown_days: float = config.get("toxic_cooldown_days", 0.02)
        cooldown_bars_duration: int = int(toxic_cooldown_days * 1440)
        max_drawdown_limit_pct: float = config.get("max_drawdown_limit", 100.0)  # по умолчанию 100% = отключён

        close_prices: npt.NDArray[np.float64] = df['close'].values.astype(np.float64)
        step_sizes = {base_ticker: 0.001}

        sim = MarketOrderSlippageSimulator(commission_pct=commission, slippage_pct=slippage)

        daily_funding_rate = Decimal('0.0002')
        bars_per_day = Decimal('1440')
        funding_drag_step = daily_funding_rate / bars_per_day
        l_lev = Decimal(str(targets["BASE_LONG"]["leverage"]))
        s_lev = Decimal(str(targets["BASE_SHORT"]["leverage"]))

        # B1: funding model — legacy_constant (default) or signed_actual.
        funding_model = config.get("funding_model", "legacy_constant")
        funding_journal = None
        if funding_model == "signed_actual":
            scenario_path = config.get("funding_scenario")
            if not scenario_path or not os.path.exists(scenario_path):
                logger.error(
                    f"funding_model=signed_actual but funding_scenario is missing "
                    f"or not found: {scenario_path}.  Provide an explicit scenario "
                    f"journal or use funding_model=legacy_constant."
                )
                return None
            with open(scenario_path, "r", encoding="utf-8") as fj:
                funding_journal = FundingJournal.from_list(json.load(fj))
            logger.info(f"Loaded funding scenario: {len(funding_journal)} events from {scenario_path}")

        state = BacktestState(
            initial_capital=Decimal(str(initial_capital)),
            val_cash=Decimal(str(initial_capital)),
            base_ticker=base_ticker,
            l_lev=l_lev,
            s_lev=s_lev,
            funding_model=funding_model,
            ledger_base=Decimal(str(initial_capital)),
        )

        if trail_distance > 0:
            state.share_trail = share_trail.fresh_state()
            state.share_trail_spec = share_trail.spec_fingerprint(
                trail_distance, targets, threshold_surplus, threshold_deficit)

        start_price = Decimal(str(close_prices[0]))
        state.last_rebalance_price = start_price
        target_v_share = Decimal(str(targets["VIRTUAL"]["share"]))
        virt_cost = target_v_share * state.initial_capital
        # VIRTUAL is a separate cash bucket: move it out of free cash exactly once.
        state.val_cash -= virt_cost
        state.virt_qty = Decimal('0')
        state.virt_debt = virt_cost
        state.check_accounting(start_price, 0, "init")

        trend_guard_cfg = config.get("trend_guard", {})
        guards_cfg = portfolio_cfg.get("safety_guards", {})
        tg_min_move_pct = trend_guard_cfg.get("min_move_pct", guards_cfg.get("trend_min_move_pct", 0.5)) / 100.0
        tg_eff_threshold = trend_guard_cfg.get("eff_threshold", guards_cfg.get("trend_eff_threshold", 0.85))
        tg_nmg_pct = trend_guard_cfg.get("net_move_block_pct", guards_cfg.get("net_move_block_pct", 1.5)) / 100.0
        tg_nmg_window = trend_guard_cfg.get("net_move_window_sec", guards_cfg.get("net_move_window_sec", 30))
        tg_nmg_bars = max(1, tg_nmg_window // 60)
        tg_trend_bars = int(guards_cfg.get("trend_lookback_bars", 30))
        if tg_trend_bars <= 0:
            raise ValueError("trend_lookback_bars must be positive")
        tg_blocked_count = 0

        # --- Velocity Guard / Net Move Guard (из safety_guards) ---
        vg_velocity_pct = guards_cfg.get("max_price_velocity_pct", 2.0) / 100.0
        vg_window_sec = guards_cfg.get("velocity_window_sec", 60)
        vg_window_bars = max(1, vg_window_sec // 60)
        vg_blocked_count = 0
        trend_add_blocks = 0
        vg_cooldown_bars = 0  # оставшиеся бары блокировки от Velocity Guard

        for i in range(len(df)):
            mid_price = Decimal(str(close_prices[i]))

            # --- 1.0 B1: Funding (EVERY bar, even during cooldown/halt) ---
            # Funding accounting continues regardless of trading state.
            if state.funding_model == "legacy_constant":
                long_notional = state.pos_long * mid_price
                short_notional = state.pos_short * mid_price
                total_drag = (long_notional + short_notional) * funding_drag_step
                state.pay_funding_legacy(total_drag)
            elif state.funding_model == "signed_actual" and funding_journal is not None:
                if time_window is None:
                    logger.error("funding_model=signed_actual requires a real time axis (feather with 'time' column)")
                    return None
                bar_ts = float(time_window[i]) / 1000.0
                for event in funding_journal.get_received():
                    if event.event_time > bar_ts:
                        continue
                    apply_funding_backtest(event.signed_amount, state)
                    funding_journal.mark_applied(event.event_id)
                    state.log_event(i, "funding", float(state.get_tpv_fast(float(mid_price))),
                                    f"{event.event_id} amount={float(event.signed_amount)}",
                                    funding_amount=float(event.signed_amount))
                    # Check shortfall after EACH event (subsequent credit must not hide it).
                    if state.get_funding_shortfall() > 0 and (state.pos_long > 0 or state.pos_short > 0):
                        state.funding_halt_active = True
                        state.funding_halt_at = float(i)
                        state.funding_shortfall = state.get_funding_shortfall()
                        logger.warning(f"Bar {i}: Funding shortfall after {event.event_id}. Halt; closing.")
                        if state.pos_long > 0:
                            state.close_position_for_funding_shortfall(
                                "BASE_LONG", state.pos_long, mid_price, sim, i)
                        if state.pos_short > 0:
                            state.close_position_for_funding_shortfall(
                                "BASE_SHORT", state.pos_short, mid_price, sim, i)
                        state.check_accounting(mid_price, i, "funding_shortfall_close")
                        state.dormant_capital = Decimal(str(
                            state.get_tpv_fast(float(mid_price)) + float(state.siphoning_reserve)))
                        state.cooldown_until_bar = i + cooldown_bars_duration
                        state.log_event(i, "funding_shortfall", float(state.dormant_capital),
                                        f"event={event.event_id}")
                        if state.share_trail is not None:
                            share_trail.reset_all(state.share_trail)
                            state.log_event(i, "share_trail_reset_all", float(state.dormant_capital), "funding halt close")
                        break  # stop processing events this bar
                # Legacy shortfall check (CASH-first may leave V < 0)
                if state.get_funding_shortfall() > 0 and (state.pos_long > 0 or state.pos_short > 0):
                    state.funding_halt_active = True
                    state.funding_halt_at = float(i)
                    state.funding_shortfall = state.get_funding_shortfall()
                    if state.pos_long > 0:
                        state.close_position_for_funding_shortfall(
                            "BASE_LONG", state.pos_long, mid_price, sim, i)
                    if state.pos_short > 0:
                        state.close_position_for_funding_shortfall(
                            "BASE_SHORT", state.pos_short, mid_price, sim, i)
                    state.check_accounting(mid_price, i, "funding_shortfall_close")
                    state.dormant_capital = Decimal(str(
                        state.get_tpv_fast(float(mid_price)) + float(state.siphoning_reserve)))
                    state.cooldown_until_bar = i + cooldown_bars_duration
                    state.log_event(i, "funding_shortfall", float(state.dormant_capital),
                                    f"shortfall={float(state.get_funding_shortfall()):.4f}")
                    if state.share_trail is not None:
                        share_trail.reset_all(state.share_trail)
                        state.log_event(i, "share_trail_reset_all", float(state.dormant_capital), "funding halt close")
                    logger.warning(f"Bar {i}: Funding halt — positions closed.")
                    state.history.append(float(state.dormant_capital))
                    continue

            # --- 1.5 Cooldown & Rebase Logic ---
            if i < state.cooldown_until_bar:
                state.history.append(float(state.dormant_capital))
                continue
            # B1: funding halt — persistent block; no timer, no auto-clear.
            # Funding events (credits/debits) are still processed above;
            # reflect them in the equity curve while preserving the trading ban.
            if state.funding_halt_active:
                state.dormant_capital = Decimal(str(
                    state.get_tpv_fast(float(mid_price)) + float(state.siphoning_reserve)))
                state.history.append(float(state.dormant_capital))
                continue

            if i == state.cooldown_until_bar and state.cooldown_until_bar > 0:
                if state.dormant_capital < Decimal('5.0'):
                    logger.critical(f"Bar {i}: Capital too low to restart ({state.dormant_capital:.2f}). Backtest effectively dead.")
                    state.log_event(i, "cooldown_dead", float(state.dormant_capital), "capital below 5 USDT")
                    state.cooldown_until_bar = len(df) + 1  # Dead forever
                    state.history.append(float(state.dormant_capital))
                    continue

                logger.info(f"Bar {i}: Cooldown finished. Reviving bot. Rebased capital: {state.dormant_capital:.2f}")
                state.log_event(i, "cooldown_end", float(state.dormant_capital),
                                f"rebased {state.dormant_capital:.2f}",
                                rebased=float(state.dormant_capital),
                                reserve_reset=float(state.siphoning_reserve))
                state.initial_capital = state.dormant_capital
                state.pos_long = Decimal('0')
                state.pos_short = Decimal('0')
                state.virt_qty = Decimal('0')
                state.long_entry_price = Decimal('0')
                state.short_entry_price = Decimal('0')
                state.siphoning_reserve = Decimal('0')
                state.tpv_ath = max(float(state.dormant_capital), float(state.initial_capital))
                state.trailing_stop_violation_start = 0.0
                state.trailing_stop_triggered = False
                state.last_rebalance_price = mid_price

                # Re-init virtual leg
                target_v_share = Decimal(str(targets["VIRTUAL"]["share"]))
                virt_cost = target_v_share * state.initial_capital
                state.virt_qty = Decimal('0')
                state.virt_debt = virt_cost
                state.val_cash = state.initial_capital - virt_cost
                state.ledger_base = (
                    state.initial_capital - state.realized_total + state.commission_paid
                    + state.funding_paid + state.liquidation_loss_total
                )
                state.cooldown_until_bar = 0
                if state.share_trail is not None:
                    share_trail.reset_all(state.share_trail)
                    state.log_event(i, "share_trail_reset_all", float(state.get_tpv_fast(float(mid_price))),
                                    "cooldown rebase: new session")
                state.check_accounting(mid_price, i, "cooldown_rebase")

            # --- 2. Portfolio Calculation ---
            wallet_balance = state.val_cash + state.margin_long() + state.margin_short()
            # VIRTUAL is already excluded from val_cash, so real_equity is the net futures wallet.
            simulated_real_equity = wallet_balance

            calculator = PortfolioCalculator(
                positions={f"{base_ticker}_LONG": float(state.pos_long), f"{base_ticker}_SHORT": float(state.pos_short)},
                spot_price=float(mid_price),
                real_equity=float(simulated_real_equity),
                virt_qty=float(state.virt_qty),
                virt_debt=float(state.virt_debt),
                long_entry_price=float(state.long_entry_price),
                short_entry_price=float(state.short_entry_price),
                base_ticker=base_ticker,
                targets=targets,
                initial_capital=float(state.initial_capital),
                last_rebalance_price=float(state.last_rebalance_price),
                commission_pct=float(sim.commission_pct),
                slippage_pct=float(sim.slippage_pct),
            )

            # Trailing diagnostics: arm/reset events and generated signals
            if state.share_trail is not None:
                trail_prev = {key: [state.share_trail[key]["peak"], state.share_trail[key]["trough"]]
                              for key in ("BASE_LONG", "BASE_SHORT")}
            calc_res = calculator.calculate_rebalance(
                targets,
                threshold_surplus=threshold_surplus,
                threshold_deficit=threshold_deficit,
                current_equity=state.get_tpv(mid_price),
                fuse_enabled=fuse_enabled,
                share_trail=state.share_trail,
                trail_distance=trail_distance,
            )
            actions = calc_res["actions"]

            # Trailing diagnostics: arm/reset events and generated signals
            if state.share_trail is not None:
                for key in ("BASE_LONG", "BASE_SHORT"):
                    prev_peak, prev_trough = trail_prev[key]
                    peak, trough = state.share_trail[key]["peak"], state.share_trail[key]["trough"]
                    for field, prev_v, cur_v in (("peak", prev_peak, peak), ("trough", prev_trough, trough)):
                        if prev_v is None and cur_v is not None:
                            state.log_event(i, "share_trail_observe", float(state.get_tpv_fast(float(mid_price))),
                                            f"{key} {field}={cur_v}", key=key, field=field)
                        elif prev_v is not None and cur_v is None:
                            state.log_event(i, "share_trail_reset", float(state.get_tpv_fast(float(mid_price))),
                                            f"{key} {field} cleared (flat leg)", key=key, field=field)
                    for act in calc_res["actions"]:
                        if act.get("key") != key:
                            continue
                        direction = "REDUCE" if act.get("is_reduction") else "ADD"
                        # a trailing signal only when the extremum was armed pre-bar
                        if trail_prev[key][0 if direction == "REDUCE" else 1] is None:
                            continue
                        state.share_trail_signals += 1
                        state.log_event(i, "share_trail_signal", float(state.get_tpv_fast(float(mid_price))),
                                        f"{key} {direction} req={abs(act.get('diff_usdt', 0.0)):.4f}",
                                        key=key, direction=direction,
                                        requested_usdt=float(abs(act.get("diff_usdt", 0.0))))

            if actions and i >= tg_trend_bars:
                lookback_prices = close_prices[max(0, i - tg_trend_bars):i + 1]
                if len(lookback_prices) >= 2:
                    p_start = lookback_prices[0]
                    p_end = lookback_prices[-1]
                    net_move = abs(p_end - p_start)
                    total_path = sum(abs(lookback_prices[j] - lookback_prices[j-1]) for j in range(1, len(lookback_prices)))
                    trend_eff = (net_move / total_path) if total_path > 0 else 0
                    net_move_pct = net_move / p_start if p_start > 0 else 0
                    if net_move_pct > tg_min_move_pct and trend_eff > tg_eff_threshold:
                        blocked = [a["key"] for a in actions]
                        actions = []
                        tg_blocked_count += 1
                        state.log_event(i, "trend_guard_block", float(state.get_tpv_fast(float(mid_price))),
                                        f"trend move {net_move_pct*100:.2f}% eff {trend_eff:.2f}", keys=blocked)
                
                if actions and i >= tg_nmg_bars:
                    nmg_prices = close_prices[max(0, i - tg_nmg_bars):i + 1]
                    if len(nmg_prices) >= 2:
                        nmg_old = nmg_prices[0]
                        nmg_move = abs(close_prices[i] - nmg_old) / nmg_old if nmg_old > 0 else 0
                        if nmg_move > tg_nmg_pct:
                            blocked = [a["key"] for a in actions]
                            actions = []
                            tg_blocked_count += 1
                            state.log_event(i, "trend_guard_block", float(state.get_tpv_fast(float(mid_price))),
                                            f"net move {nmg_move*100:.2f}%", keys=blocked)

            # --- Velocity Guard: блокировка при быстром движении цены ---
            if actions and vg_cooldown_bars > 0:
                blocked = [a["key"] for a in actions]
                vg_cooldown_bars -= 1
                actions = []
                vg_blocked_count += 1
                state.log_event(i, "velocity_guard_block", float(state.get_tpv_fast(float(mid_price))),
                                f"cooldown {vg_cooldown_bars} bars left", keys=blocked)
            elif actions and i >= vg_window_bars:
                vg_prices = close_prices[max(0, i - vg_window_bars):i + 1]
                if len(vg_prices) >= 2:
                    vg_old = vg_prices[0]
                    vg_move = abs(close_prices[i] - vg_old) / vg_old if vg_old > 0 else 0
                    if vg_move > vg_velocity_pct:
                        blocked = [a["key"] for a in actions]
                        actions = []
                        vg_blocked_count += 1
                        vg_cooldown_bars = vg_window_bars  # блокируем на окно
                        state.log_event(i, "velocity_guard_block", float(state.get_tpv_fast(float(mid_price))),
                                        f"velocity {vg_move*100:.2f}%", keys=blocked)

            # --- Trend regime: block additions opposite a one-way window move ---
            if actions:
                t_cfg = portfolio_cfg.get("trend_add_block") or {}
                t_pct, t_win = t_cfg.get("pct"), int(t_cfg.get("window_bars", 120))
                if t_pct and i >= t_win:
                    move = (close_prices[i] - close_prices[i - t_win]) / close_prices[i - t_win]
                    if abs(move) >= float(t_pct):
                        opposite = "BASE_SHORT" if move > 0 else "BASE_LONG"
                        blocked = [a for a in actions if a.get("key") == opposite and not a.get("is_reduction", False)]
                        if blocked:
                            trend_add_blocks += 1
                            actions = [a for a in actions if a not in blocked]
                            state.log_event(i, "trend_add_block", float(state.get_tpv_fast(float(mid_price))),
                                            f"move {move*100:.2f}% over {t_win} bars; adds on {opposite} blocked",
                                            keys=[a["key"] for a in blocked])

            if state.cash_invariant_violation:
                actions = [a for a in actions if a.get("is_reduction", False)]

            if state.initial_build_pending:
                missing = set(state.initial_build_pending.get("missing", []))
                actions = [
                    a for a in actions
                    if a.get("key") in missing and not a.get("is_reduction", False)
                ]

            if actions:
                is_initial_formation = (
                    state.cycles == 0
                    and state.pos_long == 0
                    and state.pos_short == 0
                )
                was_initial_pending = bool(state.initial_build_pending)
                fills_before = len(state.fills)
                tpv_before = state.get_tpv(mid_price)
                reductions = [a for a in actions if a.get("is_reduction", False)]
                expansions = [a for a in actions if not a.get("is_reduction", False)]
                execution_records: List[Dict[str, Any]] = []
                step_size = Decimal(str(step_sizes.get(base_ticker, 0.001)))

                def record(act: Dict[str, Any], status: str, executed_usdt: Decimal = Decimal('0'),
                           reason: str = "") -> None:
                    requested = abs(Decimal(str(act.get("requested_usdt", act["diff_usdt"]))))
                    approved = abs(Decimal(str(act.get("approved_usdt", act["diff_usdt"]))))
                    if status == "partial" and approved < requested - Decimal('1e-9'):
                        reason = "capped_by_available_funds" + (f"+{reason}" if reason else "")
                    execution_records.append({
                        "key": act["key"],
                        "side": action_side(act["key"], bool(act.get("is_reduction", False))),
                        "requested_usdt": float(requested),
                        "approved_usdt": float(approved),
                        "executed_usdt": float(executed_usdt),
                        "status": status,
                        "reason": reason,
                        "leverage": float(act.get("leverage", 1)),
                        "is_reduction": bool(act.get("is_reduction", False)),
                    })

                for act in reductions:
                    key = act["key"]
                    requested_usdt = abs(Decimal(str(act["diff_usdt"])))
                    lev = Decimal(str(act.get("leverage", 1)))

                    if key == "VIRTUAL":
                        released = state.release_virtual(requested_usdt)
                        if released <= Decimal('0'):
                            record(act, "skipped", reason="no_virtual_balance")
                            state.log_event(i, "skipped_action", float(state.get_tpv_fast(float(mid_price))),
                                            "no VIRTUAL balance to release", key=key,
                                            requested_usdt=float(requested_usdt))
                            continue
                        state.log_fill(i, "RELEASE", "VIRTUAL", Decimal('0'), mid_price, Decimal('0'),
                                       Decimal('0'), float(state.get_tpv_fast(float(mid_price))),
                                       mid_price=mid_price, leverage=Decimal('1'), trigger="rebalance",
                                       cycle_id=state.cycles + 1)
                        record(act, "filled" if released >= requested_usdt else "partial", released,
                               "capped_by_virtual_balance" if released < requested_usdt else "")
                        state.check_accounting(mid_price, i, "release_virtual")
                        continue

                    raw_qty = requested_usdt / mid_price
                    qty = quantize_qty(raw_qty, step_size)
                    capped_by = ""
                    if key == "BASE_LONG" and qty > state.pos_long:
                        qty = state.pos_long
                        capped_by = "capped_by_position"
                    elif key == "BASE_SHORT" and qty > state.pos_short:
                        qty = state.pos_short
                        capped_by = "capped_by_position"

                    if qty <= Decimal('0'):
                        record(act, "skipped", reason=capped_by or "zero_qty_after_step")
                        state.log_event(i, "skipped_action", float(state.get_tpv_fast(float(mid_price))),
                                        capped_by or "zero qty after step rounding", key=key,
                                        requested_usdt=float(requested_usdt))
                        continue
                    if not validate_notional(qty, mid_price, min_notional_usdt):
                        record(act, "skipped", reason="below_min_notional")
                        state.log_event(i, "skipped_action", float(state.get_tpv_fast(float(mid_price))),
                                        "below min notional", key=key,
                                        requested_usdt=float(requested_usdt))
                        continue

                    if key == "BASE_LONG":
                        exec_price, commission = sim.simulate_market_execution("SELL", qty, mid_price)
                        realized_pnl = qty * (exec_price - state.long_entry_price)
                        released_margin = (qty * state.long_entry_price) / state.l_lev
                        state.pos_long -= qty
                        state.val_cash += released_margin + realized_pnl - commission
                        if state.pos_long == Decimal('0'):
                            state.long_entry_price = Decimal('0')
                        state.log_fill(i, "SELL", "BASE_LONG", qty, exec_price, commission, realized_pnl,
                                       float(state.get_tpv_fast(float(mid_price))), mid_price=mid_price,
                                       leverage=lev, trigger="rebalance", cycle_id=state.cycles + 1)
                    else:
                        exec_price, commission = sim.simulate_market_execution("BUY", qty, mid_price)
                        realized_pnl = qty * (state.short_entry_price - exec_price)
                        released_margin = (qty * state.short_entry_price) / state.s_lev
                        state.pos_short -= qty
                        state.val_cash += released_margin + realized_pnl - commission
                        if state.pos_short == Decimal('0'):
                            state.short_entry_price = Decimal('0')
                        state.log_fill(i, "BUY", "BASE_SHORT", qty, exec_price, commission, realized_pnl,
                                       float(state.get_tpv_fast(float(mid_price))), mid_price=mid_price,
                                       leverage=lev, trigger="rebalance", cycle_id=state.cycles + 1)
                    record(act, "partial" if capped_by else "filled", qty * exec_price, capped_by)
                    if state.share_trail is not None:
                        # confirmed fill (full or partial) consumes the REDUCE signal
                        share_trail.consume(state.share_trail, key, "REDUCE")
                        state.share_trail_consumes += 1
                        state.log_event(i, "share_trail_consume", float(state.get_tpv_fast(float(mid_price))),
                                        f"{key} REDUCE {capped_by or 'full'}", key=key)
                        if capped_by == "capped_by_position":
                            share_trail.reset_leg(state.share_trail, key)
                    state.check_accounting(mid_price, i, f"reduce_{key}")

                expansions.sort(key=lambda x: x["key"] == "VIRTUAL")
                remaining_funds = max(Decimal('0'), state.val_cash)

                for act in expansions:
                    key = act["key"]
                    requested_usdt = abs(Decimal(str(act["diff_usdt"])))
                    lev = Decimal(str(act.get("leverage", 1)))

                    if remaining_funds <= Decimal('0'):
                        state.skipped_expansions_counter += 1
                        record(act, "skipped", reason="no_remaining_funds")
                        state.log_event(i, "skipped_expansion", float(state.get_tpv_fast(float(mid_price))),
                                        "no remaining funds", key=key,
                                        requested_usdt=float(requested_usdt))
                        continue

                    if key == "VIRTUAL":
                        designated = state.designate_virtual(requested_usdt)
                        if designated <= Decimal('0'):
                            state.skipped_expansions_counter += 1
                            record(act, "skipped", reason="no_free_cash")
                            state.log_event(i, "skipped_expansion", float(state.get_tpv_fast(float(mid_price))),
                                            "no free cash", key=key,
                                            requested_usdt=float(requested_usdt))
                            continue
                        state.log_fill(i, "DESIGNATE", "VIRTUAL", Decimal('0'), mid_price, Decimal('0'),
                                       Decimal('0'), float(state.get_tpv_fast(float(mid_price))),
                                       mid_price=mid_price, leverage=Decimal('1'), trigger="rebalance",
                                       cycle_id=state.cycles + 1)
                        remaining_funds -= designated
                        record(act, "filled" if designated >= requested_usdt else "partial", designated,
                               "capped_by_free_cash" if designated < requested_usdt else "")
                        state.check_accounting(mid_price, i, "designate_virtual")
                        continue

                    side = action_side(key, False)
                    position_lev = state.l_lev if key == "BASE_LONG" else state.s_lev
                    exec_price = sim.execution_price(side, mid_price)
                    unit_cost = exec_price / position_lev + exec_price * sim.commission_pct
                    affordable_qty = remaining_funds / unit_cost if unit_cost > 0 else Decimal('0')
                    raw_qty = requested_usdt / mid_price
                    qty = quantize_qty(min(raw_qty, affordable_qty), step_size)
                    capped_by = "capped_by_free_cash" if affordable_qty < raw_qty else ""

                    if qty <= Decimal('0'):
                        if capped_by:
                            state.skipped_expansions_counter += 1
                        record(act, "skipped", reason=capped_by or "zero_qty_after_step")
                        state.log_event(i, "skipped_expansion" if capped_by else "skipped_action",
                                        float(state.get_tpv_fast(float(mid_price))),
                                        capped_by or "zero qty after step rounding", key=key,
                                        requested_usdt=float(requested_usdt))
                        continue
                    if not validate_notional(qty, mid_price, min_notional_usdt):
                        record(act, "skipped", reason="below_min_notional")
                        state.log_event(i, "skipped_action", float(state.get_tpv_fast(float(mid_price))),
                                        "below min notional", key=key,
                                        requested_usdt=float(requested_usdt))
                        continue

                    exec_price, commission = sim.simulate_market_execution(side, qty, mid_price)
                    margin_required = (qty * exec_price) / position_lev
                    required_cash = margin_required + commission
                    if required_cash > remaining_funds:
                        state.skipped_expansions_counter += 1
                        record(act, "skipped", reason="cash_check")
                        state.log_event(i, "skipped_expansion", float(state.get_tpv_fast(float(mid_price))),
                                        "cash check failed", key=key,
                                        requested_usdt=float(requested_usdt),
                                        required=float(required_cash), available=float(remaining_funds))
                        continue

                    if key == "BASE_LONG":
                        state.long_entry_price = ((state.pos_long * state.long_entry_price) + (qty * exec_price)) / (state.pos_long + qty)
                        state.pos_long += qty
                    else:
                        state.short_entry_price = ((state.pos_short * state.short_entry_price) + (qty * exec_price)) / (state.pos_short + qty)
                        state.pos_short += qty
                    state.val_cash -= required_cash
                    remaining_funds -= required_cash
                    state.log_fill(i, side, key, qty, exec_price, commission, Decimal('0'),
                                   float(state.get_tpv_fast(float(mid_price))), mid_price=mid_price,
                                   leverage=lev, trigger="rebalance", cycle_id=state.cycles + 1)
                    record(act, "partial" if capped_by else "filled", qty * exec_price, capped_by)
                    if state.share_trail is not None:
                        # confirmed fill (full or partial) consumes the ADD signal
                        share_trail.consume(state.share_trail, key, "ADD")
                        state.share_trail_consumes += 1
                        state.log_event(i, "share_trail_consume", float(state.get_tpv_fast(float(mid_price))),
                                        f"{key} ADD {capped_by or 'full'}", key=key)
                    state.check_accounting(mid_price, i, f"expand_{key}")

                if is_initial_formation or was_initial_pending:
                    missing = [
                        key for key, pos in (("BASE_LONG", state.pos_long), ("BASE_SHORT", state.pos_short))
                        if Decimal(str(targets.get(key, {}).get("share", 0))) > 0 and pos == 0
                    ]
                    residual = state.val_cash
                    if residual < 0:
                        state.initial_build_pending = None
                        state.cash_invariant_violation = {
                            "bar": int(i),
                            "cash": float(residual),
                            "virtual": float(state.virt_debt),
                            "positions": {"long": float(state.pos_long), "short": float(state.pos_short)},
                            "fills": list(state.fills),
                        }
                        state.log_event(i, "cash_invariant_violation",
                                        float(state.get_tpv(mid_price)),
                                        f"CASH={float(residual):.8f} after initial formation")
                    elif missing:
                        state.initial_build_pending = {
                            "missing": missing,
                            "attempts": int((state.initial_build_pending or {}).get("attempts", 0)) + 1,
                        }
                        state.log_event(i, "initial_build_pending",
                                        float(state.get_tpv(mid_price)),
                                        f"missing {','.join(missing)}; residual CASH={float(residual):.8f}",
                                        missing=missing)
                    else:
                        state.initial_build_pending = None
                        if residual > 0:
                            moved = state.designate_virtual(residual)
                            state.log_event(i, "initial_residual_to_virtual",
                                            float(state.get_tpv(mid_price)),
                                            f"moved {float(moved):.8f} to VIRTUAL",
                                            amount=float(moved))
                    state.initial_formation = {
                        "bar": int(i),
                        "complete": not missing,
                        "cash": float(state.val_cash),
                        "virtual": float(state.virt_debt),
                        "long_position": float(state.pos_long),
                        "short_position": float(state.pos_short),
                        "long_margin": float(state.margin_long()),
                        "short_margin": float(state.margin_short()),
                        "tpv": float(state.get_tpv(mid_price)),
                        "commission_paid": float(state.commission_paid),
                        "fills": list(state.fills),
                    }
                    state.check_accounting(mid_price, i, "initial_formation")

                state.cycles += 1
                state.last_rebalance_price = mid_price
                if len(state.fills) > fills_before:
                    exchange_records = [r for r in execution_records if r["key"] != "VIRTUAL"]
                    state.rebalance_log.append({
                        "step": i, "cycle_id": state.cycles, "price": float(mid_price),
                        "tpv_before": float(tpv_before), "tpv": float(state.get_tpv(mid_price)),
                        "tpv_basis": "after", "shares_basis": "before",
                        "shares": {"L": calc_res["share_long_pct"], "S": calc_res["share_short_pct"],
                                   "V": calc_res["share_virt_pct"], "C": calc_res["share_cash_pct"]},
                        "targets": {k: float(v.get("share", 0.0)) for k, v in targets.items()},
                        "thresholds": {"surplus": float(threshold_surplus), "deficit": float(threshold_deficit)},
                        "actions": [
                            f'{r["key"]} {r["side"]} req={r["requested_usdt"]:.2f}'
                            + (f' appr={r["approved_usdt"]:.2f}'
                               if abs(r["approved_usdt"] - r["requested_usdt"]) > 1e-9 else "")
                            + f' exec={r["executed_usdt"]:.2f} {r["status"]}'
                            + (f' ({r["reason"]})' if r["reason"] else "")
                            for r in execution_records
                        ],
                        "actions_detail": execution_records,
                        "requested_volume_usdt": float(sum(
                            (Decimal(str(r["requested_usdt"])) for r in exchange_records), Decimal('0'))),
                        "volume_usdt": float(sum(
                            (Decimal(str(r["executed_usdt"])) for r in exchange_records), Decimal('0'))),
                        "internal_transfer_usdt": float(sum(
                            (Decimal(str(r["executed_usdt"])) for r in execution_records if r["key"] == "VIRTUAL"),
                            Decimal('0'))),
                    })

            # Funding is applied BEFORE rebalance each bar so that a mid-bar
            # funding shortfall is detected before any position increase.
            # The shortfall block below handles the close/halt path.
            # (Moved to the top of the bar — see funding block after cooldown/halt guard.)

            # --- 2.5 Unified Predictive Cross-Margin Liquidation Guard ---
            # Расчет метрик Кросс-Маржи согласно спецификации Binance Futures
            mmr = Decimal('0.004')  # Maintenance Margin Rate для плеча <= 5x
            
            m_long = state.margin_long()
            m_short = state.margin_short()

            if state.pos_long > 0 or state.pos_short > 0:
                cross_liq_dist_pct = state.cross_margin_distance_pct(mid_price, mmr)

                liq_distance_warn_pct = Decimal(str(portfolio_cfg.get("liquidation_distance_warn_pct", 15.0)))
                liq_distance_crit_pct = Decimal(str(portfolio_cfg.get("liquidation_distance_crit_pct", 8.0)))

                if cross_liq_dist_pct <= liq_distance_crit_pct:
                    logger.warning(f"Bar {i}: Predictive CROSS Liquidation Guard triggered! Distance={float(cross_liq_dist_pct):.1f}%. Экстренное закрытие фьючерсных позиций.")
                    
                    # Закрытие Long
                    if state.pos_long > 0:
                        qty_close = state.pos_long
                        exec_p, comm = sim.simulate_market_execution("SELL", qty_close, mid_price)
                        realized_pnl = qty_close * (exec_p - state.long_entry_price)
                        state.val_cash += m_long + realized_pnl - comm
                        state.pos_long = Decimal('0')
                        state.long_entry_price = Decimal('0')
                        state.log_fill(i, "SELL", "BASE_LONG", qty_close, exec_p, comm, realized_pnl,
                                       float(state.get_tpv_fast(float(mid_price))), mid_price=mid_price,
                                       leverage=l_lev, trigger="liquidation", cycle_id=state.cycles)

                    # Закрытие Short
                    if state.pos_short > 0:
                        qty_close = state.pos_short
                        exec_p, comm = sim.simulate_market_execution("BUY", qty_close, mid_price)
                        realized_pnl = qty_close * (state.short_entry_price - exec_p)
                        state.val_cash += m_short + realized_pnl - comm
                        state.pos_short = Decimal('0')
                        state.short_entry_price = Decimal('0')
                        state.log_fill(i, "BUY", "BASE_SHORT", qty_close, exec_p, comm, realized_pnl,
                                       float(state.get_tpv_fast(float(mid_price))), mid_price=mid_price,
                                       leverage=s_lev, trigger="liquidation", cycle_id=state.cycles)

                    state.liquidations_counter += 1
                    state.check_accounting(mid_price, i, "predictive_liquidation")
                    if state.share_trail is not None:
                        share_trail.reset_all(state.share_trail)
                        state.log_event(i, "share_trail_reset_all", float(state.get_tpv_fast(float(mid_price))),
                                        "predictive liquidation close")
                    state.dormant_capital = Decimal(str(state.get_tpv_fast(float(mid_price)) + float(state.siphoning_reserve)))
                    state.cooldown_until_bar = i + cooldown_bars_duration
                    state.log_event(i, "liquidation", float(state.dormant_capital),
                                    f"predictive cross distance {float(cross_liq_dist_pct):.2f}%")
                    state.log_event(i, "cooldown_start", float(state.dormant_capital),
                                    "predictive liquidation", until_bar=int(state.cooldown_until_bar),
                                    reason="liquidation")
                    logger.warning(f"Bar {i}: Entering {toxic_cooldown_days}d cooldown after Predictive Liquidation.")
                    state.history.append(float(state.dormant_capital))
                    continue
                elif cross_liq_dist_pct <= liq_distance_warn_pct:
                    logger.debug(f"Bar {i}: CROSS Liquidation Warning! Distance={float(cross_liq_dist_pct):.1f}%")

            # --- 3. Post-Factum Cross-Margin Liquidation Check (Абсолютный Фоллбэк) ---
            if state.pos_long > 0 or state.pos_short > 0:
                upnl_l = state.pos_long * (mid_price - state.long_entry_price) if state.pos_long > 0 else Decimal('0')
                upnl_s = state.pos_short * (state.short_entry_price - mid_price) if state.pos_short > 0 else Decimal('0')
                if state.hard_liquidation_triggered(mid_price, mmr):
                    logger.warning(f"Bar {i}: HARD CROSS LIQUIDATION! Весь баланс суб-аккаунта уничтожен.")

                    # The whole futures account (including the VIRTUAL cash bucket) is wiped.
                    wiped_tpv = state.get_tpv(mid_price)
                    state.realized_total += upnl_l + upnl_s
                    state.val_cash = Decimal('0')
                    state.virt_debt = Decimal('0')
                    state.pos_long = Decimal('0')
                    state.long_entry_price = Decimal('0')
                    state.pos_short = Decimal('0')
                    state.short_entry_price = Decimal('0')
                    state.liquidation_loss_total += wiped_tpv
                    state.liquidations_counter += 1
                    state.check_accounting(mid_price, i, "hard_liquidation")
                    if state.share_trail is not None:
                        share_trail.reset_all(state.share_trail)
                        state.log_event(i, "share_trail_reset_all", float(state.get_tpv_fast(float(mid_price))),
                                        "hard liquidation wipe")
                    state.dormant_capital = Decimal(str(state.siphoning_reserve))
                    state.cooldown_until_bar = i + cooldown_bars_duration
                    state.log_event(i, "liquidation", float(state.dormant_capital), "HARD CROSS LIQUIDATION")
                    state.log_event(i, "cooldown_start", float(state.dormant_capital),
                                    "hard liquidation", until_bar=int(state.cooldown_until_bar),
                                    reason="liquidation")
                    logger.warning(f"Bar {i}: Entering {toxic_cooldown_days}d cooldown after HARD Liquidation.")
                    state.history.append(float(state.dormant_capital))
                    continue

            # --- 4. SAFE Siphoning ---
            tpv_f = state.get_tpv_fast(float(mid_price))
            total_tpv_with_reserve = tpv_f + float(state.siphoning_reserve)
            initial_capital_f = float(state.initial_capital)
            total_surplus = total_tpv_with_reserve - initial_capital_f
            siphoning_threshold_abs = initial_capital_f * (siphoning_threshold_pct / 100.0)

            if total_surplus > float(state.siphoning_reserve) + max(0.1, siphoning_threshold_abs):
                new_profit = total_surplus - float(state.siphoning_reserve)
                siphon_amount = new_profit * (1 - reinvestment_ratio)
                if siphon_amount > 0.1:
                    state.siphoning_reserve += Decimal(str(siphon_amount))
                    state.val_cash -= Decimal(str(siphon_amount))
                    total_tpv_with_reserve -= siphon_amount
                    state.siphoning_log.append({
                        "bar": int(i), "amount": float(siphon_amount),
                        "reserve": float(state.siphoning_reserve),
                    })

            # --- 4b. Max Drawdown Limit (Emergency Stop) ---
            if max_drawdown_limit_pct < 100.0:
                _dd_base = state.tpv_ath if state.tpv_ath > 0 else initial_capital_f
                drawdown_threshold = _dd_base * (1.0 - max_drawdown_limit_pct / 100.0)
                if total_tpv_with_reserve < drawdown_threshold:
                    logger.warning(f"Bar {i}: MAX DRAWDOWN LIMIT reached! TPV={total_tpv_with_reserve:.2f} < threshold={drawdown_threshold:.2f} ({max_drawdown_limit_pct}% DD from ATH={_dd_base:.2f}). Stopping.")
                    state.trailing_stop_triggered = True
                    state.stops_counter += 1
                    # Закрыть все позиции по текущей цене
                    if state.pos_long > 0:
                        qty_close = state.pos_long
                        exec_p, comm = sim.simulate_market_execution("SELL", qty_close, mid_price)
                        realized_pnl = qty_close * (exec_p - state.long_entry_price)
                        m_l = (qty_close * state.long_entry_price) / l_lev
                        state.val_cash += m_l + realized_pnl - comm
                        state.pos_long = Decimal('0')
                        state.long_entry_price = Decimal('0')
                        state.log_fill(i, "SELL", "BASE_LONG", qty_close, exec_p, comm, realized_pnl,
                                       float(state.get_tpv_fast(float(mid_price))), mid_price=mid_price,
                                       leverage=l_lev, trigger="max_dd_stop", cycle_id=state.cycles)
                    if state.pos_short > 0:
                        qty_close = state.pos_short
                        exec_p, comm = sim.simulate_market_execution("BUY", qty_close, mid_price)
                        realized_pnl = qty_close * (state.short_entry_price - exec_p)
                        m_s = (qty_close * state.short_entry_price) / s_lev
                        state.val_cash += m_s + realized_pnl - comm
                        state.pos_short = Decimal('0')
                        state.short_entry_price = Decimal('0')
                        state.log_fill(i, "BUY", "BASE_SHORT", qty_close, exec_p, comm, realized_pnl,
                                       float(state.get_tpv_fast(float(mid_price))), mid_price=mid_price,
                                       leverage=s_lev, trigger="max_dd_stop", cycle_id=state.cycles)
                    state.check_accounting(mid_price, i, "max_dd_stop")
                    if state.share_trail is not None:
                        share_trail.reset_all(state.share_trail)
                        state.log_event(i, "share_trail_reset_all", float(state.get_tpv_fast(float(mid_price))),
                                        "max dd stop close")
                    state.dormant_capital = Decimal(str(state.get_tpv_fast(float(mid_price)) + float(state.siphoning_reserve)))
                    state.cooldown_until_bar = len(df) + 1  # больше не стартуем
                    state.log_event(i, "max_dd_stop", float(state.dormant_capital),
                                    f"max drawdown limit {max_drawdown_limit_pct}%")
                    state.log_event(i, "cooldown_start", float(state.dormant_capital),
                                    "max drawdown limit", until_bar=int(state.cooldown_until_bar),
                                    reason="max_dd_stop")
                    state.history.append(float(state.dormant_capital))
                    continue

            # --- 4c. Trailing Stop ---
            if total_tpv_with_reserve > state.tpv_ath:
                state.tpv_ath = total_tpv_with_reserve
                # Floor: tpv_ath cannot be below initial_capital
                if state.tpv_ath < initial_capital_f:
                    state.tpv_ath = initial_capital_f
                state.trailing_stop_violation_start = 0.0

            if (trailing_stop_pct > 0
                    and state.tpv_ath > initial_capital_f * (1 + trailing_stop_activation_pct / 100.0)):
                drawdown_from_ath = (1 - total_tpv_with_reserve / state.tpv_ath) * 100.0
                if drawdown_from_ath >= trailing_stop_pct:
                    if state.trailing_stop_violation_start == 0:
                        state.trailing_stop_violation_start = float(i)
                        logger.warning(f"Bar {i}: Trailing Stop threshold breached (DD {drawdown_from_ath:.2f}% from ATH).")
                    else:
                        elapsed_bars = i - int(state.trailing_stop_violation_start)
                        timeout_bars = max(1, trailing_stop_timeout_sec // 60)
                        if elapsed_bars >= timeout_bars:
                            logger.warning(f"Bar {i}: Trailing Stop triggered. Entering {toxic_cooldown_days}d cooldown.")
                            state.trailing_stop_triggered = True
                            state.stops_counter += 1
                            for leg, qty in (("BASE_LONG", state.pos_long), ("BASE_SHORT", state.pos_short)):
                                state.close_position_for_funding_shortfall(
                                    leg, qty, mid_price, sim, i, trigger="equity_trailing_stop")
                            state.check_accounting(mid_price, i, "equity_trailing_stop")
                            if state.share_trail is not None:
                                share_trail.reset_all(state.share_trail)
                            total_tpv_with_reserve = float(state.get_tpv(mid_price) + state.siphoning_reserve)
                            state.log_event(i, "trailing_stop", total_tpv_with_reserve, f"DD {drawdown_from_ath:.2f}% from ATH")
                            state.dormant_capital = Decimal(str(total_tpv_with_reserve))
                            state.cooldown_until_bar = i + cooldown_bars_duration
                            state.log_event(i, "cooldown_start", float(state.dormant_capital),
                                            f"trailing stop DD {drawdown_from_ath:.2f}%",
                                            until_bar=int(state.cooldown_until_bar),
                                            reason="trailing_stop")
                            state.history.append(float(state.dormant_capital))
                            continue

            # --- 5. Bar-boundary accounting invariant ---
            current_tpv = state.get_tpv(mid_price)
            assert current_tpv > Decimal('0'), "Critical portfolio default: TPV <= 0"
            state.check_accounting(mid_price, i, "bar_close")
            state.history.append(float(current_tpv + state.siphoning_reserve))

        equity_curve: npt.NDArray[np.float64] = np.array(state.history)
        profit_pct: float = (equity_curve[-1] / (initial_capital + 1e-9) - 1) * 100
        max_eq: npt.NDArray[np.float64] = np.maximum(np.maximum.accumulate(equity_curve), initial_capital)
        dd: npt.NDArray[np.float64] = (max_eq - equity_curve) / (max_eq + 1e-9)
        max_dd_pct: float = np.max(dd) * 100 if len(dd) > 0 else 0.0
        asset_chg_pct: float = (close_prices[-1] / (close_prices[0] + 1e-9) - 1) * 100

        # --- Sortino Ratio ---
        # Средняя доходность за бар / downside deviation
        if len(equity_curve) > 1:
            bar_returns = np.diff(equity_curve) / (equity_curve[:-1] + 1e-9)
            mean_return = np.mean(bar_returns)
            # Downside deviation: только отрицательные доходности
            negative_returns = bar_returns[bar_returns < 0]
            if len(negative_returns) > 0:
                downside_dev = np.sqrt(np.mean(negative_returns ** 2))
                sortino_ratio = mean_return / (downside_dev + 1e-9)
            else:
                sortino_ratio = mean_return / 1e-9  # нет отрицательных — идеально
        else:
            sortino_ratio = 0.0

        if not quiet:
            logger.info(f"Refactored Cross-Margin Backtest for {base_ticker}:")
            logger.info(f"Profit: {profit_pct:+.2f}% | MaxDD: {max_dd_pct:.2f}% | Cycles: {state.cycles}")
            logger.info(f"Sortino: {sortino_ratio:.4f} | Asset Change: {asset_chg_pct:+.2f}% | SAFE Reserve: {state.siphoning_reserve:.2f} USDT")
            logger.info(f"Skipped Expansions: {state.skipped_expansions_counter} | Stops: {state.stops_counter} | Liqs: {state.liquidations_counter}")
            logger.info(f"Trend Guard blocks: {tg_blocked_count} | Velocity Guard blocks: {vg_blocked_count} | Trend-add blocks: {trend_add_blocks}")
            logger.info(f"Share trailing LONG/SHORT: distance={trail_distance:g} | model=pullback_rebound | consumed fills={state.share_trail_consumes}")
            if sim:
                s = sim.get_summary()
                logger.info(f"Market Orders: {s['filled']}/{s['attempted']} filled")

        final_price = Decimal(str(close_prices[-1])) if len(close_prices) else Decimal('0')
        final_unrealized = Decimal('0')
        if state.pos_long > 0:
            final_unrealized += state.pos_long * (final_price - state.long_entry_price)
        if state.pos_short > 0:
            final_unrealized += state.pos_short * (state.short_entry_price - final_price)
        final_tpv = state.get_tpv(final_price)
        final_tpv_with_reserve = final_tpv + state.siphoning_reserve
        expected_tpv = state.accounting_equity(final_price)
        residual = final_tpv_with_reserve - expected_tpv
        tolerance = max(Decimal('1e-6'), abs(expected_tpv) * Decimal('1e-9'))

        return {
            "profit_pct": float(profit_pct),
            "max_dd_pct": float(max_dd_pct),
            "cycles": int(state.cycles),
            "asset_chg_pct": float(asset_chg_pct),
            "siphoning_reserve": float(state.siphoning_reserve),
            "skipped_expansions": int(state.skipped_expansions_counter),
            "partial_expansions": sum(
                1 for rb in state.rebalance_log for a in rb.get("actions_detail", [])
                if a.get("status") == "partial" and not a.get("is_reduction")
            ),
            "skipped_actions": sum(
                1 for rb in state.rebalance_log for a in rb.get("actions_detail", [])
                if a.get("status") == "skipped"
            ),
            "liquidations": int(state.liquidations_counter),
            "trailing_stops": int(state.stops_counter),
            "trailing_stop_triggered": state.stops_counter > 0,
            "trend_guard_blocks": int(tg_blocked_count),
            "velocity_guard_blocks": int(vg_blocked_count),
            "trend_add_blocks": int(trend_add_blocks),
            "sortino_ratio": float(sortino_ratio),
            "equity_curve": equity_curve.tolist(),
            "fills": state.fills,
            "fill_count": state.fill_count,
            "exchange_fill_count": int(state.fill_count - state.internal_transfer_count),
            "internal_transfer_count": int(state.internal_transfer_count),
            "accounting": {
                "expected_tpv": float(expected_tpv),
                "actual_tpv": float(final_tpv_with_reserve),
                "residual": float(residual),
                "tolerance": float(tolerance),
                "ok": abs(residual) <= tolerance,
            },
            "accounting_ok": bool(abs(residual) <= tolerance),
            "accounting_residual": float(residual),
            "cash_invariant_violation": state.cash_invariant_violation,
            "initial_formation": state.initial_formation,
            "initial_build_pending": state.initial_build_pending,
            "events": state.events,
            "rebalances": state.rebalance_log,
            "siphoning_log": state.siphoning_log,
            "initial_capital": float(initial_capital),
            "final_equity": float(final_tpv_with_reserve),
            "final_tpv": float(final_tpv_with_reserve),
            "final_unrealized_pnl": float(final_unrealized),
            "realized_pnl_total": float(state.realized_total),
            "commission_paid": float(state.commission_paid),
            "funding_model": state.funding_model,
            "funding_paid": float(state.funding_paid),
            "funding_from_virtual": float(state.funding_from_virtual),
            "funding_events": [f for f in state.fills if f.get("trigger") == "funding_shortfall"],
            "funding_halt_active": state.funding_halt_active,
            "slippage_paid": float(state.slippage_paid),
            "liquidation_loss_total": float(state.liquidation_loss_total),
            "open_positions": {"long": float(state.pos_long), "short": float(state.pos_short)},
            "ticker": base_ticker,
            "share_trail": state.share_trail,
            "share_trail_spec": state.share_trail_spec,
            "share_trail_signals": int(state.share_trail_signals),
            "share_trail_consumes": int(state.share_trail_consumes),
            "settings": {
                "fuse_enabled": fuse_enabled,
                "commission_pct": float(sim.commission_pct),
                "slippage_pct": float(slippage),
                "targets": {k: {"share": float(v.get("share", 0.0)), "leverage": float(v.get("leverage", 1))}
                            for k, v in targets.items()},
                "thresholds": {"surplus": float(threshold_surplus), "deficit": float(threshold_deficit)},
                "siphoning_threshold_pct": float(siphoning_threshold_pct),
                "reinvestment_ratio": float(reinvestment_ratio),
                "funding_rate_per_day": float(daily_funding_rate),
                "bars_per_day": int(bars_per_day),
                "trailing_stop_pct": float(trailing_stop_pct),
                "trailing_stop_activation_pct": float(trailing_stop_activation_pct),
                "trailing_stop_timeout_sec": int(trailing_stop_timeout_sec),
                "max_drawdown_limit_pct": float(max_drawdown_limit_pct),
                "cooldown_bars": int(cooldown_bars_duration),
                "min_notional_usdt": float(min_notional_usdt),
                "share_trail_distance": float(trail_distance),
                "trend_add_block": portfolio_cfg.get("trend_add_block"),
                "account_free_margin": 0.0,
            },
            "data_window": data_window
        }
    except Exception as e:
        logger.error(f"Backtest failed: {e}")
        traceback.print_exc()
        return None

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--ticker", default=None)
    parser.add_argument("--days", type=float, default=1.0)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--capital", type=float, default=None)
    parser.add_argument("--threshold-surplus", type=float, default=None)
    parser.add_argument("--threshold-deficit", type=float, default=None)
    parser.add_argument("--since", default="2026-03-30 07:00",
                        help="backtest start (UTC). Standard BTCUSDT window: 2026-03-30 07:00. "
                             "Pass empty string '' to fall back to pure --days mode.")
    parser.add_argument("--until", default="2026-10-18 20:59",
                        help="hard end boundary (UTC). Standard: 2026-10-18 20:59 (end of local data). "
                             "Pass empty string '' to run to end of data.")
    args = parser.parse_args()
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    since = args.since.strip() or None
    until = args.until.strip() if args.until else None
    import re
    def _norm_wd(s: str) -> str:
        s = s.strip()
        # 'YYYY-MM-DDHH:MM' -> 'YYYY-MM-DD HH:MM' (copypasta from terminals drops the space)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}\d{2}:\d{2}", s):
            s = s[:10] + " " + s[10:]
        return s.replace("T", " ").strip()
    if since: since = _norm_wd(since)
    if until: until = _norm_wd(until)
    res = asyncio.run(run_backtest(args.config, data_dir, args.live, args.ticker, args.days,
                                   capital_override=args.capital,
                                   threshold_surplus_override=args.threshold_surplus,
                                   threshold_deficit_override=args.threshold_deficit,
                                   since_utc=since, until_utc=until))

    # auto-refresh the single dashboard: write the result jsons, regenerate the html,
    # and delete stale versioned copies only for the current prefix
    if res is not None:
        import pathlib, subprocess
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        reports = pathlib.Path(root) / "reports"
        try:
            _cfg = json.load(open(args.config, "r", encoding="utf-8"))
            _ticker = (args.ticker or _cfg.get("base_ticker", "BTCUSDT")).upper()
        except Exception:
            _ticker = (args.ticker or "BTCUSDT").upper()
        if _ticker == "ZECUSDT":
            window_bars = int(res.get("data_window", {}).get("bars", int(args.days * 1440)))
            window_days = window_bars / 1440.0
            prefix = "zec_12m" if abs(window_days - 365.0) < 0.5 else f"zec_{int(round(window_days))}d"
            viz = pathlib.Path(root) / "scripts" / "visualize_zec_dashboard.py"
            viz_args = [sys.executable, str(viz), prefix]
        else:
            prefix = "btc_365d"
            viz = pathlib.Path(root) / "scripts" / "visualize_365d_dashboard.py"
            viz_args = [sys.executable, str(viz)]
        curve = res.pop("equity_curve", [])
        (reports / f"{prefix}_backtest_result.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
        (reports / f"{prefix}_equity.json").write_text(json.dumps(curve), encoding="utf-8")
        r = subprocess.run(viz_args, capture_output=True, text=True)
        if r.returncode == 0:
            print(r.stdout.strip())
            for extra in reports.glob(f"{prefix}_dashboard_v*.html"):
                extra.unlink()
                print(f"removed stale {extra.name}")
        else:
            print("dashboard regeneration failed:")
            print(r.stderr[-2000:])
