"""Bounded public REST observations -> isolated simulated portfolio, never orders.

One decision per observed UTC minute; missed minutes are NOT fabricated.
This is not an atomic account snapshot, exchange execution or a funding model.
"""
import argparse
from contextlib import closing
from copy import deepcopy
from decimal import InvalidOperation
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
import urllib.parse
import urllib.request

from .binance_readonly import NoRedirect, ReadError
from .durable import Store, encode, pack
from .kernel import Config, Portfolio, decimal


class PublicReader:
    ROUTES = {'/fapi/v1/time', '/fapi/v1/exchangeInfo',
              '/fapi/v1/premiumIndex', '/fapi/v1/ticker/bookTicker'}

    def __init__(self):
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def get(self, path, symbol=None):
        if path not in self.ROUTES or (symbol is not None and not re.fullmatch('[A-Z0-9]{5,20}', symbol)):
            raise ValueError('outside public allowlist')
        url = 'https://fapi.binance.com'+path
        if symbol:
            url += '?'+urllib.parse.urlencode({'symbol':symbol})
        try:
            with self.opener.open(urllib.request.Request(url, method='GET'), timeout=15) as response:
                return json.loads(response.read())
        except Exception:
            raise ReadError('public_request_failed') from None

    def capture(self, symbol):
        started=time.monotonic()
        before = self.get('/fapi/v1/time')['serverTime']
        mark = self.get('/fapi/v1/premiumIndex', symbol)
        book = self.get('/fapi/v1/ticker/bookTicker', symbol)
        after = self.get('/fapi/v1/time')['serverTime']
        sample = dict(symbol=symbol, before=before, after=after, mark=mark, book=book,
                      collection_elapsed_ms=int((time.monotonic()-started)*1000))
        validate(sample, symbol)
        return sample


class CaptureError(ValueError):
    def __init__(self,kind,sample,*,retryable=False):
        super().__init__(kind)
        self.kind,self.sample,self.retryable=kind,deepcopy(sample),retryable


def validate(sample,symbol):
    try:
        return _validate(sample,symbol)
    except CaptureError:
        raise
    except (KeyError,TypeError,ValueError,InvalidOperation):
        raise CaptureError('invalid_capture_payload',sample) from None


def _validate(sample, symbol):
    """10s is a capture/freshness bound, not a trading-risk policy approval."""
    before, after = sample['before'], sample['after']
    if type(before) is not int or type(after) is not int or before<=0:
        raise CaptureError('invalid_capture_timestamp',sample)
    if sample['symbol']!=symbol:
        raise CaptureError('wrong_capture_symbol',sample)
    if after<before:
        raise CaptureError('capture_time_regression',sample)
    if after-before>10000:
        raise CaptureError('capture_span_exceeded',sample,retryable=True)
    mark, book = sample['mark'], sample['book']
    for part in (mark, book):
        stamp = part['time']
        if part['symbol']!=symbol:
            raise CaptureError('wrong_quote_symbol',sample)
        if type(stamp) is not int:
            raise CaptureError('invalid_quote_timestamp',sample)
        if after<stamp:
            raise CaptureError('future_quote',sample)
        if after-stamp>10000:
            raise CaptureError('stale_quote',sample,retryable=True)
    bid, ask, price, bidqty, askqty = map(decimal,
        (book['bidPrice'],book['askPrice'],mark['markPrice'],book['bidQty'],book['askQty']))
    due = mark['nextFundingTime']
    if (min(bid,ask,price,bidqty,askqty) <= 0 or bid > ask
            or type(due) is not int or due <= after):
        raise ValueError('invalid quotes or unknown funding horizon')
    return price,bid,ask


def rules(info, symbol):
    rows = [s for s in info['symbols'] if s['symbol'] == symbol]
    if len(rows) != 1:
        raise ValueError('unknown symbol')
    row = rows[0]
    if (row['status'] != 'TRADING' or row['contractType'] != 'PERPETUAL'
            or row['quoteAsset'] != 'USDT' or row['marginAsset'] != 'USDT'):
        raise ValueError('requires trading USDT perpetual')
    filters = {f['filterType']:f for f in row['filters']}
    return {key:filters[key] for key in ('LOT_SIZE','MARKET_LOT_SIZE','MIN_NOTIONAL')}


class Paper:
    def __init__(self, path, symbol, settings, filters):
        if not re.fullmatch('[A-Z0-9]{5,20}', symbol):
            raise ValueError('invalid symbol')
        self.symbol = symbol
        self.settings = json.loads(encode(settings))
        self.filters = json.loads(encode(filters))
        config = Config(**self.settings['strategy'])
        capital = decimal(self.settings['capital'])
        self.slippage = decimal(self.settings['slippage_bps'])/10000
        if capital <= 0 or not 0 <= self.slippage < 1:
            raise ValueError('invalid simulated capital or slippage')
        self.binding = encode(dict(mode='PUBLIC_REST_PAPER_V1',symbol=symbol,
            settings=self.settings,filters=self.filters,
            code={name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                  for name in ('public_paper.py','kernel.py','durable.py')}))
        path = Path(path)
        if path.exists():
            with closing(sqlite3.connect(path)) as db:
                if not db.execute("SELECT 1 FROM sqlite_master WHERE name='public_paper'").fetchone():
                    raise ValueError('foreign database; use a dedicated public-paper path')
        path.parent.mkdir(parents=True,exist_ok=True)
        self.store = Store(path)
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                db.execute('CREATE TABLE IF NOT EXISTS public_paper (id INTEGER PRIMARY KEY CHECK(id=1), binding TEXT NOT NULL, last_time INTEGER, funding_due INTEGER)')
                old = db.execute('SELECT binding FROM public_paper WHERE id=1').fetchone()
                if old:
                    if old[0] != self.binding:
                        raise ValueError('public-paper binding changed')
                else:
                    db.execute('INSERT INTO public_paper VALUES(1,?,NULL,NULL)',(self.binding,))
                    db.execute('INSERT INTO portfolio VALUES(1,?,0)',(pack(Portfolio(config,capital)),))
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def step(self, sample, *, funding=None):
        # Detach caller-owned mutable input before validation and transaction.
        sample = json.loads(encode(sample))
        funding = None if funding is None else json.loads(encode(funding))
        mark,bid,ask = validate(sample,self.symbol)
        stamp = sample['after']
        identity = 'public:'+str(stamp)
        def change(db,p,revision):
            binding,last,due = db.execute('SELECT binding,last_time,funding_due FROM public_paper').fetchone()
            if binding != self.binding:
                raise ValueError('binding changed')
            if db.execute('SELECT 1 FROM orders LIMIT 1').fetchone() or p.order_reserve:
                raise ValueError('foreign order state')
            if last is not None and stamp <= last:
                raise ValueError('out-of-order observation')
            if last is not None and stamp-last > 120000:
                raise ValueError('observation gap; freeze until explicit restart decision')
            funding_amount = decimal(0)
            if funding is not None:
                # One exact scheduled BTC funding event. No guessed interval/rate.
                if (self.symbol != 'BTCUSDT' or due is None or last is None or not last < due <= stamp
                        or not isinstance(funding,list) or len(funding) != 1):
                    raise ValueError('unexpected or incomplete funding evidence')
                row = funding[0]
                if (row['symbol'] != self.symbol or type(row['fundingTime']) is not int
                        or row['fundingTime'] != due or row.get('rateType','Regular') != 'Regular'):
                    raise ValueError('funding identity or type mismatch')
                rate, funding_mark = decimal(row['fundingRate']),decimal(row['markPrice'])
                if funding_mark <= 0:
                    raise ValueError('invalid funding mark')
                funding_amount = (p.legs['SHORT'].units-p.legs['LONG'].units)*p.config.step*funding_mark*rate
                p.cash_event('public-funding:'+str(due),funding_amount,'FUNDING')
                due = sample['mark']['nextFundingTime']
            if due is not None and stamp >= due and any(l.units for l in p.legs.values()):
                raise ValueError('funding UNKNOWN; freeze until explicit reconciliation')
            if last is not None and stamp//60000 == last//60000:
                db.execute('UPDATE public_paper SET last_time=?,funding_due=? WHERE id=1',(stamp,due))
                result = dict(status='SAME_MINUTE',time=stamp,mode='PAPER',fills=[],trading_authorized=False)
                if funding is not None:
                    result.update(funding_delta=str(funding_amount),equity=str(p.snapshot(mark)['equity']))
                return result
            bid_cost,ask_cost = bid*(1-self.slippage),ask*(1+self.slippage)
            entry = not any(l.initial_units for l in p.legs.values())
            actions = (p.plan_entry(mark,bid_cost,ask_cost) if entry
                       else [p.next_action(mark,bid_cost,ask_cost)])
            fills = []
            for action in actions:
                if action.kind == 'WAIT':
                    continue
                buy = (action.side == 'LONG') == (action.kind == 'ADD')
                price = ask_cost if buy else bid_cost
                qty = action.units*p.config.step
                for name in ('LOT_SIZE','MARKET_LOT_SIZE'):
                    rule = self.filters[name]
                    step = decimal(rule['stepSize'])
                    if (qty < decimal(rule['minQty']) or qty > decimal(rule['maxQty'])
                            or (step > 0 and qty % step)):
                        raise ValueError('simulated order violates quantity filter')
                if qty*price < decimal(self.filters['MIN_NOTIONAL']['notional']):
                    raise ValueError('simulated order violates min notional')
                if qty > decimal(sample['book']['askQty' if buy else 'bidQty']):
                    raise ValueError('insufficient observed top-of-book volume')
                fee = qty*price*p.config.fee_rate
                p.fill(identity+':'+action.side,action,price,fee,mark)
                if entry:
                    p.legs[action.side].initial_units = action.units
                fills.append(dict(action=asdict(action),quantity=str(qty),price=str(price),fee=str(fee)))
            snap = p.snapshot(mark)
            if snap['equity'] <= snap['margin']:
                raise ValueError('simulation requires liquidation model')
            db.execute('UPDATE public_paper SET last_time=?,funding_due=? WHERE id=1',
                       (stamp,sample['mark']['nextFundingTime']))
            result = dict(status='APPLIED',mode='PAPER',time=stamp,gap_ms=None if last is None else stamp-last,
                        fills=fills,equity=str(snap['equity']),fees=str(p.fees),
                        quantities={s:str(l.units*p.config.step) for s,l in p.legs.items()},
                        trading_authorized=False)
            if funding is not None:
                result['funding_delta'] = str(funding_amount)
            return result
        request = sample if funding is None else dict(sample=sample,funding=funding)
        return self.store._apply(identity,request,change)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--symbol',default='BTCUSDT')
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--samples',type=int,default=1,choices=range(1,11))
    args = parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    reader = PublicReader()
    try:
        info = reader.get('/fapi/v1/exchangeInfo')
        filters = rules(info,args.symbol)
        settings = json.loads(args.config.read_text(encoding='utf-8-sig'))
        paper = Paper(args.database,args.symbol,settings,filters)
        (args.output/'binding.json').write_text(paper.binding,encoding='utf-8')
        for index in range(args.samples):
            if index:
                time.sleep(2)
            sample = reader.capture(args.symbol)
            (args.output/f'sample_{index}.json').write_text(encode(sample),encoding='utf-8')
            result = paper.step(sample)
            (args.output/f'result_{index}.json').write_text(encode(result),encoding='utf-8')
        print(encode(result))
    except Exception as exc:
        (args.output/'failure.json').write_text(encode(dict(status='BLOCKED',reason=str(exc),
                    trading_authorized=False)),encoding='utf-8')
        raise


if __name__ == '__main__':
    main()
