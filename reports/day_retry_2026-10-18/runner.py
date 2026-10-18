"""One bounded 24-hour public-paper window; immutable runtime source snapshot."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
RUNTIME=HERE/'runtime'
FILES=('__init__.py','public_session.py','public_paper.py','kernel.py','durable.py','binance_readonly.py')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(name,value):
    path=HERE/name
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,indent=2),encoding='utf-8')
    temporary.replace(path)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def utc(stamp):
    return datetime.fromtimestamp(stamp/1000,timezone.utc).isoformat()


def prepare():
    if (HERE/'frozen.json').exists() or RUNTIME.exists():
        raise ValueError('funding window already prepared; no overwrite or automatic restart')
    from rebalance.public_session import SessionReader
    from rebalance.public_paper import rules
    reader=SessionReader()
    info=reader.get('/fapi/v1/exchangeInfo')
    observation=reader.capture('BTCUSDT')
    now,due=observation['after'],observation['mark']['nextFundingTime']
    duration=86400
    if not 300 <= duration <= 86400:
        raise ValueError('funding window outside bounded session duration')
    code=RUNTIME/'code/rebalance'
    code.mkdir(parents=True)
    hashes={}
    for name in FILES:
        data=(ROOT/'rebalance'/name).read_bytes()
        (code/name).write_bytes(data)
        hashes[name]=hashlib.sha256(data).hexdigest()
    value=dict(head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        prepared_utc=datetime.now(timezone.utc).isoformat(),server_time=now,
        target_funding=due,target_funding_utc=utc(due),nominal_end_utc=utc(now+duration*1000),
        duration_seconds=duration,study_hours=24,minimum_funding_events=3,minimum_span_ms=23*3600000,sources=hashes,runner_sha256=sha(__file__),
        settings=read(ROOT/'rebalance/historical_example.json'),filters=rules(info,'BTCUSDT'),
        preflight=observation,
        gates=['STOPPED at bounded duration','first target funding and at least three distinct funding timestamps; span at least 23 hours',
               'no pending inputs','exact offline replay of all results and portfolio',
               'no duplicate cash or fills','SQLite integrity ok','no orders','snapshot hashes unchanged'],
        failure_policy='freeze and preserve; no rerun or parameter changes on this window',
        owner='User-owned project; simulated capital only',trading_authorized=False)
    write('frozen.json',value)
    write('status.json',dict(status='PREPARED',target_funding_utc=value['target_funding_utc'],
                             nominal_end_utc=value['nominal_end_utc'],trading_authorized=False))
    print(json.dumps(value,indent=2))


def snapshot_modules():
    f=read(HERE/'frozen.json')
    assert sha(__file__) == f['runner_sha256'], 'runner changed after freeze'
    assert all(sha(RUNTIME/'code/rebalance'/name)==digest for name,digest in f['sources'].items()), 'snapshot changed'
    if any(name=='rebalance' or name.startswith('rebalance.') for name in sys.modules):
        raise ValueError('worker must start with a fresh interpreter')
    sys.path.insert(0,str(RUNTIME/'code'))
    module=importlib.import_module('rebalance.public_session')
    assert Path(module.__file__).resolve().parent == (RUNTIME/'code/rebalance').resolve()
    return f,module


def verify(f,module,source):
    from rebalance.durable import pack
    from rebalance.public_paper import Paper
    failures=[]
    if source['status'] != 'STOPPED' or source['reason'] != 'duration complete':
        failures.append('session did not complete nominal duration')
    rows=source['inputs']
    events=[r for r in rows if r['payload']['funding'] is not None and r['result'] is not None]
    if not any(any(v['fundingTime']==f['target_funding'] for v in row['payload']['funding']) for row in events):
        failures.append('target funding not reconciled')
    times={v['fundingTime'] for row in events for v in row['payload']['funding']}
    if len(times)<f['minimum_funding_events']: failures.append('fewer than three distinct funding events')
    span=rows[-1]['payload']['sample']['after']-rows[0]['payload']['sample']['after'] if rows else 0
    if span<f['minimum_span_ms']: failures.append('insufficient wall-clock observation span')
    if any(r['result'] is None for r in rows): failures.append('pending input preserved')
    replay=Paper(RUNTIME/'replay.sqlite','BTCUSDT',f['settings'],f['filters'])
    for row in rows:
        if row['result'] is None: break
        payload=row['payload']
        actual=replay.step(payload['sample'],funding=payload['funding'])
        assert actual == row['result'], 'result replay differs'
        before=replay.store.load()
        assert replay.step(payload['sample'],funding=payload['funding']) == actual
        assert replay.store.load() == before, 'duplicate changed cash/fills'
    portfolio,revision=replay.store.load()
    assert json.loads(pack(portfolio)) == source['portfolio'] and revision==source['revision']
    assert replay.store.orders() == []
    with replay.store.connection() as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    assert all(sha(RUNTIME/'code/rebalance'/n)==d for n,d in f['sources'].items())
    return dict(status='PASS' if not failures else 'FAIL',failed_gates=failures,
        observations=len(rows),revision=revision,published_funding_events=len(events),span_ms=span,
        target_funding_utc=f['target_funding_utc'],simulated_funding=str(portfolio.funding),
        real_account_income_verified=False,real_orders=0,offline_replay='exact',trading_authorized=False)


def run():
    f,module=snapshot_modules()
    if (RUNTIME/'paper.sqlite').exists():
        raise ValueError('forward window already started; automatic restart prohibited')
    write('status.json',dict(status='RUNNING',started_utc=datetime.now(timezone.utc).isoformat(),
        target_funding_utc=f['target_funding_utc'],nominal_end_utc=f['nominal_end_utc'],trading_authorized=False))
    started=time.monotonic()
    try:
        from rebalance.public_paper import Paper, rules
        reader=module.SessionReader()
        assert rules(reader.get('/fapi/v1/exchangeInfo'),'BTCUSDT') == f['filters'], 'filters changed since freeze'
        paper=Paper(RUNTIME/'paper.sqlite','BTCUSDT',f['settings'],f['filters'])
        session=module.Session(paper,reader)
        state=session.run(duration=f['duration_seconds'])
        source=session.export(HERE/'result')
        result=verify(f,module,source)
        result.update(finished_utc=datetime.now(timezone.utc).isoformat(),elapsed_seconds=time.monotonic()-started,
                      session_status=state['status'])
        write('verification.json',result)
        write('status.json',result)
    except BaseException as exc:
        write('status.json',dict(status='FAILED',reason=type(exc).__name__+': '+str(exc),
              finished_utc=datetime.now(timezone.utc).isoformat(),trading_authorized=False))
        raise


def status():
    value=read(HERE/'status.json')
    path=RUNTIME/'paper.sqlite'
    if path.exists():
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
            value['database_state']=db.execute('SELECT status,reason FROM session_state').fetchone()
            value['captured_inputs']=db.execute('SELECT count(*) FROM session_inputs').fetchone()[0]
            value['pending_inputs']=db.execute('SELECT count(*) FROM session_inputs WHERE result IS NULL').fetchone()[0]
            value['last_server_time']=db.execute('SELECT last_time FROM public_paper').fetchone()[0]
    print(json.dumps(value,indent=2))


if __name__ == '__main__':
    if len(sys.argv)!=2 or sys.argv[1] not in ('prepare','run','status'):
        raise SystemExit('usage: python -m reports.day_retry_2026-10-18.runner prepare|run|status')
    globals()[sys.argv[1]]()
