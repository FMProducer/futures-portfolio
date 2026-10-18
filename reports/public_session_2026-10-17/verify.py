"""Verify live captured observations by replaying them offline in a fresh DB."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import xml.etree.ElementTree as ET

from rebalance.durable import pack
from rebalance.public_paper import Paper
from rebalance.public_session import Session

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    source=read(HERE/'live/session.json')
    assert source['status'] == 'STOPPED'
    binding=source['binding']['paper']
    for name,digest in binding['code'].items():
        assert hashlib.sha256((ROOT/'rebalance'/name).read_bytes()).hexdigest() == digest
    assert hashlib.sha256((ROOT/'rebalance/public_session.py').read_bytes()).hexdigest() == source['binding']['session_sha256']
    values=source['inputs']
    assert values and all(r['result'] is not None for r in values)
    span=values[-1]['payload']['sample']['after']-values[0]['payload']['sample']['after']
    assert span >= 60000
    assert sum(r['result']['status'] == 'APPLIED' for r in values) >= 2
    with tempfile.TemporaryDirectory() as directory:
        path=Path(directory)/'paper.sqlite'
        for row in values:
            p=Paper(path,binding['symbol'],binding['settings'],binding['filters'])
            session=Session(p,None)
            session.enqueue(row['payload'])
            session.recover()
            revision=p.store.load()[1]
            session.recover()
            assert p.step(row['payload']['sample'],funding=row['payload']['funding']) == row['result']
            assert p.store.load()[1] == revision
        portfolio,revision=p.store.load()
        assert json.loads(pack(portfolio)) == source['portfolio']
        assert revision == source['revision']
        assert p.store.orders() == []
        with closing(sqlite3.connect(path)) as db:
            assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    for name in ('result','trades','equity'):
        assert read(HERE/'fixed_BTCUSDT'/(name+'.json')) == read(ROOT/'reports/historical_2026-10-17/BTCUSDT'/(name+'.json'))
    suite=ET.parse(HERE/'tests.xml').getroot()[0]
    assert suite.attrib['failures'] == suite.attrib['errors'] == '0'
    result=dict(status='PASS',tests=int(suite.attrib['tests']),observations=len(values),span_ms=span,
        applied_minutes=sum(r['result']['status'] == 'APPLIED' for r in values),
        simulated_fills=sum(len(r['result']['fills']) for r in values),
        network_retries=sum(r['status']=='RETRYING' for r in source['log']),
        actual_funding_crossings=sum(r['payload']['funding'] is not None for r in values),
        offline_replay='identical portfolio/results/revision; no duplicate fills',
        regression='result/trades/equity exact match',real_orders=0,trading_authorized=False)
    (HERE/'verification.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__ == '__main__':
    main()
