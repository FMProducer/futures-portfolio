"""Build report tables for the share-trailing experiment (offline, reads results only).

- annual comparison table
- 20-day window table with per-bar exposure reconstructed from the fill ledger
- diagnostic-period slice (29.09-19.10.2026, focus 08.10)
- final-close economics (open remainder closed at final price with costs)
"""
import json
import sys
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

EXP = Path(__file__).resolve().parent
RESULTS = EXP / "results"
ROOT = EXP.parent.parent
sys.path.insert(0, str(ROOT))
from backtest.dashboard_attribution import reconstruct_attribution
DF = pd.read_feather(ROOT / "data" / "ZECUSDT_live.feather")
CLOSES = DF["close"].to_numpy()
T_MS = (pd.to_datetime(DF["time"], utc=True).astype("int64") // 1000).to_numpy()  # seconds


def index_of(ts_str: str) -> int:
    s = pd.Timestamp(ts_str, tz="UTC").timestamp()
    return int(np.searchsorted(T_MS, s, side="left"))


def _apply_fill(f, pos, entry):
    leg, side, qty = f["leg"], f["side"], f["qty"]
    if leg not in ("BASE_LONG", "BASE_SHORT") or qty <= 0:
        return
    if side == "BUY" and leg == "BASE_LONG":
        new = pos[leg] + qty
        entry[leg] = (entry[leg] * pos[leg] + f["price"] * qty) / new if new > 0 else 0.0
        pos[leg] = new
    elif side == "SELL" and leg == "BASE_SHORT":
        new = pos[leg] + qty
        entry[leg] = (entry[leg] * pos[leg] + f["price"] * qty) / new if new > 0 else 0.0
        pos[leg] = new
    elif side == "SELL" and leg == "BASE_LONG":
        pos[leg] -= qty
        if pos[leg] <= 1e-12:
            pos[leg] = 0.0
            entry[leg] = 0.0
    elif side == "BUY" and leg == "BASE_SHORT":
        pos[leg] -= qty
        if pos[leg] <= 1e-12:
            pos[leg] = 0.0
            entry[leg] = 0.0


def exposure_and_close(res: dict, lev_l: float, lev_s: float):
    """Max net/gross notional exposure vs equity + labeled final close of the remainder."""
    offset = res.get("data_window", {}).get("offset", 0)
    n = len(res.get("equity_curve") or [])
    fills = sorted(res.get("fills") or [], key=lambda f: f["bar"])
    pos = {"BASE_LONG": 0.0, "BASE_SHORT": 0.0}
    entry = {"BASE_LONG": 0.0, "BASE_SHORT": 0.0}
    max_gross = max_net = 0.0
    fi = 0
    equity = res.get("equity_curve") or [0.0] * n
    for bar in range(n):
        while fi < len(fills) and fills[fi]["bar"] <= bar:
            _apply_fill(fills[fi], pos, entry)
            fi += 1
        price = CLOSES[offset + bar]
        eq = equity[bar]
        if eq > 0:
            gross = (abs(pos["BASE_LONG"]) + abs(pos["BASE_SHORT"])) * price / eq
            net = abs(abs(pos["BASE_LONG"]) - abs(pos["BASE_SHORT"])) * price / eq
            max_gross, max_net = max(max_gross, gross), max(max_net, net)
    # labeled final close of the open remainder at the final bar price,
    # slippage 0.05% adverse + 0.04% commission
    sl, cm = Decimal("0.0005"), Decimal("0.0004")
    price = Decimal(str(CLOSES[offset + n - 1]))
    close_costs = Decimal("0")
    open_desc = {}
    for leg, side, lev in (("BASE_LONG", "sell", lev_l), ("BASE_SHORT", "buy", lev_s)):
        qty = Decimal(str(pos[leg]))
        if qty <= 0:
            continue
        ex = price * (Decimal("1") - sl) if side == "sell" else price * (Decimal("1") + sl)
        pnl = qty * (ex - Decimal(str(entry[leg]))) if side == "sell" else qty * (Decimal(str(entry[leg])) - ex)
        fee = qty * ex * cm
        released_margin = qty * Decimal(str(entry[leg])) / Decimal(str(lev))
        open_desc[leg] = {"qty": float(qty), "entry": float(entry[leg]),
                          "close_pnl": float(pnl), "close_fee": float(fee),
                          "margin_released": float(released_margin)}
        close_costs += fee + qty * abs(ex - price)
    return {"max_gross_exposure_eq": round(max_gross, 3), "max_net_exposure_eq": round(max_net, 3),
            "final_open": open_desc, "final_close_costs": float(close_costs),
            "final_equity_closed": float(res.get("final_equity", equity[-1]) - float(close_costs)),
            "final_price": float(price)}


def replay_audit(res):
    """Separate fill replay; liquidation wipes without fills cannot be certified."""
    if res.get("liquidations", 0):
        return {"status": "not_certified_liquidation_model"}
    equity = res["equity_curve"]
    offset = res["data_window"]["offset"]
    result = reconstruct_attribution(
        CLOSES[offset:offset + len(equity)], equity, res["fills"],
        initial_capital=res["initial_capital"], final_equity=res["final_equity"],
        funding_rate_per_bar=res["settings"]["funding_rate_per_day"] / 1440,
        events=res.get("events", []))
    maximum = max(abs(x) for x in result["series"]["residual"])
    return {"status": "pass" if maximum <= .05 else "fail",
            "max_residual_usdt": maximum, "tolerance_usdt": .05,
            "unmatched_close_qty": result["checks"]["unmatched_close_qty"]}


def load_full(lbl: str) -> dict:
    """Result json + the separate equity-curve file written by the CLI."""
    p = RESULTS / f"{lbl}.json"
    if not p.exists():
        return {}
    r = json.loads(p.read_text(encoding="utf-8"))
    eq = RESULTS / f"{lbl}_equity.json"
    if eq.exists():
        r["equity_curve"] = json.loads(eq.read_text(encoding="utf-8"))
    return r


def main():
    out = {}
    # --- annual / long-horizon comparison ---
    annual = []
    for lbl in ("search_x7_baseline", "search_x7_trail_0.002", "search_x1_baseline",
                "search_x1_trail_0.002", "search_x1_trail_0.005", "search_x1_trail_0.01",
                "search_x1_trail_0.02", "search_x1_trail_0.035",
                "holdout_x1_baseline", "holdout_x1_trail",
                "full_x1_baseline", "full_x1_trail",
                "seriesb_x1_baseline", "seriesb_x1_trail", "seriesb_x7_baseline_ref",
                "diag_x7_baseline", "diag_x7_trail", "diag_x1_baseline", "diag_x1_trail"):
        r = load_full(lbl)
        if not r:
            continue
        base = "x7" if "_x7" in lbl else "x1"
        lev = (7.0, 7.0) if base == "x7" else (1.0, 1.0)
        ex = exposure_and_close(r, *lev)
        annual.append({
            "run": lbl, "net_pct": r.get("profit_pct"), "maxdd_pct": r.get("max_dd_pct"),
            "liq": r.get("liquidations"), "stops": r.get("trailing_stops"),
            "fills": r.get("fill_count"), "acc_ok": r.get("accounting_ok"),
            "realized": r.get("realized_pnl_total"), "unrealized_final": r.get("final_unrealized_pnl"),
            "commission": r.get("commission_paid"), "slippage": r.get("slippage_paid"),
            "funding": r.get("funding_paid"), **ex,
            "fill_replay": replay_audit(r),
        })
    out["annual"] = annual

    # --- 20-day independent-start windows ---
    rows = []
    for w in range(18):
        for base in ("x7", "x1"):
            variants = ("baseline", "trail") if base == "x1" else ("baseline",)
            for variant in variants:
                r = load_full(f"win{w:02d}_{base}_{variant}")
                if not r:
                    continue
                lev = (7.0, 7.0) if base == "x7" else (1.0, 1.0)
                ex = exposure_and_close(r, *lev)
                fills = r.get("fills") or []
                adds = sum(1 for f in fills if f.get("trigger") == "rebalance" and
                           (f.get("leg"), f.get("side")) in (("BASE_LONG", "BUY"), ("BASE_SHORT", "SELL")))
                reduces = sum(1 for f in fills if f.get("trigger") == "rebalance" and
                              (f.get("leg"), f.get("side")) in (("BASE_LONG", "SELL"), ("BASE_SHORT", "BUY")))
                rows.append({
                    "window": w, "start": r.get("_meta", {}).get("since"),
                    "base": base, "variant": variant,
                    "net_pct": r.get("profit_pct"), "maxdd_pct": r.get("max_dd_pct"),
                    "liq": r.get("liquidations"), "fills": r.get("fill_count"),
                    "commissions": r.get("commission_paid"), "slippage": r.get("slippage_paid"),
                    "funding": r.get("funding_paid"),
                    "max_gross_exposure_eq": ex["max_gross_exposure_eq"],
                    "max_net_exposure_eq": ex["max_net_exposure_eq"],
                    "adds": adds, "reduces": reduces, "acc_ok": r.get("accounting_ok"),
                    "final_close_costs": ex["final_close_costs"],
                    "fill_replay": replay_audit(r),
                })
    out["windows_note"] = ("each window is an independent start with the same initial capital; "
                           "their sum/product is NOT an annual return of one account")
    out["windows"] = rows

    # --- diagnostic period 29.09-19.10.2026 (known example, not independent OOS) ---
    r = load_full("diag_x7_baseline")
    eq = r.get("equity_curve") or []
    min_i = int(np.argmin(eq)) if eq else -1
    off = r.get("data_window", {}).get("offset", 0)
    diag = {
        "period": "2026-09-29 -> 2026-10-19 (known diagnostic example, not independent OOS)",
        "x7_baseline": {"net_pct": r.get("profit_pct"), "maxdd_pct": r.get("max_dd_pct"),
                        "min_equity": float(min(eq)) if eq else None,
                        "min_equity_ts": str(pd.Timestamp(T_MS[off + min_i], unit="s", tz="UTC")) if eq else None,
                        "fills": r.get("fill_count"), "liq": r.get("liquidations")},
    }
    # 08.10 UTC = day index 9 of the window (day 0 = 29.09)
    i0 = index_of("2026-09-29 00:00") - off
    around = []
    for k in range(max(0, i0 + 9 * 1440 - 2), min(len(eq), i0 + 9 * 1440 + 6)):
        around.append({"ts": str(pd.Timestamp(T_MS[off + k], unit="s", tz="UTC")),
                       "equity": round(eq[k], 4) if k < len(eq) else None})
    diag["x7_around_08.10"] = around
    out["diagnostic"] = diag

    (EXP / "report_tables.json").write_text(json.dumps(out, indent=2, ensure_ascii=False, default=float), encoding="utf-8")
    print(f"tables: {EXP / 'report_tables.json'} | annual rows: {len(annual)} | window rows: {len(rows)}")


if __name__ == "__main__":
    main()
