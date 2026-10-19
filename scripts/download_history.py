import aiohttp, os, time, math, asyncio
import pandas as pd

ENDPOINT = "https://fapi.binance.com/fapi/v1/klines"
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

async def download(symbol: str, days: float):
    os.makedirs(DATA_DIR, exist_ok=True)
    total_minutes = int(days * 24 * 60)
    limit_per_request = 1500
    num_requests = math.ceil(total_minutes / limit_per_request)
    end_time_ms = int(time.time() * 1000)
    all_data = []
    async with aiohttp.ClientSession() as session:
        for i in range(num_requests):
            batch = min(limit_per_request, total_minutes - len(all_data))
            if batch <= 0:
                break
            params = {"symbol": symbol, "interval": "1m", "limit": batch}
            if i > 0:
                params["endTime"] = end_time_ms
            for attempt in range(3):
                async with session.get(ENDPOINT, params=params) as resp:
                    data = await resp.json()
                if isinstance(data, list):
                    break
                await asyncio.sleep(2)
            all_data = data + all_data
            end_time_ms = data[0][0] - 1
            if i % 25 == 0:
                print(f"{i}/{num_requests} requests, {len(all_data)} candles", flush=True)
            if i < num_requests - 1:
                await asyncio.sleep(0.25)
    df = pd.DataFrame(all_data, columns=['time','open','high','low','close','volume','close_time','q_vol','trades','t_base','t_quote','ignore'])
    df = df[['open','high','low','close','volume']]
    for col in df.columns:
        df[col] = df[col].astype(float)
    out = os.path.join(DATA_DIR, f"{symbol}_live_{int(days)}d.feather")
    df.to_feather(out)
    print(f"saved {out}: {len(df)} candles")

if __name__ == "__main__":
    import asyncio
    asyncio.run(download("BTCUSDT", 365))
