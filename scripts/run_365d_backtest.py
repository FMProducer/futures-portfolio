import sys, os, time, asyncio, json, pathlib
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from backtest.backtest_rebalance import run_backtest

DATA_DIR = os.path.join(ROOT, "data")
feather = os.path.join(DATA_DIR, "BTCUSDT_live_365d.feather")

import pandas as pd
df = pd.read_feather(feather)
df.to_feather(os.path.join(DATA_DIR, "BTCUSDT_live.feather"))
print(f"bars: {len(df)}")

t0 = time.time()
res = asyncio.run(run_backtest(
    config_path=os.path.join(ROOT, "config.json"),
    data_dir=DATA_DIR,
    live_mode=False,
    ticker_override="BTCUSDT",
    days=365.0,
    quiet=True,
))
print(f"wall: {time.time()-t0:.1f}s")
if res:
    curve = res.pop("equity_curve", [])
    import json
    pathlib.Path("reports/btc_365d_backtest_result.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    pathlib.Path("reports/btc_365d_equity.json").write_text(json.dumps(curve), encoding="utf-8")
    print(json.dumps(res, indent=2))
    print(f"equity curve points: {len(curve)}")
