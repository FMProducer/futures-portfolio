"""Verify completed three-mode runs and reconcile external funding separately."""
from decimal import Decimal as D
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(sys.argv[1]).resolve()


def read(name):
    return json.loads((HERE/name).read_text(encoding='utf-8'))


def maximum_error(left, right):
    assert len(left) == len(right) == 525600
    value = float(np.max(np.abs(np.array(left)-right)))
    assert value < 1e-8, value
    return value


if __name__ == '__main__':
    b, p, r = [read(name) for name in ('backtest_audited.json', 'paper_verified.json', 'real_verified.json')]
    audited = read('backtest_audited.json')
    plain = read('backtest_final.json')
    assert plain['equity_curve'] == b['equity_curve'] and plain['fills'] == b['fills']
    assert b['accounting_ok'] and b['status'] == 'completed'
    assert b['equity_curve'] == audited['equity_curve'] and b['fills'] == audited['fills']
    assert not audited['bucket_audit']['negative_samples']
    assert D(audited['bucket_audit']['min_cash']) >= 0 and D(audited['bucket_audit']['min_virtual']) >= 0
    pcurve, rcurve = read('paper_verified_curve.json'), read('real_verified_curve.json')
    equity_errors = dict(backtest_paper=maximum_error(b['equity_curve'], pcurve),
                         backtest_real=maximum_error(b['equity_curve'], rcurve))
    pf, rf = read('paper_verified_fills.json'), read('real_verified_fills.json')
    bf = [fill for fill in b['fills'] if fill['leg'] in ('BASE_LONG', 'BASE_SHORT')]
    assert len(bf) == len(pf) == len(rf) == b['exchange_fill_count']
    maximum_pnl_error = 0.
    for a, c, e in zip(bf, pf, rf):
        assert a['bar'] == c['bar'] == e['bar'] and a['side'] == c['side'] == e['side']
        assert a['leg'].replace('BASE_', '') == c['type'] == e['symbol'].split('_')[-1]
        assert D(str(a['qty'])) == D(c['qty']) == D(e['qty'])
        for x, y, z in [('price', 'price', 'price'), ('commission', 'commission', 'commission'),
                        ('realized_pnl', 'trade_pnl', 'pnl')]:
            delta = max(abs(float(a[x])-float(c[y])), abs(float(a[x])-float(e[z])))
            assert delta < 1e-8, (a['bar'], x, delta)
            if x == 'realized_pnl': maximum_pnl_error = max(maximum_pnl_error, delta)
    assert p['cycles'] == r['cycles'] == b['cycles']
    assert p['stops'] == r['stops'] == b['trailing_stops']
    for result in (p, r):
        assert D(result['min_cash']) >= 0 and D(result['min_virtual']) >= 0
        assert abs(result['funding']-b['funding_paid']) < 1e-8
        assert abs(float(result['funding_reserve'])-b['cash_reserve_usdt']) < 1e-8
    assert p['config_sha256'] == r['config_sha256'] == hashlib.sha256((HERE/'config.json').read_bytes()).hexdigest()
    assert p['data_sha256'] == r['data_sha256']
    paper_events = read('paper_verified_funding.json')
    real_events = read('real_verified_funding_receipts.json')
    income = {f"binance:FUNDING_FEE:{row['tranId']}": row for row in read('real_verified_funding.json') if D(row['income'])}
    assert len(paper_events) == len(real_events) == len(income) == r['funding_events'] == p['funding_events']
    assert len({event['event_id'] for event in real_events}) == len(real_events)
    for pe, re in zip(paper_events, real_events):
        raw = income[re['event_id']]
        assert pe['status'] == re['status'] == 'applied'
        assert D(str(pe['signed_amount'])) == D(str(re['signed_amount'])) == D(raw['income'])
        assert pe['event_time']+60 == re['event_time'] == raw['time']/1000
    cashflows = sum((D(row['income']) for row in income.values()), D(0))
    assert abs(D(str(r['funding_reserve']))-D(5000)-cashflows) < D('1e-18')
    worker_wallet = D(1000)+sum((D(fill['pnl'])-D(fill['commission']) for fill in rf), D(0))
    residual = worker_wallet-D(str(r['final_wallet']['balance']))
    assert abs(residual) < D('1e-8')
    after_funding = b['final_tpv']-1000-b['funding_paid']
    assert abs(after_funding-b['portfolio_profit_after_funding']) < 1e-8
    assert r['cash_guard_block_messages'] == 0
    output = dict(head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        period=['2025-10-20 00:00 UTC', '2026-10-19 23:59 UTC'], bars=525600,
        config_sha256=p['config_sha256'], data_sha256=p['data_sha256'],
        matched_fills=len(bf), matched_funding_events=len(real_events), equity_errors=equity_errors,
        maximum_pnl_error=maximum_pnl_error, independent_real_wallet_residual=str(residual),
        min_cash=dict(backtest=audited['bucket_audit']['min_cash'], paper=p['min_cash'], real=r['min_cash']),
        min_virtual=dict(backtest=audited['bucket_audit']['min_virtual'], paper=p['min_virtual'], real=r['min_virtual']),
        trading_profit_pct=b['profit_pct'], trading_max_dd_pct=b['max_dd_pct'], cycles=b['cycles'],
        stops=b['trailing_stops'], funding_paid=b['funding_paid'], external_reserve=b['cash_reserve_usdt'],
        profit_after_external_funding_usdt=after_funding, profit_after_external_funding_pct=after_funding/10,
        real_journal_adapter=r['journal_adapter'])
    files = subprocess.check_output(['git', 'diff', '--name-only'], cwd=ROOT, text=True).splitlines()
    files += ['scripts/verify_real_runtime_parity.py', 'tests/test_annual_replay_driver.py', 'docs/FUNDING_EXTERNAL_RESERVE_PARITY_2026_11_06.md']
    files.append('scripts/verify_agt_external_funding_results.py')
    output['source_sha256'] = {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in files}
    (HERE/'candidate.patch').write_bytes(subprocess.check_output(['git', 'diff', '--binary'], cwd=ROOT))
    (HERE/'verification.json').write_text(json.dumps(output, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in output.items() if k != 'source_sha256'}, indent=2))
