"""Keep funding identities in SQLite and recover the bounded config projection."""
import json
import os
import sqlite3
import hashlib
from contextlib import closing, contextmanager
from contextvars import ContextVar
from functools import wraps
from threading import RLock, get_ident
from types import SimpleNamespace
from decimal import Decimal
from uuid import uuid4
from pathlib import Path

from filelock import FileLock


KEYS = ("cash_reserve_usdt", "paper_account_free_margin")
_connections = ContextVar('funding_connections', default=None)


class _Connection(sqlite3.Connection):
    _scope = None
    _leases = 0

    def close(self):
        if self._scope is None:
            return super().close()
        with self._scope.lock:
            self._leases -= 1
            if self._scope.closed and self._leases == 0:
                super().close()


@contextmanager
def connection_session():
    """Keep per-thread WAL anchors only for the lifetime of a runtime task."""
    existing = _connections.get()
    if existing is not None and not existing.closed:
        yield
        return
    scope = SimpleNamespace(lock=RLock(), connections={}, closed=False)
    token = _connections.set(scope)
    try:
        yield
    finally:
        with scope.lock:
            scope.closed = True
            for connection in scope.connections.values():
                if connection._leases == 0:
                    sqlite3.Connection.close(connection)
            scope.connections.clear()
        _connections.reset(token)


def with_connections(function):
    @wraps(function)
    async def wrapped(*args, **kwargs):
        with connection_session():
            return await function(*args, **kwargs)
    return wrapped


def database_path(config_path):
    return os.path.abspath(config_path) + ".funding.sqlite"


def connect(config_path, require_archive=False, readonly=False):
    scope = _connections.get()
    key = (get_ident(), os.path.normcase(database_path(config_path)), readonly)
    if scope is not None:
        with scope.lock:
            db = scope.connections.get(key) if not scope.closed else None
            if db is not None:
                db._leases += 1
        if db is not None:
            if require_archive and not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='applied_events'").fetchone():
                db.close()
                raise ValueError('Applied funding archive is missing')
            return db
    target = Path(database_path(config_path)).as_uri()+'?mode=ro' if readonly else database_path(config_path)
    db = sqlite3.connect(target, timeout=30, uri=readonly, factory=_Connection, check_same_thread=False)
    if require_archive and not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='applied_events'").fetchone():
        db.close()
        raise ValueError('Applied funding archive is missing')
    if not readonly:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("CREATE TABLE IF NOT EXISTS receipts (identity TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS pending (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS checkpoint (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS applied_events (identity TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        db.commit()
    if scope is not None:
        with scope.lock:
            if not scope.closed:
                db._scope, db._leases = scope, 1
                scope.connections[key] = db
    return db


def archived_event(config_path, mode, symbol, event_id, required=False, checkpoint=0):
    if not os.path.exists(database_path(config_path)):
        if required:
            raise ValueError("Archived funding database is missing")
        return None
    with closing(connect(config_path, require_archive=required, readonly=True)) as db:
        prefix=f'{mode}:{symbol}:'
        if checkpoint and not db.execute('SELECT 1 FROM applied_events WHERE rowid=? AND identity>=? AND identity<?',
                                          (checkpoint,prefix,prefix+'\uffff')).fetchone():
            raise ValueError('Applied funding archive checkpoint is missing')
        if required and not db.execute('SELECT 1 FROM applied_events WHERE identity>=? AND identity<? LIMIT 1',
                                       (prefix,prefix+'\uffff')).fetchone():
            raise ValueError('Applied funding archive for this worker is missing')
        try:
            row = db.execute("SELECT rowid,payload FROM applied_events WHERE identity=?", (f"{mode}:{symbol}:{event_id}",)).fetchone()
        except sqlite3.OperationalError as error:
            if not required and 'no such table' in str(error):
                return None
            raise
    return {**json.loads(row[1]), '_archive_sequence': row[0]} if row else None


def archive_events(config_path, mode, symbol, events):
    """Caller has already persisted a full post-apply WAL snapshot."""
    with FileLock(os.path.abspath(config_path) + ".lock", timeout=30):
        recover_projection(config_path)
        with closing(connect(config_path)) as db, db:
            sequence = 0
            for event in events:
                identity = f"{mode}:{symbol}:{event['event_id']}"
                row = db.execute("SELECT payload FROM receipts WHERE identity=?", (identity,)).fetchone()
                if not row:
                    raise ValueError("Applied funding has no durable reserve receipt")
                receipt = json.loads(row[0])
                if (event['status'] != 'applied' or Decimal(receipt['amount']) != Decimal(str(event['signed_amount']))
                        or receipt['event_time'] != event['event_time']):
                    raise ValueError("Applied funding archive receipt conflict")
                old = db.execute("SELECT payload FROM applied_events WHERE identity=?", (identity,)).fetchone()
                if old and json.loads(old[0]) != event:
                    raise ValueError("Applied funding archive payload conflict")
                db.execute("INSERT OR IGNORE INTO applied_events VALUES (?, ?)", (identity, json.dumps(event,sort_keys=True)))
                sequence = max(sequence, db.execute('SELECT rowid FROM applied_events WHERE identity=?',(identity,)).fetchone()[0])
            return sequence


def validate_checkpoint(config, db):
    fingerprint = (hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
                   if "funding_reserve_checkpoint" not in config else None)
    row = db.execute("SELECT payload FROM checkpoint WHERE id=1").fetchone()
    if row is None:
        totals = {key: Decimal(0) for key in KEYS}
        for payload, in db.execute("SELECT payload FROM receipts"):
            receipt = json.loads(payload)
            totals[receipt["reserve_key"]] += Decimal(receipt["settled"])
        if any(value != Decimal(config.get("funding_reserve_totals", {}).get(key, "0")) for key, value in totals.items()):
            raise ValueError("Funding config/database checkpoint mismatch")
        value = dict(token=uuid4().hex, totals={key: str(value) for key, value in totals.items()}, bootstrap=fingerprint)
        with db:
            db.execute("INSERT INTO checkpoint VALUES (1, ?)", (json.dumps(value),))
    else:
        value = json.loads(row[0])
    if (("funding_reserve_checkpoint" not in config and value.get("bootstrap") != fingerprint)
            or config.get("funding_reserve_checkpoint", value["token"]) != value["token"]
            or any(Decimal(config.get("funding_reserve_totals", {}).get(key, "0")) != Decimal(value["totals"].get(key, "0")) for key in KEYS)):
        raise ValueError("Funding config/database checkpoint mismatch")
    return value


def recover_projection(config_path):
    """Caller holds the config lock; a committed receipt must precede any config writer."""
    if not os.path.exists(database_path(config_path)):
        if os.path.exists(config_path):
            with open(config_path, encoding="utf-8") as handle:
                config = json.load(handle)
            if "funding_reserve_totals" in config:
                raise ValueError("Funding receipt database is missing; reconciliation required")
        return
    from core.storage import _atomic_save_json
    with closing(connect(config_path)) as db:
        row = db.execute("SELECT payload FROM pending WHERE id=1").fetchone()
        if not row:
            with open(config_path, encoding="utf-8") as handle:
                config = json.load(handle)
            if "funding_reserve_totals" in config:
                checkpoint = validate_checkpoint(config, db)
                if "funding_reserve_checkpoint" not in config:
                    config["funding_reserve_checkpoint"] = checkpoint["token"]
                    _atomic_save_json(config_path, config)
            return
        pending = json.loads(row[0])
        with open(config_path, encoding="utf-8") as handle:
            config = json.load(handle)
        key = pending["key"]
        current = str(config["portfolios"][0].get(key, 0))
        total = str(config.get("funding_reserve_totals", {}).get(key, "0"))
        if "checkpoint" in pending and config.get("funding_reserve_checkpoint") not in (pending["before_checkpoint"], pending["checkpoint"]):
            raise ValueError("Funding config/database checkpoint mismatch")
        if (Decimal(current) == Decimal(pending["after"]) and Decimal(total) == Decimal(pending["total_after"])
                and ("checkpoint" not in pending or config.get("funding_reserve_checkpoint") == pending["checkpoint"])):
            pass
        elif Decimal(current) == Decimal(pending["before"]) and Decimal(total) == Decimal(pending["total_before"]):
            from core.funding import json_decimal
            config["portfolios"][0][key] = json_decimal(Decimal(pending["after"]))
            config.setdefault("funding_reserve_totals", {})[key] = pending["total_after"]
            if "checkpoint" in pending:
                config["funding_reserve_checkpoint"] = pending["checkpoint"]
            _atomic_save_json(config_path, config)
        else:
            raise ValueError("Funding reserve projection requires reconciliation")
        with db:
            db.execute("DELETE FROM pending WHERE id=1")


def migrate(config_path, config, db):
    """Import legacy receipts without applying their already-booked money a second time."""
    receipts = config.get("funding_reserve_receipts")
    if receipts is None:
        return
    from core.storage import _atomic_save_json
    totals = {key: Decimal(0) for key in KEYS}
    with db:
        for identity, receipt in receipts.items():
            key = receipt["reserve_key"]
            if key not in KEYS:
                raise ValueError("Invalid funding reserve key")
            values = [Decimal(receipt[k]) for k in ("amount", "settled", "reserve_before", "reserve_after", "shortfall")]
            amount, settled, before, after, shortfall = values
            if (not all(v.is_finite() for v in values) or before < 0 or after < 0 or shortfall < 0
                    or before + settled != after or amount != settled - shortfall):
                raise ValueError("Invalid legacy funding receipt")
            encoded = json.dumps(receipt, sort_keys=True)
            old = db.execute("SELECT payload FROM receipts WHERE identity=?", (identity,)).fetchone()
            if old and json.loads(old[0]) != receipt:
                raise ValueError("Funding reserve event identity conflict")
            db.execute("INSERT OR IGNORE INTO receipts VALUES (?, ?)", (identity, encoded))
            totals[key] += settled
    config.pop("funding_reserve_receipts")
    config["funding_reserve_totals"] = {key: str(value) for key, value in totals.items()}
    _atomic_save_json(config_path, config)


def settle(config_path, mode, symbol, event):
    from core.funding import reserve_funding
    key = "paper_account_free_margin" if mode == "paper" else "cash_reserve_usdt"
    identity = f"{mode}:{symbol}:{event.event_id}"
    with FileLock(os.path.abspath(config_path) + ".lock", timeout=30):
        recover_projection(config_path)
        with open(config_path, encoding="utf-8") as handle:
            config = json.load(handle)
        if "funding_reserve_totals" in config and not os.path.exists(database_path(config_path)):
            raise ValueError("Funding receipt database is missing; reconciliation required")
        with closing(connect(config_path)) as db:
            migrate(config_path, config, db)
            checkpoint = validate_checkpoint(config, db)
            if "funding_reserve_checkpoint" not in config:
                from core.storage import _atomic_save_json
                config["funding_reserve_checkpoint"] = checkpoint["token"]
                _atomic_save_json(config_path, config)
            row = db.execute("SELECT payload FROM receipts WHERE identity=?", (identity,)).fetchone()
            if row:
                receipt = json.loads(row[0])
                if (Decimal(receipt["amount"]) != event.signed_amount or receipt["reserve_key"] != key
                        or receipt["event_time"] != event.event_time):
                    raise ValueError("Funding reserve event identity conflict")
                return {**receipt, "current_reserve": str(config["portfolios"][0][key])}
            receipt = reserve_funding(event.signed_amount, config["portfolios"][0].get(key, 0))
            receipt.update(reserve_key=key, event_time=event.event_time)
            total = Decimal(config.get("funding_reserve_totals", {}).get(key, "0"))
            pending = dict(key=key, before=receipt["reserve_before"], after=receipt["reserve_after"],
                           total_before=str(total), total_after=str(total + Decimal(receipt["settled"])),
                           before_checkpoint=config.get("funding_reserve_checkpoint"), checkpoint=uuid4().hex)
            next_totals = {**checkpoint["totals"], key: pending["total_after"]}
            with db:
                db.execute("INSERT INTO receipts VALUES (?, ?)", (identity, json.dumps(receipt, sort_keys=True)))
                db.execute("INSERT INTO pending VALUES (1, ?)", (json.dumps(pending),))
                db.execute("UPDATE checkpoint SET payload=? WHERE id=1", (json.dumps(dict(token=pending["checkpoint"], totals=next_totals)),))
        recover_projection(config_path)
        return {**receipt, "current_reserve": receipt["reserve_after"]}
