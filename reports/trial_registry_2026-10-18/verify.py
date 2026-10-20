"""Retrospective registration of actual evidence; no backdated freeze claim."""
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

from rebalance.trial_registry import Registry

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]


def read(path): return json.loads(path.read_text(encoding='utf-8'))


def main():
    for name in ('result','trades','equity'):
        assert read(HERE/'fixed_BTCUSDT'/(name+'.json'))==read(ROOT/'reports/historical_2026-10-17/BTCUSDT'/(name+'.json'))
    path=HERE/'runtime/registry.sqlite'
    if path.exists(): raise ValueError('demo registry already exists')
    registry=Registry(path)
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    registry.start('fixed-BTCUSDT-current',dict(code=ROOT/'rebalance/historical.py',kernel=ROOT/'rebalance/kernel.py',
        config=ROOT/'rebalance/historical_example.json',data=ROOT/'reports/audit_2026-10-16/market_data/BTCUSDT.csv'),
        dict(head=head,recording='after completed fixed replay',data_usage='PREVIOUSLY_USED'),mode='RETROSPECTIVE')
    registry.finish('fixed-BTCUSDT-current','COMPLETED',evidence=dict(result=HERE/'fixed_BTCUSDT/result.json',
        manifest=HERE/'fixed_BTCUSDT/manifest.json'))
    old=ROOT/'reports/day_forward_2026-10-18'
    outcome=read(old/'verification.json')
    assert outcome['status']=='FAIL'
    registry.start('day-forward-failed-original',dict(code=old/'runtime/code/rebalance/public_session.py',
        config=old/'frozen.json',data=old/'result/session.json'),
        dict(recording='historical failed study, archived source snapshot',config_artifact='full frozen manifest',
             data_artifact='captured public observations and results export'),mode='RETROSPECTIVE')
    registry.finish('day-forward-failed-original','FAILED',reason='capture validation; see frozen study evidence',
                    evidence=dict(verification=old/'verification.json'))
    report=registry.report()
    assert report['total']==2 and report['counts']['FAILED']==report['counts']['COMPLETED']==1
    with registry.connection() as db: assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    (HERE/'registry_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    suite=ET.parse(HERE/'tests.xml').getroot()[0]
    assert suite.attrib['tests']=='7' and suite.attrib['failures']==suite.attrib['errors']=='0'
    result=dict(status='PASS',tests=7,head=head,regression='result/trades/equity exact match',
        registry_trials=2,retrospective_trials=2,failed_trials_visible=1,integrity='ok',
        source_sha256={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in
            ('rebalance/trial_registry.py','rebalance/scenarios/test_trial_registry.py')},
        admission_ready=False,trading_authorized=False)
    (HERE/'verification.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__': main()
