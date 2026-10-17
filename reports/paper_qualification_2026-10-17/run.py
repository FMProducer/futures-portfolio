"""Frozen paper qualification. Production sources and economic settings stay fixed."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal as D
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from rebalance.durable import encode, pack
from rebalance.public_paper import Paper, rules
from rebalance.public_session import Session, SessionReader, exclusive

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
SOURCES=('rebalance/public_paper.py','rebalance/public_session.py','rebalance/kernel.py',
         'rebalance/durable.py','rebalance/binance_readonly.py',
         'rebalance/historical_example.json','reports/paper_qualification_2026-10-17/run.py')
BASE=1800000000000
PERIOD=480*60000


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(name,value):
    (HERE/name).write_text(json.dumps(value,indent=2),encoding='utf-8')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def head():
    return subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()


def frozen():
    value=read(HERE/'frozen.json')
    assert head() == value['head'], 'HEAD changed after freeze'
    assert all(sha(ROOT/name) == digest for name,digest in value['sources'].items()), 'source changed after freeze'
    return value


def freeze():
    if (HERE/'frozen.json').exists():
        raise ValueError('protocol already frozen; do not replace it')
    info=SessionReader().get('/fapi/v1/exchangeInfo')
    settings=read(ROOT/'rebalance/historical_example.json')
    value=dict(frozen_utc=datetime.now(timezone.utc).isoformat(),head=head(),
        sources={name:sha(ROOT/name) for name in SOURCES},settings=settings,
        filters=rules(info,'BTCUSDT'),symbol='BTCUSDT',
        live=dict(duration_seconds=300,min_observation_span_ms=240000,min_applied_minutes=5,
                  required_status='STOPPED',require_no_pending_inputs=True),
        virtual=dict(observations=1441,minute_spacing=1,funding_every_minutes=480,
                     funding_rate='0.0001',mark='100',bid='99.9',ask='100.1',top_qty='100'),
        faults=['after_inbox_commit','after_portfolio_commit','inside_funding_transaction'],
        acceptance=['unchanged code/settings','exact offline replay','no duplicate fills or cash',
                    'SQLite integrity ok','no orders','long gap/UNKNOWN remains blocked'],
        stop_policy='Report any failed gate without changing parameters or rerunning the forward window',
        not_tested=['24h wall-clock live operation','real funding receipt','real execution','OOS profitability'],
        trading_authorized=False)
    write('frozen.json',value)
    print(encode(dict(status='FROZEN',head=value['head'],protocol_sha=sha(HERE/'frozen.json'))))


def paper(path,f):
    return Paper(path,'BTCUSDT',f['settings'],f['filters'])


def sample(stamp,due):
    return dict(symbol='BTCUSDT',before=stamp-1,after=stamp,
        mark=dict(symbol='BTCUSDT',time=stamp-1,markPrice='100',nextFundingTime=due),
        book=dict(symbol='BTCUSDT',time=stamp-1,bidPrice='99.9',askPrice='100.1',bidQty='100',askQty='100'))


def funding(stamp):
    return [dict(symbol='BTCUSDT',fundingTime=stamp,fundingRate='0.0001',markPrice='100',rateType='Regular')]


def crash_payload():
    return dict(sample=sample(BASE+60001,BASE+PERIOD),funding=funding(BASE+60000))


def seed(path,f):
    p=paper(path,f)
    p.step(sample(BASE,BASE+60000))
    return p


def worker(case,path):
    f=frozen()
    p=paper(path,f)
    session=Session(p,None)
    with exclusive(path):
        payload=crash_payload()
        session.enqueue(payload)
        if case == 'inbox': os._exit(71)
        if case == 'transaction':
            original=p.store.connection
            @contextmanager
            def connection():
                with original() as db:
                    db.create_function('crash_process',0,lambda:os._exit(73))
                    yield db
            p.store.connection=connection
            with p.store.connection() as db:
                db.execute('CREATE TRIGGER injected_crash BEFORE INSERT ON events BEGIN SELECT crash_process(); END')
        p.step(payload['sample'],funding=payload['funding'])
        os._exit(72)


def check_integrity(p):
    with p.store.connection() as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert p.store.orders() == []


def faults():
    f=frozen()
    target=HERE/'runtime/faults'
    target.mkdir(parents=True,exist_ok=False)
    control=seed(target/'control.sqlite',f)
    payload=crash_payload()
    expected=control.step(payload['sample'],funding=payload['funding'])
    assert D(expected['funding_delta']) != 0
    result=[]
    for case,exit_code in (('inbox',71),('commit',72),('transaction',73)):
        path=target/(case+'.sqlite')
        p=seed(path,f)
        before=p.store.load()
        completed=subprocess.run([sys.executable,'-m','reports.paper_qualification_2026-10-17.run',
            'worker','--case',case,'--database',str(path)],cwd=ROOT,capture_output=True,text=True,timeout=30)
        assert completed.returncode == exit_code, completed.stderr
        with exclusive(path): pass # process death released its OS lock
        if case != 'commit': assert p.store.load() == before
        with p.store.connection() as db:
            if case == 'transaction': db.execute('DROP TRIGGER injected_crash')
            assert db.execute('SELECT count(*) FROM session_inputs WHERE result IS NULL').fetchone()[0] == 1
        session=Session(p,None)
        session.recover()
        assert p.store.load() == control.store.load()
        state=p.store.load()
        session.recover()
        assert p.store.load() == state
        check_integrity(p)
        result.append(dict(case=case,exit_code=completed.returncode,status='PASS',
                           revision=state[1],funding=str(state[0].funding),pending=0))
    write('crash_results.json',dict(status='PASS',cases=result,protocol_sha=sha(HERE/'frozen.json')))
    print(encode(result))


def virtual():
    f=frozen()
    path=HERE/'runtime/virtual.sqlite'
    if path.exists(): raise ValueError('virtual campaign already exists')
    p=paper(path,f)
    session=Session(p,None)
    count=0
    for minute in range(1441):
        stamp=BASE+minute*60000
        due=BASE+(minute//480+1)*PERIOD
        payload=dict(sample=sample(stamp,due),funding=funding(stamp) if minute and minute%480 == 0 else None)
        session.enqueue(payload)
        session.recover()
        if minute%100 == 0:
            before=p.store.load()
            assert p.step(payload['sample'],funding=payload['funding'])
            assert p.store.load() == before
        if payload['funding']: count+=1
        if minute == 720:
            p=paper(path,f)
            session=Session(p,None)
    state,revision=p.store.load()
    assert revision == 1441 and count == 3
    assert len(state.receipts) == 5 # two entry fills and three funding cash receipts
    with p.store.connection() as db:
        assert db.execute('SELECT count(*) FROM session_inputs WHERE result IS NULL').fetchone()[0] == 0
    check_integrity(p)
    write('virtual_results.json',dict(status='PASS',virtual_hours=24,wall_clock_hours_tested=0,
        observations=1441,funding_events=count,revision=revision,receipts=len(state.receipts),
        funding=str(state.funding),fees=str(state.fees),portfolio=json.loads(pack(state)),
        protocol_sha=sha(HERE/'frozen.json'),trading_authorized=False))
    print(encode(dict(status='PASS',virtual_hours=24,observations=1441,funding_events=count)))


def live():
    f=frozen()
    reader=SessionReader()
    assert rules(reader.get('/fapi/v1/exchangeInfo'),'BTCUSDT') == f['filters'], 'filters changed after freeze'
    path=HERE/'runtime/live.sqlite'
    if path.exists(): raise ValueError('forward window already started; no automatic rerun')
    session=Session(paper(path,f),reader)
    state=session.run(duration=f['live']['duration_seconds'])
    session.export(HERE/'live')
    print(encode(state))
    if state['status'] != 'STOPPED': raise SystemExit(2)


def verify():
    f=frozen()
    source=read(HERE/'live/session.json')
    rows=source['inputs']
    failures=[]
    span=rows[-1]['payload']['sample']['after']-rows[0]['payload']['sample']['after'] if rows else 0
    minutes=sum(r['result'] is not None and r['result']['status']=='APPLIED' for r in rows)
    if source['status'] != f['live']['required_status']: failures.append('live status '+source['status'])
    if span < f['live']['min_observation_span_ms']: failures.append('insufficient live observation span')
    if minutes < f['live']['min_applied_minutes']: failures.append('insufficient applied minutes')
    if any(r['result'] is None for r in rows): failures.append('pending captured inputs')
    path=HERE/'runtime/replay.sqlite'
    if path.exists(): raise ValueError('verification replay already exists')
    p=paper(path,f)
    for row in rows:
        if row['result'] is None: break
        payload=row['payload']
        result=p.step(payload['sample'],funding=payload['funding'])
        assert result == row['result']
        before=p.store.load()
        assert p.step(payload['sample'],funding=payload['funding']) == result
        assert p.store.load() == before
    assert json.loads(pack(p.store.load()[0])) == source['portfolio']
    assert p.store.load()[1] == source['revision']
    check_integrity(p)
    assert read(HERE/'crash_results.json')['status'] == read(HERE/'virtual_results.json')['status'] == 'PASS'
    result=dict(status='FAIL' if failures else 'PASS',failed_gates=failures,
        protocol_sha=sha(HERE/'frozen.json'),live_observations=len(rows),span_ms=span,
        applied_minutes=minutes,simulated_fills=sum(len(r['result']['fills']) for r in rows if r['result']),
        offline_replay='identical results/portfolio/revision; duplicate no-op',
        actual_live_funding_events=sum(r['payload']['funding'] is not None for r in rows),
        actual_live_read_retries=sum(r['status']=='RETRYING' for r in source['log']),
        virtual_day='PASS',process_crash_cases=3,real_orders=0,trading_authorized=False)
    write('verification.json',result)
    print(json.dumps(result,indent=2))
    if failures: raise SystemExit(2)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('freeze','faults','virtual','live','verify','worker'))
    parser.add_argument('--case',choices=('inbox','commit','transaction'))
    parser.add_argument('--database',type=Path)
    args=parser.parse_args()
    if args.action == 'worker': worker(args.case,args.database)
    else: globals()[args.action]()


if __name__ == '__main__': main()
