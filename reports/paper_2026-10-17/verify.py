"""Verify already completed pause/resume artifacts; never runs a backtest."""
import hashlib
import json
from pathlib import Path
import sqlite3

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]


def main():
    baseline = ROOT/'reports/historical_2026-10-17/BTCUSDT'
    complete = OUT/'completed_43200'
    paused = json.loads((OUT/'paused_21600/manifest.json').read_text())
    final = json.loads((complete/'manifest.json').read_text())
    assert paused['status'] == 'PAUSED' and paused['progress']['next_index'] == 21600
    assert final['status'] == 'COMPLETE' and final['progress']['next_index'] == 43200
    assert paused['binding'] == final['binding']
    comparisons = {}
    for name in ('result','trades','equity'):
        before = json.loads((baseline/(name+'.json')).read_text())
        after = json.loads((complete/(name+'.json')).read_text())
        comparisons[name] = before == after
        assert before == after, name+' differs'
    for name,value in final['binding']['code_sha256'].items():
        assert hashlib.sha256((ROOT/'rebalance'/name).read_bytes()).hexdigest() == value
    db = sqlite3.connect(OUT/'runtime/paper.sqlite')
    try:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        rows = db.execute('SELECT count(*) FROM paper_bars').fetchone()[0]
        events = db.execute('SELECT count(*) FROM events').fetchone()[0]
        revision = db.execute('SELECT revision FROM portfolio').fetchone()[0]
        orders = db.execute('SELECT count(*) FROM orders').fetchone()[0]
        assert rows == events == revision == 43200 and orders == 0
    finally:
        db.close()
    result = json.loads((complete/'result.json').read_text())
    evidence = dict(base_head='41dee6e27d809646b908ddd36f64500a182090a5',tests_passed=162,
        mode='PAPER_CSV_V1',paused_at=21600,resumed_to=43200,regression_identical=comparisons,
        sqlite_integrity='ok',observations=rows,events=events,orders=orders,
        baseline_net=result['net_mtm'],candidate_net=result['net_mtm'],
        source_sha256=final['binding']['code_sha256'],trading_authorized=False)
    (OUT/'verification.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    print(json.dumps(evidence,indent=2))


if __name__ == '__main__':
    main()
