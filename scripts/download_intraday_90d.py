"""Download the approved Binance USD-M universe into an isolated timestamped dataset."""
import asyncio
import csv
import hashlib
import io
import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "data/intraday_90d_20260726_20261024"
START = pd.Timestamp("2026-07-26T00:00:00Z")
END = pd.Timestamp("2026-10-24T00:00:00Z")
COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume",
        "trades", "taker_buy_volume", "taker_buy_quote_volume", "ignore"]
BASE = "https://data.binance.vision/data/futures/um"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save(path, data):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    temp.replace(path)


def parse_archive(payload, start=START, end=END):
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members = archive.infolist()
        if len(members) != 1 or members[0].file_size > 64 * 1024 * 1024:
            raise ValueError("Unexpected archive members or expanded size")
        frame = pd.read_csv(io.BytesIO(archive.read(members[0])), header=None, dtype=str)
    if frame.shape[1] != 12:
        raise ValueError("Unexpected kline column count")
    if frame.iloc[0, 0] == "open_time":
        frame = frame.iloc[1:].reset_index(drop=True)
    frame.columns = COLS
    for key in ("open_time", "close_time", "trades"):
        numeric = pd.to_numeric(frame[key], errors="raise")
        if not (numeric % 1 == 0).all():
            raise ValueError("Nonintegral kline time/count")
        frame[key] = numeric.astype("int64")
    if (frame.open_time % 60000 != 0).any() or (frame.close_time != frame.open_time + 59999).any():
        raise ValueError("Invalid millisecond candle boundaries")
    frame = frame[(frame.open_time >= int(start.timestamp() * 1000)) &
                  (frame.open_time < int(end.timestamp() * 1000))].copy()
    for key in ("open", "high", "low", "close", "volume", "quote_volume", "taker_buy_volume", "taker_buy_quote_volume"):
        frame[key] = pd.to_numeric(frame[key], errors="raise").astype(float)
    numeric = frame[["open", "high", "low", "close", "volume", "quote_volume", "taker_buy_volume", "taker_buy_quote_volume"]]
    if not np.isfinite(numeric.to_numpy()).all():
        raise ValueError("Nonfinite kline values")
    if (frame[["open", "high", "low", "close"]] <= 0).any().any() or (frame[["volume", "trades", "quote_volume", "taker_buy_volume", "taker_buy_quote_volume"]] < 0).any().any():
        raise ValueError("Invalid prices, volume, or trade count")
    if ((frame.high < frame[["open", "close", "low"]].max(axis=1)) |
            (frame.low > frame[["open", "close", "high"]].min(axis=1))).any():
        raise ValueError("Invalid OHLC bounds")
    if frame.open_time.duplicated().any() or not frame.open_time.is_monotonic_increasing:
        raise ValueError("Duplicate or unordered candles inside archive")
    frame["time"] = pd.to_datetime(frame.open_time, unit="ms", utc=True)
    return frame


async def fetch(session, url, max_bytes):
    for attempt in range(3):
        try:
            async with session.get(url) as response:
                if response.status == 404:
                    return None
                if response.status in (418, 429):
                    raise RuntimeError(f"Rate limit {response.status}; stop rather than retrying aggressively")
                response.raise_for_status()
                chunks, length = [], 0
                async for chunk in response.content.iter_chunked(65536):
                    length += len(chunk)
                    if length > max_bytes:
                        raise ValueError("Response exceeds size bound")
                    chunks.append(chunk)
                return b"".join(chunks)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            if attempt == 2:
                raise
            await asyncio.sleep(2 ** attempt)


async def download_symbol(session, symbol):
    sources, frames = [], []
    # Monthly archives avoid thousands of API calls; October is not closed yet.
    periods = [("monthly", f"2026-{month:02}") for month in (7, 8, 9)]
    periods += [("daily", f"2026-10-{day:02}") for day in range(1, 24)]
    raw_dir = DATASET / "raw" / symbol
    raw_dir.mkdir(parents=True, exist_ok=True)
    for frequency, period in periods:
        name = f"{symbol}-1m-{period}.zip"
        url = f"{BASE}/{frequency}/klines/{symbol}/1m/{name}"
        row = {"url": url, "frequency": frequency, "period": period}
        try:
            checksum_path = raw_dir / (name + ".CHECKSUM")
            zip_path = raw_dir / name
            checksum = checksum_path.read_bytes() if checksum_path.exists() else await fetch(session, url + ".CHECKSUM", 4096)
            if checksum is None:
                row["status"] = "ARCHIVE_NOT_FOUND"
                sources.append(row)
                continue
            fields = checksum.decode("utf-8").split()
            if len(fields) != 2 or not re.fullmatch(r"[0-9a-fA-F]{64}", fields[0]) or fields[1].lstrip("*") != name:
                raise ValueError("Unexpected published checksum")
            payload = zip_path.read_bytes() if zip_path.exists() else await fetch(session, url, 32 * 1024 * 1024)
            if payload is None or sha(payload) != fields[0].lower():
                raise ValueError("Archive missing or SHA256 mismatch")
            frame = parse_archive(payload)
            if not zip_path.exists():
                temp = zip_path.with_suffix(".zip.tmp")
                temp.write_bytes(payload)
                temp.replace(zip_path)
            if not checksum_path.exists():
                checksum_path.write_bytes(checksum)
            frames.append(frame)
            row.update(status="VERIFIED", sha256=sha(payload), retained_rows=len(frame), bytes=len(payload))
        except Exception as error:
            row.update(status="ERROR", error=repr(error))
        sources.append(row)
    result = {"symbol": symbol, "requested_start_utc": str(START), "requested_end_exclusive_utc": str(END),
              "expected_rows": int((END - START).total_seconds() / 60), "sources": sources}
    if frames and any(len(frame) for frame in frames):
        frame = pd.concat(frames, ignore_index=True).sort_values("open_time").reset_index(drop=True)
        if frame.open_time.duplicated().any():
            raise ValueError(f"Overlapping candle archives for {symbol}")
        expected = np.arange(int(START.timestamp() * 1000), int(END.timestamp() * 1000), 60000, dtype=np.int64)
        missing = np.setdiff1d(expected, frame.open_time.to_numpy())
        counts = frame.groupby(frame.time.dt.floor("D")).size()
        target = DATASET / "candles" / f"{symbol}_live.feather"
        temp = target.with_suffix(".feather.tmp")
        frame.to_feather(temp)
        temp.replace(target)
        result.update(status="COMPLETE" if len(missing) == 0 and not any(r["status"] == "ERROR" for r in sources) else "PARTIAL",
                      rows=len(frame), missing_minutes=len(missing),
                      missing_open_times_ms=missing.tolist(), first_open_utc=str(frame.time.iloc[0]),
                      last_open_utc=str(frame.time.iloc[-1]),
                      complete_utc_sessions=sorted(str(day.date()) for day, count in counts.items() if count == 1440),
                      feather=str(target), sha256=sha(target.read_bytes()))
    else:
        result.update(status="NO_DATA", rows=0, missing_minutes=result["expected_rows"], complete_utc_sessions=[])
    save(DATASET / "symbols" / f"{symbol}.json", result)
    return result


async def main():
    rows = list(csv.DictReader((ROOT / "reports/sweep_1d_tickers.csv").open(encoding="utf-8")))
    universe = sorted(r["ticker"] for r in rows if not r["ticker"].startswith("AUDIT"))
    assert len(universe) == len(set(universe)) == 154
    assert not {"BTCUSDT", "ZECUSDT"}.intersection(universe)
    for child in ("raw", "candles", "symbols"):
        (DATASET / child).mkdir(parents=True, exist_ok=True)
    plan_path = DATASET / "manifest.json"
    if plan_path.exists():
        old = json.loads(plan_path.read_text(encoding="utf-8"))
        assert old["universe"] == universe and old["start_utc"] == str(START) and old["end_exclusive_utc"] == str(END)
    manifest = {"status": "RUNNING", "universe": universe, "start_utc": str(START), "end_exclusive_utc": str(END),
                "generated_utc": datetime.now(timezone.utc).isoformat(), "script_sha256": sha(Path(__file__).read_bytes()),
                "source": "Official Binance public USD-M kline archives derived from /fapi/v1/klines",
                "source_contract": "https://github.com/binance/binance-public-data", "results": []}
    protected = {str(ROOT / "data" / f"{r['ticker']}_live.feather"): sha((ROOT / "data" / f"{r['ticker']}_live.feather").read_bytes()) for r in rows}
    config_before = sha((ROOT / "config.json").read_bytes())
    save(plan_path, manifest)
    semaphore = asyncio.Semaphore(8)
    async with aiohttp.ClientSession(trust_env=True, timeout=aiohttp.ClientTimeout(total=90),
                                     connector=aiohttp.TCPConnector(limit=8)) as session:
        async def worker(symbol):
            async with semaphore:
                try:
                    result = await download_symbol(session, symbol)
                except Exception as error:
                    result = {"symbol": symbol, "status": "ERROR", "error": repr(error), "rows": 0, "complete_utc_sessions": []}
                    save(DATASET / "symbols" / f"{symbol}.json", result)
                manifest["results"].append({k: v for k, v in result.items() if k not in ("sources", "missing_open_times_ms")})
                save(plan_path, manifest)
                print(json.dumps({"done": len(manifest["results"]), "total": len(universe), "symbol": symbol,
                                  "status": result["status"], "rows": result["rows"]}), flush=True)
        await asyncio.gather(*(worker(symbol) for symbol in universe))
    assert len(manifest["results"]) == len({r["symbol"] for r in manifest["results"]}) == 154
    assert all(sha(Path(path).read_bytes()) == digest for path, digest in protected.items()), "Original input changed"
    manifest.update(status="FINISHED", original_inputs_unchanged=True,
                    working_config_unchanged=sha((ROOT / "config.json").read_bytes()) == config_before,
                    finished_utc=datetime.now(timezone.utc).isoformat())
    all_sessions = [set(r["complete_utc_sessions"]) for r in manifest["results"]]
    manifest["common_complete_utc_sessions_all_154"] = sorted(set.intersection(*all_sessions))
    manifest["status_counts"] = {status: sum(r["status"] == status for r in manifest["results"])
                                 for status in sorted({r["status"] for r in manifest["results"]})}
    save(plan_path, manifest)
    print(json.dumps(manifest["status_counts"]), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
