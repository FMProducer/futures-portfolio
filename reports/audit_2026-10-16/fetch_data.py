"""Public fixed September 2026 1-minute archives; no account API or credentials."""
import hashlib
import io
import json
import urllib.request
import zipfile
from pathlib import Path

OUT = Path(__file__).parent
DATA = OUT / "market_data"
DATA.mkdir(exist_ok=True)
records = []
for symbol in ("BTCUSDT", "ETHUSDT", "ZECUSDT"):
    url = f"https://data.binance.vision/data/futures/um/monthly/klines/{symbol}/1m/{symbol}-1m-2026-09.zip"
    record = dict(symbol=symbol, url=url)
    try:
        with urllib.request.urlopen(url, timeout=40) as response:
            body = response.read()
        with urllib.request.urlopen(url + ".CHECKSUM", timeout=20) as response:
            expected = response.read().decode().split()[0]
        actual = hashlib.sha256(body).hexdigest()
        if actual != expected:
            raise ValueError("Checksum mismatch")
        (DATA / (symbol + ".zip")).write_bytes(body)
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            csv = archive.read(archive.namelist()[0])
        (DATA / (symbol + ".csv")).write_bytes(csv)
        record.update(status="DOWNLOADED", sha256=actual, checksum=expected, bytes=len(body))
    except Exception as e:
        record.update(status="UNAVAILABLE", error=str(e))
    records.append(record)
    print(json.dumps(record), flush=True)
(OUT / "market_data_manifest.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
