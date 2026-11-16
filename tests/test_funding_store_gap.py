"""Gap coverage for core/funding_store.py branches (tmp databases only)."""
from contextlib import closing
import json
import sqlite3

import pytest

from core import funding_store


def _config(tmp_path, payload=None):
    config = tmp_path / "config.json"
    config.write_text(json.dumps(payload if payload is not None else {}))
    return config


def test_nested_connection_session_yields_the_existing_scope(tmp_path):
    config = _config(tmp_path)
    with funding_store.connection_session():
        with closing(funding_store.connect(str(config))) as db:
            assert db.execute("SELECT 1").fetchone()[0] == 1
        with funding_store.connection_session():
            pass
    # The outer scope closes idle connections when it exits.
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        db.execute("SELECT 1")


def test_cached_readonly_connection_missing_applied_archive(tmp_path):
    config = _config(tmp_path)
    # Drop (and commit) the archive table so a readonly connection genuinely
    # cannot see it; DELETE alone leaves the table and the require-archive
    # check would pass.
    with closing(funding_store.connect(str(config))) as db:
        db.execute("DROP TABLE applied_events")
        db.commit()
    with funding_store.connection_session():
        # Prime the cached readonly connection.
        with closing(funding_store.connect(str(config), readonly=True)):
            pass
        # The cached readonly connection now lacks the archive table.
        with pytest.raises(ValueError, match="Applied funding archive is missing"):
            funding_store.connect(str(config), require_archive=True, readonly=True)


def test_archived_event_required_but_worker_has_no_rows(tmp_path):
    config = _config(tmp_path)
    with closing(funding_store.connect(str(config))) as db:
        pass
    with pytest.raises(ValueError, match="for this worker is missing"):
        funding_store.archived_event(str(config), "paper", "TESTUSDT", "e1", required=True)


def test_archived_event_returns_none_when_the_table_is_absent(tmp_path):
    config = _config(tmp_path)
    with closing(funding_store.connect(str(config))) as db:
        db.execute("DROP TABLE applied_events")
    assert funding_store.archived_event(str(config), "paper", "TESTUSDT", "e1") is None


def test_archive_events_requires_a_durable_reserve_receipt(tmp_path):
    config = _config(tmp_path)
    with closing(funding_store.connect(str(config))) as db:
        pass
    event = {"event_id": "e1", "signed_amount": "-1", "event_time": 1, "status": "applied"}
    with pytest.raises(ValueError, match="durable reserve receipt"):
        funding_store.archive_events(str(config), "paper", "TESTUSDT", [event])


def test_archive_events_rejects_a_conflicting_receipt(tmp_path):
    config = _config(tmp_path)
    with closing(funding_store.connect(str(config))) as db:
        db.execute("INSERT INTO receipts VALUES(?,?)",
                   ("paper:TESTUSDT:e1", json.dumps({"amount": "-2", "reserve_key": "cash_reserve_usdt", "event_time": 1})))
        db.commit()
    event = {"event_id": "e1", "signed_amount": "-1", "event_time": 1, "status": "applied"}
    with pytest.raises(ValueError, match="receipt conflict"):
        funding_store.archive_events(str(config), "paper", "TESTUSDT", [event])


def test_archive_events_rejects_a_diverging_applied_payload(tmp_path):
    config = _config(tmp_path)
    with closing(funding_store.connect(str(config))) as db:
        db.execute("INSERT INTO receipts VALUES(?,?)",
                   ("paper:TESTUSDT:e1", json.dumps({"amount": "-1", "reserve_key": "cash_reserve_usdt", "event_time": 1})))
        db.execute("INSERT INTO applied_events VALUES(?,?)",
                   ("paper:TESTUSDT:e1", json.dumps({"event_id": "e1", "signed_amount": "-1", "event_time": 1,
                                                       "status": "applied", "other": True})))
        db.commit()
    event = {"event_id": "e1", "signed_amount": "-1", "event_time": 1, "status": "applied"}
    with pytest.raises(ValueError, match="payload conflict"):
        funding_store.archive_events(str(config), "paper", "TESTUSDT", [event])


def test_validate_checkpoint_rejects_diverging_totals(tmp_path):
    config = _config(tmp_path, {"funding_reserve_totals": {"cash_reserve_usdt": "5"}})
    with closing(funding_store.connect(str(config))) as db:
        with pytest.raises(ValueError, match="checkpoint mismatch"):
            funding_store.validate_checkpoint(json.loads(config.read_text()), db)


def test_recover_projection_persists_a_missing_checkpoint_token(tmp_path):
    config = _config(tmp_path, {"portfolios": [{"cash_reserve_usdt": "4997"}],
                                "funding_reserve_totals": {"cash_reserve_usdt": "-3"}})
    with closing(funding_store.connect(str(config))) as db:
        db.execute("INSERT INTO receipts VALUES(?,?)",
                    ("cash:TESTUSDT:e1", json.dumps({"amount": "-3", "settled": "-3",
                                                      "reserve_before": "5000", "reserve_after": "4997",
                                                      "shortfall": "0", "reserve_key": "cash_reserve_usdt",
                                                      "event_time": 100})))
        db.commit()
    funding_store.recover_projection(str(config))
    assert json.loads(config.read_text())["funding_reserve_checkpoint"]


def test_recover_projection_pending_checkpoint_mismatch(tmp_path):
    config = _config(tmp_path, {"portfolios": [{"cash_reserve_usdt": "4997"}],
                                "funding_reserve_totals": {"cash_reserve_usdt": "-3"},
                                "funding_reserve_checkpoint": "current"})
    with closing(funding_store.connect(str(config))) as db:
        db.execute("INSERT INTO pending VALUES (1,?)",
                   (json.dumps({"key": "cash_reserve_usdt", "before": "5000", "after": "4997",
                                "total_before": "0", "total_after": "-3",
                                "before_checkpoint": "old", "checkpoint": "new"}),))
    with pytest.raises(ValueError, match="checkpoint mismatch"):
        funding_store.recover_projection(str(config))


def test_recover_projection_requires_manual_reconciliation(tmp_path):
    config = _config(tmp_path, {"portfolios": [{"cash_reserve_usdt": "1"}],
                                 "funding_reserve_totals": {"cash_reserve_usdt": "0"}})
    with closing(funding_store.connect(str(config))) as db:
        db.execute("INSERT INTO pending VALUES (1,?)",
                   (json.dumps({"key": "cash_reserve_usdt", "before": "5000", "after": "4997",
                                "total_before": "0", "total_after": "-3"}),))
        db.commit()
    with pytest.raises(ValueError, match="requires reconciliation"):
        funding_store.recover_projection(str(config))


def test_migrate_rejects_an_unknown_reserve_key(tmp_path):
    config = _config(tmp_path)
    with closing(funding_store.connect(str(config))) as db:
        with pytest.raises(ValueError, match="Invalid funding reserve key"):
            funding_store.migrate(str(config),
                                  {"funding_reserve_receipts": {"cash:TESTUSDT:e1": {
                                      "reserve_key": "bogus", "amount": "-1", "settled": "-1",
                                      "reserve_before": "5000", "reserve_after": "4999", "shortfall": "0"}}}, db)


def test_migrate_rejects_a_diverging_legacy_receipt(tmp_path):
    config = _config(tmp_path)
    receipt = {"reserve_key": "cash_reserve_usdt", "amount": "-1", "settled": "-1",
               "reserve_before": "5000", "reserve_after": "4999", "shortfall": "0", "event_time": 1}
    with closing(funding_store.connect(str(config))) as db:
        db.execute("INSERT INTO receipts VALUES(?,?)", ("cash:TESTUSDT:e1", json.dumps(dict(receipt, event_time=2))))
        config_payload = {"funding_reserve_receipts": {"cash:TESTUSDT:e1": receipt}}
        with pytest.raises(ValueError, match="identity conflict"):
            funding_store.migrate(str(config), config_payload, db)
