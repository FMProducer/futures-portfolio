#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_zec_12m.py — canonical launch for the ZECUSDT 12-month backtest.

Runs the existing engine (no strategy/config changes), writes JSON results to
reports/zec_12m_backtest_result.json + reports/zec_12m_equity.json and then
regenerates the ONE canonical dashboard file reports/zec_12m_dashboard.html
via scripts/generate_dashboard_enhanced.py.

Usage:  python scripts/run_zec_12m.py [--days 365]
"""
import argparse
import asyncio
import json
import os
import pathlib
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from backtest.backtest_rebalance import run_backtest  # noqa: E402

DATA_DIR = os.path.join(ROOT, "data")
REPORTS = os.path.join(ROOT, "reports")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=365.0)
    args = ap.parse_args()

    t0 = time.time()
    res = asyncio.run(run_backtest(
        config_path=os.path.join(ROOT, "config.json"),
        data_dir=DATA_DIR,
        live_mode=False,
        ticker_override="ZECUSDT",
        days=args.days,
        quiet=True,
    ))
    if not res:
        raise SystemExit("backtest returned no result")
    print(f"wall: {time.time()-t0:.1f}s")

    curve = res.pop("equity_curve", [])
    slug = str(res.get("base_ticker", "ZECUSDT")).replace("USDT", "").lower()
    rp = pathlib.Path(REPORTS)
    (rp / f"{slug}_12m_backtest_result.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    (rp / f"{slug}_12m_equity.json").write_text(json.dumps(curve), encoding="utf-8")
    print(f"results written: {slug}_12m_backtest_result.json ({len(curve):,} equity points)")

    payload = {"equity_curve": curve, **res}
    tmp = rp / "_dashboard_payload.json"
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "generate_dashboard_enhanced.py"),
                        "--payload", str(tmp)], capture_output=True, text=True)
    tmp.unlink(missing_ok=True)
    print(r.stdout.strip())
    if r.returncode != 0:
        print(r.stderr[-3000:])
        raise SystemExit("dashboard generation failed")


if __name__ == "__main__":
    main()
