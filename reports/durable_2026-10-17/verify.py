"""Compare an already completed fixed replay; does not run another backtest."""
import hashlib
from decimal import Decimal
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
BASE = ROOT/'reports/historical_2026-10-17/BTCUSDT'
CANDIDATE = OUT/'BTCUSDT'


def main():
    comparison = {}
    for name in ('result', 'trades', 'equity'):
        before = json.loads((BASE/(name+'.json')).read_text())
        after = json.loads((CANDIDATE/(name+'.json')).read_text())
        comparison[name+'_identical'] = before == after
        assert before == after, name+' differs'
    before = json.loads((BASE/'manifest.json').read_text())
    after = json.loads((CANDIDATE/'manifest.json').read_text())
    assert before['inputs'] == after['inputs'] and before['settings'] == after['settings']
    db = sqlite3.connect(OUT/'replay.sqlite')
    try:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        body, revision = db.execute('SELECT body,revision FROM portfolio').fetchone()
        state = json.loads(body)['portfolio']
        orders = [json.loads(r[0]) for r in db.execute('SELECT body FROM orders')]
        assert all(o['status'] == 'FILLED' and Decimal(o['reserved']) == 0 for o in orders)
        assert Decimal(state['order_reserve']) == 0
        event_count = db.execute('SELECT count(*) FROM events').fetchone()[0]
        assert revision == event_count
    finally:
        db.close()
    result = json.loads((CANDIDATE/'result.json').read_text())
    files = ['rebalance/kernel.py', 'rebalance/durable.py', 'rebalance/historical.py',
             'rebalance/scenarios/test_durable.py']
    evidence = dict(base_head='424ac24ec587a221e9213f0b429bf9ebfaf6fedd',
        comparison=comparison, inputs_identical=True, settings_identical=True,
        sqlite_integrity='ok', events=event_count, revision=revision, terminal_orders=len(orders),
        reserve=state['order_reserve'], baseline_net=result['net_mtm'], candidate_net=result['net_mtm'],
        source_sha256={f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in files})
    (OUT/'verification.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    print(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    main()
