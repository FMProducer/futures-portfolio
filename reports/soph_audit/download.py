"""Download a bounded year of closed Binance USD-M minute candles."""
import asyncio
import hashlib
import json
from pathlib import Path
import aiohttp
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]

async def main():
    end = pd.Timestamp.now(tz='UTC').floor('min')
    start = end - pd.Timedelta(days=365)
    cursor = int(start.timestamp() * 1000)
    rows = []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
        while cursor < int(end.timestamp() * 1000):
            async with session.get('https://fapi.binance.com/fapi/v1/klines', params={
                'symbol': 'SOPHUSDT', 'interval': '1m', 'startTime': cursor,
                'endTime': int(end.timestamp() * 1000) - 1, 'limit': 1500}) as response:
                response.raise_for_status()
                batch = await response.json()
            if not batch:
                break
            assert isinstance(batch, list) and batch[-1][0] >= cursor
            rows.extend(batch)
            cursor = batch[-1][0] + 60000
            if len(rows) % 30000 == 0:
                print(f'{len(rows)} bars', flush=True)
            await asyncio.sleep(.3)
    assert rows, 'No Binance history returned'
    frame = pd.DataFrame(rows).iloc[:, :6]
    frame.columns = ['time', 'open', 'high', 'low', 'close', 'volume']
    frame['time'] = pd.to_datetime(frame.time, unit='ms', utc=True)
    for column in frame.columns[1:]:
        frame[column] = frame[column].astype(float)
    assert frame.time.is_unique and frame.time.is_monotonic_increasing
    gaps = int((frame.time.diff().dropna() != pd.Timedelta(minutes=1)).sum())
    assert gaps == 0, f'{gaps} minute gaps'
    path = ROOT / 'data/SOPHUSDT_year.feather'
    path.parent.mkdir(exist_ok=True)
    frame.to_feather(path)
    manifest = dict(requested_start=str(start), requested_end_exclusive=str(end),
                    actual_start=str(frame.time.iloc[0]), actual_end=str(frame.time.iloc[-1]),
                    bars=len(frame), gaps=gaps, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    (Path(__file__).parent / 'data_manifest.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest), flush=True)

if __name__ == '__main__':
    asyncio.run(main())
