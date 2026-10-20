"""Download 12 months of ZECUSDT 1m klines + current order book snapshot.

Binance does NOT serve historical order book snapshots via REST API.
Real-time depth is available only via WebSocket stream.
We save:
  1. 12 months of 1m klines (OHLCV)               -> data/ZECUSDT_12m.feather
  2. Current order book snapshot (for reference)   -> data/ZECUSDT_depth_snapshot.json
"""
import asyncio
import json
import math
import os
import time

import aiohttp
import pandas as pd

FAPI_KLINES = "https://fapi.binance.com/fapi/v1/klines"
FAPI_DEPTH  = "https://fapi.binance.com/fapi/v1/depth"
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


async def download_klines(session: aiohttp.ClientSession, symbol: str, days: float) -> pd.DataFrame:
    total_minutes = int(days * 24 * 60)
    limit_per_request = 1500
    num_requests = math.ceil(total_minutes / limit_per_request)
    end_time_ms = int(time.time() * 1000)
    all_data = []

    for i in range(num_requests):
        batch = min(limit_per_request, total_minutes - len(all_data))
        if batch <= 0:
            break
        params = {"symbol": symbol, "interval": "1m", "limit": batch}
        if i > 0:
            params["endTime"] = end_time_ms
        for attempt in range(3):
            async with session.get(FAPI_KLINES, params=params) as resp:
                data = await resp.json()
            if isinstance(data, list):
                break
            await asyncio.sleep(2)
        if not isinstance(data, list):
            print(f"  request {i} failed after retries: {data}", flush=True)
            break
        all_data = data + all_data
        end_time_ms = data[0][0] - 1
        if i % 25 == 0:
            print(f"  [{i}/{num_requests}] {len(all_data)} candles", flush=True)
        if i < num_requests - 1:
            await asyncio.sleep(0.25)

    df = pd.DataFrame(all_data, columns=[
        'time','open','high','low','close','volume',
        'close_time','q_vol','trades','t_base','t_quote','ignore'])
    df = df[['time','open','high','low','close','volume']]
    for col in ['open','high','low','close','volume']:
        df[col] = df[col].astype(float)
    df['time'] = pd.to_datetime(df['time'], unit='ms')
    return df


async def download_depth(session: aiohttp.ClientSession, symbol: str, limit: int = 1000) -> dict:
    async with session.get(FAPI_DEPTH, params={"symbol": symbol, "limit": limit}) as resp:
        return await resp.json()


async def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    symbol = "ZECUSDT"

    async with aiohttp.ClientSession(
        trust_env=False,
        headers={"User-Agent": "futures-portfolio-data/1.0"}
    ) as session:

        # 1. Klines (historical, available)
        print(f"[1/2] Downloading 12 months of {symbol} 1m klines...")
        df = await download_klines(session, symbol, 365)
        klines_path = os.path.join(DATA_DIR, f"{symbol}_12m.feather")
        df.to_feather(klines_path)
        print(f"[1/2] Saved {len(df)} candles -> {klines_path}")

        # 2. Current order book snapshot (best-effort, not historical)
        print(f"[2/2] Fetching current order book snapshot for {symbol}...")
        depth = await download_depth(session, symbol, limit=1000)
        depth_path = os.path.join(DATA_DIR, f"{symbol}_depth_snapshot.json")
        with open(depth_path, 'w') as f:
            json.dump({"fetched_at_utc": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "symbol": symbol, "bids": depth.get("bids", []),
                       "asks": depth.get("asks", [])}, f, indent=2)
        print(f"[2/2] Saved {len(depth.get('bids',[]))} bids + {len(depth.get('asks',[]))} asks"
              f" -> {depth_path}")

        # Note
        print("\n---")
        print("NOTE: Binance REST API does NOT serve historical order book snapshots.")
        print("The depth file is a ONE-TIME snapshot, useful only for liquidity")
        print("reference, not for backtesting. For tick-level backtesting, consider")
        print("Binance's aggregate_trades stream or a third-party data provider.")
        print("---")


if __name__ == "__main__":
    asyncio.run(main())