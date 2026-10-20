"""Verify fixed replay and capture-fix candidate provenance without rerunning."""
import difflib
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]


def read(path): return json.loads(path.read_text(encoding='utf-8'))


def main():
    for name in ('result','trades','equity'):
        assert read(HERE/'fixed_BTCUSDT'/(name+'.json'))==read(ROOT/'reports/historical_2026-10-17/BTCUSDT'/(name+'.json'))
    suite=ET.parse(HERE/'tests.xml').getroot()[0]
    assert suite.attrib['tests']=='224' and suite.attrib['failures']==suite.attrib['errors']=='0'
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    paths=('rebalance/public_paper.py','rebalance/public_session.py','rebalance/scenarios/test_capture_recovery.py')
    patches=[]
    hashes={}
    for name in paths:
        data=(ROOT/name).read_bytes()
        hashes[name]=hashlib.sha256(data).hexdigest()
        base=subprocess.run(['git','show',head+':'+name],cwd=ROOT,capture_output=True)
        patches.extend(difflib.unified_diff(base.stdout.decode().splitlines(keepends=True) if base.returncode==0 else [],
            data.decode().splitlines(keepends=True),fromfile='a/'+name,tofile='b/'+name))
    frozen=read(ROOT/'reports/day_retry_2026-10-18/frozen.json')
    for name in ('public_paper.py','public_session.py'):
        assert hashes['rebalance/'+name]==frozen['sources'][name]
    (HERE/'code.patch').write_text(''.join(patches),encoding='utf-8')
    report=dict(status='PASS',tests=224,head=head,code_sha256=hashes,
        regression='result/trades/equity exact match',new_snapshot_matches_candidate=True,
        original_failure_cause='generic capture validation; exact failed public payload unavailable',
        real_orders=0,trading_authorized=False)
    (HERE/'verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__': main()
