"""Restartable CSV paper runner. No credentials, network, or real account state.

One completed observation, simulated fills, funding, extrema and cursor commit
together. This is atomic PAPER execution, not atomic exchange execution.
"""
import argparse
from copy import deepcopy
from decimal import Decimal as D
import hashlib
import json
from pathlib import Path
import sqlite3

from .durable import Store, encode, pack, unpack
from .historical import candles, funding_rows
from .kernel import Config, Portfolio, decimal


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


class Runner:
    def __init__(self,path,rows,settings,funding=(),*,funding_mode='ASSUMED_ZERO'):
        self.path = Path(path)
        rows = deepcopy(rows)
        self.rows = rows
        self.settings = json.loads(encode(settings))
        self.config = Config(**self.settings['strategy'])
        self.capital = decimal(settings['capital'])
        self.friction = (decimal(settings['spread_bps'])/2+decimal(settings['slippage_bps']))/10000
        if (self.capital <= 0 or not 0 <= self.friction < 1
                or min(decimal(settings['spread_bps']),decimal(settings['slippage_bps'])) < 0):
            raise ValueError('invalid paper capital or costs')
        if funding_mode not in ('ASSUMED_ZERO','CSV_RATES') or (funding_mode == 'ASSUMED_ZERO' and funding):
            raise ValueError('invalid funding source mode')
        if len(rows) < 2:
            raise ValueError('paper requires at least two observations')
        for i,bar in enumerate(rows):
            values = [decimal(bar[k]) for k in ('open','high','low','close')]
            if (type(bar['time']) is not int or bar['time'] < 0 or min(values) <= 0
                    or values[2] > min(values[0],values[3]) or values[1] < max(values[0],values[3])
                    or i and bar['time']-rows[i-1]['time'] != 60000):
                raise ValueError('invalid paper candle sequence')
        self.funding = [dict(time=e['time'],rate=decimal(e['rate']),mark=decimal(e['mark'])) for e in funding]
        for i,e in enumerate(self.funding):
            if (type(e['time']) is not int or not rows[0]['time'] <= e['time'] <= rows[-1]['time']+59999
                    or e['mark'] <= 0 or i and e['time'] <= self.funding[i-1]['time']):
                raise ValueError('invalid paper funding sequence')
        code = {name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                for name in ('paper.py','kernel.py','durable.py','historical.py')}
        self.binding = dict(mode='PAPER_CSV_V1',candles_sha256=digest(rows),settings=self.settings,
                            funding_sha256=digest(self.funding),funding_mode=funding_mode,code_sha256=code)
        self.binding_id = digest(self.binding)
        # Reject observer/live/accounting databases BEFORE Store can add any schema.
        if self.path.exists():
            with sqlite3.connect(self.path) as db:
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if tables and 'paper_binding' not in tables:
                    raise ValueError('not a paper database')
        self._keeper = sqlite3.connect(self.path,isolation_level=None)
        try:
            # A dedicated paper DB; hold the last connection open to avoid a
            # checkpoint on every short-lived Store connection. FULL remains set.
            self._keeper.execute('PRAGMA journal_mode=WAL')
            self.store = Store(self.path)
            self._initialize()
        except BaseException:
            self._keeper.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self,*args):
        self.close()

    def close(self):
        self._keeper.close()

    def _initialize(self):
        with self.store.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS paper_binding(id INTEGER PRIMARY KEY CHECK(id=1),body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS paper_cursor(id INTEGER PRIMARY KEY CHECK(id=1),body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS paper_bars(idx INTEGER PRIMARY KEY,body TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS paper_binding_immutable BEFORE UPDATE ON paper_binding
                    BEGIN SELECT RAISE(ABORT,'immutable paper binding'); END;
                CREATE TRIGGER IF NOT EXISTS paper_binding_no_delete BEFORE DELETE ON paper_binding
                    BEGIN SELECT RAISE(ABORT,'immutable paper binding'); END;
                CREATE TRIGGER IF NOT EXISTS paper_bars_immutable BEFORE UPDATE ON paper_bars
                    BEGIN SELECT RAISE(ABORT,'immutable paper observations'); END;
                CREATE TRIGGER IF NOT EXISTS paper_bars_no_delete BEFORE DELETE ON paper_bars
                    BEGIN SELECT RAISE(ABORT,'immutable paper observations'); END;
            ''')
            db.execute('BEGIN IMMEDIATE')
            try:
                old = db.execute('SELECT body FROM paper_binding WHERE id=1').fetchone()
                if old:
                    if old[0] != encode(self.binding):
                        raise ValueError('paper inputs/config/code changed; migration required')
                else:
                    if db.execute('SELECT 1 FROM portfolio').fetchone():
                        raise ValueError('paper state requires reconciliation')
                    p = Portfolio(self.config,self.capital)
                    progress = dict(next_index=0,previous_close=None,funding_index=0,trade_count=0,
                                    rejected=0,peak=str(self.capital),max_dd='0')
                    db.execute('INSERT INTO portfolio VALUES(1,?,0)',(pack(p),))
                    db.execute('INSERT INTO paper_binding VALUES(1,?)',(encode(self.binding),))
                    db.execute('INSERT INTO paper_cursor VALUES(1,?)',(encode(progress),))
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def progress(self):
        with self.store.connection() as db:
            return json.loads(db.execute('SELECT body FROM paper_cursor WHERE id=1').fetchone()[0])

    def step(self,index):
        if type(index) is not int or not 0 <= index < len(self.rows):
            raise ValueError('invalid paper observation index')
        bar = self.rows[index]
        request = dict(op='paper-step',binding=self.binding_id,index=index,bar=bar)

        def change(db,p,revision):
            progress = json.loads(db.execute('SELECT body FROM paper_cursor WHERE id=1').fetchone()[0])
            if progress['next_index'] != index:
                raise ValueError('non-sequential paper observation')
            if index and progress['previous_close'] != str(self.rows[index-1]['close']):
                raise ValueError('paper cursor/price mismatch')
            if p.order_reserve != 0 or db.execute('SELECT 1 FROM orders LIMIT 1').fetchone():
                raise ValueError('external execution intent in paper database')
            fills = []

            def fund(until):
                while progress['funding_index'] < len(self.funding):
                    e = self.funding[progress['funding_index']]
                    if e['time'] > until:
                        break
                    amount = (p.legs['SHORT'].units-p.legs['LONG'].units)*self.config.step*e['mark']*e['rate']
                    p.cash_event('funding-'+str(e['time']),amount,'FUNDING')
                    progress['funding_index'] += 1

            fund(bar['time'])
            bid,ask = bar['open']*(1-self.friction),bar['open']*(1+self.friction)
            if index == 0:
                actions = p.plan_entry(bar['open'],bid,ask)
                if actions[0].kind == 'WAIT':
                    raise ValueError('paper entry blocked: '+actions[0].reason)
            else:
                action = p.next_action(decimal(progress['previous_close']),bid,ask)
                actions = [] if action.kind == 'WAIT' else [action]
            for action in actions:
                buy = (action.side == 'LONG') == (action.kind == 'ADD')
                price = ask if buy else bid
                fee = action.units*self.config.step*price*self.config.fee_rate
                try:
                    p.fill(f'bar-{index}-{action.side}',action,price,fee,bar['open'])
                except ValueError as exc:
                    if str(exc) != 'insufficient shared funded balance':
                        raise
                    progress['rejected'] += 1
                    continue
                if index == 0:
                    p.legs[action.side].initial_units = action.units
                fills.append(dict(time=bar['time'],kind=action.kind,side=action.side,
                                  units=action.units,price=str(price),fee=str(fee)))
            fund(bar['time']+59999)
            snapshot = p.snapshot(bar['close'])
            equity = snapshot['equity']
            if equity <= snapshot['margin']:
                raise ValueError('paper collateral exhausted; no fictional liquidation')
            peak = max(decimal(progress['peak']),equity)
            progress.update(next_index=index+1,previous_close=str(bar['close']),
                trade_count=progress['trade_count']+len(fills),peak=str(peak),
                max_dd=str(max(decimal(progress['max_dd']),(peak-equity)/peak*100)))
            record = dict(index=index,time=bar['time']+59999,equity=str(equity),fills=fills,mode='PAPER')
            db.execute('INSERT INTO paper_bars VALUES(?,?)',(index,encode(record)))
            db.execute('UPDATE paper_cursor SET body=? WHERE id=1',(encode(progress),))
            return record
        return self.store._apply('paper-bar:'+str(index),request,change)

    def run(self,max_bars=None):
        if max_bars is not None and (type(max_bars) is not int or max_bars < 0):
            raise ValueError('max_bars must be a nonnegative integer')
        start = self.progress()['next_index']
        target = len(self.rows) if max_bars is None else min(len(self.rows),start+max_bars)
        while (index := self.progress()['next_index']) < target:
            self.step(index)
        return self.progress()

    def report(self):
        # One read transaction prevents mixing state/cursor/curve from different steps.
        with self.store.connection() as db:
            db.execute('BEGIN')
            p = unpack(db.execute('SELECT body FROM portfolio WHERE id=1').fetchone()[0])
            progress = json.loads(db.execute('SELECT body FROM paper_cursor WHERE id=1').fetchone()[0])
            records = [json.loads(r[0]) for r in db.execute('SELECT body FROM paper_bars ORDER BY idx')]
            db.commit()
        count = progress['next_index']
        fills = [f for row in records for f in row['fills']]
        curve = [dict(time=r['time'],equity=r['equity']) for r in records]
        s = p.snapshot(decimal(progress['previous_close']) if count else self.rows[0]['open'])
        result = dict(candles=count,start=self.rows[0]['time'],
            end=self.rows[count-1]['time']+59999 if count else None,initial_equity=str(self.settings['capital']),
            equity=str(s['equity']),net_mtm=str(s['equity']-self.capital),realized=str(p.realized),
            unrealized=str(sum((r['upl'] for r in s['legs'].values()),D(0))),fees=str(p.fees),
            funding=str(p.funding),funding_events=progress['funding_index'],
            rebalances=max(0,len(fills)-2),rejected_funding_checks=progress['rejected'],
            max_close_drawdown_pct=progress['max_dd'],terminal='OPEN_MTM',
            quantities={side:str(l.units*self.config.step) for side,l in p.legs.items()})
        return result,fills,curve


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candles',type=Path,required=True)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--max-bars',type=int)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--funding',type=Path)
    group.add_argument('--funding-zero',action='store_true')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('output exists; choose a new report folder, keep the SAME paper database to resume')
    settings = json.loads(args.config.read_text(encoding='utf-8-sig'))
    with Runner(args.database,candles(args.candles),settings,funding_rows(args.funding),
                funding_mode='CSV_RATES' if args.funding else 'ASSUMED_ZERO') as runner:
        progress = runner.run(args.max_bars)
        result,fills,curve = runner.report()
        manifest = dict(binding=runner.binding,progress=progress,trading_authorized=False,
                        status='COMPLETE' if progress['next_index'] == len(runner.rows) else 'PAUSED')
    args.output.mkdir(parents=True,exist_ok=False)
    for name,value in (('result',result),('trades',fills),('equity',curve),('manifest',manifest)):
        (args.output/(name+'.json')).write_text(json.dumps(value,indent=2),encoding='utf-8')
    print(json.dumps(dict(status=manifest['status'],mode='PAPER',result=result,trading_authorized=False),indent=2))


if __name__ == '__main__':
    main()
