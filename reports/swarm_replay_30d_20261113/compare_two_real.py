"""Compare two independently verified integrated shared-account runs."""
import json
from pathlib import Path
import sys


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def selections(folder):
    return [json.loads(line) for line in (folder/'events.jsonl').open(encoding='utf-8')
            if '"kind": "selection_policy"' in line]


def main():
    a,b = [Path(name).resolve() for name in sys.argv[1:]]
    av,bv = read(a/'two_real_verification.json'),read(b/'two_real_verification.json')
    assert av['status'] == bv['status'] == 'PASS'
    checks = dict(exchange_and_paper_fills=(a/'fills.jsonl').read_bytes() == (b/'fills.jsonl').read_bytes(),
        frozen_sources=av['source_hashes'] == bv['source_hashes'],
        frozen_data=av['data_sha256'] == bv['data_sha256'],
        synchronized_equity=(a/'equity.csv').read_bytes() == (b/'equity.csv').read_bytes(),
        selection_decisions=selections(a) == selections(b),
        paper_ledger=read(a/'paper_budget_events.json') == read(b/'paper_budget_events.json'),
        real_allocations=read(a/'core/real_allocations.json') == read(b/'core/real_allocations.json'),
        independent_real_results=av['per_ticker'] == bv['per_ticker'],
        financial_checkpoints=[read(p/'clock.json')['financial_sha256'] for p in sorted((a/'checkpoints').iterdir())]
            == [read(p/'clock.json')['financial_sha256'] for p in sorted((b/'checkpoints').iterdir())])
    assert all(checks.values()), checks
    result = dict(status='PASS',checks=checks,runs=[str(a),str(b)],
                  source_hashes=av['source_hashes'],scope='Two independent completed integrated historical simulations; no trading-state or pure-gate cache reused')
    (a.parent/'two_real_reproducibility.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))


if __name__ == '__main__':
    main()
