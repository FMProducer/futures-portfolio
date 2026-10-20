"""Offline interactive dashboard for a single backtest run.

Usage: python scripts/visualize_zec_dashboard.py [prefix]
       default prefix = zec_12m -> reports/zec_12m_*.json

Reads the engine JSON + the local feather used by the run and writes one
self-contained HTML (local Plotly asset, no network requests).
"""
from __future__ import annotations

import html
import json
import math
import os
import sys
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from backtest.dashboard_attribution import reconstruct_attribution  # noqa: E402

REPORTS = os.path.join(ROOT, "reports")
DATA = os.path.join(ROOT, "data")
TEMPLATE = os.path.join(ROOT, "scripts", "dashboard_template.html")
PLOTLY_JS = "plotly-2.27.0.min.js"
MAX_CANDLES = 36_000
MAX_LINE_POINTS = 20_000


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _round(value: Any, digits: int = 6) -> Optional[float]:
    out = _finite(value, default=float("nan"))
    if not math.isfinite(out):
        return None
    return round(out, digits)


def _money(value: float) -> str:
    value = _finite(value)
    if value == 0:
        return "0"
    if abs(value) >= 1e9 or abs(value) < 1e-4:
        return f"{value:.3e}"
    if abs(value) >= 1e6:
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def _pct(value: float, digits: int = 2) -> str:
    value = _finite(value)
    if abs(value) >= 1e6:
        return f"{value:+.3e}%"
    return f"{value:+.{digits}f}%"


def _int(value: Any) -> str:
    return f"{int(_finite(value)):,}"


def _utc(ms: int) -> str:
    return pd.Timestamp(int(ms), unit="ms", tz="UTC").strftime("%Y-%m-%d %H:%M:%S")


def _lttb_indices(values: Sequence[float], threshold: int) -> List[int]:
    y = [float(v) for v in values]
    n = len(y)
    if threshold >= n or n <= 2:
        return list(range(n))
    every = (n - 2) / (threshold - 2)
    out = [0]
    anchor = 0
    for i in range(threshold - 2):
        point_start = int(math.floor(i * every)) + 1
        point_end = min(int(math.floor((i + 1) * every)) + 1, n - 1)
        avg_start = point_end
        avg_end = min(int(math.floor((i + 2) * every)) + 1, n)
        if avg_end <= avg_start:
            avg_end = min(avg_start + 1, n)
        bucket = y[avg_start:avg_end]
        if not bucket:
            continue
        avg_y = sum(bucket) / len(bucket)
        avg_x = (avg_start + avg_end - 1) / 2.0
        max_area = -1.0
        max_idx = point_start
        ya = y[anchor]
        for j in range(point_start, max(point_start + 1, point_end)):
            area = abs((anchor - avg_x) * (y[j] - ya) - (anchor - j) * (avg_y - ya))
            if area > max_area:
                max_area = area
                max_idx = j
        out.append(max_idx)
        anchor = max_idx
    out.append(n - 1)
    return list(dict.fromkeys(out))


def _minmax_indices(values: Sequence[float], target: int) -> List[int]:
    n = len(values)
    if n <= target:
        return list(range(n))
    bucket = max(1, n // max(1, target // 2))
    out = {0, n - 1}
    for start in range(0, n, bucket):
        end = min(n, start + bucket)
        if end <= start:
            continue
        chunk = np.asarray(values[start:end], dtype=float)
        out.add(start + int(np.argmin(chunk)))
        out.add(start + int(np.argmax(chunk)))
    return sorted(out)


def _sample_indices(n: int, target: int, extra: Iterable[int]) -> set:
    step = max(1, math.ceil(n / max(1, target)))
    out = set(range(0, n, step))
    out.add(n - 1)
    for idx in extra:
        idx = int(idx)
        if 0 <= idx < n:
            out.add(idx)
    return out


def _aggregate_ohlc(times: Sequence[int], open_: np.ndarray, high: np.ndarray,
                    low: np.ndarray, close: np.ndarray) -> Dict[str, Any]:
    n = len(times)
    if n <= MAX_CANDLES:
        bin_size = 1
    else:
        bin_size = 1440
        for candidate in (1, 2, 3, 5, 10, 15, 20, 30, 60, 120, 180, 240, 360, 720, 1440):
            if n / candidate <= MAX_CANDLES:
                bin_size = candidate
                break
    if bin_size == 1:
        return {
            "times": [int(t) for t in times],
            "open": [_round(v, 6) for v in open_],
            "high": [_round(v, 6) for v in high],
            "low": [_round(v, 6) for v in low],
            "close": [_round(v, 6) for v in close],
            "bin": "1m",
        }
    groups = np.arange(n, dtype=np.int64) // bin_size
    frame = pd.DataFrame({
        "group": groups, "time": np.asarray(times, dtype=np.int64),
        "open": open_, "high": high, "low": low, "close": close,
    })
    agg = frame.groupby("group", sort=True).agg(
        time=("time", "first"), open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"),
    )
    label = f"{bin_size}m" if bin_size < 60 else f"{bin_size // 60}h"
    return {
        "times": [int(v) for v in agg["time"].to_numpy()],
        "open": [_round(v, 6) for v in agg["open"].to_numpy()],
        "high": [_round(v, 6) for v in agg["high"].to_numpy()],
        "low": [_round(v, 6) for v in agg["low"].to_numpy()],
        "close": [_round(v, 6) for v in agg["close"].to_numpy()],
        "bin": label,
    }


def _series(values: Sequence[float], indices: Sequence[int]) -> List[Optional[float]]:
    return [_round(values[i], 2) for i in indices]


def _clip_range(values: Sequence[float]) -> Dict[str, float]:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if not arr.size:
        return {"min": 0.0, "max": 1.0, "p1": 0.0, "p99": 1.0, "median": 0.0}
    return {
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
        "p1": float(np.percentile(arr, 1)),
        "p99": float(np.percentile(arr, 99)),
        "median": float(np.median(arr)),
    }


def _infer_ticker(prefix: str, result: Dict[str, Any]) -> str:
    ticker = str(result.get("ticker") or "").upper()
    if ticker:
        return ticker
    return "ZECUSDT" if prefix.lower().startswith("zec") else "BTCUSDT"


def _safe_events(events: Sequence[Dict[str, Any]], times: np.ndarray, n: int,
                 closes: np.ndarray, equity: np.ndarray) -> List[Dict[str, Any]]:
    out = []
    for event in events:
        bar = int(event.get("bar", -1))
        if not 0 <= bar < n:
            continue
        item = {
            "bar": bar,
            "time": int(times[bar]),
            "utc": _utc(int(times[bar])),
            "kind": str(event.get("kind", "event")),
            "tpv": _round(event.get("tpv"), 2),
            "price": _round(closes[bar], 8),
            "equity": _round(equity[bar], 2),
            "detail": str(event.get("detail", "")),
        }
        for key in ("reason", "until_bar", "keys", "rebased", "reserve_reset", "key"):
            if key in event:
                item[key] = event[key]
        out.append(item)
    return out


def _safe_fills(fills: Sequence[Dict[str, Any]], times: np.ndarray, n: int,
                ticker: str) -> List[Dict[str, Any]]:
    out = []
    for idx, fill in enumerate(fills):
        bar = int(fill.get("bar", 0))
        bar = min(max(bar, 0), n - 1) if n else 0
        price = _finite(fill.get("price"))
        mid = _finite(fill.get("mid"), price)
        qty = _finite(fill.get("qty"))
        leg = str(fill.get("leg", ""))
        trigger = str(fill.get("trigger", "rebalance"))
        out.append({
            "i": idx,
            "bar": bar,
            "time": int(times[bar]) if n else 0,
            "utc": _utc(int(times[bar])) if n else "",
            "side": str(fill.get("side", "")),
            "leg": leg,
            "symbol": "VIRTUAL" if leg == "VIRTUAL" else ticker,
            "qty": _round(qty, 8),
            "price": _round(price, 8),
            "mid": _round(mid, 8),
            "notional": _round(abs(qty) * price, 2),
            "leverage": _round(fill.get("leverage"), 3),
            "commission": _round(fill.get("commission"), 6),
            "funding_cum": _round(fill.get("funding_cum"), 4),
            "realized_pnl": _round(fill.get("realized_pnl"), 6),
            "tpv": _round(fill.get("tpv"), 2),
            "trigger": trigger,
            "rebalance_id": int(fill.get("rebalance_id") or 0),
            "event_type": trigger,
            "slip": _round(abs(qty) * abs(price - mid), 6),
        })
    return out


def _safe_rebalances(rebalances: Sequence[Dict[str, Any]], times: np.ndarray, n: int,
                     settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    defaults = settings.get("targets", {})
    for idx, reb in enumerate(rebalances):
        bar = int(reb.get("step", 0))
        bar = min(max(bar, 0), n - 1) if n else 0
        targets = reb.get("targets") or {k: v.get("share", 0.0) for k, v in defaults.items()}
        out.append({
            "i": idx,
            "bar": bar,
            "time": int(times[bar]) if n else 0,
            "utc": _utc(int(times[bar])) if n else "",
            "cycle_id": int(reb.get("cycle_id") or idx + 1),
            "price": _round(reb.get("price"), 8),
            "tpv_before": _round(reb.get("tpv_before"), 2),
            "tpv": _round(reb.get("tpv"), 2),
            "shares": reb.get("shares", {}),
            "shares_basis": reb.get("shares_basis", "before"),
            "targets": targets,
            "thresholds": reb.get("thresholds") or settings.get("thresholds", {}),
            "actions": reb.get("actions", []),
            "actions_detail": reb.get("actions_detail", []),
            "requested_volume_usdt": _round(reb.get("requested_volume_usdt"), 2),
            "volume_usdt": _round(reb.get("volume_usdt"), 2),
            "internal_transfer_usdt": _round(reb.get("internal_transfer_usdt"), 2),
        })
    return out


def _cooldowns(events: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    current = None
    for event in events:
        kind = event.get("kind")
        if kind == "cooldown_start":
            current = event
        elif kind in ("cooldown_end", "cooldown_dead") and current is not None:
            out.append({
                "start_bar": current["bar"],
                "end_bar": event["bar"],
                "start_utc": current.get("utc", ""),
                "end_utc": event.get("utc", ""),
                "bars": max(0, int(event["bar"]) - int(current["bar"])),
                "reason": current.get("reason", current.get("detail", "")),
                "detail": current.get("detail", ""),
            })
            current = None
    if current is not None:
        out.append({
            "start_bar": current["bar"],
            "end_bar": None,
            "start_utc": current.get("utc", ""),
            "end_utc": "",
            "bars": None,
            "reason": current.get("reason", current.get("detail", "")),
            "detail": current.get("detail", ""),
        })
    return out


def _quality_checks(
    *, n: int, dw: Dict[str, Any], equity: np.ndarray, closes: np.ndarray,
    ohlc: Optional[Dict[str, Any]], times: np.ndarray, time_source: str,
    fills_count: int, fills_total: int, exchange_fills: int, internal_transfers: int,
    attr_totals: Dict[str, float], result: Dict[str, Any], initial: float, final: float,
    outlier_ratio: float, max_abs_return: float,
) -> List[Dict[str, str]]:
    checks: List[Dict[str, str]] = []

    def add(level: str, label: str, detail: str = "") -> None:
        checks.append({"level": level, "label": label, "detail": detail})

    input_bars = int(dw.get("input_bars") or n)
    add("ok", "Bars", f"{n:,} used / {input_bars:,} input")
    if dw.get("bars") and int(dw["bars"]) != n:
        add("warn", "Bars vs data_window", f"data_window.bars={dw['bars']:,}, used={n:,}")
    if time_source == "mtime":
        add("warn", "Timestamp source", "feather has no time column; axis synthesized from file mtime")
    else:
        add("ok", "Timestamp source", "feather time column")
    if n > 1:
        diffs = np.diff(times)
        gaps = int(np.sum(diffs > 60_000))
        missing = int(np.sum(np.maximum(0, diffs // 60_000 - 1)))
        dups = int(np.sum(diffs == 0))
        negative = int(np.sum(diffs < 0))
        add("warn" if gaps or dups or negative else "ok", "Timestamp gaps",
            f"{gaps:,} gaps / {missing:,} missing minutes / {dups:,} duplicates / {negative:,} out of order")
    start_year = pd.Timestamp(int(times[0]), unit="ms", tz="UTC").year if n else 0
    end_year = pd.Timestamp(int(times[-1]), unit="ms", tz="UTC").year if n else 0
    add("ok" if 2000 <= start_year <= 2100 and 2000 <= end_year <= 2100 else "fail",
        "Date sanity", f"{start_year} → {end_year}")
    bad_prices = int(np.sum(~np.isfinite(closes)))
    bad_equity = int(np.sum(~np.isfinite(equity)))
    if ohlc:
        bad_prices += sum(int(np.sum(~np.isfinite(np.asarray(ohlc[k], dtype=float)))) for k in ("open", "high", "low", "close"))
    add("warn" if bad_prices or bad_equity else "ok", "Finite values",
        f"price={bad_prices:,} / equity={bad_equity:,}")
    add("fail" if exchange_fills == 0 else "ok", "Trades", f"{exchange_fills:,} exchange fills")
    add("ok" if n else "fail", "Equity", f"{n:,} points")
    add("ok", "Fill split", f"{exchange_fills:,} exchange / {internal_transfers:,} internal VIRTUAL transfers")
    if fills_total > fills_count:
        add("warn", "Fill log cap", f"{fills_count:,} of {fills_total:,} fills embedded")
    tol = max(1.0, abs(initial) * 1e-6, abs(final - initial) * 1e-6)
    residual = _finite(attr_totals.get("residual"))
    add("ok" if abs(residual) <= tol else "fail", "Attribution balance",
        f"residual {_money(residual)} USDT (tolerance {_money(tol)})")
    accounting_ok = result.get("accounting_ok")
    if accounting_ok is not None:
        acc_res = _finite(result.get("accounting_residual"))
        acc_tol = _finite((result.get("accounting") or {}).get("tolerance"))
        add("ok" if accounting_ok else "fail", "Engine accounting",
            f"TPV residual {_money(acc_res)} USDT (tolerance {_money(acc_tol)})")
    engine_net = (_finite(result.get("realized_pnl_total")) + _finite(result.get("final_unrealized_pnl"))
                  - _finite(result.get("commission_paid")) - _finite(result.get("funding_paid")))
    engine_diff = engine_net - (final - initial)
    add("ok" if abs(engine_diff) <= tol else "warn", "Execution-basis PnL",
        f"engine net vs final equity diff {_money(engine_diff)} USDT")
    for key, label in (("funding_paid", "Funding"), ("commission_paid", "Commissions"),
                       ("slippage_paid", "Slippage")):
        if key not in result:
            continue
        if key == "funding_paid":
            diff = _finite(result[key]) - _finite(attr_totals.get("funding"))
        elif key == "commission_paid":
            diff = _finite(result[key]) - _finite(attr_totals.get("commissions"))
        else:
            diff = _finite(result[key]) - _finite(attr_totals.get("slippage"))
        scale = max(1.0, abs(_finite(result.get(key))))
        add("ok" if abs(diff) <= scale * 1e-6 else "warn", f"{label} reconstruction",
            f"engine vs dashboard diff {_money(diff)}")
    add("warn" if outlier_ratio > 1e4 else "ok", "Equity outliers",
        f"max/median ratio {outlier_ratio:,.1f}")
    add("warn" if max_abs_return > 10 else "ok", "Largest bar return",
        f"{max_abs_return * 100:,.2f}%")
    if _finite(result.get("liquidations")):
        add("warn", "Liquidations", f"{int(result.get('liquidations', 0)):,} events")
    if _finite(result.get("trailing_stops")):
        add("warn", "Trailing / max-DD stops", f"{int(result.get('trailing_stops', 0)):,} events")
    return checks


def build_payload(prefix: str) -> Dict[str, Any]:
    result_path = os.path.join(REPORTS, f"{prefix}_backtest_result.json")
    equity_path = os.path.join(REPORTS, f"{prefix}_equity.json")
    if not os.path.exists(result_path) or not os.path.exists(equity_path):
        raise FileNotFoundError(f"Missing {result_path} or {equity_path}")

    with open(result_path, "r", encoding="utf-8") as fh:
        result = json.load(fh)
    with open(equity_path, "r", encoding="utf-8") as fh:
        equity_all = np.asarray(json.load(fh), dtype=float)

    dw = result.get("data_window") or {}
    feather = os.path.join(DATA, dw.get("feather") or f"{_infer_ticker(prefix, result)}_12m.feather")
    if not os.path.exists(feather):
        for candidate in (f"{_infer_ticker(prefix, result)}_12m.feather",
                          f"{_infer_ticker(prefix, result)}_live.feather"):
            path = os.path.join(DATA, candidate)
            if os.path.exists(path):
                feather = path
                break
    if not os.path.exists(feather):
        raise FileNotFoundError(f"Source feather not found: {feather}")

    frame = pd.read_feather(feather)
    offset = int(dw.get("offset", 0) or 0)
    n = min(len(equity_all), max(0, len(frame) - offset))
    if dw.get("bars"):
        n = min(n, int(dw["bars"]))
    if n <= 0:
        raise ValueError("Empty equity/price window")
    equity = equity_all[:n]
    closes = frame["close"].to_numpy(dtype=float)[offset:offset + n]
    ohlc_cols = all(col in frame.columns for col in ("open", "high", "low", "close"))
    open_ = frame["open"].to_numpy(dtype=float)[offset:offset + n] if ohlc_cols else None
    high = frame["high"].to_numpy(dtype=float)[offset:offset + n] if ohlc_cols else None
    low = frame["low"].to_numpy(dtype=float)[offset:offset + n] if ohlc_cols else None

    time_source = "mtime"
    times: Optional[np.ndarray] = None
    if "time" in frame.columns:
        try:
            candidate = pd.to_datetime(frame["time"], utc=True).dt.as_unit("ms").astype("int64").to_numpy()
            candidate = candidate[offset:offset + n]
            if len(candidate) == n and n and candidate[0] > 0:
                year = pd.Timestamp(int(candidate[0]), unit="ms", tz="UTC").year
                if 2000 <= year <= 2100:
                    times = candidate
                    time_source = "feather_time"
        except Exception:
            times = None
    if times is None:
        end_ts = None
        if dw.get("end_utc"):
            end_ts = pd.Timestamp(dw["end_utc"], tz="UTC")
        elif dw.get("until_utc"):
            end_ts = pd.Timestamp(dw["until_utc"], tz="UTC")
        else:
            end_ts = pd.Timestamp.fromtimestamp(os.path.getmtime(feather), tz="UTC").floor("min")
        end_ms = int(end_ts.timestamp() * 1000)
        times = end_ms - (n - 1 - np.arange(n, dtype=np.int64)) * 60_000

    ticker = _infer_ticker(prefix, result)
    initial = _finite(result.get("initial_capital", equity[0] if n else 0.0))
    final = _finite(equity[-1])
    profit_pct = _finite(result.get("profit_pct", (final / initial - 1) * 100 if initial else 0.0))
    settings = result.get("settings") or {}
    fills_raw = result.get("fills") or []
    events_raw = result.get("events") or []
    rebalances_raw = result.get("rebalances") or []
    siphoning_raw = result.get("siphoning_log") or []

    fills = _safe_fills(fills_raw, times, n, ticker)
    events = _safe_events(events_raw, times, n, closes, equity)
    rebalances = _safe_rebalances(rebalances_raw, times, n, settings)

    rate = _finite(settings.get("funding_rate_per_day", 0.0002), 0.0002) / max(
        1.0, _finite(settings.get("bars_per_day", 1440), 1440))
    extra = {int(f["bar"]) for f in fills_raw if 0 <= int(f.get("bar", 0)) < n}
    extra |= {int(e["bar"]) for e in events_raw if 0 <= int(e.get("bar", 0)) < n}
    attr_sample = _sample_indices(n, MAX_LINE_POINTS, extra)
    attr = reconstruct_attribution(
        closes.tolist(), equity.tolist(), fills_raw,
        initial_capital=initial, final_equity=final,
        funding_rate_per_bar=rate, events=events_raw,
        siphoning_log=siphoning_raw, sample_indices=attr_sample,
    )
    attr_totals = attr["totals"]
    attr_series = attr["series"]
    attr_idx = attr_series["idx"]

    runmax = np.maximum.accumulate(equity)
    dd_pct = np.where(runmax > 0, (runmax - equity) / runmax * 100.0, 0.0)
    dd_pct = np.maximum(dd_pct, 0.0)

    eq_idx = _minmax_indices(equity, MAX_LINE_POINTS)
    price_idx = _lttb_indices(closes, MAX_LINE_POINTS)
    ohlc = _aggregate_ohlc(times, open_, high, low, closes) if ohlc_cols else None

    price_stats = _clip_range(closes)
    equity_stats = _clip_range(equity)
    median = abs(equity_stats["median"])
    outlier_ratio = max(abs(equity_stats["min"]), abs(equity_stats["max"])) / max(1e-12, median)
    returns = np.diff(equity) / np.where(equity[:-1] != 0, equity[:-1], np.nan)
    returns = returns[np.isfinite(returns)]
    max_abs_return = float(np.max(np.abs(returns))) if returns.size else 0.0
    if returns.size > 1 and float(np.std(returns)) > 0:
        annual_bars = max(1.0, _finite(settings.get("bars_per_day", 1440), 1440) * 365.0)
        sharpe = float(np.mean(returns) / np.std(returns) * math.sqrt(annual_bars))
    else:
        sharpe = 0.0

    exchange_fills = [f for f in fills_raw if str(f.get("leg", "")) != "VIRTUAL"]
    internal_fills = [f for f in fills_raw if str(f.get("leg", "")) == "VIRTUAL"]
    closing = [f for f in exchange_fills if abs(_finite(f.get("realized_pnl"))) > 0]
    wins = [f for f in closing if _finite(f.get("realized_pnl")) > 0]
    losses = [f for f in closing if _finite(f.get("realized_pnl")) < 0]
    win_rate = (len(wins) / len(closing) * 100.0) if closing else 0.0
    gross_win = sum(_finite(f.get("realized_pnl")) for f in wins)
    gross_loss = abs(sum(_finite(f.get("realized_pnl")) for f in losses))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (math.inf if gross_win > 0 else 0.0)
    total_notional = sum(abs(_finite(f.get("qty"))) * _finite(f.get("price")) for f in exchange_fills)
    avg_trade = total_notional / len(exchange_fills) if exchange_fills else 0.0
    avg_equity = (initial + final) / 2.0
    turnover = total_notional / avg_equity if avg_equity else 0.0
    span_min = max(0.0, (int(times[-1]) - int(times[0])) / 60_000.0) if n else 0.0
    cycles = int(_finite(result.get("cycles")))
    executed_rebalances = len(rebalances_raw)
    avg_rebalance_min = span_min / max(1, executed_rebalances or cycles)
    buys = sum(1 for f in exchange_fills if str(f.get("side", "")).upper() == "BUY")
    sells = sum(1 for f in exchange_fills if str(f.get("side", "")).upper() == "SELL")
    exchange_fill_count = int(_finite(result.get("exchange_fill_count", len(exchange_fills))))
    internal_transfer_count = int(_finite(result.get("internal_transfer_count", len(internal_fills))))
    # One basis for KPIs and reconciliation: mark-to-mid attribution (slippage shown separately).
    funding = _finite(attr_totals.get("funding"))
    funding_from_virtual = _finite(result.get("funding_from_virtual", 0.0))
    commissions = _finite(attr_totals.get("commissions"))
    realized = _finite(attr_totals.get("realized"))
    unrealized = _finite(attr_totals.get("unrealized"))
    slippage = _finite(attr_totals.get("slippage"))
    reserve = _finite(attr_totals.get("reserve"))
    residual = _finite(attr_totals.get("residual"))
    net_pnl = final - initial
    accounting_ok = result.get("accounting_ok")
    accounting_residual = _finite(result.get("accounting_residual"))
    accounting_tolerance = _finite((result.get("accounting") or {}).get("tolerance"))
    partial_expansions = sum(
        1 for rb in rebalances_raw for a in (rb.get("actions_detail") or [])
        if a.get("status") == "partial" and not a.get("is_reduction")
    )

    def kpi(label: str, value: str, tip: str, cls: str = "") -> Dict[str, str]:
        return {"label": label, "value": value, "tip": tip, "cls": cls}

    kpis = [
        kpi("Period (UTC)", f"{_utc(int(times[0]))} → {_utc(int(times[-1]))}",
            "Backtest window. Table and hover show both UTC and browser-local time.", ""),
        kpi("Timezone", "browser local", "Timestamps are stored in UTC; local strings use the browser timezone.", ""),
        kpi("Ticker", ticker, "Instrument traded by this backtest run.", ""),
        kpi("Initial capital", f"{_money(initial)} USDT", "Capital at the first bar of the run.", ""),
        kpi("Final capital", f"{_money(final)} USDT", "Final total portfolio value (TPV), including reserve.", "pos" if final >= initial else "neg"),
        kpi("Net PnL", f"{_money(net_pnl)} USDT", "Final equity - initial capital.", "pos" if net_pnl >= 0 else "neg"),
        kpi("Net PnL %", _pct(profit_pct), "(final equity / initial capital - 1) * 100.", "pos" if profit_pct >= 0 else "neg"),
        kpi("Max Drawdown", f"{_finite(result.get('max_dd_pct')):.2f}%", "Maximum peak-to-trough decline of the equity curve.", "neg"),
        kpi("Sortino", f"{_finite(result.get('sortino_ratio')):.4f}", "Mean per-minute return / downside deviation (engine value).", ""),
        kpi("Sharpe", f"{sharpe:.4f}", "Mean per-minute return / std per-minute return * sqrt(525600), annualized.", ""),
        kpi("Rebalances", _int(executed_rebalances), "Executed rebalances with at least one logged fill.", ""),
        kpi("Exchange fills", _int(exchange_fill_count), "Fills on the exchange; VIRTUAL cash transfers are excluded.", ""),
        kpi("Internal transfers", _int(internal_transfer_count), "VIRTUAL designate/release transfers; not exchange fills.", ""),
        kpi("BUY / SELL", f"{_int(buys)} / {_int(sells)}", "Exchange fill count by side; BUY also closes shorts and SELL closes longs.", ""),
        kpi("Commissions", f"-{_money(commissions)} USDT", "Taker commissions, mark-to-mid attribution basis.", "neg"),
        kpi("Funding", f"-{_money(funding)} USDT", "Funding drag reconstructed from open notional per bar.", "neg"),
        kpi("Funding from VIRTUAL", f"{_money(funding_from_virtual)} USDT",
            "Portion of funding paid by transferring VIRTUAL cash. Already included in Funding; not deducted again in PnL attribution.", ""),
        kpi("Realized PnL", f"{_money(realized)} USDT", "Mark-to-mid realized PnL; slippage is shown separately.", "pos" if realized >= 0 else "neg"),
        kpi("Unrealized PnL", f"{_money(unrealized)} USDT", "Mark-to-mid PnL of positions open at the final bar.", "pos" if unrealized >= 0 else "neg"),
        kpi("Accounting", "OK" if accounting_ok is not False else "FAILED",
            f"Engine TPV invariant residual {_money(accounting_residual)} USDT (tolerance {_money(accounting_tolerance)}).",
            "pos" if accounting_ok is not False else "neg"),
        kpi("Liquidations", _int(result.get("liquidations", 0)), "Predictive/hard cross-margin liquidation events.", "neg" if _finite(result.get("liquidations")) else ""),
        kpi("Trailing stops", _int(result.get("trailing_stops", 0)), "Trailing-stop and max-drawdown emergency stop events.", "warn" if _finite(result.get("trailing_stops")) else ""),
        kpi("Skipped expansions", _int(result.get("skipped_expansions", 0)),
            f"Fully skipped expansions; {partial_expansions} more were partially capped by free cash.",
            "warn" if _finite(result.get("skipped_expansions")) or partial_expansions else ""),
        kpi("Avg trade size", f"{_money(avg_trade)} USDT", "Gross exchange notional / exchange fills.", ""),
        kpi("Turnover", f"{turnover:,.2f}x", "Gross fill notional / average equity.", ""),
        kpi("Win rate", f"{win_rate:.2f}%", "Winning closing fills / all closing fills (realized PnL != 0).", ""),
        kpi("Profit factor", "∞" if math.isinf(profit_factor) else f"{profit_factor:.3f}",
            "Sum winning realized PnL / abs(sum losing realized PnL).", ""),
        kpi("Avg rebalance interval", f"{avg_rebalance_min:,.1f} min", "Backtest duration / rebalances that produced fills.", ""),
        kpi("Asset change", _pct(_finite(result.get("asset_chg_pct"))), "Base asset close price change over the window.", "pos" if _finite(result.get("asset_chg_pct")) >= 0 else "neg"),
        kpi("Trend-guard blocks", _int(result.get("trend_guard_blocks", 0)), "Actions blocked by the trend / net-move guard.", "warn" if _finite(result.get("trend_guard_blocks")) else ""),
        kpi("Velocity-guard blocks", _int(result.get("velocity_guard_blocks", 0)), "Actions blocked by the price-velocity guard.", "warn" if _finite(result.get("velocity_guard_blocks")) else ""),
    ]

    quality = _quality_checks(
        n=n, dw=dw, equity=equity, closes=closes, ohlc=ohlc, times=times,
        time_source=time_source, fills_count=len(fills_raw),
        fills_total=int(result.get("fill_count", len(fills_raw))),
        exchange_fills=exchange_fill_count, internal_transfers=internal_transfer_count,
        attr_totals=attr_totals, result=result, initial=initial, final=final,
        outlier_ratio=outlier_ratio, max_abs_return=max_abs_return,
    )
    final_initial_ratio = (final / initial) if initial else 0.0
    warnings = []
    if outlier_ratio > 1e4 or abs(profit_pct) > 1e6 or abs(net_pnl) > max(1.0, abs(initial)) * 1e4:
        warnings.append({
            "level": "error",
            "text": "Visualization is valid, but economic result may be invalid. Inspect equity/PnL accounting.",
            "detail": f"final/initial={final_initial_ratio:,.1f}x, net PnL={_money(net_pnl)} USDT",
        })
    if time_source == "mtime":
        warnings.append({
            "level": "warn",
            "text": "Synthetic time axis: the feather has no time column.",
            "detail": "Dates are derived from the file mtime; gaps cannot be verified.",
        })
    residual_tol = max(1.0, abs(initial) * 1e-6, abs(final - initial) * 1e-6)
    if abs(residual) > residual_tol:
        warnings.append({
            "level": "error",
            "text": "PnL attribution does not reconcile to final equity.",
            "detail": f"residual {_money(residual)} USDT; inspect margin/accounting, liquidation and cooldown paths.",
        })
    if accounting_ok is False:
        warnings.append({
            "level": "error",
            "text": "Backtest accounting did not pass reconciliation.",
            "detail": f"TPV residual {_money(accounting_residual)} USDT; result is not trustworthy.",
        })
    if int(result.get("fill_count", len(fills_raw))) > len(fills_raw):
        warnings.append({
            "level": "warn",
            "text": "Fill log is capped by the engine.",
            "detail": f"{len(fills_raw):,} of {int(result.get('fill_count', 0)):,} fills embedded.",
        })

    def _recon(label: str, value: float, operator: str = "", absolute: bool = False) -> Dict[str, Any]:
        if absolute:
            return {"label": label, "operator": operator, "value": abs(value), "signed_value": -abs(value)}
        op = "+" if value >= 0 else "-"
        return {"label": label, "operator": op, "value": abs(value), "signed_value": value}

    recon_rows = [
        {"label": "Initial capital", "operator": "", "value": initial, "signed_value": initial},
        _recon("Realized PnL (mark-to-mid)", realized),
        _recon("Unrealized PnL (mark-to-mid)", unrealized),
        _recon("Commissions", commissions, "-", True),
        _recon("Funding", funding, "-", True),
        _recon("Slippage", slippage, "-", True),
        _recon("Reserve / siphoning", reserve, "-", True),
        _recon("Residual / other", residual),
        {"label": "Final equity", "operator": "=", "value": final, "signed_value": final},
    ]

    cooldowns = _cooldowns(events)

    payload: Dict[str, Any] = {
        "meta": {
            "ticker": ticker,
            "prefix": prefix,
            "generated_utc": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M:%S"),
            "period_start_ms": int(times[0]),
            "period_end_ms": int(times[-1]),
            "period_start_utc": _utc(int(times[0])),
            "period_end_utc": _utc(int(times[-1])),
            "time_source": time_source,
            "bars": n,
            "input_bars": int(dw.get("input_bars") or n),
            "bars_per_day": int(_finite(settings.get("bars_per_day", 1440), 1440)),
            "initial_capital": initial,
            "final_equity": final,
            "profit_pct": profit_pct,
            "final_initial_ratio": final_initial_ratio,
            "settings": settings,
            "data_window": dw,
        },
        "kpis": kpis,
        "warnings": warnings,
        "price": {
            "times": [int(t) for t in times[price_idx]],
            "line": {
                "close": [_round(closes[i], 6) for i in price_idx],
                "times": [int(times[i]) for i in price_idx],
            },
            "ohlc": {
                "times": ohlc["times"], "open": ohlc["open"], "high": ohlc["high"],
                "low": ohlc["low"], "close": ohlc["close"], "bin": ohlc["bin"],
            } if ohlc else None,
            "stats": price_stats,
        },
        "equity": {
            "times": [int(times[i]) for i in eq_idx],
            "values": _series(equity, eq_idx),
            "hwm": _series(runmax, eq_idx),
            "drawdown": [_round(float(dd_pct[i]), 4) for i in eq_idx],
            "initial": initial,
            "stats": equity_stats,
            "initial_range": [min(equity_stats["p1"], initial), max(equity_stats["p99"], initial)],
        },
        "drawdown": {
            "times": [int(times[i]) for i in eq_idx],
            "values": [_round(float(dd_pct[i]), 4) for i in eq_idx],
        },
        "attribution": {
            "times": [int(times[i]) for i in attr_idx],
            "series": {
                "equity_pnl": [_round(v, 2) for v in attr_series["equity_pnl"]],
                "realized": [_round(v, 2) for v in attr_series["realized"]],
                "unrealized": [_round(v, 2) for v in attr_series["unrealized"]],
                "commissions": [-_round(v, 2) for v in attr_series["commissions"]],
                "funding": [-_round(v, 2) for v in attr_series["funding"]],
                "slippage": [-_round(v, 2) for v in attr_series["slippage"]],
                "reserve": [-_round(v, 2) for v in attr_series["reserve"]],
                "residual": [_round(v, 2) for v in attr_series["residual"]],
                "net": [_round(v, 2) for v in attr_series["net"]],
            },
            "totals": {k: _round(v, 4) for k, v in attr_totals.items()},
            "recon": recon_rows,
            "tolerance": residual_tol,
            "ok": abs(residual) <= residual_tol,
            "checks": attr["checks"],
        },
        "rebalances": rebalances,
        "events": events,
        "fills": fills,
        "cooldowns": cooldowns,
        "counts": {
            "fills": len(fills_raw),
            "fill_count": int(result.get("fill_count", len(fills_raw))),
            "exchange_fills": exchange_fill_count,
            "internal_transfers": internal_transfer_count,
            "buy": buys,
            "sell": sells,
            "events": len(events),
            "rebalances": len(rebalances),
            "liquidations": int(_finite(result.get("liquidations"))),
            "trailing_stops": int(_finite(result.get("trailing_stops"))),
            "skipped": int(_finite(result.get("skipped_expansions"))),
            "partial_expansions": partial_expansions,
            "trend_blocks": int(_finite(result.get("trend_guard_blocks"))),
            "velocity_blocks": int(_finite(result.get("velocity_guard_blocks"))),
            "closing": len(closing),
            "wins": len(wins),
            "losses": len(losses),
        },
        "quality": quality,
    }
    return payload


def main(prefix: str = "") -> int:
    prefix = prefix or (sys.argv[1] if len(sys.argv) > 1 else "zec_12m")
    started = time.time()
    payload = build_payload(prefix)
    if not os.path.exists(TEMPLATE):
        raise FileNotFoundError(f"Dashboard template not found: {TEMPLATE}")

    payload_json = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).replace("</", "<\\/")
    title = f"{payload['meta']['ticker']} backtest dashboard — {prefix}"
    with open(TEMPLATE, "r", encoding="utf-8") as fh:
        template = fh.read()
    page = template.replace("/*__PAYLOAD__*/", payload_json).replace("__TITLE__", html.escape(title))

    plotly_path = os.path.join(REPORTS, PLOTLY_JS)
    if not os.path.exists(plotly_path):
        raise FileNotFoundError(
            f"Local Plotly asset missing: {plotly_path}. The dashboard must run offline from file:///."
        )

    out = os.path.join(REPORTS, f"{prefix}_dashboard.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(page)
    print(f"saved {out} ({os.path.getsize(out) / 1024 / 1024:.1f} MB)")
    print(f"fills: {payload['counts']['fills']:,} | rebalances: {payload['counts']['rebalances']:,} | "
          f"period: {payload['meta']['period_start_utc']} -> {payload['meta']['period_end_utc']} UTC | "
          f"generated in {time.time() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
