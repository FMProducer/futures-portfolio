"""Supervised public REST paper sessions with durable recovery; no trading API.

Bounded foreground execution, not a background daemon. Long data gaps freeze.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import time
import urllib.parse
import urllib.request

from .binance_readonly import ReadError
from .durable import encode
from .public_paper import CaptureError, Paper, PublicReader, rules, validate


class SessionReader(PublicReader):
    def funding(self, symbol, timestamp):
        if symbol != 'BTCUSDT' or type(timestamp) is not int or timestamp <= 0:
            raise ValueError('only exact scheduled BTCUSDT funding is supported')
        query = urllib.parse.urlencode(dict(symbol=symbol,startTime=timestamp,endTime=timestamp,limit=1000))
        request = urllib.request.Request('https://fapi.binance.com/fapi/v1/fundingRate?'+query,method='GET')
        try:
            with self.opener.open(request,timeout=15) as response:
                rows = json.loads(response.read())
        except Exception:
            raise ReadError('funding_request_failed') from None
        if rows == []:
            raise ReadError('funding_not_published')
        return rows


@contextmanager
def exclusive(path):
    """OS lock is released on process death; no stale lock deletion shortcut."""
    with open(str(path)+'.lock','a+b') as stream:
        stream.seek(0,2)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        if __import__('os').name == 'nt':
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:
                raise ValueError('another paper session owns this database') from None
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError:
                raise ValueError('another paper session owns this database') from None
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(),fcntl.LOCK_UN)


class Session:
    def __init__(self, paper, reader, *, monotonic=time.monotonic, sleep=time.sleep):
        if paper.symbol != 'BTCUSDT':
            raise ValueError('session funding supports BTCUSDT only')
        self.paper,self.reader = paper,reader
        self.monotonic,self.sleep = monotonic,sleep
        binding = encode(dict(paper=json.loads(paper.binding),
            session_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()))
        with paper.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                db.execute('CREATE TABLE IF NOT EXISTS session_state (id INTEGER PRIMARY KEY CHECK(id=1), binding TEXT NOT NULL, status TEXT NOT NULL, reason TEXT)')
                db.execute('CREATE TABLE IF NOT EXISTS session_inputs (id TEXT PRIMARY KEY, payload TEXT NOT NULL, result TEXT)')
                db.execute('CREATE TABLE IF NOT EXISTS session_log (seq INTEGER PRIMARY KEY, body TEXT NOT NULL)')
                db.execute("CREATE TRIGGER IF NOT EXISTS session_payload_immutable BEFORE UPDATE ON session_inputs WHEN NEW.id != OLD.id OR NEW.payload != OLD.payload OR OLD.result IS NOT NULL BEGIN SELECT RAISE(ABORT,'immutable session input'); END")
                db.execute("CREATE TRIGGER IF NOT EXISTS session_input_no_delete BEFORE DELETE ON session_inputs BEGIN SELECT RAISE(ABORT,'immutable session input'); END")
                for operation in ('UPDATE','DELETE'):
                    db.execute(f"CREATE TRIGGER IF NOT EXISTS session_log_no_{operation.lower()} BEFORE {operation} ON session_log BEGIN SELECT RAISE(ABORT,'immutable session log'); END")
                old = db.execute('SELECT binding FROM session_state').fetchone()
                if old:
                    if old[0] != binding:
                        raise ValueError('session code or input binding changed')
                else:
                    db.execute("INSERT INTO session_state VALUES(1,?,'STOPPED',NULL)",(binding,))
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def state(self):
        with self.paper.store.connection() as db:
            status,reason = db.execute('SELECT status,reason FROM session_state').fetchone()
        return dict(status=status,reason=reason,trading_authorized=False)

    def log(self, status, **data):
        with self.paper.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                db.execute('INSERT INTO session_log(body) VALUES(?)',(encode(dict(status=status,**data)),))
                db.execute('UPDATE session_state SET status=?,reason=? WHERE id=1',(status,data.get('reason')))
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def enqueue(self, payload):
        body = encode(payload)
        identity = 'capture:'+str(payload['sample']['after'])
        with self.paper.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                old = db.execute('SELECT payload FROM session_inputs WHERE id=?',(identity,)).fetchone()
                if old and old[0] != body:
                    raise ValueError('conflicting captured input')
                if not old:
                    db.execute('INSERT INTO session_inputs VALUES(?,?,NULL)',(identity,body))
                db.commit()
            except BaseException:
                db.rollback()
                raise
        return identity

    def recover(self):
        with self.paper.store.connection() as db:
            rows = db.execute('SELECT id,payload FROM session_inputs WHERE result IS NULL ORDER BY rowid').fetchall()
        for identity,body in rows:
            payload = json.loads(body)
            result = self.paper.step(payload['sample'],funding=payload['funding'])
            # If the process dies after step, replay returns its cached result.
            with self.paper.store.connection() as db:
                db.execute('UPDATE session_inputs SET result=? WHERE id=? AND result IS NULL',(encode(result),identity))

    def capture(self):
        sample = self.reader.capture(self.paper.symbol)
        validate(sample,self.paper.symbol)
        with self.paper.store.connection() as db:
            last,due = db.execute('SELECT last_time,funding_due FROM public_paper').fetchone()
        funding = None
        if last is not None and sample['after']-last > 120000:
            raise ValueError('observation gap; freeze until explicit restart decision')
        if due is not None and sample['after'] >= due:
            funding = self.reader.funding(self.paper.symbol,due)
            # Funding fetch can take time; never fill using the earlier quotes.
            sample = self.reader.capture(self.paper.symbol)
            validate(sample,self.paper.symbol)
        return dict(sample=sample,funding=funding)

    def run(self, *, duration=60, interval=5, max_errors=3):
        if (type(duration) is not int or not 1 <= duration <= 86400
                or type(interval) is not int or not 2 <= interval <= 30
                or type(max_errors) is not int or not 1 <= max_errors <= 10):
            raise ValueError('invalid bounded session schedule')
        with exclusive(self.paper.store.path):
            if self.state()['status'] == 'FROZEN':
                raise ValueError('session is FROZEN; no automatic reset')
            start = self.monotonic()
            errors = 0
            try:
                self.log('RECOVERING')
                self.recover()
                self.log('RUNNING')
                while self.monotonic()-start < duration:
                    try:
                        payload = self.capture()
                        self.enqueue(payload)
                        self.recover()
                        errors = 0
                        self.log('RUNNING',server_time=payload['sample']['after'])
                    except CaptureError as exc:
                        # Rejected public data is evidence, never a trading input.
                        errors+=1
                        with self.paper.store.connection() as db:
                            last=db.execute('SELECT last_time FROM public_paper').fetchone()[0]
                        stamp=exc.sample.get('after') if isinstance(exc.sample,dict) else None
                        gap=last is not None and type(stamp) is int and stamp-last>120000
                        self.log('RETRYING' if exc.retryable and not gap and errors<max_errors else 'FROZEN',
                            reason=exc.kind,consecutive_errors=errors,rejected_capture=exc.sample,
                            retryable=exc.retryable,observation_gap_exceeded=gap)
                        if not exc.retryable or gap or errors>=max_errors:
                            return self.state()
                    except ReadError as exc:
                        errors += 1
                        self.log('RETRYING',reason=exc.details['kind'],consecutive_errors=errors)
                        if errors >= max_errors:
                            self.log('FROZEN',reason='public read retries exhausted')
                            return self.state()
                    remaining = duration-(self.monotonic()-start)
                    if remaining > 0:
                        self.sleep(min(interval,remaining))
                if errors:
                    self.log('FROZEN',reason='session ended with unresolved public read failure')
                else:
                    self.log('STOPPED',reason='duration complete')
            except KeyboardInterrupt:
                self.log('STOPPED',reason='operator interrupt')
            except Exception as exc:
                self.log('FROZEN',reason=type(exc).__name__+': '+str(exc))
            return self.state()

    def export(self, path):
        path = Path(path)
        path.mkdir(parents=True,exist_ok=False)
        with self.paper.store.connection() as db:
            db.execute('BEGIN')
            binding,status,reason = db.execute('SELECT binding,status,reason FROM session_state').fetchone()
            inputs = [dict(id=i,payload=json.loads(p),result=None if r is None else json.loads(r))
                      for i,p,r in db.execute('SELECT id,payload,result FROM session_inputs ORDER BY rowid')]
            logs = [json.loads(r[0]) for r in db.execute('SELECT body FROM session_log ORDER BY seq')]
            portfolio,revision = db.execute('SELECT body,revision FROM portfolio').fetchone()
            db.commit()
        body = dict(binding=json.loads(binding),status=status,reason=reason,inputs=inputs,
            log=logs,portfolio=json.loads(portfolio),revision=revision,trading_authorized=False)
        (path/'session.json').write_text(json.dumps(body,indent=2),encoding='utf-8')
        return body


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--duration',type=int,default=60)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('output exists; choose a new output folder')
    reader = SessionReader()
    settings = json.loads(args.config.read_text(encoding='utf-8-sig'))
    paper = Paper(args.database,'BTCUSDT',settings,rules(reader.get('/fapi/v1/exchangeInfo'),'BTCUSDT'))
    session = Session(paper,reader)
    state = session.run(duration=args.duration)
    session.export(args.output)
    print(encode(state))
    if state['status'] == 'FROZEN':
        raise SystemExit(2)


if __name__ == '__main__':
    main()
