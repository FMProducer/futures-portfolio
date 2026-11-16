import importlib.util
import io
import json
import sqlite3
from pathlib import Path

import pytest


@pytest.mark.parametrize('problem', [None, 'allocation', 'receipt'])
def test_audit_checks_allocation_receipts_and_preserves_runtime_files(tmp_path, monkeypatch, problem):
    spec = importlib.util.spec_from_file_location('gap_audit', Path(__file__).resolve().parents[1] / 'scripts/audit_paper_gap.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / 'runtime'
    (root / 'core').mkdir(parents=True)
    (root / 'config.json').write_text(json.dumps(dict(paper_mode=True, funding_model='disabled', portfolios=[{}])))
    state = dict(base_ticker='TEST', started_at=100, initial_tpv=1000, strategy_bar_ms=60000,
                 strategy_origin_ms=60000, rebalance_cycles=1, virt_debt=500, valuation_price=100, last_tpv=1000)
    wallet = dict(base_ticker='TEST', balance=1000, last_price=100, positions={'TEST_LONG': 1, 'TEST_SHORT': 1},
                  long_entry_price=100, short_entry_price=100, share_fill_receipts={
                      leg: dict(symbol='TEST_' + leg, qty='1', commission='0', trade_pnl='0', reduce_only='False')
                      for leg in ('LONG', 'SHORT')})
    if problem == 'receipt':
        wallet['share_fill_receipts']['LONG']['qty'] = '2'
    for name, value in [('paper_state_TEST.json', state), ('paper_shadow_TEST.json', wallet)]:
        (root / 'core' / name).write_text(json.dumps(value))
    originals = {path: path.read_bytes() for path in root.rglob('*.json')}
    with sqlite3.connect(root / 'core/paper_budget.sqlite') as connection:
        connection.execute('CREATE TABLE allocations(ticker,capital,released,session)')
        connection.execute('INSERT INTO allocations VALUES(?,?,?,?)', ('TEST', '1000', 0, 'different' if problem == 'allocation' else '100'))
    monkeypatch.setattr(module.time, 'time', lambda: 305)
    candles = json.dumps([[t, 0, 0, 0, 100] for t in (120000, 180000, 240000, 300000)]).encode()

    class PublicHistory:
        def open(self, *args, **kwargs):
            return io.BytesIO(candles)

    monkeypatch.setattr(module.urllib.request, 'build_opener', lambda *args: PublicHistory())
    if problem:
        with pytest.raises(ValueError, match='mismatch'):
            module.audit(root, tmp_path / 'report')
    else:
        report = module.audit(root, tmp_path / 'report')
        assert report['bots'][0]['gap']['missed_minutes'] == 2
        assert report['bots'][0]['receipt_balance_residual'] == '0'
    assert all(path.read_bytes() == before for path, before in originals.items())
