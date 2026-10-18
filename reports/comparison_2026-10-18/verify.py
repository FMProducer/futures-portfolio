"""Read-only verification of the single fixed matched comparison."""
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]


def read(path): return json.loads(path.read_text(encoding='utf-8'))


def main():
    run=HERE/'fixed_BTCUSDT'
    for name in ('result','trades','equity'):
        assert read(run/('candidate_'+name+'.json'))==read(ROOT/'reports/historical_2026-10-17/BTCUSDT'/(name+'.json'))
    candidate,control=read(run/'candidate_trades.json'),read(run/'hold_trades.json')
    assert candidate[:2]==control and len(control)==2
    assert [r['time'] for r in read(run/'candidate_equity.json')]==[r['time'] for r in read(run/'hold_equity.json')]
    manifest=read(run/'manifest.json')
    for name,digest in manifest['code'].items():
        assert hashlib.sha256((ROOT/'rebalance'/name).read_bytes()).hexdigest()==digest
    for name,digest in manifest['inputs'].items():
        assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==digest
    suite=ET.parse(HERE/'tests.xml').getroot()[0]
    assert suite.attrib['tests']=='18' and suite.attrib['failures']==suite.attrib['errors']=='0'
    result=dict(status='PASS',head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        tests=18,candidate_regression='result/trades/equity exact match',initial_fills='exact match',
        valuation_timestamps='exact match',hold_fills=2,data_usage='PREVIOUSLY_USED',
        test_sha256=hashlib.sha256((ROOT/'rebalance/scenarios/test_comparison.py').read_bytes()).hexdigest(),
        admission_ready=False,trading_authorized=False)
    (HERE/'verification.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__': main()
