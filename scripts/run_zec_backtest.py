"""Run ZECUSDT backtest — configurable days/precision. No live Binance calls."""
import asyncio, json, os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZEC_SRC = os.path.join(ROOT, "data", "ZECUSDT_12m.feather")

import pandas as pd
if not os.path.exists(ZEC_SRC):
    print(f"ERROR: {ZEC_SRC} not found. Run scripts/download_zec_12m.py first.")
    sys.exit(1)

# Backtest runner picks from any *.feather in data_dir.  Copy our source so it picks.
df = pd.read_feather(ZEC_SRC)
df.to_feather(os.path.join(ROOT, "data", "ZECUSDT_live.feather"))

sys.path.insert(0, ROOT)
from backtest.backtest_rebalance import run_backtest

days = float(sys.argv[1]) if len(sys.argv) > 1 else 365.0
prefix = sys.argv[2] if len(sys.argv) > 2 else "zec_12m"
if days != 365.0:
    prefix = prefix if prefix != "zec_12m" else f"zec_{int(days)}d"

DATA_DIR = os.path.join(ROOT, "data")

t0 = time.time()
res = asyncio.run(run_backtest(
    config_path=os.path.join(ROOT, "config.json"),
    data_dir=DATA_DIR,
    live_mode=False,
    ticker_override="ZECUSDT",
    days=days,
    quiet=True,
))
print(f"wall: {time.time()-t0:.1f}s")

if res:
    curve = res.pop("equity_curve", [])
    os.makedirs(os.path.join(ROOT, "reports"), exist_ok=True)
    json.dump(res, open(os.path.join(ROOT, "reports", f"{prefix}_backtest_result.json"), "w", encoding="utf-8"), indent=2)
    json.dump(curve, open(os.path.join(ROOT, "reports", f"{prefix}_equity.json"), "w", encoding="utf-8"))
    print(json.dumps({k: v for k, v in res.items() if isinstance(v, (int, float, str))}, indent=2, default=str))
    print(f"equity_curve: {len(curve)} points")