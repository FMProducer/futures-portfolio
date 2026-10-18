"""Serialized, event-backed fixed-data ledger; amounts are decimal strings on disk.

One database is one project/account. Events, register deltas, and the materialized
state commit together. A caller supplies evidence identifiers and UTC effective
times; this offline module does not authenticate provider evidence.
"""
import copy
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal, Inexact, ROUND_HALF_EVEN, ROUND_CEILING, localcontext


class Rejected(ValueError):
    pass


class Conflict(Rejected):
    pass


def amount(value):
    if not isinstance(value, str):
        raise Rejected("Amounts must be decimal strings")
    n = Decimal(value)
    if not n.is_finite() or n < 0 or len(n.as_tuple().digits) > 28 or abs(n.as_tuple().exponent) > 18:
        raise Rejected("Invalid amount or unsupported precision")
    return n


def utc(value):
    t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if t.tzinfo is None or t.utcoffset() != timedelta(0):
        raise Rejected("UTC timestamp required")
    return t


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


REGISTERS = ("equity", "hwm_reward", "hwm_risk", "debt", "liability", "b_usdt",
             "b_gas", "expenses", "uncovered", "credit", "card")


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class Ledger:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY, event_key TEXT UNIQUE NOT NULL,
                    request TEXT NOT NULL, result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS postings(event_seq INTEGER NOT NULL REFERENCES events(seq),
                    register TEXT NOT NULL, delta TEXT NOT NULL, PRIMARY KEY(event_seq, register));
                CREATE TRIGGER IF NOT EXISTS immutable_events_update BEFORE UPDATE ON events
                    BEGIN SELECT RAISE(ABORT,'immutable events'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_events_delete BEFORE DELETE ON events
                    BEGIN SELECT RAISE(ABORT,'immutable events'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_postings_update BEFORE UPDATE ON postings
                    BEGIN SELECT RAISE(ABORT,'immutable postings'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_postings_delete BEFORE DELETE ON postings
                    BEGIN SELECT RAISE(ABORT,'immutable postings'); END;
            """)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None, factory=ClosingConnection)
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        return db

    def read(self):
        with self.connect() as db:
            row = db.execute("SELECT body FROM state WHERE id=1").fetchone()
            return json.loads(row[0]) if row else None

    def apply(self, key, kind, at, **data):
        """Retry with the same natural key AND payload; changed payload is a conflict.

        IDs should be period keys or provider natural event keys, not random UUIDs.
        Never put credentials or full card details into data.
        """
        if not isinstance(key, str) or not key:
            raise Rejected("Natural event key required")
        stamp = utc(at)
        request = encode(dict(kind=kind, at=at, data=data))
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT request,result FROM events WHERE event_key=?", (key,)).fetchone()
            if prior:
                if prior[0] != request:
                    raise Conflict("Event key reused with different payload")
                db.rollback()
                return json.loads(prior[1])
            row = db.execute("SELECT body FROM state WHERE id=1").fetchone()
            s = json.loads(row[0]) if row else None
            if s:
                last = db.execute("SELECT result FROM events ORDER BY seq DESC LIMIT 1").fetchone()
                if not last or json.loads(last[0])["state"] != s:
                    raise Rejected("State/journal mismatch: reconciliation required")
            external = kind in ("advance", "fund_b", "user_flow", "settle_payout", "settle_order",
                                "card_purchase", "card_return", "refund")
            if s and external and data.get("evidence") in s["source_events"]:
                source_key = s["source_events"][data["evidence"]]
                source = db.execute("SELECT request,result FROM events WHERE event_key=?", (source_key,)).fetchone()
                if source[0] != request:
                    raise Conflict("Provider event already accounted with different data")
                db.rollback()
                return json.loads(source[1])
            before = copy.deepcopy(s)
            if s and stamp < utc(s["at"]):
                raise Rejected("Out-of-order event: reconcile historical boundary first")
            with localcontext() as ctx:
                ctx.prec = 80
                ctx.traps[Inexact] = True
                s, detail = self._transition(s, kind, at, data)
                if external:
                    s["source_events"][data["evidence"]] = key
                self._check(s)
                s["at"] = at
                result = dict(detail=detail, state=s)
                cursor = db.execute("INSERT INTO events(event_key,request,result) VALUES(?,?,?)",
                                    (key, request, encode(result)))
                for name in REGISTERS:
                    delta = Decimal(s[name]) - Decimal(before[name] if before else "0")
                    if delta:
                        db.execute("INSERT INTO postings VALUES(?,?,?)", (cursor.lastrowid, name, str(delta)))
                db.execute("INSERT INTO state VALUES(1,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                           (encode(s),))
            db.commit()
            return json.loads(encode(result))
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _add(s, name, value):
        s[name] = str(Decimal(s[name]) + value)

    @staticmethod
    def trading(s):
        return Decimal(s["equity"]) - Decimal(s["liability"])

    @staticmethod
    def _check(s):
        for name in REGISTERS:
            n = Decimal(s[name])
            if not n.is_finite() or (name not in ("equity", "hwm_reward", "hwm_risk", "b_usdt", "b_gas") and n < 0):
                raise Rejected("Invalid register: " + name)
        reserved = sum((Decimal(o["value"]) for o in s["payouts"].values()
                        if o["status"] not in ("SUCCESS", "FAILED")), Decimal(0))
        if reserved > Decimal(s["liability"]):
            raise Rejected("Payouts exceed liability")

    @staticmethod
    def reserves(s, at):
        t = utc(at)
        due = {k: v for k, v in s["calendar"].items()
               if not v["paid"] and utc(v["due"]) <= t + timedelta(days=30)}
        total = sum((Decimal(v["value"]) for v in due.values()), Decimal(0))
        with localcontext() as ctx:
            ctx.prec = 80
            ctx.traps[Inexact] = False
            ctx.rounding = ROUND_CEILING
            normalized = sum((Decimal(v["value"]) * 30 / Decimal(v["days"])
                              for v in s["subscriptions"].values()), Decimal(0))
            normalized = normalized.quantize(Decimal(".000000000000000001"))
        active = [o for o in s["orders"].values() if o["status"] not in ("SUCCESS", "FAILED")]
        mandatory = sum((Decimal(o["value"]) for o in active if o.get("bill") in due), Decimal(0))
        return dict(protected=max(Decimal(0), max(total, normalized) - mandatory),
                    usdt=sum((Decimal(o["value"]) + Decimal(o["fee_usdt"]) for o in active), Decimal(0)),
                    gas=sum((Decimal(o["fee_gas"]) for o in active), Decimal(0)))

    def _expense(self, s, expense_id, value, category, evidence):
        if category not in ("base", "extra") or expense_id in s["costs"] or not evidence:
            raise Rejected("Expense category, unique ID and evidence required")
        s["costs"][expense_id] = dict(value=str(value), uncovered=str(value if category == "base" else 0),
                                    refunded="0", category=category, evidence=evidence)
        self._add(s, "expenses", value)
        if category == "base":
            self._add(s, "uncovered", value)

    def _transition(self, s, kind, at, d):
        a = lambda field: amount(d[field])
        if kind == "initialize":
            if s is not None or not d["project"] or not d["account"] or not d["evidence"]:
                raise Rejected("Initialization requires a new account and fixed snapshot evidence")
            e = a("equity")
            s = {k: "0" for k in REGISTERS}
            s.update(equity=str(e), hwm_reward=str(e), hwm_risk=str(e), project=d["project"],
                     account=d["account"], at=at, advance_received=False, rewarded=False,
                     orders={}, payouts={}, costs={}, calendar={}, subscriptions={}, periods={}, asset_outflows_a={}, source_events={},
                     reserve_gas="0", last_period=d["previous_period"])
            self._period(d["previous_period"])
            return s, {}
        if s is None:
            raise Rejected("Initialize first")
        if kind == "advance":
            if s["advance_received"] or not d["evidence"]:
                raise Rejected("Advance already received or unconfirmed")
            s["advance_received"] = True
            self._add(s, "debt", Decimal("200.00"))
            self._add(s, "b_usdt", Decimal("200.00"))
        elif kind == "fund_b":
            if not d["evidence"]:
                raise Rejected("Evidence required")
            self._add(s, "b_usdt", a("usdt"))
            self._add(s, "b_gas", a("gas"))
        elif kind == "snapshot":
            if not d["evidence"] or d["valid"] is not True:
                raise Rejected("Reconciled fixed snapshot required")
            s["equity"] = str(a("equity"))
            s["hwm_risk"] = str(max(Decimal(s["hwm_risk"]), self.trading(s)))
        elif kind == "user_flow":
            if d["direction"] not in ("deposit", "withdrawal") or not d["evidence"]:
                raise Rejected("Classified flow required")
            v = a("value") * (1 if d["direction"] == "deposit" else -1)
            for name in ("equity", "hwm_reward", "hwm_risk"):
                self._add(s, name, v)
        elif kind == "close":
            p = d["period"]
            if p in s["periods"]:
                raise Conflict("Period already closed under another event key")
            start = self._period(p)
            previous = self._period(s["last_period"])
            if start != self._next(previous) or utc(at) != self._next(start):
                raise Rejected("Sequential month boundary required")
            if d["valid"] is not True or not d["evidence"] or a("equity") != Decimal(s["equity"]):
                raise Rejected("PENDING_DATA: historical reconciled snapshot required")
            e, h = self.trading(s), Decimal(s["hwm_reward"])
            k = max(Decimal(s["hwm_risk"]), e)
            gain = max(Decimal(0), e - h)
            covered = min(gain, Decimal(s["uncovered"]))
            profit = gain - covered + Decimal(s["credit"])
            raw = (Decimal(".15") * min(profit, Decimal(1500)) + Decimal(".20") *
                   min(max(profit - 1500, Decimal(0)), Decimal(3500)) + Decimal(".25") * max(profit - 5000, Decimal(0)))
            with localcontext() as rounding:
                rounding.traps[Inexact] = False
                reward = raw.quantize(Decimal(".01"), rounding=ROUND_HALF_EVEN)
            withholding = min(reward, Decimal(s["debt"]))
            net = reward - withholding
            remaining = covered
            # Stable expense-ID order; refunds identify the original cost.
            for cost_id in sorted(s["costs"]):
                cost = s["costs"][cost_id]
                used = min(remaining, Decimal(cost["uncovered"]))
                cost["uncovered"] = str(Decimal(cost["uncovered"]) - used)
                remaining -= used
            self._add(s, "uncovered", -covered)
            s["credit"] = "0"
            self._add(s, "debt", -withholding)
            self._add(s, "liability", net)
            s["hwm_reward"], s["hwm_risk"] = str(max(h, e) - net), str(k - net)
            detail = dict(gain=str(gain), covered=str(covered), profit=str(profit), reward=str(reward),
                          withholding=str(withholding), net=str(net), evidence=d["evidence"])
            s["periods"][p] = detail
            s["last_period"] = p
            return s, detail
        elif kind == "reserve_payout":
            v = a("value")
            reserved = sum((Decimal(o["value"]) for o in s["payouts"].values()
                            if o["status"] not in ("SUCCESS", "FAILED")), Decimal(0))
            if d["id"] in s["payouts"] or v <= 0 or v > Decimal(s["liability"]) - reserved:
                raise Rejected("Insufficient unreserved liability")
            s["payouts"][d["id"]] = dict(value=str(v), status="PREPARED", fee_cap=str(a("fee_cap")))
            if a("fee_cap") > v:
                raise Rejected("Fee cap exceeds payout")
        elif kind in ("unknown_payout", "unknown_order"):
            table = s["payouts"] if kind == "unknown_payout" else s["orders"]
            o = table[d["id"]]
            if o["status"] in ("SUCCESS", "FAILED"):
                raise Conflict("Terminal operation")
            o["status"] = "UNKNOWN"
        elif kind == "settle_payout":
            o = s["payouts"][d["id"]]
            if o["status"] in ("SUCCESS", "FAILED"):
                raise Conflict("Terminal payout already recorded")
            debit, receipt, fee = a("debit"), a("received"), a("fee")
            fee_lines = d.get("fee_lines", [])
            if not isinstance(fee_lines, list):
                raise Rejected("Fee lines must be a list")
            non_usdt = Decimal(0)
            for line in fee_lines:
                if not line["asset"] or line["asset"] == "USDT" or not line["source"]:
                    raise Rejected("Non-USDT fee requires asset and valuation source")
                quantity, price = amount(line["quantity"]), amount(line["price"])
                if quantity <= 0 or price <= 0:
                    raise Rejected("Positive fee quantity and price required")
                non_usdt += quantity * price
            if non_usdt > fee:
                raise Rejected("Asset fees exceed total equivalent")
            if d["status"] not in ("SUCCESS", "FAILED") or not d["evidence"]:
                raise Rejected("Proven final result required")
            if debit != receipt + fee or debit > Decimal(o["value"]) or fee > Decimal(o["fee_cap"]):
                raise Rejected("Disputed payout; retain reserve for reconciliation")
            if d["status"] == "FAILED" and receipt != 0:
                raise Rejected("Failed payout cannot have receipt")
            for line in fee_lines:
                asset = line["asset"]
                s["asset_outflows_a"][asset] = str(Decimal(s["asset_outflows_a"].get(asset, "0")) + amount(line["quantity"]))
            # Equity and liability use one equivalent; physical fee quantities remain separate.
            o.update(usdt_debit=str(receipt + fee - non_usdt), fee_lines=fee_lines)
            self._add(s, "equity", -debit)
            self._add(s, "liability", -debit)
            self._add(s, "b_usdt", receipt)
            if fee:
                self._expense(s, "payout:" + d["id"], fee, d["category"], d["evidence"])
            if receipt > 0:
                s["rewarded"] = True
            o.update(status=d["status"], evidence=d["evidence"])
        elif kind == "calendar":
            if d["id"] in s["calendar"] or a("value") <= 0:
                raise Rejected("Unique positive calendar occurrence required")
            utc(d["due"])
            s["calendar"][d["id"]] = dict(value=str(a("value")), due=d["due"], paid=False)
        elif kind == "subscription":
            if d["id"] in s["subscriptions"] or a("days") <= 0:
                raise Rejected("Unique subscription and positive cycle required")
            s["subscriptions"][d["id"]] = dict(value=str(a("value")), days=str(a("days")))
        elif kind == "gas_protection":
            s["reserve_gas"] = str(a("value"))
        elif kind == "reserve_order":
            if d["id"] in s["orders"] or d["category"] not in ("base", "extra") or d["purpose"] not in ("purchase", "card", "gas"):
                raise Rejected("Unique classified order required")
            value, fu, fg, price = a("value"), a("fee_usdt"), a("fee_gas"), a("gas_price")
            if value <= 0 or (fg > 0 and (price <= 0 or not d["valuation_source"])):
                raise Rejected("Positive amount and gas valuation required")
            bill = d.get("bill")
            if bill:
                b = s["calendar"][bill]
                if b["paid"] or value != Decimal(b["value"]) or any(
                        o.get("bill") == bill and o["status"] not in ("SUCCESS", "FAILED") for o in s["orders"].values()):
                    raise Rejected("Calendar occurrence paid, reserved, or amount mismatch")
                if d["purpose"] != "purchase":
                    raise Rejected("Mandatory payment must be a purchase")
            if d["purpose"] == "card" and any(Decimal(o.get("card_remaining", "0")) > 0 and
                    utc(at) > utc(o["card_due"]) for o in s["orders"].values() if "card_due" in o):
                raise Rejected("Overdue card advance")
            s["orders"][d["id"]] = dict(d, status="PREPARED", at=at)
            r = self.reserves(s, at)
            if r["gas"] > 0 and (price <= 0 or not d["valuation_source"]):
                raise Rejected("All outgoing gas reserves require a current fixed valuation")
            if r["usdt"] + r["protected"] > Decimal(s["b_usdt"]) or r["gas"] + Decimal(s["reserve_gas"]) > Decimal(s["b_gas"]):
                raise Rejected("Insufficient physical funds or protected reserves")
            if not s["rewarded"] and Decimal(s["expenses"]) + Decimal(s["card"]) + r["usdt"] + r["gas"] * price > 200:
                raise Rejected("Initial advance period budget exceeded")
        elif kind == "settle_order":
            o = s["orders"][d["id"]]
            if o["status"] in ("SUCCESS", "FAILED"):
                raise Conflict("Terminal order already recorded")
            if d["status"] not in ("SUCCESS", "FAILED") or not d["evidence"]:
                raise Rejected("Proven terminal evidence required")
            fu, fg, price = a("fee_usdt"), a("fee_gas"), a("gas_price")
            if fg > 0 and (price <= 0 or not d["valuation_source"]):
                raise Rejected("Gas valuation required")
            principal = Decimal(o["value"]) if d["status"] == "SUCCESS" else Decimal(0)
            self._add(s, "b_usdt", -principal - fu)
            self._add(s, "b_gas", -fg)
            cost = fu + fg * price
            if o["purpose"] == "card" and principal:
                self._add(s, "card", principal)
                o.update(card_remaining=str(principal), card_due=(utc(at) + timedelta(days=7)).isoformat())
            elif o["purpose"] == "gas" and principal:
                acquired = a("gas_received")
                if acquired <= 0:
                    raise Rejected("Confirmed acquired gas quantity required")
                self._add(s, "b_gas", acquired)
                o["gas_received"] = str(acquired)
            else:
                cost += principal
            if cost:
                self._expense(s, "order:" + d["id"], cost, o["category"], d["evidence"])
            o.update(status=d["status"], evidence=d["evidence"])
            if principal and o.get("bill"):
                s["calendar"][o["bill"]]["paid"] = True
            # Actual fees may exceed caps; record reality and expose a deficit.
            r = self.reserves(s, at)
            s["deficit"] = (r["protected"] + r["usdt"] > Decimal(s["b_usdt"]) or
                            r["gas"] + Decimal(s["reserve_gas"]) > Decimal(s["b_gas"]))
        elif kind in ("card_purchase", "card_return"):
            o, v = s["orders"][d["id"]], a("value")
            if not d["evidence"] or v <= 0 or v > Decimal(o["card_remaining"]):
                raise Rejected("Confirmed amount exceeds outstanding card advance")
            o["card_remaining"] = str(Decimal(o["card_remaining"]) - v)
            self._add(s, "card", -v)
            if kind == "card_purchase":
                self._expense(s, d["expense_id"], v, o["category"], d["evidence"])
            else:
                self._add(s, "b_usdt", v)
        elif kind == "refund":
            cost, v = s["costs"][d["expense_id"]], a("value")
            if not d["evidence"] or v <= 0 or v > Decimal(cost["value"]) - Decimal(cost["refunded"]):
                raise Rejected("Refund exceeds unrefunded expense")
            cost["refunded"] = str(Decimal(cost["refunded"]) + v)
            self._add(s, "expenses", -v)
            self._add(s, "b_usdt", v)
            if cost["category"] == "base":
                uncovered = min(v, Decimal(cost["uncovered"]))
                cost["uncovered"] = str(Decimal(cost["uncovered"]) - uncovered)
                self._add(s, "uncovered", -uncovered)
                self._add(s, "credit", v - uncovered)
        else:
            raise Rejected("Unsupported event kind")
        return s, {}

    def audit(self):
        """Verify projection against the durable event journal and register deltas."""
        with self.connect() as db, localcontext() as ctx:
            ctx.prec = 80
            db.execute("BEGIN")
            row = db.execute("SELECT body FROM state WHERE id=1").fetchone()
            last = db.execute("SELECT result FROM events ORDER BY seq DESC LIMIT 1").fetchone()
            if not row or not last or json.loads(row[0]) != json.loads(last[0])["state"]:
                raise Rejected("State/journal mismatch: reconciliation required")
            s = json.loads(row[0])
            totals = {k: Decimal(0) for k in REGISTERS}
            for name, value in db.execute("SELECT register,delta FROM postings ORDER BY event_seq"):
                totals[name] += Decimal(value)
            if any(totals[k] != Decimal(s[k]) for k in REGISTERS):
                raise Rejected("Posting/register mismatch: reconciliation required")
            self._check(s)
            return {"integrity": db.execute("PRAGMA integrity_check").fetchone()[0],
                    "events": db.execute("SELECT count(*) FROM events").fetchone()[0]}

    @staticmethod
    def _period(p):
        t = datetime.strptime(p, "%Y-%m").replace(tzinfo=timezone.utc)
        if t.strftime("%Y-%m") != p:
            raise Rejected("YYYY-MM period required")
        return t

    @staticmethod
    def _next(t):
        return t.replace(year=t.year + (t.month == 12), month=t.month % 12 + 1)
