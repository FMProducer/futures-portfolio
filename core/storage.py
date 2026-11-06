import os
import json
import time
import asyncio
import random
import tempfile
from copy import deepcopy
from decimal import Decimal
from filelock import FileLock
from typing import Dict, Any

async def safe_load_json(path: str, default: Dict[str, Any], retries: int = 15) -> Dict[str, Any]:
    """
    Безопасное чтение JSON без блокировок.
    Использует ретраи при ошибках чтения или декодирования.
    """
    for attempt in range(retries):
        try:
            return await asyncio.to_thread(safe_load_json_sync, path, default)
        except Exception:
            if attempt == retries - 1:
                return default
            await asyncio.sleep(0.05 + random.random() * 0.1)
    return default

def safe_load_json_sync(path: str, default: Dict[str, Any], retries: int = 5) -> Dict[str, Any]:
    """Синхронная версия безопасного чтения JSON."""
    if not os.path.exists(path):
        return default
        
    for attempt in range(retries):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
                if not content:
                    return default
                return json.loads(content)
        except (json.JSONDecodeError, PermissionError, OSError):
            if attempt == retries - 1:
                return default
            time.sleep(0.05 + random.random() * 0.1)
    return default

async def safe_save_json(path: str, data: Dict[str, Any]) -> None:
    """
    Atomic JSON save; shared configuration updates also hold the account lock.
    Гарантирует целостность данных при сбоях.
    """
    await asyncio.to_thread(safe_save_json_sync, path, data)

def merge_funding_receipts(data, latest):
    """Preserve reserve settlements when a config writer holds an older snapshot."""
    merged = deepcopy(data)
    receipts = merged.setdefault("funding_reserve_receipts", {})
    for identity, receipt in latest.get("funding_reserve_receipts", {}).items():
        if identity in receipts:
            if receipts[identity] != receipt:
                raise ValueError("Funding reserve receipt changed")
            continue
        key = receipt["reserve_key"]
        if key not in ("cash_reserve_usdt", "paper_account_free_margin"):
            raise ValueError("Invalid funding reserve key")
        portfolio = merged["portfolios"][0]
        value = Decimal(str(portfolio.get(key, 0))) + Decimal(receipt["settled"])
        if not value.is_finite() or value < 0:
            raise ValueError("Stale config cannot reconcile funding reserve")
        from core.funding import json_decimal
        portfolio[key] = json_decimal(value)
        receipts[identity] = receipt
    return merged


def update_json_sync(path, update):
    """Commit a read-modify-write under the same lock as other config writers."""
    with FileLock(os.path.abspath(path) + ".lock"):
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        result = update(data)
        _atomic_save_json(path, data)
        return result


def safe_save_json_sync(path: str, data: Dict[str, Any]) -> None:
    if isinstance(data, dict) and "portfolios" in data:
        with FileLock(os.path.abspath(path) + ".lock"):
            if os.path.exists(path):
                with open(path, encoding="utf-8") as handle:
                    latest = json.load(handle)
                if latest.get("funding_reserve_receipts"):
                    data = merge_funding_receipts(data, latest)
            _atomic_save_json(path, data)
    else:
        _atomic_save_json(path, data)


def _atomic_save_json(path: str, data: Dict[str, Any]) -> None:
    """Синхронная версия атомарной записи JSON."""
    dir_name = os.path.dirname(os.path.abspath(path))
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)

    fd, temp_path = tempfile.mkstemp(dir=dir_name or ".", prefix=".tmp_state_", suffix=".json")
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

        max_retries = 5
        for i in range(max_retries):
            try:
                # os.replace атомарен на большинстве систем
                os.replace(temp_path, path)
                break
            except PermissionError:
                if i == max_retries - 1:
                    raise
                time.sleep(0.05 * (i + 1))
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
