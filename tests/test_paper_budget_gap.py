"""Gap coverage for core/paper_budget.py allocation guards (tmp roots only)."""
import json
import sqlite3

import pytest

from core import paper_budget
from core.paper_budget import (amount, bind, initialize, reconcile_terminal_allocations,
                               release, reserve, ticker_history)


def _terminal_root(tmp_path):
    root = tmp_path / "root"
    (root / "core").mkdir(parents=True)
    initialize(root, 1, 1000, {})
    reserve(root, "T", 1000)
    bind(root, "T", {"initial_tpv": 1000, "started_at": 5})
    state = {"base_ticker": "T", "started_at": 5, "initial_tpv": 1000, "last_tpv": 900,
             "paper_close_complete": True, "paper_closed_at": 100, "rebalance_cycles": 0,
             "siphoning_reserve": 0}
    wallet = {"base_ticker": "T", "balance": 900,
              "positions": {"T_LONG": 0, "T_SHORT": 0},
              "share_fill_receipts": {"paper-close:stop:5:0:LONG": {
                  "event_id": "stop:5:0", "type": "LONG", "symbol": "T_LONG", "reduce_only": True,
                  "commission": "0", "trade_pnl": "0"}}}
    (root / "core" / "paper_state_T.json").write_text(json.dumps(state))
    (root / "core" / "paper_shadow_T.json").write_text(json.dumps(wallet))
    (root / "history").mkdir()
    (root / "history" / "paper_close_T_5.json").write_text(json.dumps(
        {"state": state, "paper_state": wallet, "timestamp": 100, "close_event": "stop:5:0"}))
    (root / "config.json").write_text(json.dumps({}))
    return root, state, wallet


def test_terminal_reconciliation_releases_a_proven_close(tmp_path):
    root, _, _ = _terminal_root(tmp_path)
    returned, blocked = reconcile_terminal_allocations(root)
    assert returned == ["T"] and not blocked


def test_confirm_close_releases_the_allocation(tmp_path):
    root, _, _ = _terminal_root(tmp_path)
    assert paper_budget.confirm_close(root, "T") == ["T"]


def test_confirm_close_rejects_an_unconfirmed_close(tmp_path):
    root, state, wallet = _terminal_root(tmp_path)
    state.pop("paper_close_complete")
    (root / "core" / "paper_state_T.json").write_text(json.dumps(state))
    with pytest.raises(ValueError, match="not confirmed"):
        paper_budget.confirm_close(root, "T")


def test_confirm_close_rejects_an_invalid_cooldown(tmp_path):
    root, state, wallet = _terminal_root(tmp_path)
    state["last_tpv"] = 1100
    wallet["balance"] = 1100
    (root / "core" / "paper_state_T.json").write_text(json.dumps(state))
    (root / "core" / "paper_shadow_T.json").write_text(json.dumps(wallet))
    (root / "history" / "paper_close_T_5.json").write_text(json.dumps(
        {"state": state, "paper_state": wallet, "timestamp": 100, "close_event": "stop:5:0"}))
    (root / "config.json").write_text(json.dumps({"probation_period_days": "NaN"}))
    with pytest.raises(ValueError, match="Invalid PAPER cooldown"):
        paper_budget.confirm_close(root, "T")


def test_confirm_close_requires_closing_receipts(tmp_path):
    root, state, wallet = _terminal_root(tmp_path)
    wallet["share_fill_receipts"] = {}
    (root / "core" / "paper_shadow_T.json").write_text(json.dumps(wallet))
    (root / "history" / "paper_close_T_5.json").write_text(json.dumps(
        {"state": state, "paper_state": wallet, "timestamp": 100, "close_event": "stop:5:0"}))
    with pytest.raises(ValueError, match="closing receipts missing"):
        paper_budget.confirm_close(root, "T")


def test_confirm_close_propagates_reconciliation_block(tmp_path):
    root, state, wallet = _terminal_root(tmp_path)
    with sqlite3.connect(root / "core" / "paper_budget.sqlite") as db:
        db.execute("UPDATE allocations SET session='99' WHERE ticker='T'")
    with pytest.raises(ValueError, match="identity mismatch"):
        paper_budget.confirm_close(root, "T")


@pytest.mark.parametrize("value", ["NaN", "-1"])
def test_amount_rejects_invalid_capital(value):
    with pytest.raises(ValueError, match="Invalid PAPER capital"):
        amount(value)
    assert amount("1") == 1


def test_initialize_rejects_a_nonpositive_budget(tmp_path):
    (tmp_path / "core").mkdir()
    with pytest.raises(ValueError, match="must be positive"):
        initialize(tmp_path, 0, 1000, {})


def test_initialize_rejects_overfunded_migration(tmp_path):
    (tmp_path / "core").mkdir()
    with pytest.raises(ValueError, match="exceed fixed system budget"):
        initialize(tmp_path, 1, 1000, {"X": {"initial_tpv": 2000, "started_at": 1}})


def test_initialize_rejects_an_unidentified_session(tmp_path):
    (tmp_path / "core").mkdir()
    with pytest.raises(ValueError, match="unidentified PAPER session"):
        initialize(tmp_path, 1, 1000, {"X": {"initial_tpv": 500}})


def test_bind_rejects_allocation_state_mismatch(tmp_path):
    root = tmp_path / "root"
    (root / "core").mkdir(parents=True)
    initialize(root, 1, 1000, {})
    reserve(root, "T", 1000)
    with pytest.raises(ValueError, match="allocation/state mismatch"):
        bind(root, "T", {"initial_tpv": 500, "started_at": 5})
    with pytest.raises(ValueError, match="allocation/state mismatch"):
        bind(root, "GHOST", {"initial_tpv": 1000, "started_at": 5})
    bind(root, "T", {"initial_tpv": 1000, "started_at": 5})
    with pytest.raises(ValueError, match="session identity mismatch"):
        bind(root, "T", {"initial_tpv": 1000, "started_at": 6})


def test_release_rejects_an_identity_mismatch(tmp_path):
    root = tmp_path / "root"
    (root / "core").mkdir(parents=True)
    initialize(root, 1, 1000, {})
    reserve(root, "T", 1000)
    bind(root, "T", {"initial_tpv": 1000, "started_at": 5})
    state = {"started_at": 6, "paper_close_complete": True, "last_tpv": 900}
    wallet = {"balance": 900, "positions": {"T_LONG": 0}}
    with pytest.raises(ValueError, match="release identity mismatch"):
        release(root, "T", state, wallet)
    with pytest.raises(ValueError, match="release identity mismatch"):
        release(root, "GHOST", dict(state, started_at=5), wallet)


def test_ticker_history_rejects_return_without_opening(tmp_path):
    root = tmp_path / "root"
    (root / "core").mkdir(parents=True)
    initialize(root, 1, 1000, {})
    with sqlite3.connect(root / "core" / "paper_budget.sqlite") as db:
        db.execute("INSERT INTO events VALUES (1, 'Z', 'return', '100')")
    with pytest.raises(ValueError, match="return without opening allocation"):
        ticker_history(root, "Z")


def test_reconcile_reports_an_unidentified_allocation(tmp_path):
    root = tmp_path / "root"
    (root / "core").mkdir(parents=True)
    initialize(root, 1, 1000, {})
    reserve(root, "B", 1000)
    returned, blocked = reconcile_terminal_allocations(root)
    assert returned == [] and blocked == {"B": "Unidentified PAPER allocation"}


def test_reconcile_reports_an_invalid_identity(tmp_path):
    root = tmp_path / "root"
    (root / "core").mkdir(parents=True)
    initialize(root, 1, 1000, {})
    reserve(root, "C", 1000)
    with sqlite3.connect(root / "core" / "paper_budget.sqlite") as db:
        db.execute("UPDATE allocations SET session='NaN' WHERE ticker='C'")
    returned, blocked = reconcile_terminal_allocations(root)
    assert blocked == {"C": "Invalid PAPER identity"}


def test_reconcile_rejects_a_diverging_archive(tmp_path):
    root, state, wallet = _terminal_root(tmp_path)
    archive = json.loads((root / "history" / "paper_close_T_5.json").read_text())
    archive["state"]["last_tpv"] = 800
    (root / "history" / "paper_close_T_5.json").write_text(json.dumps(archive))
    returned, blocked = reconcile_terminal_allocations(root)
    assert returned == [] and blocked == {"T": "PAPER terminal archive/state/wallet mismatch"}


def test_reconcile_rejects_a_mismatched_close_event(tmp_path):
    root, state, wallet = _terminal_root(tmp_path)
    archive = json.loads((root / "history" / "paper_close_T_5.json").read_text())
    archive["close_event"] = "weird:5:0"
    (root / "history" / "paper_close_T_5.json").write_text(json.dumps(archive))
    returned, blocked = reconcile_terminal_allocations(root)
    assert blocked == {"T": "PAPER terminal archive event mismatch"}


def test_reconcile_detects_a_changed_snapshot(tmp_path, monkeypatch):
    from pathlib import Path
    root, state, wallet = _terminal_root(tmp_path)
    state_file = str(root / "core" / "paper_state_T.json")
    original = Path.read_bytes
    reads = {"n": 0}

    def drifting(self):
        data = original(self)
        if str(self) == state_file:
            reads["n"] += 1
            if reads["n"] > 1:
                data += b" "
        return data

    monkeypatch.setattr(Path, "read_bytes", drifting)
    returned, blocked = reconcile_terminal_allocations(root)
    assert blocked == {"T": "PAPER terminal snapshots changed during reconciliation"}
