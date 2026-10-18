"""Verify the one registered fixed study without recomputing its backtest."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from rebalance.trial_registry import Registry, artifacts

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]


def read(path): return json.loads(path.read_text(encoding='utf-8'))


def main():
    output=HERE/'fixed_BTCUSDT'
    for name in ('result','trades','equity'):
        assert read(output/('candidate_'+name+'.json'))==read(ROOT/'reports/historical_2026-10-17/BTCUSDT'/(name+'.json'))
        assert read(output/('hold_'+name+'.json'))==read(ROOT/'reports/comparison_2026-10-18/fixed_BTCUSDT'/('hold_'+name+'.json'))
    registry=Registry(HERE/'runtime/trials.sqlite')
    trial=registry.report()['trials'][0]
    assert trial['outcome']['status']=='COMPLETED' and trial['snapshot']['mode']=='PROSPECTIVE'
    paths={k:v['path'] for k,v in trial['snapshot']['artifacts'].items()}
    assert artifacts(paths)==trial['snapshot']['artifacts']
    assert artifacts({k:v['path'] for k,v in trial['outcome']['evidence'].items()})==trial['outcome']['evidence']
    manifest=read(output/'execution_manifest.json')
    assert manifest['artifacts']==trial['snapshot']['artifacts']
    assert manifest['executed_module']==str(output/'frozen/code/rebalance/research_runner.py')
    suite=ET.parse(HERE/'tests.xml').getroot()[0]
    assert suite.attrib['tests']=='13' and suite.attrib['failures']==suite.attrib['errors']=='0'
    with registry.connection() as db: assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    result=dict(status='PASS',tests=13,source_head=trial['snapshot']['metadata']['source_head'],
        candidate_regression='result/trades/equity exact match',hold_regression='result/trades/equity exact match',
        outcome='COMPLETED',recording_mode='PROSPECTIVE',isolated_import_verified=True,
        artifact_and_evidence_hashes_match=True,data_usage='PREVIOUSLY_USED',
        source_sha256={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
            ('rebalance/research_runner.py','rebalance/scenarios/test_research_runner.py')},
        admission_ready=False,trading_authorized=False)
    (HERE/'verification.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__': main()
