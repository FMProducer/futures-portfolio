"""Offline SQLite state/outbox. This module NEVER sends an order.

One database is one portfolio/instrument. One outstanding intent serializes
execution; DISPATCHING/UNKNOWN are never eligible for a second dispatch.
"""
from contextlib import contextmanager
from dataclasses import asdict
import json
import sqlite3

from .kernel import Action, Config, Leg, Portfolio, SIDES, decimal, ZERO

TERMINAL = ('FILLED', 'CANCELED', 'REJECTED')
VERSION = 'shared-wallet-state-v1'


def encode(value):
    return json.dumps(value, default=str, sort_keys=True, separators=(',', ':'))


def pack(p):
    return encode(dict(version=VERSION, portfolio=asdict(p)))


def unpack(body):
    data = json.loads(body)
    if data['version'] != VERSION:
        raise ValueError('state migration required')
    row = data['portfolio']
    row['config'] = Config(**row['config'])
    for side in SIDES:
        leg = row['legs'][side]
        for key in ('avg', 'margin', 'open_fees'):
            leg[key] = decimal(leg[key])
        if any(type(leg[k]) is not int or leg[k] < 0 for k in ('units', 'initial_units')):
            raise ValueError('invalid stored quantity')
        row['legs'][side] = Leg(**leg)
    for key in ('wallet', 'liability', 'order_reserve', 'fees', 'realized', 'funding'):
        row[key] = decimal(row[key])
    row['extrema'] = {s: {k: None if v is None else decimal(v) for k,v in r.items()}
                      for s,r in row['extrema'].items()}
    row['receipts'] = {key: tuple(value) for key,value in row['receipts'].items()}
    return Portfolio(**row)


class Store:
    def __init__(self, path):
        self.path = str(path)
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS portfolio (
                    id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL,
                    revision INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS orders (
                    client_id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY, event_id TEXT UNIQUE NOT NULL,
                    request TEXT NOT NULL, result TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
                    BEGIN SELECT RAISE(ABORT,'immutable events'); END;
                CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
                    BEGIN SELECT RAISE(ABORT,'immutable events'); END;
            ''')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.execute('PRAGMA synchronous=FULL')
        try:
            yield db
        finally:
            db.close()

    def initialize(self, portfolio):
        body = pack(portfolio)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                old = db.execute('SELECT body FROM portfolio').fetchone()
                if old:
                    raise ValueError('portfolio already initialized; load existing state')
                if portfolio.order_reserve != 0:
                    raise ValueError('outbox owns order_reserve')
                db.execute('INSERT INTO portfolio VALUES(1,?,0)', (body,))
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def load(self):
        with self.connection() as db:
            row = db.execute('SELECT body,revision FROM portfolio WHERE id=1').fetchone()
            if not row:
                raise ValueError('portfolio not initialized')
            return unpack(row[0]), row[1]

    def orders(self):
        with self.connection() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT body FROM orders ORDER BY client_id')]

    @staticmethod
    def _order(db, client_id):
        row = db.execute('SELECT body FROM orders WHERE client_id=?', (client_id,)).fetchone()
        if not row:
            raise ValueError('unknown intent')
        return json.loads(row[0])

    @staticmethod
    def _write_order(db, order):
        db.execute('INSERT INTO orders VALUES(?,?) ON CONFLICT(client_id) '
                   'DO UPDATE SET body=excluded.body', (order['client_id'], encode(order)))

    def _apply(self, event_id, request, change):
        if not isinstance(event_id, str) or not event_id:
            raise ValueError('missing event identity')
        request = encode(request)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                old = db.execute('SELECT request,result FROM events WHERE event_id=?', (event_id,)).fetchone()
                if old:
                    if old[0] != request:
                        raise ValueError('conflicting event replay')
                    db.commit()
                    result = json.loads(old[1])
                    if result.get('dispatch_granted'):
                        result['dispatch_granted'] = False
                    return result
                row = db.execute('SELECT body,revision FROM portfolio WHERE id=1').fetchone()
                if not row:
                    raise ValueError('portfolio not initialized')
                p = unpack(row[0])
                result = change(db, p, row[1])
                db.execute('UPDATE portfolio SET body=?,revision=revision+1 WHERE id=1', (pack(p),))
                db.execute('INSERT INTO events(event_id,request,result) VALUES(?,?,?)',
                           (event_id, request, encode(result)))
                db.commit()
                return json.loads(encode(result))
            except BaseException:
                db.rollback()
                raise

    def prepare(self, event_id, client_id, action, price_bound, fee_cap, mark,
                expected_revision, *, entry=False):
        """Durable intent + reserve. Caller must use the revision of its plan.

        price_bound/fee_cap are offline reservation assumptions, not exchange
        price protection. No second outstanding intent, including UNKNOWN.
        """
        price, fee, mark = map(decimal, (price_bound, fee_cap, mark))
        if (not isinstance(client_id, str) or not client_id or len(client_id) > 36
                or action.kind not in ('ADD', 'REDUCE') or action.side not in SIDES
                or type(action.units) is not int or action.units <= 0
                or min(price, mark) <= 0 or fee < 0 or type(entry) is not bool
                or type(expected_revision) is not int):
            raise ValueError('invalid intent')
        request = dict(op='prepare', client_id=client_id, action=asdict(action),
                       price=str(price), fee=str(fee), mark=str(mark),
                       revision=expected_revision, entry=entry)

        def change(db, p, revision):
            if revision != expected_revision:
                raise ValueError('stale portfolio revision')
            if db.execute('SELECT 1 FROM orders WHERE client_id=?', (client_id,)).fetchone():
                raise ValueError('client id already used')
            if any(json.loads(r[0])['status'] not in TERMINAL for r in db.execute('SELECT body FROM orders')):
                raise ValueError('outstanding intent requires reconciliation')
            leg = p.legs[action.side]
            if entry and (action.kind != 'ADD' or leg.units or leg.initial_units):
                raise ValueError('entry requires uninitialized leg')
            if action.kind == 'REDUCE' and action.units > leg.units:
                raise ValueError('over-close intent')
            notional = action.units*p.config.step*price
            if notional < p.config.min_notional:
                raise ValueError('intent below minimum notional')
            reserved = fee + (notional/p.config.leverage if action.kind == 'ADD' else ZERO)
            if reserved > p.snapshot(mark)['available']:
                raise ValueError('insufficient shared reserve')
            p.order_reserve += reserved
            order = dict(client_id=client_id, action=asdict(action), status='PREPARED',
                         filled=0, reserved=str(reserved), initial_reserved=str(reserved), entry=entry)
            self._write_order(db, order)
            return order
        return self._apply(event_id, request, change)

    def transition(self, event_id, client_id, status):
        """Claim dispatch ONCE; timeout/restart => UNKNOWN, never PREPARED."""
        allowed = {
            'DISPATCHING': ('PREPARED',),
            'OPEN': ('DISPATCHING', 'UNKNOWN'),
            'UNKNOWN': ('DISPATCHING', 'OPEN', 'PARTIAL', 'CANCEL_PENDING', 'UNKNOWN'),
            'CANCEL_PENDING': ('DISPATCHING', 'OPEN', 'PARTIAL', 'UNKNOWN'),
        }
        if status not in allowed:
            raise ValueError('unsupported transition')

        def change(db, p, revision):
            order = self._order(db, client_id)
            if order['status'] not in allowed[status]:
                raise ValueError('invalid transition; no blind redispatch')
            order['status'] = status
            self._write_order(db, order)
            return dict(order, dispatch_granted=True) if status == 'DISPATCHING' else order
        return self._apply(event_id, dict(op='transition', client_id=client_id, status=status), change)

    def fill(self, trade_id, client_id, units, price, fee, mark):
        """Unique confirmed trade, not cumulative order quantity.

        Failed affordability preserves the raw event and UNKNOWN for later
        reconciliation. It never fabricates cash or silently retries an order.
        """
        if not isinstance(trade_id, str) or not trade_id:
            raise ValueError('missing trade identity')
        price, fee, mark = map(decimal, (price, fee, mark))
        if type(units) is not int or units <= 0 or min(price, mark) <= 0 or fee < 0:
            raise ValueError('invalid trade')
        request = dict(op='fill', client_id=client_id, units=units,
                       price=str(price), fee=str(fee), mark=str(mark))

        def change(db, p, revision):
            order = self._order(db, client_id)
            if order.get('blocked_trade'):
                raise ValueError('blocked trade requires reconciliation')
            if order['status'] in TERMINAL or order['status'] == 'PREPARED':
                raise ValueError('fill inconsistent with order state')
            action = Action(**order['action'])
            if order['filled']+units > action.units:
                raise ValueError('overfill requires reconciliation')
            remaining = action.units-order['filled']-units
            new_reserve = decimal(order['initial_reserved'])*remaining/action.units
            old_reserve = p.order_reserve
            p.order_reserve = new_reserve
            try:
                p.fill('trade:'+trade_id, Action(action.kind, action.side, units), price, fee, mark)
            except ValueError as exc:
                if str(exc) != 'insufficient shared funded balance':
                    raise
                p.order_reserve = old_reserve
                order['status'] = 'UNKNOWN'
                order['blocked_trade'] = trade_id
                self._write_order(db, order)
                return dict(status='RECONCILIATION_REQUIRED', order=order)
            order['filled'] += units
            order['reserved'] = str(new_reserve)
            if order['entry']:
                p.legs[action.side].initial_units += units
            if not remaining:
                order['status'] = 'FILLED'
            elif order['status'] not in ('UNKNOWN', 'DISPATCHING', 'CANCEL_PENDING'):
                order['status'] = 'PARTIAL'
            self._write_order(db, order)
            return order
        return self._apply('trade:'+trade_id, request, change)

    def finalize(self, event_id, client_id, status, confirmed_filled):
        """Authoritative terminal result ONLY after all known fills are posted."""
        if status not in ('CANCELED', 'REJECTED') or type(confirmed_filled) is not int or confirmed_filled < 0:
            raise ValueError('invalid terminal evidence')

        def change(db, p, revision):
            order = self._order(db, client_id)
            if (order['status'] in TERMINAL or order.get('blocked_trade')
                    or confirmed_filled != order['filled'] or order['status'] == 'PREPARED'):
                raise ValueError('terminal evidence requires reconciliation')
            if status == 'REJECTED' and confirmed_filled:
                raise ValueError('partially executed order cannot be rejected')
            p.order_reserve -= decimal(order['reserved'])
            order['reserved'], order['status'] = '0', status
            self._write_order(db, order)
            return order
        return self._apply(event_id, dict(op='finalize', client_id=client_id,
                                         status=status, confirmed_filled=confirmed_filled), change)

    def observe(self, event_id, mark, bid, ask):
        """Persist extrema even on WAIT; an outstanding intent freezes planning."""
        mark, bid, ask = map(decimal, (mark, bid, ask))

        def change(db, p, revision):
            if any(json.loads(r[0])['status'] not in TERMINAL for r in db.execute('SELECT body FROM orders')):
                return asdict(Action('WAIT', reason='outstanding_intent'))
            return asdict(p.next_action(mark, bid, ask))
        return self._apply(event_id, dict(op='observe', mark=str(mark), bid=str(bid), ask=str(ask)), change)

    def cash_event(self, event_id, amount, kind):
        amount = decimal(amount)

        def change(db, p, revision):
            p.cash_event(event_id, amount, kind)
            return dict(wallet=str(p.wallet), funding=str(p.funding))
        return self._apply(event_id, dict(op='cash', amount=str(amount), kind=kind), change)
