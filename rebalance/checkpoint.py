"""UID-bound OBSERVER database and provisional history cursor. Never trades.

Only flat USDT Hedge accounts are supported. No allocation of account funds to
the strategy, no initialization of a trading Store from an observed balance.
"""
import argparse
from contextlib import contextmanager
from decimal import Decimal as D
import hashlib
import json
from pathlib import Path
import sqlite3

from .binance_readonly import Reader, ReadError, capture
from .durable import encode
from .kernel import decimal
from .reconcile import analyze, history, DAY

OVERLAP = 300000


def identity(reader, symbol):
    raw = reader.get('/api/v3/account').get('uid')
    if type(raw) is not int or raw <= 0:
        raise ReadError('stable_uid_unavailable')
    return dict(account=hashlib.sha256(('binance-mainnet:'+str(raw)).encode()).hexdigest(),
                market='USD-M', symbol=symbol, asset='USDT', purpose='OBSERVER_ONLY_V1')


def flat(observation):
    if (observation['blocking_reasons'] or not observation['rest_bracket_equal']
            or observation['active_positions'] or observation['open_orders']
            or decimal(observation['initial_margin_usdt']) != 0
            or decimal(observation['unrealized_usdt']) != 0):
        raise ValueError('checkpoint requires stable flat USDT Hedge account')


def collect(reader, symbol, *, start=None):
    reader.synchronize()
    bound = identity(reader, symbol)
    before = int(reader.get('/fapi/v1/time')['serverTime'])
    algo_before = reader.get('/fapi/v1/openAlgoOrders')
    observation = capture(reader, symbol)
    algo_after = reader.get('/fapi/v1/openAlgoOrders')
    if not isinstance(algo_before,list) or not isinstance(algo_after,list) or algo_before or algo_after:
        raise ValueError('checkpoint requires no algo orders')
    flat(observation)
    if identity(reader, symbol) != bound:
        raise ValueError('account changed during collection')
    end = int(reader.get('/fapi/v1/time')['serverTime'])
    begin = max(0,before-OVERLAP) if start is None else start
    trades = history(reader,'/fapi/v1/userTrades',begin,end,symbol=symbol)
    incomes = history(reader,'/fapi/v1/income',begin,end)
    return dict(binding=bound,start=begin,before=before,end=end,observation=observation,
                trades=trades,incomes=incomes)


def facts(proof):
    result = {}
    for kind, rows in (('trade',proof['trades']),('income',proof['incomes'])):
        for row in rows:
            stamp = int(row['time'])
            if not proof['start'] <= stamp <= proof['end']:
                raise ValueError('fact outside history interval')
            key = ('trade:'+row['symbol']+':'+str(row['id'])) if kind == 'trade' else (
                'income:'+row['incomeType']+':'+str(row['tranId']))
            if key in result:
                raise ValueError('duplicate history fact')
            result[key] = (kind,stamp,encode(row))
    return result


class Observer:
    def __init__(self,path):
        self.path = str(path)
        with self.connect() as db:
            # Never add observer tables to an existing trading/accounting DB.
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and 'observer_binding' not in tables:
                raise ValueError('not an observer database')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS observer_binding(id INTEGER PRIMARY KEY CHECK(id=1),body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS observer_cursor(id INTEGER PRIMARY KEY CHECK(id=1),through INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS observer_facts(identity TEXT PRIMARY KEY,kind TEXT NOT NULL,time INTEGER NOT NULL,body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS observer_checks(seq INTEGER PRIMARY KEY,through INTEGER UNIQUE NOT NULL,body TEXT NOT NULL);
            ''')
            for table in ('observer_binding','observer_facts','observer_checks'):
                for op in ('UPDATE','DELETE'):
                    db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{op} BEFORE {op} ON {table} "
                               "BEGIN SELECT RAISE(ABORT,'immutable observer evidence'); END")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path,timeout=15,isolation_level=None)
        db.execute('PRAGMA synchronous=FULL')
        try:
            yield db
        finally:
            db.close()

    def state(self):
        with self.connect() as db:
            db.execute('BEGIN')
            binding = db.execute('SELECT body FROM observer_binding WHERE id=1').fetchone()
            cursor = db.execute('SELECT through FROM observer_cursor WHERE id=1').fetchone()
            db.commit()
            if not binding or not cursor:
                raise ValueError('observer not initialized')
            return json.loads(binding[0]),cursor[0]

    def initialize(self,proof):
        flat(proof['observation'])
        rows = facts(proof)
        if proof['start'] > proof['before'] or proof['end'] < proof['before']:
            raise ValueError('invalid checkpoint interval')
        if any(stamp >= proof['before'] for _,stamp,_ in rows.values()):
            raise ValueError('activity during initial checkpoint collection')
        binding = dict(proof['binding'],origin=proof['end'],
                       wallet=proof['observation']['wallet_usdt'])
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                if db.execute('SELECT 1 FROM observer_binding').fetchone():
                    raise ValueError('observer already bound; no reset')
                db.execute('INSERT INTO observer_binding VALUES(1,?)',(encode(binding),))
                db.execute('INSERT INTO observer_cursor VALUES(1,?)',(proof['end'],))
                db.executemany('INSERT INTO observer_facts VALUES(?,?,?,?)',[(k,*v) for k,v in rows.items()])
                db.execute('INSERT INTO observer_checks(through,body) VALUES(?,?)',
                           (proof['end'],encode(proof)))
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def ingest(self,proof,expected_through,*,commit_guard=None):
        flat(proof['observation'])
        incoming = facts(proof)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                row = db.execute('SELECT body FROM observer_binding').fetchone()
                if not row:
                    raise ValueError('observer not initialized')
                binding = json.loads(row[0])
                if proof['binding'] != {k:v for k,v in binding.items() if k not in ('origin','wallet')}:
                    raise ValueError('account, market or instrument mismatch')
                through = db.execute('SELECT through FROM observer_cursor').fetchone()[0]
                if through != expected_through or proof['end'] <= through:
                    raise ValueError('stale cursor')
                if proof['start'] > max(0,through-OVERLAP):
                    raise ValueError('history overlap missing')
                old = {r[0]:(r[1],r[2],r[3]) for r in db.execute('SELECT identity,kind,time,body FROM observer_facts')}
                for key, record in old.items():
                    if proof['start'] <= record[1] <= proof['end'] and incoming.get(key) != record:
                        raise ValueError('previously observed history missing or changed')
                new = {}
                for key,value in incoming.items():
                    if key in old:
                        if old[key] != value:
                            raise ValueError('conflicting history identity')
                    else:
                        if value[1] <= binding['origin']:
                            raise ValueError('late fact invalidates initial checkpoint')
                        new[key] = value
                combined = dict(old,**new)
                active = [(kind,json.loads(body)) for kind,stamp,body in combined.values() if stamp > binding['origin']]
                trades = [r for k,r in active if k == 'trade']
                incomes = [r for k,r in active if k == 'income']
                normalized = analyze(trades,incomes)
                if normalized['blocking_reasons']:
                    raise ValueError('history bundle blocked')
                inventory = {}
                for trade in normalized['trades']:
                    key = (trade['symbol'],trade['position_side'])
                    opening = (trade['side'] == 'BUY') == (trade['position_side'] == 'LONG')
                    inventory[key] = inventory.get(key,D(0)) + decimal(trade['qty'])*(1 if opening else -1)
                if any(inventory.values()):
                    raise ValueError('trade inventory conflicts with flat checkpoint')
                delta = sum((decimal(t['realized'])-decimal(t['fee']) for t in normalized['trades']),D(0))
                delta += sum((decimal(c['amount']) for c in normalized['cash']),D(0))
                expected = decimal(binding['wallet'])+delta
                if expected != decimal(proof['observation']['wallet_usdt']):
                    raise ValueError('wallet bridge mismatch; cursor not advanced')
                if commit_guard is not None:
                    commit_guard(db)
                db.executemany('INSERT INTO observer_facts VALUES(?,?,?,?)',[(k,*v) for k,v in new.items()])
                db.execute('INSERT INTO observer_checks(through,body) VALUES(?,?)',(proof['end'],encode(proof)))
                db.execute('UPDATE observer_cursor SET through=? WHERE id=1',(proof['end'],))
                db.commit()
                return dict(status='OBSERVER_CHECKPOINT_MATCHED',new_facts=len(new),
                            cursor=proof['end'],trading_authorized=False)
            except BaseException:
                db.rollback()
                raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file',type=Path,required=True)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--symbol',default='BTCUSDT')
    parser.add_argument('--initialize',action='store_true')
    parser.add_argument('--full-backfill',action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    (args.output/'.gitignore').write_text('*\n!.gitignore\n',encoding='utf-8')
    try:
        reader = Reader.from_env(args.env_file)
        observer = Observer(args.database)
        if args.initialize:
            proof = collect(reader,args.symbol)
            observer.initialize(proof)
            result = dict(status='OBSERVER_INITIALIZED',trading_authorized=False)
        else:
            binding,through = observer.state()
            # UID is verified before querying history from a persisted cursor.
            reader.synchronize()
            if identity(reader,args.symbol) != {k:v for k,v in binding.items() if k not in ('origin','wallet')}:
                raise ValueError('account binding mismatch')
            anchor = binding['origin'] if args.full_backfill else through
            proof = collect(reader,args.symbol,start=max(0,anchor-OVERLAP))
            result = observer.ingest(proof,through)
        (args.output/'checkpoint.json').write_text(json.dumps(proof,indent=2),encoding='utf-8')
        print(json.dumps(result))
    except (ReadError,ValueError) as exc:
        error = exc.details if isinstance(exc,ReadError) else dict(kind=str(exc))
        (args.output/'error.json').write_text(json.dumps(error,indent=2),encoding='utf-8')
        print(json.dumps(dict(status='BLOCKED',error=error,trading_authorized=False)))
        raise SystemExit(2) from None


if __name__ == '__main__':
    main()
