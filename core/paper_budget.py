"""Fixed PAPER capital pool; allocations survive process and config changes."""

import json
import sqlite3
import time
from decimal import Decimal


def amount(value):
    value = Decimal(str(value))
    if not value.is_finite() or value < 0:
        raise ValueError("Invalid PAPER capital")
    return value.quantize(Decimal("0.00000001"))


def database(root):
    return root / "core/paper_budget.sqlite"


def initialize(root, slots, capital, states, closed_result=0):
    initial = amount(capital) * int(slots)
    if initial <= 0:
        raise ValueError("PAPER initial budget must be positive")
    with sqlite3.connect(database(root)) as db:
        db.execute("PRAGMA synchronous=FULL")
        db.execute("CREATE TABLE IF NOT EXISTS account(initial TEXT, cash TEXT, created REAL)")
        db.execute("CREATE TABLE IF NOT EXISTS allocations(ticker TEXT PRIMARY KEY, capital TEXT, released INTEGER, session TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS events(ts REAL, ticker TEXT, kind TEXT, amount TEXT)")
        if db.execute("SELECT count(*) FROM account").fetchone()[0]:
            return
        reserved = sum((amount(s["initial_tpv"]) for s in states.values()), Decimal(0))
        funded = (initial + Decimal(str(closed_result))).quantize(Decimal("0.00000001"))
        if not funded.is_finite() or funded < 0 or reserved > funded:
            raise ValueError("Existing PAPER allocations exceed fixed system budget")
        db.execute("INSERT INTO account VALUES(?,?,?)", (str(initial), str(funded-reserved), time.time()))
        db.execute("INSERT INTO events VALUES(?,?,?,?)", (time.time(), "SYSTEM", "migration_result", str(closed_result)))
        for ticker, state in states.items():
            if not state.get("started_at"):
                raise ValueError("Cannot adopt unidentified PAPER session")
            db.execute("INSERT INTO allocations VALUES(?,?,0,?)", (ticker, str(amount(state['initial_tpv'])), str(state['started_at'])))
            db.execute("INSERT INTO events VALUES(?,?,?,?)", (time.time(), ticker, "adopt", str(amount(state['initial_tpv']))))


def reserve(root, ticker, requested):
    with sqlite3.connect(database(root)) as db:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT capital,released,session FROM allocations WHERE ticker=?", (ticker,)).fetchone()
        if old and not old[1]:
            return float(old[0]), False, old[2]
        cash = amount(db.execute("SELECT cash FROM account").fetchone()[0])
        capital = amount(requested)
        if capital <= 0 or cash < capital:
            return 0, True, None
        db.execute("UPDATE account SET cash=?", (str(cash-capital),))
        db.execute("INSERT OR REPLACE INTO allocations VALUES(?,?,0,NULL)", (ticker, str(capital)))
        db.execute("INSERT INTO events VALUES(?,?,?,?)", (time.time(), ticker, "reserve", str(capital)))
        return float(capital), True, None


def bind(root, ticker, state):
    with sqlite3.connect(database(root)) as db:
        row = db.execute("SELECT capital,session,released FROM allocations WHERE ticker=?", (ticker,)).fetchone()
        if not row or row[2] or amount(row[0]) != amount(state["initial_tpv"]):
            raise ValueError("PAPER allocation/state mismatch")
        identity = str(state["started_at"])
        if row[1] is not None and row[1] != identity:
            raise ValueError("PAPER session identity mismatch")
        db.execute("UPDATE allocations SET session=? WHERE ticker=?", (identity, ticker))


def release(root, ticker, state, wallet, rotation=False):
    if not state.get("paper_close_complete") or any(Decimal(str(q)) != 0 for q in wallet.get("positions", {}).values()):
        raise ValueError("PAPER capital return requires confirmed flat close")
    equity = amount(wallet["balance"]) + amount(state.get("siphoning_reserve", 0))
    if abs(equity - Decimal(str(state['last_tpv']))) > Decimal('0.0001'):
        raise ValueError("PAPER closing equity mismatch")
    with sqlite3.connect(database(root)) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT released,session,capital FROM allocations WHERE ticker=?", (ticker,)).fetchone()
        if not row or row[1] != str(state['started_at']):
            raise ValueError("PAPER release identity mismatch")
        if row[0]:
            return
        cash = amount(db.execute("SELECT cash FROM account").fetchone()[0])
        db.execute("UPDATE account SET cash=?", (str(cash+equity),))
        db.execute("UPDATE allocations SET released=1 WHERE ticker=?", (ticker,))
        db.execute("INSERT INTO events VALUES(?,?,?,?)", (time.time(), ticker, "return", str(equity)))
        if rotation and equity < amount(row[2]):
            db.execute("INSERT INTO events VALUES(?,?,?,?)", (time.time(), ticker, "rotation_loss", str(equity-amount(row[2]))))


def ticker_history(root, ticker):
    closed, opening, loss_at = Decimal(0), None, 0.0
    with sqlite3.connect(database(root).resolve().as_uri()+"?mode=ro", uri=True) as db:
        for ts, kind, value in db.execute("SELECT ts,kind,amount FROM events WHERE ticker=? ORDER BY rowid", (ticker,)):
            if kind in ("reserve", "adopt"):
                opening = amount(value)
            elif kind == "return":
                if opening is None:
                    raise ValueError("PAPER return without opening allocation")
                closed += amount(value)-opening
                opening = None
            elif kind == "rotation_loss":
                loss_at = max(loss_at, ts)
        row = db.execute("SELECT released,session FROM allocations WHERE ticker=?", (ticker,)).fetchone()
    return {"closed_net": float(closed), "loss_at": loss_at,
            "active_session": row[1] if row and not row[0] else None}


def read_budget(root):
    path = database(root)
    if not path.exists():
        return None
    with sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True) as db:
        initial, cash, created = db.execute("SELECT initial,cash,created FROM account").fetchone()
        allocations = {t:float(c) for t,c in db.execute("SELECT ticker,capital FROM allocations WHERE released=0")}
        excluded = sum(float(row[0]) for row in db.execute("SELECT amount FROM events WHERE kind='excluded_historical_gain'"))
    return {"initial":float(initial), "cash":float(cash), "created":created, "allocations":allocations,
            "unverified_historical_gain":excluded}
