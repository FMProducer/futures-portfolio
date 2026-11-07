"""Receipt migration and interrupted SQLite-to-config projection recovery."""
from contextlib import closing
import json
import sqlite3
from decimal import Decimal

import pytest

from core import funding, funding_store, storage
from tests.test_funding_reserves import account, event


def count_receipts(path):
    with closing(sqlite3.connect(funding_store.database_path(path))) as db:
        return db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]


@pytest.mark.parametrize("mode,key", [("paper", "paper_account_free_margin"), ("real", "cash_reserve_usdt")])
def test_migrate_legacy_receipts_without_second_debit(tmp_path, mode, key):
    path = account(tmp_path)
    config = json.loads(path.read_text())
    receipt = funding.reserve_funding(-3, 5000)
    receipt.update(reserve_key=key, event_time=100)
    config["portfolios"][0][key] = 4997
    config["funding_reserve_receipts"] = {f"{mode}:TESTUSDT:1": receipt}
    path.write_text(json.dumps(config))
    funding.settle_funding_reserve(str(path), mode, "TESTUSDT", event())
    compact = json.loads(path.read_text())
    assert "funding_reserve_receipts" not in compact
    assert compact["portfolios"][0][key] == 4997
    assert Decimal(compact["funding_reserve_totals"][key]) == -3
    assert count_receipts(path) == 1
    storage.safe_save_json_sync(str(path), config)
    assert json.loads(path.read_text()) == compact


@pytest.mark.parametrize("failure_stage", ["migration", "projection"])
def test_retry_after_database_commit_before_json_write(tmp_path, monkeypatch, failure_stage):
    path = account(tmp_path)
    stale = json.loads(path.read_text())
    if failure_stage == "migration":
        receipt = funding.reserve_funding(-3, 5000)
        receipt.update(reserve_key="paper_account_free_margin", event_time=100)
        stale["portfolios"][0]["paper_account_free_margin"] = 4997
        stale["funding_reserve_receipts"] = {"paper:TESTUSDT:1": receipt}
        path.write_text(json.dumps(stale))
    original = storage._atomic_save_json

    def fail(target, data, **kwargs):
        if str(target) == str(path):
            raise PermissionError("injected config sharing violation")
        return original(target, data, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(storage, "_atomic_save_json", fail)
        with pytest.raises(PermissionError):
            funding.settle_funding_reserve(str(path), "paper", "TESTUSDT", event())
    assert count_receipts(path) == 1
    funding.settle_funding_reserve(str(path), "paper", "TESTUSDT", event())
    storage.safe_save_json_sync(str(path), stale)
    current = json.loads(path.read_text())
    assert current["portfolios"][0]["paper_account_free_margin"] == 4997
    assert "funding_reserve_receipts" not in current
    assert count_receipts(path) == 1


def test_stale_writer_recovers_pending_projection_before_siphon_credit(tmp_path, monkeypatch):
    path = account(tmp_path)
    stale = json.loads(path.read_text())
    with monkeypatch.context() as scoped:
        scoped.setattr(storage, "_atomic_save_json", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk error")))
        with pytest.raises(OSError):
            funding.settle_funding_reserve(str(path), "real", "TESTUSDT", event())
    stale["portfolios"][0]["cash_reserve_usdt"] += 10
    storage.safe_save_json_sync(str(path), stale)
    assert json.loads(path.read_text())["portfolios"][0]["cash_reserve_usdt"] == 5007
    funding.settle_funding_reserve(str(path), "real", "TESTUSDT", event())
    assert json.loads(path.read_text())["portfolios"][0]["cash_reserve_usdt"] == 5007


def test_missing_database_blocks_duplicate_settlement(tmp_path):
    path = account(tmp_path)
    funding.settle_funding_reserve(str(path), "paper", "TESTUSDT", event())
    __import__('pathlib').Path(funding_store.database_path(path)).unlink()
    with pytest.raises(ValueError, match="database is missing"):
        funding.settle_funding_reserve(str(path), "paper", "TESTUSDT", event())


def test_config_size_does_not_grow_with_receipt_count(tmp_path):
    path = account(tmp_path)
    funding.settle_funding_reserve(str(path), "paper", "TESTUSDT", event("0", "0.01"))
    initial_size = path.stat().st_size
    for index in range(1, 100):
        funding.settle_funding_reserve(str(path), "paper", "TESTUSDT", event(str(index), "0.01"))
    assert count_receipts(path) == 100
    assert abs(path.stat().st_size - initial_size) < 32
    assert len(json.loads(path.read_text())["funding_reserve_totals"]) == 1


def test_invalid_legacy_receipt_preserves_config(tmp_path):
    path = account(tmp_path)
    config = json.loads(path.read_text())
    receipt = funding.reserve_funding(-3, 5000)
    receipt.update(reserve_key="paper_account_free_margin", event_time=100, reserve_after="1")
    config["funding_reserve_receipts"] = {"paper:TESTUSDT:1": receipt}
    path.write_text(json.dumps(config))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="legacy funding receipt"):
        funding.settle_funding_reserve(str(path), "paper", "TESTUSDT", event())
    assert path.read_bytes() == before
