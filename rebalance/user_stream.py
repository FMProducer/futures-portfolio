"""Bounded user-stream OBSERVER. No financial writes or trading admission.

Local receive fences plus REST evidence do not prove exchange sequence completeness.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import time
import urllib.error
import urllib.request
import uuid

from .binance_readonly import Reader, ReadError
from .checkpoint import Observer, identity, collect, OVERLAP
from .durable import encode
from .kernel import decimal


def redact(value):
    if isinstance(value, dict):
        return {k:redact(v) for k,v in value.items()
                if k.lower() not in ('listenkey','apikey','signature','secret','secretkey')}
    if isinstance(value,list):
        return [redact(v) for v in value]
    return value


def start_stream(reader):
    """Only this exact non-financial POST. Never log token or request URL."""
    request = urllib.request.Request('https://fapi.binance.com/fapi/v1/listenKey',
        data=b'', headers={'X-MBX-APIKEY':reader._key}, method='POST')
    try:
        with reader._opener.open(request,timeout=15) as response:
            token = json.loads(response.read()).get('listenKey')
        if not isinstance(token,str) or not token or not token.isalnum():
            raise ReadError('invalid_stream_session')
        return token
    except ReadError:
        raise
    except urllib.error.HTTPError as exc:
        raise ReadError('stream_session_http_error',status=exc.code) from None
    except Exception:
        raise ReadError('stream_session_unavailable') from None


class Journal:
    def __init__(self,observer):
        self.observer = observer
        observer.state()  # A proven UID binding must already exist.
        with observer.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS stream_state(id INTEGER PRIMARY KEY CHECK(id=1),body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS stream_records(seq INTEGER PRIMARY KEY,kind TEXT NOT NULL,body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS stream_messages(digest TEXT PRIMARY KEY,epoch TEXT NOT NULL,event_type TEXT NOT NULL,event_time INTEGER NOT NULL,transaction_time INTEGER NOT NULL,body TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS stream_records_no_update BEFORE UPDATE ON stream_records
                    BEGIN SELECT RAISE(ABORT,'immutable stream journal'); END;
                CREATE TRIGGER IF NOT EXISTS stream_records_no_delete BEFORE DELETE ON stream_records
                    BEGIN SELECT RAISE(ABORT,'immutable stream journal'); END;
                CREATE TRIGGER IF NOT EXISTS stream_messages_no_update BEFORE UPDATE ON stream_messages
                    BEGIN SELECT RAISE(ABORT,'immutable stream messages'); END;
                CREATE TRIGGER IF NOT EXISTS stream_messages_no_delete BEFORE DELETE ON stream_messages
                    BEGIN SELECT RAISE(ABORT,'immutable stream messages'); END;
            ''')

    def state(self):
        with self.observer.connect() as db:
            row = db.execute('SELECT body FROM stream_state WHERE id=1').fetchone()
            return json.loads(row[0]) if row else None

    @staticmethod
    def write(db,state,kind,detail):
        db.execute('INSERT INTO stream_records(kind,body) VALUES(?,?)',(kind,encode(detail)))
        db.execute('INSERT INTO stream_state VALUES(1,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body',
                   (encode(state),))

    def begin(self):
        epoch = uuid.uuid4().hex
        with self.observer.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                state = dict(epoch=epoch,status='NEEDS_REST',last_seq=0,last_times={},fault=None,
                             trading_authorized=False)
                self.write(db,state,'CONNECTED_REQUIRES_REST',dict(epoch=epoch))
                db.commit()
                return epoch
            except BaseException:
                db.rollback()
                raise

    def disconnect(self,epoch,reason='connection_closed'):
        with self.observer.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                row = db.execute('SELECT body FROM stream_state WHERE id=1').fetchone()
                state = json.loads(row[0])
                if state['epoch'] != epoch:
                    raise ValueError('stale stream epoch')
                state['status'] = 'DISCONNECTED_NEEDS_REST'
                self.write(db,state,'DISCONNECTED',dict(epoch=epoch,reason=reason))
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def append(self,epoch,payload):
        if not isinstance(payload,dict) or not isinstance(payload.get('e'),str):
            raise ReadError('malformed_stream_event')
        clean = redact(payload)
        kind = clean['e']
        event_time, transaction_time = clean.get('E'),clean.get('T',clean.get('E'))
        if type(event_time) is not int or type(transaction_time) is not int or min(event_time,transaction_time) < 0:
            raise ReadError('invalid_stream_time')
        body = encode(clean)
        digest = hashlib.sha256(body.encode()).hexdigest()
        with self.observer.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                state = json.loads(db.execute('SELECT body FROM stream_state WHERE id=1').fetchone()[0])
                if state['epoch'] != epoch or state['status'] == 'DISCONNECTED_NEEDS_REST':
                    raise ValueError('stale or disconnected stream epoch')
                prior = db.execute('SELECT body FROM stream_messages WHERE digest=?',(digest,)).fetchone()
                if prior:
                    db.commit()
                    return False
                previous = state['last_times'].get(kind)
                if previous and (event_time < previous[0] or transaction_time < previous[1]):
                    state['fault'] = 'OUT_OF_ORDER'
                if kind not in ('ACCOUNT_UPDATE','ORDER_TRADE_UPDATE'):
                    state['fault'] = 'EXPIRED' if kind == 'listenKeyExpired' else 'UNSUPPORTED_OR_RISK_EVENT'
                state['last_times'][kind] = [event_time,transaction_time]
                state['last_seq'] += 1
                state['status'] = 'NEEDS_REST'
                db.execute('INSERT INTO stream_messages VALUES(?,?,?,?,?,?)',
                           (digest,epoch,kind,event_time,transaction_time,body))
                self.write(db,state,'MESSAGE',dict(digest=digest,epoch=epoch,received_ms=int(time.time()*1000)))
                db.commit()
                return True
            except BaseException:
                db.rollback()
                raise

    def reconcile(self,proof,through,epoch,sequence):
        """Cursor + local receive fence commit atomically in the observer DB."""
        def guard(db):
            state = json.loads(db.execute('SELECT body FROM stream_state WHERE id=1').fetchone()[0])
            if (state['epoch'] != epoch or state['last_seq'] != sequence
                    or state['status'] == 'DISCONNECTED_NEEDS_REST' or state['fault']):
                raise ValueError('stream fence changed or requires recovery')
            # Check all messages since account checkpoint, including prior epochs:
            # reconnect cannot erase a trade missing from REST history.
            binding = json.loads(db.execute('SELECT body FROM observer_binding').fetchone()[0])
            messages = [json.loads(r[0]) for r in db.execute('SELECT body FROM stream_messages')]
            rest_trades = {(r['symbol'],str(r['id'])):r for r in proof['trades']}
            latest_balance = None
            positions = {}
            for message in sorted(messages,key=lambda x:(x['E'],x.get('T',x['E']))):
                if message['e'] == 'listenKeyExpired':
                    # Expiry carries no economic delta; a NEW epoch still needs REST.
                    continue
                if message.get('T',message['E']) <= binding['origin']:
                    raise ValueError('late stream event invalidates initial checkpoint')
                if message['E'] > proof['end'] or message.get('T',message['E']) > proof['end']:
                    raise ValueError('stream event beyond REST barrier')
                if message['e'] == 'ORDER_TRADE_UPDATE':
                    o = message['o']
                    response = proof.get('order_evidence',{}).get(o['s']+':'+str(o['i']))
                    if (not response or response.get('clientOrderId') != o['c']
                            or str(response.get('orderId')) != str(o['i']) or response.get('symbol') != o['s']
                            or response.get('side') != o['S'] or response.get('positionSide') != o['ps']
                            or decimal(response['origQty']) != decimal(o['q'])
                            or decimal(response['executedQty']) < decimal(o['z'])
                            or int(response.get('updateTime',0)) > proof['end']):
                        raise ValueError('order lifecycle REST evidence missing or conflicting')
                    if o['X'] in ('FILLED','CANCELED','EXPIRED','EXPIRED_IN_MATCH') and o['X'] != response['status']:
                        raise ValueError('terminal stream order conflicts with REST')
                    covered = sum((decimal(r['qty']) for r in proof['trades']
                                   if r['symbol'] == o['s'] and str(r['orderId']) == str(o['i'])),decimal(0))
                    if covered != decimal(response['executedQty']):
                        raise ValueError('order executed quantity is not covered by REST trades')
                    if o['x'] != 'TRADE':
                        continue
                    match = rest_trades.get((o['s'],str(o['t'])))
                    if not match:
                        raise ValueError('stream trade missing from REST window')
                    comparisons = [('l','qty'),('L','price'),('n','commission'),('rp','realizedPnl')]
                    if (str(o['i']) != str(match['orderId']) or o['ps'] != match['positionSide']
                            or o['S'] != match['side'] or o.get('N') != match['commissionAsset']
                            or any(decimal(o[a]) != decimal(match[b]) for a,b in comparisons)):
                        raise ValueError('stream trade conflicts with REST')
                elif message['e'] == 'ACCOUNT_UPDATE':
                    for balance in message['a'].get('B',[]):
                        if balance['a'] != 'USDT':
                            raise ValueError('unsupported stream balance asset')
                        latest_balance = decimal(balance['wb'])
                    for p in message['a'].get('P',[]):
                        positions[(p['s'],p['ps'])] = decimal(p['pa'])
                else:
                    raise ValueError('unresolved historical risk or configuration event')
            if latest_balance is not None and latest_balance != decimal(proof['observation']['wallet_usdt']):
                raise ValueError('stream wallet conflicts with REST')
            if any(positions.values()):
                raise ValueError('stream positions conflict with flat checkpoint')
            state['status'] = 'OBSERVING_NOT_CERTIFIED'
            self.write(db,state,'REST_FENCE_MATCHED',dict(epoch=epoch,sequence=sequence,through=proof['end']))
        try:
            return self.observer.ingest(proof,through,commit_guard=guard)
        except Exception:
            # Failed REST validation must also invalidate an earlier successful
            # observation; never leave the previous fence looking current.
            with self.observer.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                try:
                    state = json.loads(db.execute('SELECT body FROM stream_state WHERE id=1').fetchone()[0])
                    if state['epoch'] == epoch and state['status'] != 'DISCONNECTED_NEEDS_REST':
                        state['status'] = 'NEEDS_REST'
                        self.write(db,state,'REST_RECONCILIATION_FAILED',dict(epoch=epoch))
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise
            raise


async def monitor(reader,observer,seconds):
    import aiohttp
    bound, _ = observer.state()
    reader.synchronize()
    expected = {k:v for k,v in bound.items() if k not in ('origin','wallet')}
    if identity(reader,bound['symbol']) != expected:
        raise ValueError('account binding mismatch')
    token = start_stream(reader)
    journal = Journal(observer)
    epoch = None
    fences = 0
    # The listenKey might be shared with another client; never DELETE it on exit.
    try:
        async with aiohttp.ClientSession(trust_env=False) as session:
            async with session.ws_connect('wss://fstream.binance.com/private/ws/'+token,
                                          heartbeat=5,max_msg_size=1048576) as ws:
                epoch = journal.begin()
                async def pump():
                    try:
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                journal.append(epoch,json.loads(msg.data))
                            elif msg.type == aiohttp.WSMsgType.ERROR:
                                raise ReadError('stream_transport_error')
                    finally:
                        journal.disconnect(epoch)
                task = asyncio.create_task(pump())
                async def fence():
                    nonlocal fences
                    _,through = observer.state()
                    proof = await asyncio.to_thread(collect,reader,bound['symbol'],start=max(0,bound['origin']-OVERLAP))
                    with observer.connect() as db:
                        messages = [json.loads(r[0]) for r in db.execute('SELECT body FROM stream_messages')]
                    orders = {(m['o']['s'],str(m['o']['i']),m['o']['c']) for m in messages if m['e'] == 'ORDER_TRADE_UPDATE'}
                    if len(orders) > 128:
                        raise ReadError('stream_order_evidence_budget_exhausted')
                    proof['order_evidence'] = {}
                    for symbol,order_id,client in sorted(orders):
                        proof['order_evidence'][symbol+':'+order_id] = await asyncio.to_thread(
                            reader.get,'/fapi/v1/order',symbol=symbol,origClientOrderId=client)
                    if task.done():
                        raise ReadError('stream_closed_during_rest')
                    journal.reconcile(proof,through,epoch,journal.state()['last_seq'])
                    fences += 1
                try:
                    await fence()
                    await asyncio.sleep(seconds)
                    await fence()
                finally:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
        return dict(status='STREAM_OBSERVATION_COMPLETED',rest_fences=fences,
                    received_events=journal.state()['last_seq'],final_state=journal.state()['status'],
                    trading_authorized=False)
    except (ReadError,ValueError):
        raise
    except Exception:
        raise ReadError('stream_session_failed') from None
    finally:
        if epoch and journal.state()['status'] != 'DISCONNECTED_NEEDS_REST':
            journal.disconnect(epoch)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file',type=Path,required=True)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--seconds',type=int,default=15)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 120:
        parser.error('bounded observer duration must be 1..120 seconds')
    args.output.mkdir(parents=True,exist_ok=False)
    (args.output/'.gitignore').write_text('*\n!.gitignore\n',encoding='utf-8')
    try:
        result = asyncio.run(monitor(Reader.from_env(args.env_file),Observer(args.database),args.seconds))
        (args.output/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps(result))
    except (ReadError,ValueError) as exc:
        error = exc.details if isinstance(exc,ReadError) else dict(kind=str(exc))
        (args.output/'error.json').write_text(json.dumps(error,indent=2),encoding='utf-8')
        print(json.dumps(dict(status='BLOCKED',error=error,trading_authorized=False)))
        raise SystemExit(2) from None


if __name__ == '__main__':
    main()
