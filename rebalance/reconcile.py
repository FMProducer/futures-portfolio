"""Bounded read-only history and atomic, fail-closed offline reconciliation.

Live collection NEVER writes a portfolio DB. apply_offline is for an explicitly
bound local fixture/state, not permission to import arbitrary account history.
"""
import argparse
from collections import defaultdict
from decimal import Decimal as D
import json
from pathlib import Path

from .binance_readonly import Reader, ReadError
from .durable import encode
from .kernel import Action, decimal

DAY = 86400000


def history(reader, path, start, end, *, symbol=None, limit=1000, budget=200):
    """Inclusive disjoint intervals. Never skip equal-timestamp records."""
    if type(start) is not int or type(end) is not int or start < 0 or not start <= end < start+7*DAY:
        raise ValueError('history requires a nonnegative interval shorter than seven days')
    calls = 0
    found = {}

    def fetch(left, right):
        nonlocal calls
        calls += 1
        if calls > budget:
            raise ReadError('history_request_budget_exhausted')
        params = dict(startTime=left, endTime=right, limit=limit)
        if symbol is not None:
            params['symbol'] = symbol
        rows = reader.get(path, **params)
        if not isinstance(rows, list) or len(rows) > limit:
            raise ReadError('invalid_history_response')
        for r in rows:
            if not left <= int(r['time']) <= right:
                raise ReadError('history_outside_requested_window')
        if len(rows) == limit:
            if left == right:
                raise ReadError('history_saturated_single_millisecond')
            middle = (left+right)//2
            fetch(left, middle)
            fetch(middle+1, right)
            return
        for r in rows:
            identity = (str(r['symbol']), str(r['id'])) if symbol is not None else (r['incomeType'], str(r['tranId']))
            if identity in found and found[identity] != r:
                raise ReadError('conflicting_history_identity')
            found[identity] = r

    fetch(start, end)
    return sorted(found.values(), key=lambda r:(int(r['time']), encode(r)))


def analyze(trades, incomes):
    """Cross-check trading income, emit each cash fact from ONE source only."""
    errors, cash, normalized = [], [], []
    trading_income = defaultdict(lambda:D(0))
    trade_keys, income_keys = set(), set()
    for row in incomes:
        identity = (row['incomeType'], str(row['tranId']))
        if identity in income_keys:
            raise ValueError('duplicate income identity')
        income_keys.add(identity)
        amount = decimal(row['income'])
        if row['asset'] != 'USDT':
            errors.append('NON_USDT_INCOME')
            continue
        kind = row['incomeType']
        if kind in ('COMMISSION', 'REALIZED_PNL'):
            trading_income[(row.get('symbol',''), str(row.get('tradeId','')), kind)] += amount
        elif kind in ('FUNDING_FEE', 'TRANSFER'):
            cash.append(dict(id='binance-income:'+kind+':'+str(row['tranId']),
                amount=str(amount), kind='FUNDING' if kind == 'FUNDING_FEE' else 'EXTERNAL', time=int(row['time'])))
        else:
            errors.append('UNSUPPORTED_INCOME_TYPE:'+kind)
    for row in trades:
        identity = (row['symbol'], str(row['id']))
        if identity in trade_keys:
            raise ValueError('duplicate trade identity')
        trade_keys.add(identity)
        qty, price, fee, realized = map(decimal, (row['qty'], row['price'], row['commission'], row['realizedPnl']))
        if min(qty, price) <= 0 or fee < 0 or row['side'] not in ('BUY','SELL') or row['positionSide'] not in ('LONG','SHORT'):
            raise ValueError('invalid trade economics')
        if row['commissionAsset'] != 'USDT':
            errors.append('NON_USDT_COMMISSION')
        for kind, expected in (('COMMISSION', -fee), ('REALIZED_PNL', realized)):
            actual = trading_income.pop((row['symbol'], str(row['id']), kind), D(0))
            if actual != expected:
                errors.append('TRADING_INCOME_MISMATCH:'+kind)
        normalized.append(dict(id='binance:'+row['symbol']+':'+str(row['id']),
            symbol=row['symbol'], order_id=str(row['orderId']), side=row['side'],
            position_side=row['positionSide'], qty=str(qty), price=str(price), fee=str(fee),
            realized=str(realized), time=int(row['time'])))
    if trading_income:
        errors.append('UNMATCHED_TRADING_INCOME')
    return dict(blocking_reasons=sorted(set(errors)), trades=normalized, cash=cash,
                cash_source_rule='trades for PnL/fees; income for funding/transfers', trading_authorized=False)


def apply_offline(store, event_id, bundle, evidence, *, symbol, expected_revision):
    """All fills, terminal orders and cash commit together or not at all.

    evidence is client_id -> authoritative order query AFTER history coverage.
    The caller owns account/symbol binding; CLI deliberately cannot call this.
    """
    if bundle['blocking_reasons']:
        raise ValueError('blocked history bundle')
    request = dict(op='reconcile-offline', bundle=bundle, evidence=evidence,
                   symbol=symbol, revision=expected_revision)

    def change(db, p, revision):
        if revision != expected_revision:
            raise ValueError('stale reconciliation revision')
        orders = {}
        by_exchange = {}
        for client, response in evidence.items():
            order = store._order(db, client)
            action = Action(**order['action'])
            side = 'BUY' if (action.side == 'LONG') == (action.kind == 'ADD') else 'SELL'
            if (response['clientOrderId'] != client or response['symbol'] != symbol
                    or response['positionSide'] != action.side or response['side'] != side
                    or decimal(response['origQty']) != action.units*p.config.step
                    or order['status'] == 'PREPARED' or order.get('blocked_trade')):
                raise ValueError('order identity, quantity or local state mismatch')
            exchange_id = str(response['orderId'])
            if exchange_id in by_exchange:
                raise ValueError('duplicate exchange order identity')
            by_exchange[exchange_id] = client
            orders[client] = order
            covered = sum((decimal(t['qty']) for t in bundle['trades']
                           if t['symbol'] == symbol and t['order_id'] == exchange_id), D(0))
            if covered != decimal(response['executedQty']):
                raise ValueError('incomplete order fill coverage')
            status = response['status']
            if (status == 'FILLED' and covered != action.units*p.config.step
                    or status == 'REJECTED' and covered != 0
                    or status not in ('NEW','PARTIALLY_FILLED','FILLED','CANCELED','REJECTED','EXPIRED','EXPIRED_IN_MATCH')):
                raise ValueError('unsupported or inconsistent order status')
            terminal = 'CANCELED' if status.startswith('EXPIRED') else status
            if order['status'] in ('FILLED','CANCELED','REJECTED') and order['status'] != terminal:
                raise ValueError('order evidence regresses terminal state')
        # Account transfers must not be applied around unrelated unknown orders.
        all_local = [json.loads(r[0]) for r in db.execute('SELECT body FROM orders')]
        if any(o['status'] not in ('FILLED','CANCELED','REJECTED') and o['client_id'] not in orders for o in all_local):
            raise ValueError('uncovered outstanding intent')
        events = [(t['time'], 'trade', t) for t in bundle['trades']]
        events += [(c['time'], 'cash', c) for c in bundle['cash']]
        # Equal-ms cash/trade ordering cannot be inferred from REST records.
        if set(t['time'] for t in bundle['trades']) & set(c['time'] for c in bundle['cash']):
            raise ValueError('ambiguous same-millisecond cash and trade ordering')
        for _, kind, item in sorted(events, key=lambda e:(e[0],e[1],e[2]['id'])):
            if kind == 'cash':
                p.cash_event(item['id'], item['amount'], item['kind'])
                continue
            if item['symbol'] != symbol or item['order_id'] not in by_exchange:
                raise ValueError('unbound account trade')
            client = by_exchange[item['order_id']]
            order, response = orders[client], evidence[client]
            action = Action(**order['action'])
            if item['side'] != response['side'] or item['position_side'] != action.side:
                raise ValueError('trade direction mismatch')
            units = decimal(item['qty'])/p.config.step
            if units != units.to_integral_value():
                raise ValueError('off-step trade quantity')
            units = int(units)
            receipt_id = 'trade:'+item['id']
            if receipt_id in p.receipts:
                expected = (action.kind, action.side, units, item['price'], item['fee'])
                if p.receipts[receipt_id] != expected:
                    raise ValueError('conflicting posted trade')
                continue
            if order['status'] in ('FILLED','CANCELED','REJECTED'):
                raise ValueError('new fill contradicts terminal local state')
            remaining = action.units-order['filled']-units
            if remaining < 0:
                raise ValueError('overfill')
            reserved = decimal(order['initial_reserved'])*remaining/action.units
            p.order_reserve -= decimal(order['reserved'])-reserved
            gross = p.realized
            p.fill(receipt_id, Action(action.kind, action.side, units), item['price'], item['fee'], item['price'])
            correction = decimal(item['realized'])-(p.realized-gross)
            if (action.kind == 'ADD' and decimal(item['realized']) != 0
                    or abs(correction) > D('.00000001')):
                raise ValueError('realized PnL does not match local basis')
            # Exchange-rounded realized PnL is authoritative within the explicit
            # USDT reconciliation tolerance; do not accumulate model cash drift.
            p.realized += correction
            p.wallet += correction
            if order['entry']:
                p.legs[action.side].initial_units += units
            order['filled'] += units
            order['reserved'] = str(reserved)
        for client, order in orders.items():
            response = evidence[client]
            if order['filled']*p.config.step != decimal(response['executedQty']):
                raise ValueError('posted quantity mismatch')
            status = response['status']
            if status in ('FILLED','CANCELED','REJECTED','EXPIRED','EXPIRED_IN_MATCH'):
                p.order_reserve -= decimal(order['reserved'])
                order['reserved'] = '0'
                order['status'] = 'CANCELED' if status.startswith('EXPIRED') else status
            elif order['status'] not in ('FILLED','CANCELED','REJECTED'):
                order['status'] = 'PARTIAL' if order['filled'] else 'OPEN'
            else:
                raise ValueError('order evidence regresses terminal state')
            store._write_order(db, order)
        return dict(status='OFFLINE_RECONCILED', wallet=str(p.wallet), reserve=str(p.order_reserve),
                    orders={client:o['status'] for client,o in orders.items()}, trading_authorized=False)
    return store._apply(event_id, request, change)


def main():
    parser = argparse.ArgumentParser(description='Read-only Binance history; no DB writes')
    parser.add_argument('--env-file', required=True, type=Path)
    parser.add_argument('--symbol', default='BTCUSDT')
    parser.add_argument('--hours', type=int, default=24)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if not 1 <= args.hours <= 144:
        parser.error('hours must be 1..144')
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output/'.gitignore').write_text('*\n!.gitignore\n',encoding='utf-8')
    try:
        reader = Reader.from_env(args.env_file)
        end = reader.synchronize()['server_time']-10000
        start = end-args.hours*3600000
        trades = history(reader, '/fapi/v1/userTrades', start, end, symbol=args.symbol)
        incomes = history(reader, '/fapi/v1/income', start, end)
        result = analyze(trades, incomes)
        payload = dict(start=start, end=end, symbol=args.symbol, raw_trades=trades, raw_income=incomes, result=result,
                       coverage='BOUNDED_REST_WINDOW_NOT_ACCOUNT_CHECKPOINT')
        (args.output/'history.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
        print(json.dumps(dict(status='READ_ONLY_HISTORY_COLLECTED', trades=len(trades), incomes=len(incomes),
                             blocking_reasons=result['blocking_reasons'], trading_authorized=False)))
    except ReadError as exc:
        (args.output/'error.json').write_text(json.dumps(exc.details,indent=2),encoding='utf-8')
        print(json.dumps(dict(status='BLOCKED',**exc.details)))
        raise SystemExit(2) from None


if __name__ == '__main__':
    main()
