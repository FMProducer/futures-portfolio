"""Independent regression verification for the SQLite leak repairs (2026-11-21).

Owned by the independent reviewer; Claude's tests/test_sqlite_leak_repairs_20261119.py
stays untouched. Covers the 7 leak sites with REAL connection tracking - no
GC-only detector, no global warning filters:

  1. core/real_allocation.claim        (write: commit + close)
  2. core/real_allocation.validate     (read-only URI: close)
  3. core/real_allocation.retire read  (no active owner: read-only no-op)
  4. core/real_allocation.retire write (success commit / exception rollback)
  5. rebalance/paper Runner keeper original lifetime
     (constructor creates Store + WAL + _initialize; progress(); keeper close)
  6. supervisor/supervisor_service.latest_return (read-only: close)
  7. dashboard/swarm_curve.read_curve (read-only: close)

All databases are temp; all credentials and wallet files are local fakes;
no network, no services, no live system.
"""
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

TICKER = "TESTUSDT"
FAKE_API_KEY = "fake-key-20261121"


class _TrackedConnection:
    """Delegating proxy so the tracker sees every close() the SUT performs."""

    def __init__(self, real, entry):
        object.__setattr__(self, "real", real)
        object.__setattr__(self, "entry", entry)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "real"), name)

    def close(self):
        entry = object.__getattribute__(self, "entry")
        if not entry.closed:
            entry.closed = True
            object.__getattribute__(self, "real").close()

    def __enter__(self):
        return object.__getattribute__(self, "real").__enter__()

    def __exit__(self, *exc):
        return object.__getattribute__(self, "real").__exit__(*exc)


class ConnectionTracker:
    """Records every sqlite3.connect() call and proves close() actually ran."""

    class Entry:
        __slots__ = ("real", "args", "kwargs", "closed")

        def __init__(self, real, args, kwargs):
            self.real = real
            self.args = args
            self.kwargs = kwargs
            self.closed = False

    def __init__(self):
        self.entries = []

    def install(self, monkeypatch):
        original = sqlite3.connect

        def factory(*args, **kwargs):
            real = original(*args, **kwargs)
            entry = self.Entry(real, args, kwargs)
            self.entries.append(entry)
            return _TrackedConnection(real, entry)

        monkeypatch.setattr(sqlite3, "connect", factory)
        return self

    def mark(self):
        return len(self.entries)

    def assert_closed_since(self, mark):
        leaked = [e for e in self.entries[mark:] if not e.closed]
        assert not leaked, (
            "leaked sqlite connections (close() never called): "
            f"{[(e.args, e.kwargs) for e in leaked]}")

    def assert_postclose_raises(self, mark):
        for entry in self.entries[mark:]:
            with pytest.raises(sqlite3.ProgrammingError):
                entry.real.execute("SELECT 1")

    def readonly_entries(self, mark):
        return [e for e in self.entries[mark:]
                if any("mode=ro" in str(arg) for arg in e.args)]


@pytest.fixture
def tracker(monkeypatch):
    return ConnectionTracker().install(monkeypatch)


def fake_account_key():
    return hashlib.sha256(("True:" + FAKE_API_KEY).encode()).hexdigest()


class StubConnector:
    """Fake exchange connector: local wallet numbers only, never the network."""

    testnet = True
    api_key = FAKE_API_KEY

    def __init__(self, wallet=2000.0):
        self.wallet = wallet

    async def get_usdt_account(self):
        return {"wallet": self.wallet, "available": self.wallet + 1000.0}


def _seed_real_allocation(tmp, key=None):
    """Create the ledger via claim(), then return (root, proof, key)."""
    from core.real_allocation import claim

    key = key or fake_account_key()
    root = Path(tmp)
    (root / "core").mkdir(parents=True, exist_ok=True)
    proof = claim(root, key, TICKER, 1000.0, 0.0, 1000.0, 1000.0, 1234567890)
    return root, proof, key


def _seed_retire_states(root, proof, key):
    """Local fake strategy/wallet snapshots that satisfy retire()'s checks."""
    state = {
        "base_ticker": TICKER,
        "initial_tpv": 1000.0,
        "last_tpv": 1000.0,
        "trailing_stop_triggered": True,
        "live_close": {"status": "complete"},
        "live_funding": {"symbol": TICKER, "snapshot_ms": 123,
                         "allocation": dict(proof)},
        "virt_debt": 0,
    }
    wallet = {
        "base_ticker": TICKER,
        "balance": "2000",
        "positions": {f"{TICKER}_LONG": 0.0, f"{TICKER}_SHORT": 0.0},
        "profit_budget": {"blocked": False},
        "live_funding_snapshot": {"symbol": TICKER, "snapshot_ms": 123},
    }
    (root / "core" / f"real_state_{TICKER}.json").write_text(json.dumps(state), encoding="utf-8")
    (root / "core" / f"shadow_state_{TICKER}.json").write_text(json.dumps(wallet), encoding="utf-8")
    return state, wallet


# ── 1. core/real_allocation.claim ────────────────────────────────────────────

def test_claim_commits_and_closes(tmp_path, tracker):
    from core.real_allocation import claim

    (Path(tmp_path) / "core").mkdir(parents=True, exist_ok=True)
    mark = tracker.mark()
    proof = claim(Path(tmp_path), fake_account_key(), TICKER,
                  1000.0, 0.0, 1000.0, 1000.0, 1234567890)
    assert proof["id"]
    tracker.assert_closed_since(mark)
    tracker.assert_postclose_raises(mark)
    # persisted commit: the row survives a fresh connection
    db = sqlite3.connect(Path(tmp_path) / "core" / "real_budget.sqlite")
    try:
        row = db.execute("SELECT active, proof FROM allocations WHERE id=?",
                         (proof["id"],)).fetchone()
        assert row[0] == 1
        assert json.loads(row[1])["id"] == proof["id"]
    finally:
        db.close()
    tracker.assert_closed_since(mark)


def test_claim_refuses_second_active_owner(tmp_path, tracker):
    from core.real_allocation import claim

    _seed_real_allocation(tmp_path)
    mark = tracker.mark()
    with pytest.raises(ValueError, match="already allocated"):
        claim(Path(tmp_path), fake_account_key(), "OTHERUSDT",
              100.0, 0.0, 100.0, 100.0, 1)
    tracker.assert_closed_since(mark)


# ── 2. core/real_allocation.validate ─────────────────────────────────────────

def test_validate_readonly_and_closes(tmp_path, tracker):
    from core.real_allocation import claim, validate

    root, proof, key = _seed_real_allocation(tmp_path)
    mark = tracker.mark()
    validate(root, proof, TICKER, key)
    assert tracker.readonly_entries(mark), "validate must open the ledger read-only"
    tracker.assert_closed_since(mark)
    tracker.assert_postclose_raises(mark)
    with pytest.raises(ValueError, match="not the active confirmed owner"):
        validate(root, {**proof, "capital": "999"}, TICKER, key)


# ── 3. retire(): no active owner is a read-only no-op ────────────────────────

async def test_retire_no_active_owner_is_readonly_noop(tmp_path, tracker):
    from core import real_allocation

    root, proof, key = _seed_real_allocation(tmp_path)
    # deactivate the owner through our own (tracked, closed) connection
    db = sqlite3.connect(root / "core" / "real_budget.sqlite")
    db.execute("UPDATE allocations SET active=0 WHERE active=1")
    db.commit()
    db.close()

    mark = tracker.mark()
    result = await real_allocation.retire(StubConnector(), root, "2000")
    assert result is None
    assert tracker.readonly_entries(mark), "retire no-op path must not open a write connection"
    tracker.assert_closed_since(mark)
    tracker.assert_postclose_raises(mark)


# ── 4. retire(): success commits, exception rolls back ───────────────────────

def _empty_funding_journal():
    from core.funding import FundingJournal

    return FundingJournal.from_list([], config_path=None, state=None)


async def test_retire_success_commits_and_closes(tmp_path, tracker, monkeypatch):
    import core.funding as funding
    from core import real_allocation

    async def fake_poll(connector, state, symbol, end_ms, config_path=None):
        return _empty_funding_journal()

    monkeypatch.setattr(funding, "poll_live_funding", fake_poll)

    root, proof, key = _seed_real_allocation(tmp_path)
    _seed_retire_states(root, proof, key)
    mark = tracker.mark()
    result = await real_allocation.retire(StubConnector(2000.0), root, "2000")
    assert result is None  # no funding events in the stub journal
    tracker.assert_closed_since(mark)
    tracker.assert_postclose_raises(mark)
    # persisted commit: the owner row is closed, not just updated in memory
    db = sqlite3.connect(root / "core" / "real_budget.sqlite")
    try:
        row = db.execute("SELECT active, closed FROM allocations WHERE id=?",
                         (proof["id"],)).fetchone()
        assert row[0] == 0
        assert json.loads(row[1])["closed_at"] > 0
    finally:
        db.close()


async def test_retire_exception_rolls_back_and_closes(tmp_path, tracker, monkeypatch):
    import core.funding as funding
    from core import real_allocation

    async def fake_poll(connector, state, symbol, end_ms, config_path=None):
        return _empty_funding_journal()

    monkeypatch.setattr(funding, "poll_live_funding", fake_poll)

    root, proof, key = _seed_real_allocation(tmp_path)
    _seed_retire_states(root, proof, key)
    mark = tracker.mark()
    # wallet mismatch forces ValueError inside retire()'s try block
    with pytest.raises(ValueError, match="Account changed during REAL handover"):
        await real_allocation.retire(StubConnector(1999.0), root, "2000")
    tracker.assert_closed_since(mark)
    tracker.assert_postclose_raises(mark)
    # rollback semantics: the owner row must be untouched (no partial commit)
    db = sqlite3.connect(root / "core" / "real_budget.sqlite")
    try:
        row = db.execute("SELECT active, closed FROM allocations WHERE id=?",
                         (proof["id"],)).fetchone()
        assert row[0] == 1
        assert row[1] is None
    finally:
        db.close()
    # the error path persisted the blocked flag for operator reconciliation
    state = json.loads((root / "core" / f"real_state_{TICKER}.json").read_text(encoding="utf-8"))
    assert state["startup_blocked"] is True
    assert state["live_funding"]["blocked"]


async def test_retire_rowcount_mismatch_rolls_back_and_closes(tmp_path, tracker, monkeypatch):
    """Fault-injected concurrent handover: UPDATE matches 0 rows -> the defensive
    rowcount!=1 branch must roll back, close, and leave the active row untouched."""
    import core.funding as funding
    from core import real_allocation

    async def fake_poll(connector, state, symbol, end_ms, config_path=None):
        return _empty_funding_journal()

    monkeypatch.setattr(funding, "poll_live_funding", fake_poll)

    key = fake_account_key()
    proof = dict(id="json_id_b", account_key=key, ticker=TICKER, capital="1000", reserve="0",
                 account_wallet="1000", available="1000", snapshot_ms=1234567890)
    root = Path(tmp_path)
    (root / "core").mkdir(parents=True, exist_ok=True)
    ledger = root / "core" / "real_budget.sqlite"
    db = sqlite3.connect(ledger)
    try:
        db.execute("CREATE TABLE allocations(id TEXT PRIMARY KEY, active INTEGER, proof TEXT, closed TEXT)")
        db.execute("CREATE UNIQUE INDEX one_real_owner ON allocations(active) WHERE active=1")
        # the column id differs from the embedded proof id: UPDATE matches zero rows
        db.execute("INSERT INTO allocations VALUES (?, 1, ?, NULL)",
                   ("column_id_a", json.dumps(proof, sort_keys=True)))
        db.commit()
    finally:
        db.close()
    _seed_retire_states(root, proof, key)
    # with the mismatched ids the real validate() would refuse the proof; bypass it
    # so the write path reaches its defensive rowcount check
    monkeypatch.setattr(real_allocation, "validate", lambda *args, **kwargs: None)

    mark = tracker.mark()
    with pytest.raises(ValueError, match="REAL owner changed during handover"):
        await real_allocation.retire(StubConnector(2000.0), root, "2000")
    tracker.assert_closed_since(mark)
    tracker.assert_postclose_raises(mark)
    # rollback: the active row is untouched, no closed payload was persisted
    db = sqlite3.connect(ledger)
    try:
        row = db.execute("SELECT active, closed FROM allocations WHERE id=?",
                         ("column_id_a",)).fetchone()
        assert row[0] == 1
        assert row[1] is None
    finally:
        db.close()


# ── 5. Runner constructor: Store + WAL + progress + keeper close ────────────

def _paper_rows():
    return [{"open": "100.0", "high": "101.0", "low": "99.0", "close": "100.0",
             "volume": "1000", "time": i * 60000} for i in range(10)]


def _paper_settings():
    return {"capital": "1000.0", "spread_bps": "4", "slippage_bps": "2",
            "funding": [], "funding_mode": "ASSUMED_ZERO",
            "strategy": {"step": "1", "min_notional": "1", "leverage": "3",
                         "fee_rate": "0.0004", "surplus_pp": "4",
                         "deficit_pp": "4", "trail_pp": "5"}}


def test_runner_constructor_creates_store_wal_progress(tmp_path, tracker):
    from rebalance.paper import Runner

    db_path = Path(tmp_path) / "paper.db"
    mark = tracker.mark()
    runner = Runner(db_path, _paper_rows(), _paper_settings())
    try:
        assert hasattr(runner, "store"), (
            "Runner constructor lost the original keeper lifetime: "
            "self.store (Store + _initialize) is no longer created")
        # WAL is a persisted file property: a fresh connection must see it
        probe = sqlite3.connect(str(db_path))
        try:
            mode = probe.execute("PRAGMA journal_mode").fetchone()[0].lower()
            assert mode == "wal", f"paper database left in {mode!r} journal mode, expected wal"
        finally:
            probe.close()
        progress = runner.progress()
        assert progress["next_index"] == 0
    finally:
        runner.close()
    tracker.assert_closed_since(mark)
    tracker.assert_postclose_raises(mark)


def test_runner_context_success_closes_keeper(tmp_path, tracker):
    from rebalance.paper import Runner

    mark = tracker.mark()
    with Runner(Path(tmp_path) / "paper.db", _paper_rows(), _paper_settings()) as runner:
        keeper = runner._keeper
        assert not keeper.entry.closed
    assert keeper.entry.closed
    with pytest.raises(sqlite3.ProgrammingError):
        keeper.real.execute("SELECT 1")
    tracker.assert_closed_since(mark)


def test_runner_context_exception_closes_keeper(tmp_path, tracker):
    from rebalance.paper import Runner

    mark = tracker.mark()
    runner = Runner(Path(tmp_path) / "paper.db", _paper_rows(), _paper_settings())
    try:
        with runner:
            raise ValueError("forced")
    except ValueError:
        pass
    assert runner._keeper.entry.closed
    tracker.assert_closed_since(mark)
    tracker.assert_postclose_raises(mark)


# ── 6. supervisor/supervisor_service.latest_return ──────────────────────────

def test_supervisor_latest_return_readonly_closes(tmp_path, tracker):
    import supervisor.supervisor_service as ss

    db_path = Path(tmp_path) / "core" / "paper_budget.sqlite"
    db_path.parent.mkdir(parents=True)
    db = sqlite3.connect(db_path)
    try:
        db.execute("CREATE TABLE events(ts INTEGER, kind TEXT)")
        db.executemany("INSERT INTO events VALUES (?, 'return')", [(100,), (200,)])
        db.commit()
    finally:
        db.close()

    original_root = ss.ROOT
    ss.ROOT = Path(tmp_path)
    try:
        mark = tracker.mark()
        assert ss.latest_return() == 200
        assert tracker.readonly_entries(mark), "latest_return must open the budget read-only"
        tracker.assert_closed_since(mark)
        tracker.assert_postclose_raises(mark)
    finally:
        ss.ROOT = original_root


# ── 7. dashboard/swarm_curve.read_curve ──────────────────────────────────────

def test_swarm_curve_read_curve_readonly_closes(tmp_path, tracker):
    from dashboard.swarm_curve import read_curve

    db_path = Path(tmp_path) / "reports" / "paper_swarm_continuous" / "observations.sqlite"
    db_path.parent.mkdir(parents=True)
    db = sqlite3.connect(db_path)
    try:
        db.execute("CREATE TABLE observations(ts INTEGER, gap_seconds INTEGER, summary TEXT)")
        for i in range(5):
            db.execute("INSERT INTO observations VALUES (?, ?, ?)",
                       (i * 1000, 60, json.dumps({"reported_cumulative_net": 100.0 + i * 0.1})))
        db.commit()
    finally:
        db.close()

    mark = tracker.mark()
    result = read_curve(Path(tmp_path))
    assert len(result["points"]) == 5
    assert tracker.readonly_entries(mark), "read_curve must open the observations DB read-only"
    tracker.assert_closed_since(mark)
    tracker.assert_postclose_raises(mark)
