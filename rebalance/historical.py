"""Offline candle replay: previous close valuation, current open execution.

Trade candles proxy mark prices. No intra-bar extrema, liquidation or depth model.
"""
import argparse
import csv
from decimal import Decimal as D
import hashlib
import json
from pathlib import Path

from .kernel import Action, Config, Portfolio, decimal


def candles(path):
    rows = []
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream):
            stamp = int(row['open_time'])
            values = {k: decimal(row[k]) for k in ('open', 'high', 'low', 'close')}
            if (min(values.values()) <= 0 or values['low'] > min(values['open'], values['close'])
                    or values['high'] < max(values['open'], values['close'])):
                raise ValueError('invalid OHLC')
            if rows and stamp-rows[-1]['time'] != 60000:
                raise ValueError('requires ordered contiguous 1-minute candles')
            rows.append(dict(time=stamp, **values))
    if len(rows) < 2:
        raise ValueError('requires at least two candles')
    return rows


def funding_rows(path):
    if path is None:
        return []
    result = []
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream):
            event = dict(time=int(row['timestamp']), rate=decimal(row['rate']),
                         mark=decimal(row['mark_price']))
            if event['mark'] <= 0 or (result and event['time'] <= result[-1]['time']):
                raise ValueError('invalid or duplicate funding event')
            result.append(event)
    return result


def replay(rows, config, capital, spread_bps, slippage_bps, funding, *, store=None, state_sink=None):
    spread_bps, slippage_bps = map(decimal, (spread_bps, slippage_bps))
    if min(spread_bps, slippage_bps) < 0 or spread_bps/2+slippage_bps >= 10000:
        raise ValueError('invalid execution costs')
    if any(e['time'] < rows[0]['time'] or e['time'] > rows[-1]['time']+59999 for e in funding):
        raise ValueError('funding event outside candle interval')
    p = Portfolio(config, capital)
    if store is not None:
        store.initialize(p)
    trades, curve = [], []
    funding_index = 0
    rejected = 0
    peak = p.wallet
    max_dd = D(0)

    def prices(mid):
        friction = (spread_bps/2+slippage_bps)/10000
        return mid*(1-friction), mid*(1+friction)

    def apply_funding(until):
        nonlocal funding_index, p
        while funding_index < len(funding) and funding[funding_index]['time'] <= until:
            e = funding[funding_index]
            amount = (p.legs['SHORT'].units-p.legs['LONG'].units)*config.step*e['mark']*e['rate']
            identity = 'funding-'+str(e['time'])
            if store is None:
                p.cash_event(identity, amount, 'FUNDING')
            else:
                store.cash_event(identity, amount, 'FUNDING')
                p, _ = store.load()
            funding_index += 1

    for i, bar in enumerate(rows):
        apply_funding(bar['time'])
        bid, ask = prices(bar['open'])
        if i == 0:
            actions = p.plan_entry(bar['open'], bid, ask)
            if actions[0].kind == 'WAIT':
                raise ValueError('entry blocked: '+actions[0].reason)
        else:
            # Signal observes only the fully completed previous bar. Execution
            # quotes are current open with explicit adverse cost assumptions.
            if store is None:
                action = p.next_action(rows[i-1]['close'], bid, ask)
            else:
                action = Action(**store.observe(f'observe-{i}', rows[i-1]['close'], bid, ask))
                p, _ = store.load()
            actions = [] if action.kind == 'WAIT' else [action]
        for action in actions:
            buy = (action.side == 'LONG') == (action.kind == 'ADD')
            price = ask if buy else bid
            fee = action.units*config.step*price*config.fee_rate
            try:
                identity = f'bar-{i}-{action.side}'
                if store is None:
                    p.fill(identity, action, price, fee, bar['open'])
                else:
                    store.prepare('prepare-'+identity, identity, action, price, fee,
                                  bar['open'], store.load()[1], entry=i == 0)
                    claim = store.transition('dispatch-'+identity, identity, 'DISPATCHING')
                    if not claim['dispatch_granted']:
                        raise ValueError('unexpected repeated dispatch claim')
                    result = store.fill(identity, identity, action.units, price, fee, bar['open'])
                    if result['status'] != 'FILLED':
                        raise ValueError('durable fill requires reconciliation')
                    p, _ = store.load()
            except ValueError as exc:
                if str(exc) not in ('insufficient shared funded balance', 'insufficient shared reserve'):
                    raise
                rejected += 1
                continue
            if i == 0 and store is None:
                p.legs[action.side].initial_units = action.units
            trades.append(dict(time=bar['time'], kind=action.kind, side=action.side,
                               units=action.units, price=str(price), fee=str(fee)))
        apply_funding(bar['time']+59999)
        snapshot = p.snapshot(bar['close'])
        equity = snapshot['equity']
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak-equity)/peak*100)
        curve.append(dict(time=bar['time']+59999, equity=str(equity)))
        if equity <= snapshot['margin']:
            raise ValueError('simulation collateral exhausted; liquidation model required')
    result = dict(candles=len(rows), start=rows[0]['time'], end=rows[-1]['time']+59999,
        initial_equity=str(capital), equity=str(equity), net_mtm=str(equity-decimal(capital)),
        realized=str(p.realized), unrealized=str(sum((r['upl'] for r in snapshot['legs'].values()), D(0))),
        fees=str(p.fees), funding=str(p.funding), funding_events=funding_index,
        rebalances=len(trades)-2, rejected_funding_checks=rejected,
        max_close_drawdown_pct=str(max_dd), terminal='OPEN_MTM',
        quantities={s:str(l.units*config.step) for s,l in p.legs.items()})
    if state_sink is not None:
        from copy import deepcopy
        state_sink(deepcopy(p))
    return result, trades, curve


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candles', required=True, type=Path)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--state-db', type=Path, help='optional NEW SQLite database for durable replay')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--funding', type=Path)
    group.add_argument('--funding-zero', action='store_true', help='explicitly assume zero funding')
    args = parser.parse_args()
    settings = json.loads(args.config.read_text(encoding='utf-8-sig'))
    config = Config(**settings['strategy'])
    if args.state_db:
        from .durable import Store
        store = Store(args.state_db)
    else:
        store = None
    result, trades, curve = replay(candles(args.candles), config, settings['capital'],
        settings['spread_bps'], settings['slippage_bps'], funding_rows(args.funding), store=store)
    inputs = [args.candles, args.config] + ([args.funding] if args.funding else [])
    code = [Path(__file__), Path(__file__).with_name('kernel.py')]
    if store is not None:
        code.append(Path(__file__).with_name('durable.py'))
    manifest = dict(inputs={str(p.resolve()):sha(p) for p in inputs},
        code={p.name:sha(p) for p in code}, durable_state=bool(store),
        settings=settings, funding_mode='CSV_RATES' if args.funding else 'ASSUMED_ZERO',
        limitations=['trade candles proxy mark', 'synthetic spread and slippage',
            'full fills', 'fixed leverage collateral', 'no maintenance brackets or liquidation',
            'one decision per minute', 'terminal open MTM; closing costs excluded'])
    args.output.mkdir(parents=True, exist_ok=False)
    for name, data in (('result', result), ('manifest', manifest), ('trades', trades), ('equity', curve)):
        (args.output/(name+'.json')).write_text(json.dumps(data, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
