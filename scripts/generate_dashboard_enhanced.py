#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Enhanced offline backtest dashboard generator (visualization only).

Reads a backtest result payload (reports/_dashboard_payload.json or the classic
btc_365d_backtest_result.json + btc_365d_equity.json pair) and writes ONE
self-contained HTML file:  reports/<ticker>_12m_dashboard.html
(e.g. reports/zec_12m_dashboard.html for ZECUSDT).

No trading logic, formulas, risk parameters or config are modified here —
everything is derived from data the backtest already produced.

Design goals (mirroring t-invest-2-build/reports/dashboard_unified_enhanced.html):
  * fully offline: plotly.min.js is INLINED into the html (no CDN at runtime);
  * KPI header with formula tooltips;
  * price chart with OHLC candles + trade/stop/liquidation/rebalance markers;
  * equity chart with HWM, drawdown panel, log/clipped/full scale switch;
  * PnL attribution: cumulative lines, stacked area, reconciliation table,
    balance check (red when mismatched);
  * rebalance analysis: volume, actual vs target shares, guard blocks;
  * full trades table: sort/search/filters/CSV export/virtualized rendering;
  * control panel with series toggles, theme toggle, JSON/summary export;
  * data-quality section incl. "economic result may be invalid" warning;
  * all embedded arrays are plain python lists of float/int/str before JSON
    serialization (never numpy), so browser Plotly can read everything.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS = os.path.join(ROOT, "reports")
DATA = os.path.join(ROOT, "data")


def _clean(v):
    """numpy/pandas scalars -> plain python; NaN/Inf -> None (JSON-safe)."""
    try:
        import numpy as _np
        if isinstance(v, _np.generic):
            v = v.item()
    except Exception:
        pass
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            return None
    return v


def _numlist(seq, nd=None):
    out = []
    for x in seq:
        x = _clean(x)
        if x is None:
            out.append(None)
        else:
            out.append(round(float(x), nd) if nd is not None else float(x))
    return out


def load_payload(args):
    if args.payload:
        res = json.load(open(args.payload, encoding="utf-8"))
        curve = res.pop("equity_curve", [])
        if not curve:
            eqp = os.path.join(REPORTS, "btc_365d_equity.json")
            if os.path.exists(eqp):
                curve = json.load(open(eqp, encoding="utf-8"))
        res["equity_curve"] = curve
        return res
    # classic pipeline files (written by backtest_rebalance __main__)
    res = json.load(open(os.path.join(REPORTS, "btc_365d_backtest_result.json"), encoding="utf-8"))
    res["equity_curve"] = json.load(open(os.path.join(REPORTS, "btc_365d_equity.json"), encoding="utf-8"))
    return res


def build_data(res):
    import numpy as np
    import pandas as pd

    equity = np.asarray(res.get("equity_curve", []), dtype=np.float64)
    fills = res.get("fills", [])
    events = res.get("events", [])
    dw = res.get("data_window") or {}
    ticker = res.get("base_ticker") or dw.get("ticker") or "BTCUSDT"
    initial = float(res.get("initial_capital", 1000.0))

    feather_path = os.path.join(DATA, dw.get("feather", f"{ticker}_live.feather"))
    closes = opens = highs = lows = vols = None
    n = len(equity)

    # --- time axis -----------------------------------------------------------
    wt = res.get("window_times")
    times = None
    if wt and len(wt) >= n:
        times = list(pd.to_datetime(wt[-n:], utc=True, format="mixed"))
    elif os.path.exists(feather_path):
        sidecar = os.path.splitext(feather_path)[0] + "_times.txt"
        if os.path.exists(sidecar):
            all_times = [ln.strip() for ln in open(sidecar, encoding="utf-8") if ln.strip()]
            offset = int(dw.get("offset", max(0, len(all_times) - n)))
            times = list(pd.to_datetime(all_times[offset:offset + n], utc=True, format="mixed"))
    if times is None or len(times) != n:
        # fallback: reconstruct from feather mtime (legacy behaviour; NOT ns -> 1970)
        end_ts = pd.Timestamp.fromtimestamp(
            os.path.getmtime(feather_path) if os.path.exists(feather_path) else __import__("time").time(),
            tz="UTC").floor("min")
        if dw.get("until_utc"):
            end_ts = min(pd.Timestamp(dw["until_utc"], tz="UTC").floor("min"), end_ts)
        offset = int(dw.get("offset", 0))
        times = list(pd.date_range(end=end_ts, periods=n, freq="min")) - \
            pd.Timedelta(minutes=0)  # bar i closes at times[i]; label = close time
    if n == 0:
        raise SystemExit("empty equity curve — nothing to visualize")

    # --- prices --------------------------------------------------------------
    ohlc_ok = False
    if os.path.exists(feather_path):
        df = pd.read_feather(feather_path)
        offset = int(dw.get("offset", max(0, len(df) - n)))
        sl = df.iloc[offset:offset + n]
        if len(sl) == n:
            closes = sl["close"].to_numpy(np.float64)
            if {"open", "high", "low"} <= set(sl.columns):
                opens = sl["open"].to_numpy(np.float64)
                highs = sl["high"].to_numpy(np.float64)
                lows = sl["low"].to_numpy(np.float64)
                ohlc_ok = True
            if "volume" in sl.columns:
                vols = sl["volume"].to_numpy(np.float64)
    if closes is None:
        closes = equity.copy()  # last-resort: never crash the dashboard

    ts_ms = [int(t.value // 1_000_000) for t in pd.DatetimeIndex(times)]

    # --- per-bar aggregates ---------------------------------------------------
    bar_realized = np.zeros(n)
    bar_comm = np.zeros(n)
    bar_notional = np.zeros(n)
    bar_funding_est = np.zeros(n)
    for f in fills:
        b = int(f["bar"])
        if 0 <= b < n:
            bar_realized[b] += float(f.get("realized_pnl", 0.0))
            bar_comm[b] += float(f.get("commission", 0.0))
            bar_notional[b] += abs(float(f.get("qty", 0.0))) * float(f.get("price", 0.0))
    # funding drag model in the engine: daily 0.02% on long+short notionals.
    # reconstruct per-bar estimate from fill flow (proxy, labelled as such in UI).
    cum_realized = np.cumsum(bar_realized)
    cum_comm = np.cumsum(bar_comm)
    total_realized = float(cum_realized[-1])
    total_comm = float(cum_comm[-1])
    siphoning = float(res.get("siphoning_reserve", 0.0))
    final_tpv = float(equity[-1])
    net_pnl = final_tpv - initial
    # residual = funding drag + quantization + skipped effects, by construction
    residual = initial + total_realized - total_comm - siphoning - final_tpv
    funding_est = float(-residual)  # positive number = cost paid (approximation)

    runmax = np.maximum.accumulate(equity)
    dd_pct = (runmax - equity) / np.maximum(runmax, 1e-12) * 100.0

    # --- trade stats -----------------------------------------------------------
    buy_n = sum(1 for f in fills if f.get("side") == "BUY")
    sell_n = sum(1 for f in fills if f.get("side") == "SELL")
    virt_n = sum(1 for f in fills if f.get("leg") == "VIRTUAL")
    rp = [float(f.get("realized_pnl", 0.0)) for f in fills if abs(float(f.get("realized_pnl", 0.0))) > 1e-12]
    wins = sum(1 for x in rp if x > 0)
    gross_win = sum(x for x in rp if x > 0)
    gross_loss = -sum(x for x in rp if x < 0)
    win_rate = (wins / len(rp) * 100.0) if rp else 0.0
    profit_factor = (gross_win / gross_loss) if gross_loss > 1e-12 else (float("inf") if gross_win > 0 else 0.0)
    turnover = float(sum(bar_notional))
    avg_trade_size = (turnover / len(fills)) if fills else 0.0

    cycles_bars = [int(r["step"]) for r in res.get("rebalance_log", [])] if "rebalance_log" in res else []
    reb_steps = [int(f["bar"]) for f in fills]
    # average minutes between rebalances from fills grouped by bar
    uniq_bars = sorted(set(b for b in reb_steps))
    avg_gap_min = ((uniq_bars[-1] - uniq_bars[0]) / (len(uniq_bars) - 1)) if len(uniq_bars) > 1 else 0.0

    sharpe = sortino = float(_clean(res.get("sortino_ratio", 0.0)) or 0.0)
    try:
        if n > 2:
            rets = np.diff(equity) / np.maximum(equity[:-1], 1e-12)
            sd = float(np.std(rets))
            sharpe = float(np.mean(rets) / (sd + 1e-12))
    except Exception:
        pass

    liq_events = [(int(e["bar"]), e.get("tpv", 0.0), e.get("detail", "")) for e in events if e.get("kind") == "liquidation"]
    stop_events = [(int(e["bar"]), e.get("tpv", 0.0), e.get("detail", "")) for e in events if e.get("kind") == "trailing_stop"]

    # per-leg leverage + target shares + rebalance thresholds from config
    # (visualization metadata only — config is READ, never modified)
    leg_lev = {"BASE_LONG": 7.0, "BASE_SHORT": 7.0, "VIRTUAL": 1.0}
    share_target_pct = {"L": 33.0, "S": 33.0, "V": 34.0}
    thr = {"surplus": 0.0, "deficit": 0.0}
    try:
        cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
        tconf = next((t for t in cfg.get("tickers", []) if t.get("ticker") == ticker), None) or \
                 (cfg.get("tickers") or [{}])[0]
        tt = tconf.get("targets", {})
        for k in ("BASE_LONG", "BASE_SHORT", "VIRTUAL"):
            if k in tt and "leverage" in tt[k]:
                leg_lev[k] = float(tt[k]["leverage"])
        if all(k in tt for k in ("BASE_LONG", "BASE_SHORT", "VIRTUAL")):
            share_target_pct = {"L": float(tt["BASE_LONG"]["share"]) * 100,
                                "S": float(tt["BASE_SHORT"]["share"]) * 100,
                                "V": float(tt["VIRTUAL"]["share"]) * 100}
        rt = tconf.get("risk_thresholds", {})
        tk_thr = rt.get("ticker_thresholds", {}).get(ticker, {})
        thr = {"surplus": float(tk_thr.get("surplus", rt.get("rebalance_threshold_surplus", 0))),
               "deficit": float(tk_thr.get("deficit", rt.get("rebalance_threshold_deficit", 0)))}
    except Exception:
        pass
    kpis = {
        "ticker": ticker,
        "tz": "UTC",
        "start": ts_ms[0], "end": ts_ms[-1],
        "bars": n,
        "initial_capital": initial,
        "final_capital": final_tpv,
        "net_pnl": net_pnl,
        "net_pnl_pct": float(res.get("profit_pct", 0.0)),
        "max_dd_pct": float(res.get("max_dd_pct", 0.0)),
        "sortino": sortino,
        "sharpe": sharpe,
        "rebalances": int(res.get("cycles", 0)),
        "trades": len(fills),
        "fill_count_total": int(res.get("fill_count", len(fills))),
        "buy": buy_n, "sell": sell_n, "virtual": virt_n,
        "commissions": total_comm,
        "funding_est": funding_est,
        "realized_pnl": total_realized,
        # unrealized at end via accounting identity: net = realized - comm - reserve - funding + upnl
        "unrealized_pnl": float(net_pnl - (total_realized - total_comm - siphoning - residual)),
        "reserve": siphoning,
        "residual": residual,
        "liquidations": int(res.get("liquidations", 0)),
        "trailing_stops": int(res.get("trailing_stops", 0)),
        "skipped_expansions": int(res.get("skipped_expansions", 0)),
        "trend_guard_blocks": int(res.get("trend_guard_blocks", 0)),
        "velocity_guard_blocks": int(res.get("velocity_guard_blocks", 0)),
        "avg_trade_size": avg_trade_size,
        "turnover": turnover,
        "win_rate": win_rate,
        "profit_factor": (None if profit_factor == float("inf") else profit_factor),
        "avg_rebal_gap_min": avg_gap_min,
        "asset_chg_pct": float(res.get("asset_chg_pct", 0.0)),
        "cooldown_bars": int(res.get("cooldown_bars", 0)),
        "market_orders_filled": int(res.get("market_orders_filled", 0)),
        "threshold_surplus_pct": thr["surplus"] * 100.0,
        "threshold_deficit_pct": thr["deficit"] * 100.0,
    }

    # --- downsampling for LINES only (markers/trades keep every point) --------
    MAX_LINE = 6000
    ds = max(1, int(np.ceil(n / MAX_LINE)))
    idx_ds = list(range(0, n, ds))
    if idx_ds[-1] != n - 1:
        idx_ds.append(n - 1)

    def dsv(arr, nd=4):
        return _numlist([arr[i] for i in idx_ds], nd)

    data = {
        "meta": {
            "generated_from": os.path.basename(feather_path),
            "timestamps_source": "engine window_times" if wt else ("sidecar *_times.txt" if times and len(times) == n else "feather mtime reconstruction"),
            "downsample_step_lines": ds,
            "feather": dw,
        },
        "kpis": kpis,
        "ts_ds": [ts_ms[i] for i in idx_ds],
        "close_ds": dsv(closes, 6),
        "equity_ds": dsv(equity, 4),
        "dd_ds": dsv(dd_pct, 4),
        "cum_realized_ds": dsv(cum_realized, 4),
        "cum_comm_ds": dsv(-cum_comm, 4),
        "cum_net_ds": dsv(equity - initial, 4),
        "cum_upnl_ds": dsv(equity - initial - cum_realized + cum_comm, 4),
        "ohlc": {"ok": ohlc_ok,
                 "x": [ts_ms[i] for i in idx_ds],
                 "o": dsv(opens, 6) if ohlc_ok else None,
                 "h": dsv(highs, 6) if ohlc_ok else None,
                 "l": dsv(lows, 6) if ohlc_ok else None,
                 "c": dsv(closes, 6)},
        "trades": {
            "x": [], "y": [], "text": [], "color": [], "symbol": [], "size": [],
            "bar": [], "side": [], "leg": [], "qty": [], "price": [], "notional": [],
            "comm": [], "rpn": [], "tpv": [], "lev": [],
        },
        "stops": {"x": [], "y": [], "text": []},
        "liqs": {"x": [], "y": [], "text": []},
        "rebals": {"x": [], "y": [], "text": [], "vol": []},
    }


    px_at = lambda b: float(closes[min(int(b), n - 1)])
    for f in fills:
        b = int(f["bar"])
        if not (0 <= b < n):
            continue
        t = data["trades"]
        tsx = ts_ms[b]
        pr = float(f.get("price", 0.0))
        qty = float(f.get("qty", 0.0))
        side = f.get("side", "")
        leg = f.get("leg", "")
        t["x"].append(tsx)
        t["y"].append(round(pr, 6))
        t["text"].append(
            f"{side} {leg}<br>qty {qty:.6g}<br>price {pr:,.4f}<br>"
            f"notional {abs(qty*pr):,.2f} USDT<br>commission {float(f.get('commission',0)):,.4f}<br>"
            f"realized PnL {float(f.get('realized_pnl',0)):+,.4f}<br>TPV after {float(f.get('tpv',0)):,.2f}")
        is_buy = side == "BUY"
        t["color"].append("#51cf66" if is_buy else "#ff6b6b")
        sym = "circle" if leg == "BASE_LONG" else ("diamond" if leg == "BASE_SHORT" else "square")
        if side in ("DESIGNATE", "RELEASE"):
            sym = "square"
        t["symbol"].append(sym)
        t["size"].append(6 if leg != "VIRTUAL" else 5)
        t["bar"].append(b)
        t["side"].append(side)
        t["leg"].append(leg)
        t["qty"].append(round(qty, 8))
        t["price"].append(round(pr, 6))
        t["notional"].append(round(abs(qty * pr), 4))
        t["comm"].append(round(float(f.get("commission", 0.0)), 6))
        t["rpn"].append(round(float(f.get("realized_pnl", 0.0)), 6))
        t["tpv"].append(round(float(f.get("tpv", 0.0)), 4))
        t["lev"].append(leg_lev.get(leg, 1.0))

    for b, tpv, det in stop_events:
        if 0 <= b < n:
            data["stops"]["x"].append(ts_ms[b])
            data["stops"]["y"].append(round(px_at(b), 6))
            data["stops"]["text"].append(f"TRAILING STOP @ bar {b}<br>{det}<br>TPV {tpv:,.2f}")
    for b, tpv, det in liq_events:
        if 0 <= b < n:
            data["liqs"]["x"].append(ts_ms[b])
            data["liqs"]["y"].append(round(px_at(b), 6))
            data["liqs"]["text"].append(f"LIQUIDATION @ bar {b}<br>{det}<br>dormant capital {tpv:,.2f}")

    # rebalance groups = distinct bars that contain fills
    from collections import defaultdict
    by_bar = defaultdict(list)
    for f in fills:
        by_bar[int(f["bar"])].append(f)
    for b in sorted(by_bar):
        if not (0 <= b < n):
            continue
        fl = by_bar[b]
        vol = sum(abs(float(x.get("qty", 0))) * float(x.get("price", 0)) for x in fl)
        data["rebals"]["x"].append(ts_ms[b])
        data["rebals"]["y"].append(round(px_at(b), 6))
        acts = ", ".join(f"{x['side']} {x['leg']}" for x in fl[:6])
        data["rebals"]["text"].append(f"REBALANCE #{len(data['rebals']['x'])} @ bar {b}<br>volume {vol:,.2f} USDT<br>{acts}")
        data["rebals"]["vol"].append(round(vol, 4))

    # --- share evolution per rebalance (actual shares recorded by the engine) --
    # reconstructed from fills tpv snapshots: L/S/V/C shares at each rebalance bar
    shares = {"x": [], "L": [], "S": [], "V": [], "C": [],
              "target": {"L": (share_target_pct["L"] or 33.0) / 100.0,
                         "S": (share_target_pct["S"] or 33.0) / 100.0,
                         "V": (share_target_pct["V"] or 34.0) / 100.0,
                         "C": 0.0}}
    cfg_targets = res.get("targets_snapshot")
    if cfg_targets:
        shares["target"] = cfg_targets
    prev_side_val = None
    # approximate actual shares from position values: use tpv after last fill per bar
    # and margin math from entry prices is unavailable post-hoc → derive from
    # running position simulation over fills (pure replay, no new formulas):
    pos_l = pos_s = 0.0
    ep_l = ep_s = 0.0
    lev_l = lev_s = 7.0
    cash = initial
    ti = 0
    tl = ts_ms
    for b in sorted(by_bar):
        if not (0 <= b < n):
            continue
        for f in by_bar[b]:
            side, leg = f.get("side"), f.get("leg")
            q = float(f.get("qty", 0)); p = float(f.get("price", 0))
            c = float(f.get("commission", 0)); r = float(f.get("realized_pnl", 0))
            if leg == "BASE_LONG":
                if side == "BUY":
                    ep_l = (pos_l * ep_l + q * p) / (pos_l + q) if pos_l + q > 0 else p
                    cash -= q * p / lev_l + c
                    pos_l += q
                else:
                    cash += q * ep_l / lev_l + r - c
                    pos_l -= q
                    if pos_l <= 1e-12: pos_l, ep_l = 0.0, 0.0
            elif leg == "BASE_SHORT":
                if side == "SELL":
                    ep_s = (pos_s * ep_s + q * p) / (pos_s + q) if pos_s + q > 0 else p
                    cash -= q * p / lev_s + c
                    pos_s += q
                else:
                    cash += q * ep_s / lev_s + r - c
                    pos_s -= q
                    if pos_s <= 1e-12: pos_s, ep_s = 0.0, 0.0
        tpv = float(by_bar[b][-1].get("tpv", 0)) or 1.0
        mL = pos_l * ep_l / lev_l
        mS = pos_s * ep_s / lev_s
        uL = pos_l * (px_at(b) - ep_l) if pos_l > 0 else 0.0
        uS = pos_s * (ep_s - px_at(b)) if pos_s > 0 else 0.0
        shares["x"].append(ts_ms[b])
        shares["L"].append(round((mL + uL) / tpv * 100, 3))
        shares["S"].append(round((mS + uS) / tpv * 100, 3))
        shares["V"].append(round(min(max(tpv - mL - mS - uL - uS, 0), tpv) / tpv * 100, 3))
        shares["C"].append(0.0)
    # normalize V+C to remaining cash share
    for i in range(len(shares["x"])):
        rem = 100.0 - shares["L"][i] - shares["S"][i]
        shares["V"][i] = round(max(rem, 0), 3)
        shares["C"][i] = 0.0

    # --- quality checks --------------------------------------------------------
    qual = {
        "bars_input": int(dw.get("bars_total_in_file", dw.get("bars", n))),
        "bars_used": n,
        "bars_total_in_file": int(res.get("bars_total_in_file", 0)),
        "period_days": round((ts_ms[-1] - ts_ms[0]) / 86400000.0, 3),
        "ts_gaps": 0, "ts_dupes": 0, "nan_inf": 0,
        "fills_capped": kpis["fill_count_total"] > len(fills),
        "no_fills": len(fills) == 0,
        "no_equity": n == 0,
        "bars_prices_mismatch": False,
        "attribution_mismatch": abs(residual) > max(1.0, initial * 0.05),
        "outlier_equity": False,
        "flags": [],
    }
    arr_ts = np.asarray(ts_ms)
    diffs = np.diff(arr_ts)
    qual["ts_gaps"] = int(np.sum(diffs != 60000))
    qual["ts_dupes"] = int(np.sum(diffs == 0))
    qual["nan_inf"] = int(np.sum(~np.isfinite(equity)) + np.sum(~np.isfinite(closes)))
    qual["bars_prices_mismatch"] = bool(len(closes) != n)
    growth = final_tpv / max(initial, 1e-9)
    if growth > 10 or growth < 0.01:
        qual["outlier_equity"] = True
        qual["flags"].append(
            "EQUITY GROWTH MULTIPLE ORDERS OF MAGNITUDE — Visualization is valid, but economic "
            "result may be invalid. Inspect equity/PnL accounting.")
    if qual["fills_capped"]:
        qual["flags"].append(f"fills log capped at {len(fills):,} rows (engine logged {kpis['fill_count_total']:,})")
    if qual["attribution_mismatch"]:
        qual["flags"].append(f"PnL attribution residual {residual:+,.2f} USDT exceeds tolerance — reconcile accounting")
    if kpis["liquidations"]:
        qual["flags"].append(f"{kpis['liquidations']} liquidation event(s)")
    if kpis["trailing_stops"]:
        qual["flags"].append(f"{kpis['trailing_stops']} trailing-stop trigger(s)")
    if kpis["skipped_expansions"]:
        qual["flags"].append(f"{kpis['skipped_expansions']} skipped expansion action(s)")
    if qual["ts_gaps"]:
        qual["flags"].append(f"{qual['ts_gaps']} non-1-minute timestamp step(s) in window")
    if qual["nan_inf"]:
        qual["flags"].append(f"{qual['nan_inf']} NaN/Inf values in price/equity")
    if not wt and not dw.get("timestamps_available"):
        qual["flags"].append("timestamps reconstructed from feather mtime (no sidecar *_times.txt)")

    data["quality"] = qual
    data["shares"] = shares

    # percentile clip bounds for pathological spikes (display-only)
    q_hi = float(np.percentile(equity[np.isfinite(equity)], 99.9)) if n else 0.0
    q_lo = float(np.percentile(equity[np.isfinite(equity)], 0.1)) if n else 0.0
    data["equity_bounds"] = {"full": [None if not np.isfinite(float(np.min(equity))) else float(np.min(equity)),
                                      None if not np.isfinite(float(np.max(equity))) else float(np.max(equity))],
                             "clip": [round(q_lo, 4), round(q_hi, 4)]}
    return data


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#0d1117;--panel:#161b22;--border:#30363d;--fg:#e6edf3;--mut:#8b949e;--acc:#4dabf7;
--pos:#51cf66;--neg:#ff6b6b;--warn:#ffd43b;}
body.light{--bg:#f6f8fa;--panel:#ffffff;--border:#d0d7de;--fg:#1f2328;--mut:#59636e;}
*{box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--fg);margin:0;padding:18px 22px;transition:background .2s}
h1{font-size:20px;font-weight:600;margin:0 0 2px}
h2{font-size:14px;font-weight:600;margin:26px 0 8px;color:var(--mut);text-transform:uppercase;letter-spacing:.06em}
.sub{color:var(--mut);font-size:12px;margin:2px 0 12px}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(165px,1fr));gap:9px}
.tile{background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:10px 12px;position:relative;cursor:help}
.tl{font-size:10.5px;color:var(--mut);margin-bottom:5px;text-transform:uppercase;letter-spacing:.04em}
.tv{font-size:17px;font-weight:650;font-variant-numeric:tabular-nums}
.tv.pos{color:var(--pos)}.tv.neg{color:var(--neg)}.tv.warn{color:var(--warn)}
.tile .tip{visibility:hidden;opacity:0;position:absolute;z-index:30;left:6px;top:100%;width:270px;background:#1c2128;
color:#e6edf3;border:1px solid #444c56;border-radius:6px;padding:8px 10px;font-size:11.5px;line-height:1.45;
transition:opacity .15s;pointer-events:none;box-shadow:0 6px 18px rgba(0,0,0,.5)}
.tile:hover .tip{visibility:visible;opacity:1}
.card{background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:12px;margin-bottom:12px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:1100px){.grid2{grid-template-columns:1fr}}
table{border-collapse:collapse;width:100%;font-size:12px}
th,td{padding:4px 8px;text-align:left;border-bottom:1px solid var(--border);white-space:nowrap}
th{color:var(--mut);position:sticky;top:0;background:var(--panel);cursor:pointer;user-select:none;z-index:5}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.attr td{padding:6px 10px}.tot td{font-weight:650;border-top:2px solid var(--border)}
.scroll{max-height:480px;overflow:auto;border:1px solid var(--border);border-radius:8px;background:var(--panel)}
.count{color:var(--mut);font-size:12px;margin:5px 0}
.controls{display:flex;flex-wrap:wrap;gap:8px;align-items:center;background:var(--panel);border:1px solid var(--border);
border-radius:8px;padding:10px 12px;margin:10px 0;position:sticky;top:0;z-index:40;backdrop-filter:blur(4px)}
button,select,input[type=text],input[type=search],input[type=datetime-local]{background:#21262d;color:var(--fg);
border:1px solid var(--border);border-radius:6px;padding:5px 9px;font-size:12px}
body.light button,body.light select,body.light input{background:#f0f2f5}
button:hover{border-color:var(--acc)}
label.chk{font-size:12px;display:inline-flex;align-items:center;gap:4px;color:var(--mut);cursor:pointer}
.bad{color:var(--neg);font-weight:650}.ok{color:var(--pos)}
.alert{background:#3d1418;border:1px solid #f85149;color:#ffc1c7;border-radius:8px;padding:10px 14px;margin:10px 0;font-size:13px}
.seg{display:inline-flex;border:1px solid var(--border);border-radius:6px;overflow:hidden}
.seg button{border:0;border-radius:0;background:transparent;padding:4px 9px}
.seg button.on{background:var(--acc);color:#fff}
.pill{display:inline-block;background:#21262d;border-radius:10px;padding:1px 8px;font-size:11px;color:var(--mut)}
#tblSearch{width:210px}
footer{color:var(--mut);font-size:11px;margin-top:24px}
</style>
<script>__PLOTLY_JS__</script>
</head><body>
<h1 id="hdrTitle">Backtest Dashboard</h1>
<div class="sub" id="hdrSub"></div>
<div id="globalAlert"></div>

<div class="controls" id="ctrlPanel">
  <span class="pill">Range:</span>
  <span class="seg" id="rangeSeg">
    <button data-r="1">1h</button><button data-r="6">6h</button><button data-r="24">1d</button>
    <button data-r="168">1w</button><button data-r="all" class="on">All</button>
  </span>
  <span class="pill">Series:</span>
  <label class="chk"><input type="checkbox" id="tgPrice" checked>price</label>
  <label class="chk"><input type="checkbox" id="tgTrades" checked>trades</label>
  <label class="chk"><input type="checkbox" id="tgStops" checked>stops</label>
  <label class="chk"><input type="checkbox" id="tgLiqs" checked>liquidations</label>
  <label class="chk"><input type="checkbox" id="tgRebals" checked>rebalances</label>
  <label class="chk"><input type="checkbox" id="tgEquity" checked>equity</label>
  <label class="chk"><input type="checkbox" id="tgDD" checked>drawdown</label>
  <span class="pill">Equity scale:</span>
  <span class="seg" id="scaleSeg">
    <button data-s="full" class="on">Full</button><button data-s="clip">Clipped&nbsp;p99.9</button><button data-s="log">Log</button>
  </span>
  <button id="btnResetZoom">Reset zoom</button>
  <button id="btnResetFilters">Reset filters</button>
  <button id="btnCsv">Export trades CSV</button>
  <button id="btnJson">Export visible JSON</button>
  <button id="btnCopy">Copy summary</button>
  <button id="btnTheme">◐ Theme</button>
</div>

<h2>KPI overview</h2>
<div class="tiles" id="kpiTiles"></div>

<h2>Price &amp; trade events</h2>
<div class="card"><div id="cPrice" style="height:460px"></div></div>

<h2>Equity (TPV) &amp; drawdown</h2>
<div class="card"><div id="cEquity" style="height:520px"></div></div>

<h2>PnL attribution</h2>
<div class="grid2">
  <div class="card"><div id="cAttrLines" style="height:380px"></div></div>
  <div class="card"><div id="cAttrStack" style="height:380px"></div></div>
</div>
<div class="card"><h2 style="margin-top:0">Reconciliation</h2><table class="attr" id="attrTable"></table>
<div class="count" id="attrCheck"></div></div>

<h2>Rebalance analysis</h2>
<div class="grid2">
  <div class="card"><div id="cRebalVol" style="height:340px"></div></div>
  <div class="card"><div id="cShares" style="height:340px"></div></div>
</div>
<div class="card" id="guardCard"></div>

<h2>Trades table</h2>
<div class="controls">
  <input type="search" id="tblSearch" placeholder="search side/leg/text…">
  <select id="fSide"><option value="">side: all</option><option>BUY</option><option>SELL</option><option>DESIGNATE</option><option>RELEASE</option></select>
  <select id="fLeg"><option value="">leg: all</option><option>BASE_LONG</option><option>BASE_SHORT</option><option>VIRTUAL</option></select>
  <select id="fProfit"><option value="">P/L: all</option><option value="pos">profitable</option><option value="neg">loss</option></select>
  <input type="text" id="fTfrom" placeholder="from 2026-01-01" size="12">
  <input type="text" id="fTto" placeholder="to 2026-12-31" size="12">
  <input type="text" id="fNotMin" placeholder="min notional" size="7">
  <input type="text" id="fNotMax" placeholder="max notional" size="7">
  <input type="text" id="fPnMin" placeholder="min PnL" size="7">
  <input type="text" id="fPnMax" placeholder="max PnL" size="7">
  <span class="count" id="tblCount"></span>
</div>
<div class="scroll" id="tblWrap">
<table id="tradesTable"><thead><tr id="trHead"></tr></thead><tbody id="trBody"></tbody></table>
</div>
<div class="count" id="tblFoot"></div>

<h2>Data quality</h2>
<div class="card"><div id="quality"></div></div>

<footer id="footNote"></footer>

<script>
"use strict";
const D = __BDATA__;
const K = D.kpis;
const fmt = (v,d)=> v==null||!isFinite(v) ? "—" : Number(v).toLocaleString("en-US",{minimumFractionDigits:d,maximumFractionDigits:d});
const money = v => Math.abs(v)>=1e6? fmt(v,0)+" USDT" : fmt(v,2)+" USDT";
const pct = v => (v>=0?"+":"")+fmt(v,2)+"%";
const dtstr = ms => new Date(ms).toISOString().slice(0,16).replace("T"," ")+" UTC";

/* ---------- KPI tiles with formula tooltips ---------- */
const TIPS = {
 period:"Window actually processed by the engine. TZ: UTC (exchange time).",
 ticker:"Base asset traded by the portfolio legs LONG/SHORT/VIRTUAL.",
 cap:"Final TPV − Initial capital. TPV = cash + long/short margin + unrealized PnL.",
 netpct:"Net PnL / initial capital × 100.",
 maxdd:"max over time of (runningHigh − equity)/runningHigh × 100.",
 sortino:"mean(bar returns)/downside-deviation(only negative returns), per-bar (not annualized).",
 sharpe:"mean(bar returns)/std(bar returns), per-bar (not annualized).",
 rebal:"Number of bars where the calculator emitted executed actions (engine 'cycles').",
 trades:"Logged fills (executed order records). BUY/SELL counts by side; VIRTUAL = cash relabeling.",
 commissions:"Σ commission = Σ qty×price×takerRate over all fills.",
 funding:"Estimated funding drag: engine charges 0.02%/day × (long+short notional) each bar; shown as derived residual (funding + quantization + skips).",
 realized:"Σ realized PnL of closing fills: long qty×(exit−entry), short qty×(entry−exit).",
 unrealized:"End-of-run TPV minus realized accounting: cash+margin mark-to-market still open.",
 reserve:"Siphoning reserve: profit above threshold moved out of active TPV (engine siphoning_reserve).",
 liq:"Predictive/hard cross-margin liquidation events (positions force-closed, cooldown entered).",
 stops:"Equity trailing-stop triggers (TPV drawdown from ATH ≥ configured %).",
 skipped:"Expansion actions skipped because available funds ≤ 0 at execution time.",
 avgtrade:"Total traded notional / number of fills.",
 turnover:"Σ |qty|×price over all fills (both sides).",
 winrate:"Winning closed fills / all closed fills with nonzero realized PnL.",
 pf:"Gross profit / gross loss over closed fills.",
 gap:"Mean spacing between consecutive rebalance bars (1 bar = 1 minute).",
 asset:"(lastClose/firstClose − 1)×100 for the base asset over the window.",
};
const tiles = [
 ["Period", dtstr(K.start)+" → "+dtstr(K.end)+" ("+K.bars.toLocaleString()+" 1m bars)", "", "period"],
 ["Ticker", K.ticker, "", "ticker"],
 ["Initial capital", money(K.initial_capital), "", "cap"],
 ["Final capital", money(K.final_capital), K.net_pnl>=0?"pos":"neg", "cap"],
 ["Net PnL", (K.net_pnl>=0?"+":"")+money(K.net_pnl), K.net_pnl>=0?"pos":"neg", "netpct"],
 ["Net PnL %", pct(K.net_pnl_pct), K.net_pnl_pct>=0?"pos":"neg", "netpct"],
 ["Max Drawdown", fmt(K.max_dd_pct,2)+"%", K.max_dd_pct>0?"neg":"", "maxdd"],
 ["Sortino", fmt(K.sortino,4), "", "sortino"],
 ["Sharpe", fmt(K.sharpe,4), "", "sharpe"],
 ["Rebalances", fmt(K.rebalances,0), "", "rebal"],
 ["Trades", fmt(K.trades,0)+(K.fill_count_total>K.trades?` <span class="pill">of ${fmt(K.fill_count_total,0)}</span>`:""), "", "trades"],
 ["BUY / SELL", K.buy+" / "+K.sell+(K.virtual?` <span class="pill">V:${K.virtual}</span>`:""), "", "trades"],
 ["Commissions", money(K.commissions), "neg", "commissions"],
 ["Funding (est.)", "-"+money(Math.abs(K.funding_est)), "neg", "funding"],
 ["Realized PnL", (K.realized_pnl>=0?"+":"")+money(K.realized_pnl), K.realized_pnl>=0?"pos":"neg", "realized"],
 ["Unrealized PnL", (K.unrealized_pnl>=0?"+":"")+money(K.unrealized_pnl), K.unrealized_pnl>=0?"pos":"neg", "unrealized"],
 ["Reserve/siphoned", money(K.reserve), "", "reserve"],
 ["Liquidations", K.liquidations, K.liquidations?"neg":"", "liq"],
 ["Trailing stops", K.trailing_stops, K.trailing_stops?"warn":"", "stops"],
 ["Skipped actions", K.skipped_expansions, K.skipped_expansions?"warn":"", "skipped"],
 ["Avg trade size", money(K.avg_trade_size), "", "avgtrade"],
 ["Turnover", money(K.turnover), "", "turnover"],
 ["Win rate", fmt(K.win_rate,1)+"%", "", "winrate"],
 ["Profit factor", K.profit_factor==null?"∞":fmt(K.profit_factor,2), "", "pf"],
 ["Avg rebal gap", fmt(K.avg_rebal_gap_min,1)+" min", "", "gap"],
 ["Asset change", pct(K.asset_chg_pct), K.asset_chg_pct>=0?"pos":"neg", "asset"],
];
document.getElementById("kpiTiles").innerHTML = tiles.map(t=>
 `<div class="tile"><div class="tl">${t[0]}</div><div class="tv ${t[2]}">${t[1]}</div><div class="tip">${TIPS[t[3]]||""}</div></div>`).join("");

document.getElementById("hdrTitle").textContent = `${K.ticker} Backtest — Enhanced Offline Dashboard`;
document.getElementById("hdrSub").innerHTML =
 `window ${dtstr(K.start)} → ${dtstr(K.end)} · ${K.tz} · ${K.bars.toLocaleString()} 1m bars · `+
 `lines downsampled ×${D.meta.downsample_step_lines} (markers keep every point) · timestamps: ${D.meta.timestamps_source}`;

if (D.quality && (D.quality.outlier_equity || Math.abs(K.net_pnl_pct) > 500)) {
  document.getElementById("globalAlert").innerHTML =
   `<div class="alert"><b>⚠ Economic sanity warning.</b> Net PnL is ${pct(K.net_pnl_pct)} — equity grew/shrank by multiple orders of magnitude vs initial capital. `+
   `Visualization is valid, but economic result may be invalid. Inspect equity/PnL accounting.</div>`;
}

/* ---------- shared helpers ---------- */
// NOTE: Plotly config.locale must be a registered locale STRING; passing an object
// breaks supplyDefaults (TypeError: r.split is not a function). Keep default English.
const CFG = {responsive:true, displayModeBar:true, scrollZoom:true,
  modeBarButtonsToRemove:["lasso2d","select2d","autoScale2d"]};
const DARK = ()=> ({template:"plotly_dark", paper_bgcolor:"rgba(0,0,0,0)", font:{color:getComputedStyle(document.body).getPropertyValue("--fg")}});
let charts = {};
function plot(id,data,layout){ layout=Object.assign({},DARK(),layout); charts[id]=Plotly.newPlot(id,data,layout,CFG); return charts[id]; }

/* ---------- price chart ---------- */
const priceTraces = [];
if (D.ohlc.ok && D.ohlc.o) {
  priceTraces.push({type:"candlestick", x:D.ohlc.x, open:D.ohlc.o, high:D.ohlc.h, low:D.ohlc.l, close:D.ohlc.c,
    name:"OHLC", increasing:{line:{color:"#51cf66"}}, decreasing:{line:{color:"#ff6b6b"}}, yaxis:"y"});
} else {
  priceTraces.push({type:"scatter",mode:"lines",x:D.ts_ds,y:D.close_ds,name:K.ticker+" close",line:{color:"#ffd43b",width:1.2}});
}
// trades: one trace per (side,leg) group keeps legend clean; hover text per point
const grp = {};
for(let i=0;i<D.trades.x.length;i++){
  const key=D.trades.side[i]+"·"+D.trades.leg[i];
  (grp[key]=grp[key]||{x:[],y:[],t:[],c:D.trades.color[i],s:D.trades.symbol[i]});
  grp[key].x.push(D.trades.x[i]); grp[key].y.push(D.trades.y[i]); grp[key].t.push(D.trades.text[i]);
}
Object.keys(grp).forEach(k=>{
  const g=grp[k];
  priceTraces.push({type:"scatter",mode:"markers",x:g.x,y:g.y,text:g.t,name:k,
    marker:{color:g.c,size:7,symbol:g.s,line:{width:1,color:"rgba(255,255,255,.35)"}},hoverlabel:{align:"left"}});
});
if(D.stops.x.length) priceTraces.push({type:"scatter",mode:"markers",x:D.stops.x,y:D.stops.y,text:D.stops.text,name:"trailing stop",
  marker:{color:"#fa5252",symbol:"triangle-down",size:11,line:{width:1,color:"#fff"}}});
if(D.liqs.x.length) priceTraces.push({type:"scatter",mode:"markers",x:D.liqs.x,y:D.liqs.y,text:D.liqs.text,name:"liquidation",
  marker:{color:"#ff6b6b",symbol:"x",size:12,line:{width:2,color:"#fff"}}});
if(D.rebals.x.length) priceTraces.push({type:"scatter",mode:"markers",x:D.rebals.x,y:D.rebals.y,text:D.rebals.text,name:"rebalance",
  marker:{color:"#4dabf7",symbol:"circle-open-dot",size:8},showlegend:true});
plot("cPrice", priceTraces, {
  height:460, margin:{l:55,r:20,t:30,b:40},
  xaxis:{rangeslider:{visible:true,thickness:.06}, gridcolor:"rgba(128,128,128,.15)"},
  yaxis:{title:K.ticker+" price", side:"right"},
  legend:{orientation:"h",y:-0.18}, hovermode:"closest"});

/* ---------- equity chart (equity panel + separate drawdown panel) ---------- */
window.__eqScale = window.__eqScale || "full";
function buildEquity(){
  const sc = window.__eqScale;
  let type="linear", range=null;
  if(sc==="log"){type="log";}
  if(sc==="clip"){range=[Math.max(D.equity_bounds.clip[0]*0.95,1e-9), D.equity_bounds.clip[1]*1.05];}
  // high-water mark on the downsampled grid (display-only line)
  const hwmy=[]; { let run=-Infinity; for(let i=0;i<D.equity_ds.length;i++){ if(D.equity_ds[i]>run)run=D.equity_ds[i]; hwmy.push(run);} }
  const evY=(arr)=>arr.map(()=>null); // event markers live on their own hidden axis row
  const figData=[
   {type:"scatter",mode:"lines",x:D.ts_ds,y:D.equity_ds,name:"TPV equity",line:{color:"#4dabf7",width:1.6},yaxis:"y",
    customdata:D.dd_ds, hovertemplate:"%{x|%Y-%m-%d %H:%M}<br>equity %{y:,.2f}<br>DD at point %{customdata:.2f}%<extra></extra>"},
   {type:"scatter",mode:"lines",x:[D.ts_ds[0],D.ts_ds[D.ts_ds.length-1]],y:[K.initial_capital,K.initial_capital],
    name:"initial capital",line:{color:"#868e96",width:1,dash:"dash"},yaxis:"y"},
   {type:"scatter",mode:"lines",x:D.ts_ds,y:hwmy,name:"high-water mark",line:{color:"#ffd43b",width:1,dash:"dot"},yaxis:"y"},
   {type:"scatter",mode:"markers",x:D.stops.x,y:D.stops.y,name:"trailing stops",yaxis:"ypx",
    marker:{color:"#fa5252",symbol:"triangle-down",size:11,line:{width:1,color:"#fff"}},text:D.stops.text,hoverinfo:"text+x"},
   {type:"scatter",mode:"markers",x:D.liqs.x,y:D.liqs.y,name:"liquidations",yaxis:"ypx",
    marker:{color:"#ff6b6b",symbol:"x",size:12,line:{width:2,color:"#fff"}},text:D.liqs.text,hoverinfo:"text+x"},
   {type:"scatter",mode:"lines",x:D.ts_ds,y:D.dd_ds,name:"Drawdown %",fill:"tozeroy",yaxis:"y2",
    line:{color:"#fa5252",width:.8}},
  ];
  const lay={height:520,margin:{l:60,r:60,t:30,b:40},hovermode:"closest",
    xaxis:{rangeslider:{visible:false},anchor:"y"},
    yaxis:{title:"equity USDT",type:type,side:"left",domain:[0.34,1]},
    ypx:{anchor:"x",showgrid:false,showticklabels:false,domain:[0.34,1],matches:false,fixedrange:true,range:[0,1]},
    yaxis2:{title:"drawdown %",side:"right",domain:[0,0.28],gridcolor:"rgba(128,128,128,.15)",zeroline:false},
    legend:{orientation:"h",y:-0.28}};
  if(range){lay.yaxis.range=range;}
  // events plotted against a normalized 0..1 overlay axis using price-independent positions:
  // map stop/liq y-values into [0,1] of the equity panel via helper below
  const eqMin = range? range[0]: Math.min(...D.equity_ds.filter(v=>v!=null));
  const eqMax = range? range[1]: Math.max(...D.equity_ds.filter(v=>v!=null));
  const norm = arr=>arr.map(v=>{const t=(v-eqMin)/((eqMax-eqMin)||1);return Math.min(Math.max(t,0.02),0.98);});
  figData[3].y=norm(D.stops.y); figData[4].y=norm(D.liqs.y);
  Plotly.react("cEquity",figData,lay,CFG);
  charts["cEquity"]=document.getElementById("cEquity");
}
buildEquity();

/* ---------- PnL attribution ---------- */
plot("cAttrLines",[
 {x:D.ts_ds,y:D.cum_net_ds,name:"Net equity PnL",line:{color:"#4dabf7",width:1.6}},
 {x:D.ts_ds,y:D.cum_realized_ds,name:"Cumulative realized PnL",line:{color:"#51cf66",width:1.3}},
 {x:D.ts_ds,y:D.cum_comm_ds,name:"Cumulative commissions",line:{color:"#fa5252",width:1.2}},
 {x:D.ts_ds,y:D.cum_upnl_ds,name:"Implied unrealized",line:{color:"#b197fc",width:1,dash:"dot"}},
],{height:380,margin:{l:60,r:20,t:30,b:40},xaxis:{rangeslider:{visible:false}},hovermode:"x unified",title:"Cumulative PnL components (USDT)"});

// stacked area: realized gains/losses + costs cumulative
let rg=0, rl=0; const cumRg=[],cumRl=[];
for(let i=0;i<D.trades.bar.length;i++){ /* filled later via bars */ }
// build per-bar realized split then downsample
(function(){
  const n=D.equity_ds.length;
})();
const stackX=D.ts_ds;
const sPos=[],sNeg=[],sComm=[];
{
  // aggregate realized by ds bucket
  const nb=stackX.length, pos=new Array(nb).fill(0), neg=new Array(nb).fill(0), com=new Array(nb).fill(0);
  const t0=stackX[0], span=stackX[nb-1]-t0||1;
  for(let i=0;i<D.trades.x.length;i++){
    const bi=Math.min(nb-1,Math.floor((D.trades.x[i]-t0)/span*nb));
    const r=D.trades.rpn[i]; if(r>0)pos[bi]+=r; else neg[bi]+=r;
    com[bi]+=D.trades.comm[i];
  }
  let a=0,b=0,c=0;
  for(let i=0;i<nb;i++){a+=pos[i];b+=neg[i];c+=com[i];sPos.push(+a.toFixed(2));sNeg.push(+b.toFixed(2));sComm.push(+(-c).toFixed(2));}
}
plot("cAttrStack",[
 {x:stackX,y:sPos,name:"realized gains",mode:"lines",stackgroup:"p",line:{width:0},fillcolor:"rgba(81,207,102,.55)"},
 {x:stackX,y:sNeg,name:"realized losses",mode:"lines",stackgroup:"p",line:{width:0},fillcolor:"rgba(255,107,107,.55)"},
 {x:stackX,y:sComm,name:"commissions",mode:"lines",stackgroup:"p",line:{width:0},fillcolor:"rgba(255,212,59,.5)"},
],{height:380,margin:{l:60,r:20,t:30,b:40},hovermode:"x unified",title:"Stacked cumulative attribution (USDT)"});

/* reconciliation table */
const attrRows=[
 ["Initial capital", K.initial_capital, false],
 ["+ Realized PnL", K.realized_pnl, true],
 ["+ Unrealized PnL", K.unrealized_pnl, true],
 ["− Commissions", -K.commissions, true],
 ["− Funding (est.)", -Math.abs(K.funding_est), true],
 ["− Slippage (in exec price)", null, true],
 ["− Reserve/siphoning", -K.reserve, true],
 ["± Residual/other", K.residual, true],
];
document.getElementById("attrTable").innerHTML = (function(){
 let sum=0, bad=false;
 const rowsHtml = attrRows.map(r=>{const v=r[1]; if(v!=null&&r[2])sum+=v;
   return `<tr><td>${r[0]}</td><td class="num">${v==null?"included in exec price":(v>=0?"+":"")+fmt(v,2)}</td></tr>`}).join("");
 const calc=K.initial_capital+K.realized_pnl+K.unrealized_pnl-K.commissions-Math.abs(K.funding_est)-K.reserve+K.residual;
 const diff=calc-K.final_capital; const ok=Math.abs(diff)<Math.max(1,K.initial_capital*0.01);
 return rowsHtml + `<tr class="tot"><td>= Final equity (computed)</td><td class="num ${ok?'ok':'bad'}">${fmt(calc,2)}</td></tr>
        <tr><td>Final equity (engine TPV)</td><td class="num">${fmt(K.final_capital,2)}</td></tr>
        <tr><td>Balance difference</td><td class="num ${ok?'ok':'bad'}">${ok?('OK ('+fmt(diff,2)+' USDT)'):('MISMATCH '+fmt(diff,2)+' USDT')}</td></tr>`;
})();

/* ---------- rebalance analysis ---------- */
plot("cRebalVol",[{type:"bar",x:D.rebals.x,y:D.rebals.vol,name:"rebalance volume",marker:{color:"rgba(77,171,247,.7)"},
  text:D.rebals.text}],
  {height:340,margin:{l:55,r:20,t:30,b:40},title:"Volume per rebalance (USDT)",xaxis:{rangeslider:{visible:false}}});
const sh=D.shares;
plot("cShares",[
 {x:sh.x,y:sh.L,name:"LONG actual",mode:"lines",line:{color:"#51cf66",width:1.3}},
 {x:sh.x,y:sh.S,name:"SHORT actual",mode:"lines",line:{color:"#ff6b6b",width:1.3}},
 {x:sh.x,y:sh.V,name:"VIRTUAL/CASH actual",mode:"lines",line:{color:"#4dabf7",width:1.3}},
 {x:[sh.x[0],sh.x[sh.x.length-1]],y:[sh.target.L*100,sh.target.L*100],name:"LONG target",line:{color:"#51cf66",dash:"dot",width:1}},
 {x:[sh.x[0],sh.x[sh.x.length-1]],y:[sh.target.S*100,sh.target.S*100],name:"SHORT target",line:{color:"#ff6b6b",dash:"dot",width:1}},
 {x:[sh.x[0],sh.x[sh.x.length-1]],y:[sh.target.V*100,sh.target.V*100],name:"VIRTUAL target",line:{color:"#4dabf7",dash:"dot",width:1}},
],{height:340,margin:{l:50,r:20,t:30,b:40},title:"Actual vs target share % per leg",hovermode:"x unified"});
document.getElementById("guardCard").innerHTML =
 `<b>Guards &amp; skips:</b> trend-guard blocked <span class="${K.trend_guard_blocks?'warn':''}">${K.trend_guard_blocks}</span> ·
  velocity-guard blocked <span class="${K.velocity_guard_blocks?'warn':''}">${K.velocity_guard_blocks}</span> ·
  skipped expansions <span class="${K.skipped_expansions?'warn':''}">${K.skipped_expansions}</span> ·
  cooldown after toxic event: ${K.cooldown_bars} bars (${(K.cooldown_bars/60).toFixed(1)} h) ·
  market orders filled ${K.market_orders_filled.toLocaleString()} ·
  surplus threshold ${fmt(K.threshold_surplus_pct,2)}% / deficit threshold ${fmt(K.threshold_deficit_pct,2)}% (config ticker_thresholds).<br>
  <span class="count">Rebalance count over time = blue markers on the price chart + this volume histogram. Triggers: every rebalance bar groups executed actions; trailing stops and liquidations force dormant/cooldown states (see markers).</span>`;

/* ---------- trades table (virtualized) ---------- */
const COLS=[["ts","Time UTC",0],["local","Local",0],["side","Side",0],["leg","Leg",0],["sym","Symbol",0],
 ["qty","Qty",1],["price","Price",1],["notional","Notional",1],["lev","Lev",1],["comm","Commission",1],
 ["fund","Funding*",1],["rpn","Realized PnL",1],["tpv","TPV after",1],["trig","Trigger",0],["rid","Rebal #",0],["ev","Event",0]];
document.getElementById("trHead").innerHTML = COLS.map((c,i)=>`<th data-i="${i}" class="${c[2]?'num':''}">${c[1]} ⇅</th>`).join("");
const ROWS=[];
let rebId=0, lastBar=-1;
const barToReb={};
for(let i=0;i<D.trades.x.length;i++){
  const b=D.trades.bar[i];
  if(b!==lastBar){lastBar=b;}
  const ms=D.trades.x[i];
  ROWS.push({ms,x:ms,ts:new Date(ms).toISOString().slice(0,16).replace("T"," "),
    local:new Date(ms).toLocaleString(), side:D.trades.side[i], leg:D.trades.leg[i], sym:K.ticker,
    qty:D.trades.qty[i], price:D.trades.price[i], notional:D.trades.notional[i],
    lev:D.trades.lev[i], comm:D.trades.comm[i], fund:null,
    rpn:D.trades.rpn[i], tpv:D.trades.tpv[i], trig:"rebalance", rid:0, ev:D.trades.leg[i]+":"+D.trades.side[i], i:i});
}
// assign rebalance ids by grouping consecutive same-bar fills
{ let cur=-2,id=0; for(const r of ROWS){ if(r.i!=null){} } }
{ const seen={}; let id=0; let prevBar=null;
  for(const r of ROWS){ const b=D.trades.bar[r.i]; if(b!==prevBar){id++;prevBar=b;} r.rid=id; } }
let filtered=ROWS.slice(), sortKey=0, sortAsc=true, renderStart=0;
const PAGE=200;
function cellVal(r,i){switch(i){case 0:return r.ts;case 1:return r.local;case 2:return r.side;case 3:return r.leg;case 4:return r.sym;
 case 5:return r.qty;case 6:return r.price;case 7:return r.notional;case 8:return r.lev;case 9:return r.comm;case 10:return 0;
 case 11:return r.rpn;case 12:return r.tpv;case 13:return r.trig;case 14:return r.rid;case 15:return r.ev;}}
function fmtCell(v,num){ if(v==null)return"—"; if(num){ if(typeof v!=="number")return v;
    return Math.abs(v)>=1000? v.toLocaleString("en-US",{maximumFractionDigits:2}) : v.toLocaleString("en-US",{maximumFractionDigits:6}); }
  return String(v);}
function applyFilters(){
  const q=document.getElementById("tblSearch").value.toLowerCase();
  const side=document.getElementById("fSide").value, leg=document.getElementById("fLeg").value, prof=document.getElementById("fProfit").value;
  const tf=Date.parse(document.getElementById("fTfrom").value)||null;
  const tt=(document.getElementById("fTto").value? (Date.parse(document.getElementById("fTto").value)+86399999):null);
  const nmin=parseFloat(document.getElementById("fNotMin").value), nmax=parseFloat(document.getElementById("fNotMax").value);
  const pmin=parseFloat(document.getElementById("fPnMin").value), pmax=parseFloat(document.getElementById("fPnMax").value);
  filtered=ROWS.filter(r=>{
    if(side&&r.side!==side)return false;
    if(leg&&r.leg!==leg)return false;
    if(prof==="pos"&&!(r.rpn>0))return false;
    if(prof==="neg"&&!(r.rpn<0))return false;
    if(tf&&r.ms<tf)return false;
    if(tt&&r.ms>tt)return false;
    if(isFinite(nmin)&&r.notional<nmin)return false;
    if(isFinite(nmax)&&r.notional>nmax)return false;
    if(isFinite(pmin)&&r.rpn<pmin)return false;
    if(isFinite(pmax)&&r.rpn>pmax)return false;
    if(q&&!(r.side+" "+r.leg+" "+r.ts+" "+r.ev).toLowerCase().includes(q))return false;
    return true;});
  doSort();
}
function doSort(){
  filtered.sort((a,b)=>{const x=cellVal(a,sortKey),y=cellVal(b,sortKey);
    let c=(typeof x==="number"&&typeof y==="number")?x-y:String(x).localeCompare(String(y));
    return sortAsc?c:-c;});
  renderStart=0; renderTable();
}
const ROWH=25;
function renderTable(){
  const body=filtered.slice(renderStart,renderStart+PAGE);
  const cells=r=>COLS.map((c,i)=>{
      let style="";
      if(i===11) style=`style="color:${r.rpn>=0?'var(--pos)':'var(--neg)'}"`;
      return `<td class="${c[2]?'num':''}" ${style}>${fmtCell(cellVal(r,i),c[2])}</td>`;}).join("");
  const top=renderStart*ROWH, bot=Math.max(0,(filtered.length-renderStart-PAGE)*ROWH);
  document.getElementById("trBody").innerHTML=
    (top?`<tr style="height:${top}px"><td colspan="${COLS.length}"></td></tr>`:"")
    +body.map(r=>`<tr style="height:${ROWH}px">`+cells(r)+"</tr>").join("")
    +(bot?`<tr style="height:${bot}px"><td colspan="${COLS.length}"></td></tr>`:"");
  document.getElementById("tblCount").textContent=`${filtered.length.toLocaleString()} of ${ROWS.length.toLocaleString()} trades shown`;
  document.getElementById("tblFoot").textContent=`rendered ${Math.min(renderStart+1,filtered.length)}–${Math.min(renderStart+PAGE,filtered.length)} (virtualized window of ${PAGE} rows; scroll to page)`;
}
document.getElementById("tblWrap").addEventListener("scroll",e=>{
  const start=Math.floor(e.target.scrollTop/ROWH/PAGE)*PAGE;
  if(start!==renderStart){renderStart=start;renderTable();}
});
document.querySelectorAll("#trHead th").forEach(th=>th.addEventListener("click",()=>{
  const i=+th.dataset.i; if(sortKey===i)sortAsc=!sortAsc;else{sortKey=i;sortAsc=true;} doSort();}));
["tblSearch","fSide","fLeg","fProfit","fTfrom","fTto","fNotMin","fNotMax","fPnMin","fPnMax"].forEach(id=>
  document.getElementById(id).addEventListener("input",applyFilters));
applyFilters();

/* ---------- exports ---------- */
function download(name,text,mime){const a=document.createElement("a");a.href=URL.createObjectURL(new Blob([text],{type:mime}));a.download=name;a.click();}
function csvOf(rows){
  const head=COLS.map(c=>c[1]).join(",");
  const body=rows.map(r=>COLS.map((c,i)=>{let v=cellVal(r,i);return typeof v==="string"&&v.includes(",")?`"${v}"`:v;}).join(",")).join("\n");
  return head+"\n"+body;}
document.getElementById("btnCsv").onclick=()=>download(`${K.ticker}_trades.csv`,csvOf(filtered),"text/csv");
document.getElementById("btnJson").onclick=()=>download(`${K.ticker}_visible.json`,
  JSON.stringify({kpis:K,trades:filtered.map(r=>({ts:r.ts,side:r.side,leg:r.leg,qty:r.qty,price:r.price,notional:r.notional,commission:r.comm,realized:r.rpn,tpv:r.tpv}))}),
  "application/json");
document.getElementById("btnCopy").onclick=async()=>{
  const s=[`Backtest ${K.ticker}`,`Window: ${dtstr(K.start)} → ${dtstr(K.end)} UTC`,`Bars: ${K.bars}`,
   `Net PnL: ${money(K.net_pnl)} (${pct(K.net_pnl_pct)})`,`MaxDD: ${fmt(K.max_dd_pct,2)}%`,`Sortino: ${fmt(K.sortino,4)}`,
   `Rebalances: ${K.rebalances}`,`Trades: ${K.trades} (BUY ${K.buy}/SELL ${K.sell})`,`Commissions: ${money(K.commissions)}`,
   `Realized: ${money(K.realized_pnl)}`].join("\n");
  try{await navigator.clipboard.writeText(s);}catch(e){prompt("Copy:",s);}};
document.getElementById("btnResetFilters").onclick=()=>{
  document.getElementById("tblSearch").value="";
  ["fSide","fLeg","fProfit"].forEach(i=>document.getElementById(i).value="");
  ["fTfrom","fTto","fNotMin","fNotMax","fPnMin","fPnMax"].forEach(i=>document.getElementById(i).value="");
  applyFilters();};

/* ---------- range selector & toggles ---------- */
function setRange(hours){
  const end=D.ts_ds[D.ts_ds.length-1];
  const from=hours==="all"?D.ts_ds[0]:end-hours*3600000;
  ["cPrice","cEquity","cAttrLines","cAttrStack","cRebalVol","cShares"].forEach(id=>{
    const el=document.getElementById(id);
    if(el&&el.data)Plotly.relayout(id,{"xaxis.range":[from,end]});});
}
document.querySelectorAll("#rangeSeg button").forEach(b=>b.onclick=()=>{
  document.querySelectorAll("#rangeSeg button").forEach(x=>x.classList.remove("on"));
  b.classList.add("on"); setRange(b.dataset.r==="all"?"all":+b.dataset.r);});
document.getElementById("btnResetZoom").onclick=()=>setRange("all");

// price series is trace index 0 (candle or close line); trades are all "·"-named groups
document.getElementById("tgPrice").onchange=e=>{Plotly.restyle("cPrice",{visible:e.target.checked},[0]);};
document.getElementById("tgTrades").onchange=e=>{
  const gd=document.getElementById("cPrice");
  const idx=gd.data.map((t,i)=>String(t.name).includes("\u00b7")?i:-1).filter(i=>i>=0);
  if(idx.length)Plotly.restyle(gd,{visible:e.target.checked},idx);};
document.getElementById("tgStops").onchange=e=>toggleByName("cPrice",["trailing stop"],e.target.checked);
document.getElementById("tgLiqs").onchange=e=>{toggleByName("cPrice",["liquidation"],e.target.checked);toggleByName("cEquity",["liquidations"],e.target.checked);};
document.getElementById("tgRebals").onchange=e=>toggleByName("cPrice",["rebalance"],e.target.checked);
document.getElementById("tgEquity").onchange=e=>toggleByName("cEquity",["TPV equity","high-water mark","initial capital","trailing stops","liquidations"],e.target.checked);
document.getElementById("tgDD").onchange=e=>toggleByName("cEquity",["Drawdown %"],e.target.checked);
function toggleByName(id,names,on){
  const gd=document.getElementById(id);
  const idx=gd.data.map((t,i)=>names.includes(t.name)?i:-1).filter(i=>i>=0);
  if(idx.length)Plotly.restyle(gd,{visible:on},idx);
}
document.querySelectorAll("#scaleSeg button").forEach(b=>b.onclick=()=>{
  document.querySelectorAll("#scaleSeg button").forEach(x=>x.classList.remove("on"));
  b.classList.add("on"); window.__eqScale=b.dataset.s; buildEquity();});

/* ---------- theme ---------- */
document.getElementById("btnTheme").onclick=()=>{
  document.body.classList.toggle("light");
  const dark=!document.body.classList.contains("light");
  Object.keys(document.querySelectorAll("[id^='c']")).forEach(()=>{});
  ["cPrice","cEquity","cAttrLines","cAttrStack","cRebalVol","cShares"].forEach(id=>{
    Plotly.relayout(id,{"paper_bgcolor":dark?"rgba(0,0,0,0)":"rgba(255,255,255,0)",
      "font.color":dark?"#e6edf3":"#1f2328"});});
};

/* ---------- quality ---------- */
(function(){
  const Q=D.quality;
  let h=`<div class="tiles" style="margin-bottom:10px">
   <div class="tile"><div class="tl">Input bars (file)</div><div class="tv">${Q.bars_total_in_file.toLocaleString()}</div></div>
   <div class="tile"><div class="tl">Used bars</div><div class="tv">${Q.bars_used.toLocaleString()}</div></div>
   <div class="tile"><div class="tl">Actual period</div><div class="tv">${Q.period_days} d</div></div>
   <div class="tile"><div class="tl">Timestamp gaps</div><div class="tv ${Q.ts_gaps?'warn':''}">${Q.ts_gaps}</div></div>
   <div class="tile"><div class="tl">Duplicate timestamps</div><div class="tv ${Q.ts_dupes?'neg':''}">${Q.ts_dupes}</div></div>
   <div class="tile"><div class="tl">NaN/Inf values</div><div class="tv ${Q.nan_inf?'neg':''}">${Q.nan_inf}</div></div>
   </div>`;
  if(Q.outlier_equity) h+=`<div class="alert"><b>⚠ Suspicious economic result.</b> Equity grew by multiple orders of magnitude. `+
    `Visualization is valid, but economic result may be invalid. Inspect equity/PnL accounting.</div>`;
  h+="<ul>"+(Q.flags.length?Q.flags.map(f=>`<li>${f}</li>`).join(""):"<li>no flags</li>")+"</ul>";
  h+=`<div class="count">fills embedded: ${D.trades.x.length.toLocaleString()} (all preserved, none dropped) · events: ${(D.stops.x.length+D.liqs.x.length)} · source: ${D.meta.generated_from}</div>`;
  document.getElementById("quality").innerHTML=h;
})();
document.getElementById("footNote").textContent=
 "Offline self-contained dashboard · Plotly inlined (no network requests) · generated by scripts/generate_dashboard_enhanced.py · strategy/backtest formulas untouched.";
</script>
</body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--payload", default=None, help="json result with equity_curve inside")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    res = load_payload(args)
    res["rebalance_log"] = []  # not persisted by engine; shares replayed from fills
    data = build_data(res)

    ticker = data["kpis"]["ticker"]
    slug = ticker.replace("USDT", "").lower()
    out = args.out or os.path.join(REPORTS, f"{slug}_12m_dashboard.html")

    # locate plotly js asset (offline requirement)
    candidates = [
        os.path.join(REPORTS, "plotly-2.27.0.min.js"),
        os.path.join(REPORTS, "plotly.min.js"),
    ]
    try:
        import plotly
        pj = os.path.join(os.path.dirname(plotly._plotly_utils.__file__), "..", "package_data", "plotly.min.js")
        candidates.insert(0, os.path.normpath(pj))
    except Exception:
        pass
    js_path = next((c for c in candidates if os.path.exists(c)), None)
    if js_path is None:
        raise SystemExit("plotly.min.js not found — dashboard must stay offline (copy reports/plotly-2.27.0.min.js)")
    plotly_js = open(js_path, encoding="utf-8").read()

    import datetime
    title = f"{ticker} 12m Backtest Dashboard"
    html = (HTML_TEMPLATE
            .replace("__TITLE__", title)
            .replace("__PLOTLY_JS__", plotly_js)
            .replace("__BDATA__", json.dumps(data, separators=(",", ":"))))
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"saved {out} ({os.path.getsize(out)//1024} KB) | trades {len(data['trades']['x']):,} | "
          f"bars {data['kpis']['bars']:,} | net {data['kpis']['net_pnl']:+,.0f} USDT | "
          f"window {datetime.datetime.utcfromtimestamp(data['kpis']['start']/1000)} → "
          f"{datetime.datetime.utcfromtimestamp(data['kpis']['end']/1000)} UTC")


if __name__ == "__main__":
    main()
