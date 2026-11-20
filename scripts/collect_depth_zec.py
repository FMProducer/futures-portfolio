"""Collect 24h of ZECUSDT 1-min order book snapshots via Binance WebSocket.

Uses a physical-interface socket with proxy discovery disabled.
TLS certificate checking is preserved.
"""
import asyncio
import json
import os
import time
from datetime import datetime, timezone

import websockets
import pandas as pd

WS_URL = "wss://fstream.binance.com/public/stream"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys
sys.path.insert(0, ROOT)
from core.direct_network import connected_socket
DATA_DIR = os.path.join(ROOT, "data")
SNAPSHOT_INTERVAL = 60  # seconds — one snapshot per minute
DURATION_SEC = 86400    # 24 hours

async def collect():
    os.makedirs(DATA_DIR, exist_ok=True)
    out_feather = os.path.join(DATA_DIR, "ZECUSDT_depth_24h.feather")

    snapshots = []            # list of dicts — one row per minute
    last_ts = None
    start_time = time.time()
    started_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    async with websockets.connect(
        WS_URL, ping_interval=20, ping_timeout=10, close_timeout=5,
        proxy=None, sock=await connected_socket('fstream.binance.com', 443)
    ) as ws:
        await ws.send(json.dumps({'method': 'SUBSCRIBE', 'params': ['zecusdt@depth20@100ms'], 'id': 1}))
        print(f"[{started_at}] Connected to {WS_URL}", flush=True)
        print(f"Duration: {DURATION_SEC // 3600}h | Snapshot interval: {SNAPSHOT_INTERVAL}s", flush=True)

        while time.time() - start_time < DURATION_SEC:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=65)
            except asyncio.TimeoutError:
                print(f"  timeout at {datetime.now(timezone.utc).isoformat()}", flush=True)
                continue

            data = json.loads(msg)
            if 'result' in data:
                continue
            data = data.get('data', data)
            if 'b' not in data or 'a' not in data:
                raise ValueError('Binance depth message lacks bid/ask data')
            now = time.time()

            # Take one snapshot per minute
            # Binance depth stream keys: 'b'=bids, 'a'=asks, 'u'=finalUpdateId
            if last_ts is None or now - last_ts >= SNAPSHOT_INTERVAL:
                last_ts = now
                bids = data.get("b", [])[:20]
                asks = data.get("a", [])[:20]
                bid_count = len(bids)
                best_bid = float(bids[0][0]) if bids else None
                best_ask = float(asks[0][0]) if asks else None
                spread = round(best_ask - best_bid, 2) if best_bid and best_ask else None

                row = {
                    "timestamp": int(now * 1000),
                    "dt": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
                    "lastUpdateId": data.get("u", 0),
                    "bids": json.dumps(bids),
                    "asks": json.dumps(asks),
                    "best_bid": best_bid,
                    "best_ask": best_ask,
                    "spread": spread,
                }
                snapshots.append(row)
                elapsed = now - start_time
                pct = 100 * elapsed / DURATION_SEC
                bid_s = f"{best_bid:.2f}" if best_bid is not None else "N/A"
                ask_s = f"{best_ask:.2f}" if best_ask is not None else "N/A"
                print(f"  [{pct:.0f}%] {row['dt']}  bid={bid_s} ask={ask_s} "
                      f"spread={spread}  ({len(snapshots)} snaps, {bid_count} bids/asks)",
                      flush=True)

    # Save
    df = pd.DataFrame(snapshots)
    df_num = df[["timestamp", "dt", "best_bid", "best_ask", "spread"]].copy()
    df_num.to_feather(out_feather)

    # Full data as json for reference
    out_json = os.path.join(DATA_DIR, "ZECUSDT_depth_24h.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({"started_at": started_at, "symbol": "ZECUSDT",
                   "interval_sec": SNAPSHOT_INTERVAL, "snapshots": snapshots}, f, indent=2)

    elapsed = time.time() - start_time
    print(f"\nDone: {len(snapshots)} snapshots in {elapsed:.0f}s ({elapsed/3600:.1f}h)", flush=True)
    print(f"Numerics saved to: {out_feather} ({os.path.getsize(out_feather)//1024} KB)", flush=True)
    print(f"Full data saved to: {out_json} ({os.path.getsize(out_json)//1024//1024} MB)", flush=True)

    return snapshots


if __name__ == "__main__":
    asyncio.run(collect())
