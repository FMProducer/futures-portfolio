"""Two-objective (Net, MaxDD) config optimizer for the TUSDT 89d backtest window.
Pure-stdlib engine (random explore + local blend exploit), no new dependencies.
Artifacts: optimization/netdd_trials.csv, optimization/netdd_report.md,
and --pick exports best_netdd_config.json at the project root.
"""

import argparse
import asyncio
import csv
import json
import logging
import os
import random
import sys
import tempfile
import time
from copy import deepcopy
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_DIR / "config.json"
DATA_DIR = PROJECT_DIR / "data"
CSV_PATH = PROJECT_DIR / "optimization" / "netdd_trials.csv"
REPORT_PATH = PROJECT_DIR / "optimization" / "netdd_report.md"
BEST_PATH = PROJECT_DIR / "best_netdd_config.json"

sys.path.insert(0, str(PROJECT_DIR))
from backtest.backtest_rebalance import run_backtest

TICKER = "TUSDT"
SINCE = "2026-07-27 00:00"
UNTIL = "2026-10-24 23:59"
DAYS = 89.0

CHOICES = {
    "equity_trailing_stop_timeout_sec": [10, 20, 30, 40, 60, 90, 120],
}

# (name, kind, low, high, step, base_value); kinds: f=float, i=int, c=choice, b=bool
SPEC = [
    ("equity_trailing_stop_pct", "f", 0.05, 3.0, 0.05, 0.3),
    ("equity_trailing_stop_activation_pct", "f", 0.0, 15.0, 0.5, 7.0),
    ("equity_trailing_stop_timeout_sec", "c", None, None, None, 40),
    ("toxic_cooldown_days", "f", 0.01, 0.5, None, 0.1),
    ("max_drawdown_limit", "f", 15.0, 200.0, 5.0, 200.0),
    ("max_drawdown_cooldown_days", "f", 0.01, 2.0, None, 1.0),
    ("rebalance_share_trail_distance", "f", 0.0, 0.12, 0.005, 0.04),
    ("min_notional_usdt", "f", 5.0, 15.0, 1.0, 6.0),
    ("rebalance_threshold_surplus", "f", 0.005, 0.06, 0.005, 0.031),
    ("rebalance_threshold_deficit", "f", 0.005, 0.09, 0.005, 0.031),
    ("virtual_share", "f", 0.05, 0.75, 0.05, 0.5),
    ("leverage", "i", 2, 15, 1, 7),
    ("fuse_enabled", "b", 0, 1, None, False),
    ("trend_add_block_pct", "f", 0.0, 0.05, 0.005, 0.01),
    ("trend_add_block_window", "i", 30, 240, 6, 60),
    ("sg_trend_min_move_pct", "f", 0.2, 2.0, None, 0.5),
    ("sg_trend_eff_threshold", "f", 0.4, 0.9, None, 0.55),
    ("sg_net_move_block_pct", "f", 0.5, 5.0, None, 2.14),
    ("sg_net_move_window_sec", "i", 30, 180, 6, 72),
    ("sg_trend_lookback_bars", "i", 10, 90, 5, 30),
    ("sg_max_price_velocity_pct", "f", 0.5, 5.0, None, 2.0),
    ("sg_velocity_window_sec", "i", 30, 120, 6, 60),
    ("liq_warn_pct", "f", 0.0, 20.0, 0.5, 0.0),
    ("liq_crit_pct", "f", 0.0, 10.0, 0.5, 0.0),
]
BASE_PARAMS = {key: base_val for key, _k, _a, _b, _s, base_val in SPEC}


def load_base() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def raw_base_params(base: dict) -> dict:
    # exact current-config values (no step quantization) for the reference trial
    pf = base["portfolios"][0]
    sg = pf.get("safety_guards", {})
    tdb = pf.get("trend_add_block", {})
    targets = pf["targets"]
    return {
        "equity_trailing_stop_pct": base["equity_trailing_stop_pct"],
        "equity_trailing_stop_activation_pct": base["equity_trailing_stop_activation_pct"],
        "equity_trailing_stop_timeout_sec": base["equity_trailing_stop_timeout_sec"],
        "toxic_cooldown_days": base["toxic_cooldown_days"],
        "max_drawdown_limit": base["max_drawdown_limit"],
        "max_drawdown_cooldown_days": base["max_drawdown_cooldown_days"],
        "rebalance_share_trail_distance": base["rebalance_share_trail_distance"],
        "min_notional_usdt": base["min_notional_usdt"],
        "rebalance_threshold_surplus": pf["rebalance_threshold_surplus"],
        "rebalance_threshold_deficit": pf["rebalance_threshold_deficit"],
        "virtual_share": targets["VIRTUAL"]["share"],
        "leverage": targets["BASE_LONG"]["leverage"],
        "fuse_enabled": pf["fuse_enabled"],
        "trend_add_block_pct": tdb["pct"],
        "trend_add_block_window": tdb["window_bars"],
        "sg_trend_min_move_pct": sg["trend_min_move_pct"],
        "sg_trend_eff_threshold": sg["trend_eff_threshold"],
        "sg_net_move_block_pct": sg["net_move_block_pct"],
        "sg_net_move_window_sec": sg["net_move_window_sec"],
        "sg_trend_lookback_bars": sg["trend_lookback_bars"],
        "sg_max_price_velocity_pct": sg["max_price_velocity_pct"],
        "sg_velocity_window_sec": sg["velocity_window_sec"],
        "liq_warn_pct": pf["liquidation_distance_warn_pct"],
        "liq_crit_pct": pf["liquidation_distance_crit_pct"],
    }


def encode(params: dict) -> list:
    vec = []
    for key, kind, a, b, _step, _base in SPEC:
        v = params[key]
        if kind == "c":
            opts = CHOICES[key]
            vec.append(opts.index(v) / max(1, len(opts) - 1))
        elif kind == "b":
            vec.append(1.0 if v else 0.0)
        else:
            vec.append((v - a) / (b - a))
    return vec


def decode(vec: list) -> dict:
    params = {}
    for i, (key, kind, a, b, step, _base) in enumerate(SPEC):
        x = min(1.0, max(0.0, vec[i]))
        if kind == "c":
            opts = CHOICES[key]
            params[key] = opts[min(len(opts) - 1, round(x * (len(opts) - 1)))]
        elif kind == "b":
            params[key] = x >= 0.5
        elif kind == "i":
            params[key] = int(round(a + x * (b - a)))
        else:
            v = a + x * (b - a)
            if step:
                v = a + round((v - a) / step) * step
            params[key] = round(v, 4)
    # constraint: activation must stay above the stop distance
    params["equity_trailing_stop_activation_pct"] = min(
        15.0, max(params["equity_trailing_stop_activation_pct"],
                  round(params["equity_trailing_stop_pct"] + 0.5, 4)))
    # constraint: crit distance cannot exceed warn distance
    params["liq_crit_pct"] = round(min(params["liq_crit_pct"], params["liq_warn_pct"]), 4)
    return params


def patch_config(base: dict, p: dict) -> dict:
    cfg = deepcopy(base)
    for key in ("equity_trailing_stop_pct", "equity_trailing_stop_activation_pct",
                "equity_trailing_stop_timeout_sec", "toxic_cooldown_days",
                "max_drawdown_limit", "max_drawdown_cooldown_days",
                "rebalance_share_trail_distance", "min_notional_usdt"):
        cfg[key] = p[key]
    pf = cfg["portfolios"][0]
    pf["rebalance_threshold_surplus"] = p["rebalance_threshold_surplus"]
    pf["rebalance_threshold_deficit"] = p["rebalance_threshold_deficit"]
    pf["ticker_thresholds"] = {}
    pf["fuse_enabled"] = p["fuse_enabled"]
    targets = pf["targets"]
    base_share = round((1.0 - p["virtual_share"]) / 2.0, 4)
    targets["VIRTUAL"]["share"] = p["virtual_share"]
    for leg in ("BASE_LONG", "BASE_SHORT"):
        targets[leg]["share"] = base_share
        targets[leg]["leverage"] = p["leverage"]
    pf["trend_add_block"] = {
        "pct": p["trend_add_block_pct"],
        "window_bars": p["trend_add_block_window"],
    }
    guards = dict(pf.get("safety_guards", {}))
    guards.update({
        "trend_min_move_pct": p["sg_trend_min_move_pct"],
        "trend_eff_threshold": p["sg_trend_eff_threshold"],
        "net_move_block_pct": p["sg_net_move_block_pct"],
        "net_move_window_sec": p["sg_net_move_window_sec"],
        "trend_lookback_bars": p["sg_trend_lookback_bars"],
        "max_price_velocity_pct": p["sg_max_price_velocity_pct"],
        "velocity_window_sec": p["sg_velocity_window_sec"],
    })
    pf["safety_guards"] = guards
    pf["liquidation_distance_warn_pct"] = p["liq_warn_pct"]
    pf["liquidation_distance_crit_pct"] = p["liq_crit_pct"]
    return cfg


def run_trial(params: dict, base: dict):
    cfg = patch_config(base, params)
    fd, path = tempfile.mkstemp(suffix=".json", prefix="tmp_netdd_", dir=str(PROJECT_DIR))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    try:
        # quiet=True would reset the "Backtest" logger to WARNING inside run_backtest,
        # so silence the whole logging stack for the trial instead
        logging.disable(logging.CRITICAL)
        try:
            return asyncio.run(
                run_backtest(path, str(DATA_DIR), False, TICKER, DAYS,
                             quiet=False, since_utc=SINCE, until_utc=UNTIL))
        finally:
            logging.disable(logging.NOTSET)
    finally:
        os.remove(path)


def pareto_front(points: list) -> list:
    # maximize net, minimize dd; points with identical metrics both survive
    front = []
    for i, p in enumerate(points):
        dominated = any(
            q["net"] >= p["net"] and q["dd"] <= p["dd"] and (q["net"] > p["net"] or q["dd"] < p["dd"])
            for j, q in enumerate(points) if i != j
        )
        if not dominated:
            front.append(p)
    return front


CSV_HEADER = ["trial", "stage", "net", "maxdd", "cycles", "stops", "liquidations",
              "accounting_ok", "rejected", "reason", "params_json"]


def run_optimize(n_trials: int, seed: int) -> None:
    rng = random.Random(seed)
    base = load_base()
    raw_params = raw_base_params(base)
    for stale in PROJECT_DIR.glob("tmp_netdd_*.json"):
        stale.unlink()

    points = []
    rejected = 0
    started = time.time()

    with open(CSV_PATH, "w", newline="", encoding="utf-8") as cf:
        writer = csv.writer(cf)
        writer.writerow(CSV_HEADER)
        cf.flush()

        def run_one(tid: int, stage: str, params: dict) -> None:
            nonlocal rejected
            res = run_trial(params, base)
            reason = ""
            ok = False
            if res is None:
                reason = "backtest_exception"
            elif not res.get("accounting_ok", False):
                reason = "accounting_mismatch"
            elif res.get("liquidations", 0) > 0:
                reason = f"liquidations={res['liquidations']}"
            else:
                ok = True
                points.append({
                    "tid": tid, "stage": stage,
                    "net": round(res["profit_pct"], 4),
                    "dd": round(res["max_dd_pct"], 4),
                    "cycles": res["cycles"], "stops": res["trailing_stops"],
                    "vec": encode(params),
                    "params": params,
                })
            if not ok:
                rejected += 1
            row = [tid, stage,
                   points[-1]["net"] if ok and points else "",
                   points[-1]["dd"] if ok and points else "",
                   points[-1]["cycles"] if ok and points else "",
                   points[-1]["stops"] if ok and points else "",
                   0 if ok else (res["liquidations"] if res else ""),
                   "1" if ok else "",
                   "0" if ok else "1",
                   reason,
                   json.dumps(params),
                   ]
            writer.writerow(row)
            cf.flush()
            if ok and points:
                p = points[-1]
                print(f"[{tid:03d}] {stage:7s} net={p['net']:+8.2f}% dd={p['dd']:5.2f}% "
                      f"cycles={p['cycles']:4d} stops={p['stops']} front={len(pareto_front(points))}")
            else:
                print(f"[{tid:03d}] {stage:7s} REJECTED: {reason}")

        run_one(0, "baseline", raw_params)
        explore = max(1, (n_trials - 1) // 2)
        exploit = n_trials - 1 - explore

        for i in range(explore):
            run_one(1 + i, "explore", decode([rng.random() for _ in SPEC]))

        front = sorted(pareto_front(points), key=lambda p: (-p["net"], p["dd"]))
        anchors = [p["vec"] for p in front[:5]] or [encode(raw_params)]
        for i in range(exploit):
            width = max(0.1, 0.5 - 0.4 * i / max(1, exploit - 1))
            anchor = anchors[i % len(anchors)]
            noise = [rng.random() for _ in SPEC]
            vec = [(1.0 - width) * a + width * x for a, x in zip(anchor, noise)]
            run_one(explore + 1 + i, "exploit", decode(vec))

    elapsed = time.time() - started
    write_report(points, rejected, n_trials, seed, elapsed)
    print(f"\nDone: {len(points)} ok, {rejected} rejected, {elapsed / 60:.1f} min. "
          f"CSV: {CSV_PATH.name} | Report: {REPORT_PATH.name}")


def write_report(points: list, rejected: int, n_trials: int, seed: int, elapsed: float) -> None:
    front = pareto_front(points)
    top = {}
    if points:
        top["max_net"] = max(points, key=lambda p: p["net"])
        top["min_dd"] = min(points, key=lambda p: p["dd"])
        with_dd = [p for p in points if p["dd"] > 0.5]
        if with_dd:
            top["max_ratio"] = max(with_dd, key=lambda p: p["net"] / p["dd"])

    chosen = list(top.values())
    rest = sorted([p for p in front if p not in chosen], key=lambda p: p["net"], reverse=True)
    need = max(0, 10 - len(chosen))
    if need and rest:
        step = max(1, len(rest) // need)
        chosen += rest[::step][:need]
    chosen = chosen[:10]

    base_params = BASE_PARAMS
    lines = [
        "# Net/MaxDD optimization report — TUSDT",
        "",
        f"- Window: {SINCE} -> {UNTIL} UTC | ticker: {TICKER} | engine: pure-stdlib explore+blend",
        f"- Trials requested: {n_trials} | completed ok: {len(points)} | rejected: {rejected} | seed: {seed} | elapsed: {elapsed / 60:.1f} min",
        f"- Baseline (current config.json): net={points[0]['net'] if points else '?'}% dd={points[0]['dd'] if points else '?'}%",
        f"- Pareto front size: {len(front)} (maximize Net%, minimize MaxDD%)",
        "",
        "## Top points",
        "",
        "| # | trial | stage | Net% | MaxDD% | Net/DD | cycles | stops |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for rank, p in enumerate(chosen, 1):
        ratio = p["net"] / p["dd"] if p["dd"] > 0.5 else float("inf")
        lines.append(f"| {rank} | {p['tid']} | {p['stage']} | {p['net']:+.2f} | {p['dd']:.2f} "
                     f"| {ratio if ratio != float('inf') else 'inf':.2f} | {p['cycles']} | {p['stops']} |")
    lines += ["", "## Params vs current config", ""]
    for p in chosen:
        diffs = {k: (base_params[k], p["params"][k]) for k in base_params if p["params"][k] != base_params[k]}
        if diffs:
            lines.append(f"- trial {p['tid']}: " + ", ".join(f"{k}: {old} -> {new}" for k, (old, new) in diffs.items()))
        else:
            lines.append(f"- trial {p['tid']}: identical to current config")
    lines += [
        "",
        "## Apply a point",
        "",
        "```powershell",
        "python optimization/optimize_all_netdd.py --pick <trial_id>",
        "python backtest/backtest_rebalance.py --config best_netdd_config.json --ticker TUSDT "
        f"--since \"{SINCE}\" --until \"{UNTIL}\"",
        "```",
        "",
        "Note: in-sample fit on a single TUSDT window; not a guarantee on other tickers/regimes.",
        "",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def pick_trial(tid: int) -> None:
    base = load_base()
    with open(CSV_PATH, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if int(row["trial"]) != tid:
                continue
            if row.get("rejected") == "1":
                raise SystemExit(f"trial {tid} was rejected ({row.get('reason')}); pick a completed trial")
            params = json.loads(row["params_json"])
            cfg = patch_config(base, params)
            BEST_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            changed = {k: (BASE_PARAMS[k], params[k]) for k in BASE_PARAMS if params[k] != BASE_PARAMS[k]}
            print(f"trial {tid}: wrote {BEST_PATH.name}")
            if changed:
                print("changed vs current config:")
                for k, (old, new) in changed.items():
                    print(f"  {k}: {old} -> {new}")
            else:
                print("identical to current config")
            return
    raise SystemExit(f"trial {tid} not found in {CSV_PATH.name}")


def smoke() -> None:
    base = load_base()
    res0 = run_trial(raw_base_params(base), base)
    assert res0 is not None, "baseline backtest failed"
    assert res0["accounting_ok"] and res0["liquidations"] == 0
    # raw current config on the fixed TUSDT window: 94.18% / 49.42%
    assert 90.0 < res0["profit_pct"] < 97.0, res0["profit_pct"]
    assert 45.0 < res0["max_dd_pct"] < 55.0, res0["max_dd_pct"]

    rng = random.Random(0)
    p1 = decode([rng.random() for _ in SPEC])
    assert p1["equity_trailing_stop_activation_pct"] >= p1["equity_trailing_stop_pct"] + 0.5
    assert p1["liq_crit_pct"] <= p1["liq_warn_pct"]
    patched = patch_config(base, p1)
    assert patched is not base and base["portfolios"][0]["rebalance_threshold_surplus"] == 0.031
    res1 = run_trial(p1, base)
    assert res1 is not None
    pts = [{"net": res0["profit_pct"], "dd": res0["max_dd_pct"], "tid": 0},
           {"net": res1["profit_pct"], "dd": res1["max_dd_pct"], "tid": 1}]
    assert pareto_front(pts), "pareto front empty"
    print(f"smoke OK | baseline net={res0['profit_pct']:.2f}% dd={res0['max_dd_pct']:.2f}% "
          f"| random trial net={res1['profit_pct']:.2f}% dd={res1['max_dd_pct']:.2f}%")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n-trials", type=int, default=120)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--smoke", action="store_true", help="run 2-trial self test")
    parser.add_argument("--pick", type=int, default=None,
                        help="trial id from netdd_trials.csv to export as best_netdd_config.json")
    args = parser.parse_args()

    if args.smoke:
        smoke()
        return
    if args.pick is not None:
        pick_trial(args.pick)
        return
    run_optimize(args.n_trials, args.seed)


if __name__ == "__main__":
    main()
