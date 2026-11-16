"""Coverage for the backtest/ operator entry points and attribution branches (offline)."""
import asyncio
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BACKTEST = ROOT / 'backtest'


def _load_at(name, path, fake_file):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(fake_file)
    spec.loader.exec_module(module)
    return module


def _patch_run_backtest(monkeypatch, fake):
    import backtest.backtest_rebalance as bt
    monkeypatch.setattr(bt, 'run_backtest', fake)


def _feather(path, rows=120):
    import numpy as np
    import pandas as pd
    close = 100.0 + 0.1 * (np.arange(rows) % 50)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({'open': close, 'high': close + 0.5, 'low': close - 0.5,
                  'close': close, 'volume': np.full(rows, 10.0)}).to_feather(path)


def test_run_backtest_30d_filters_and_saves(monkeypatch, tmp_path):
    module = _load_at('run_backtest_30d', BACKTEST / 'run_backtest_30d.py',
                      tmp_path / 'backtest' / 'run_backtest_30d.py')
    data = tmp_path / 'data'
    tickers = ['AAAUSDT', 'BBBUSDT', 'CCCUSDT', 'DDDUSDT', 'EEEUSDT']
    for ticker in ('AAAUSDT', 'BBBUSDT', 'CCCUSDT', 'DDDUSDT'):
        _feather(data / f'{ticker}_live_30d.feather')
    (tmp_path / 'tickers.txt').write_text('\n'.join(tickers), encoding='utf-8')
    monkeypatch.setattr(module, 'DATA_DIR', str(data))
    monkeypatch.setattr(module, 'TICKERS_FILE', str(tmp_path / 'tickers.txt'))
    monkeypatch.setattr(module, 'CONFIG_PATH', str(tmp_path / 'config.json'))

    seen = []

    async def fake_run_backtest(config_path=None, data_dir=None, ticker_override=None,
                                live_mode=False, days=30.0, quiet=True, **kwargs):
        seen.append(ticker_override)
        if ticker_override == 'DDDUSDT':
            raise RuntimeError('engine exploded')
        good = ticker_override == 'AAAUSDT'
        return {'profit_pct': 5.0 if good else 1.0, 'max_dd_pct': 2.0 if good else 20.0,
                'cycles': 25 if good else 5, 'liquidations': 0, 'trend_guard_blocks': 1}

    monkeypatch.setattr(module, 'run_backtest', fake_run_backtest)
    asyncio.run(module.main())
    assert seen == ['AAAUSDT', 'BBBUSDT', 'CCCUSDT', 'DDDUSDT']  # EEEUSDT had no 30d data
    saved = json.loads((tmp_path / 'backtest_30d_results.json').read_text(encoding='utf-8'))
    assert [r['ticker'] for r in saved['passed']] == ['AAAUSDT']
    assert [r['ticker'] for r in saved['rejected']] == ['BBBUSDT', 'CCCUSDT']


def test_run_all_backtests_handles_success_failure_and_none(monkeypatch, tmp_path):
    module = _load_at('run_all_backtests', BACKTEST / 'run_all_backtests.py',
                      tmp_path / 'backtest' / 'run_all_backtests.py')
    monkeypatch.chdir(tmp_path)

    # early-return branch: no tickers.txt in CWD
    assert asyncio.run(module.main()) is None
    (tmp_path / 'tickers.txt').write_text('AAAUSDT\nBBBUSDT\nCCCUSDT\n', encoding='utf-8')

    async def fake_run_backtest(config_path=None, data_dir=None, ticker_override=None,
                                live_mode=True, days=3.5, quiet=True, **kwargs):
        if ticker_override == 'AAAUSDT':
            return {'profit_pct': 3.0, 'max_dd_pct': 4.0, 'cycles': 9, 'liquidations': 0,
                    'trend_guard_blocks': 1, 'trailing_stop_triggered': True}
        if ticker_override == 'BBBUSDT':
            raise RuntimeError('broken data')
        return None

    monkeypatch.setattr(module, 'run_backtest', fake_run_backtest)
    asyncio.run(module.main())


def test_48h_test_runs_all_feather_tickers(monkeypatch, tmp_path):
    # 48h_test executes main() at import time (no __main__ guard): the engine must be
    # patched on backtest.backtest_rebalance BEFORE exec_module.
    tree = tmp_path / 'tree'
    _feather(tree / 'data' / 'AAAUSDT.feather')
    _feather(tree / 'data' / 'BBBUSDT_12m.feather')
    _feather(tree / 'data' / 'CCCUSDT.feather')

    def fake_strict(mapping):
        async def fake(config_path=None, data_dir=None, ticker_override=None,
                       live_mode=False, days=2, quiet=False, **kwargs):
            return mapping[ticker_override]  # KeyError -> except branch

        return fake

    def fake_lenient(mapping):
        async def fake(config_path=None, data_dir=None, ticker_override=None,
                       live_mode=False, days=2, quiet=False, **kwargs):
            return mapping.get(ticker_override)  # None -> failed branch

        return fake

    base = {'AAAUSDT': None,  # None -> failed branch
            'BBBUSDT': {'profit_pct': 2.0, 'max_dd_pct': 3.0, 'cycles': 5,
                        'siphoning_reserve': 1.0, 'commission_paid': 0.5,
                        'skipped_expansions': 1, 'trailing_stops': 1, 'liquidations': 0,
                        'trend_guard_blocks': 1, 'velocity_guard_blocks': 0,
                        'trend_add_blocks': 0, 'exchange_fill_count': 2,
                        'share_trail_consumes': 1, 'trailing_stop_triggered': False}}
    _patch_run_backtest(monkeypatch, fake_strict(base))
    module = _load_at('48h_test', BACKTEST / '48h_test.py', tree / 'backtest' / '48h_test.py')
    # second exec: all tickers resolve (success + None), no exception branch
    _patch_run_backtest(monkeypatch, fake_lenient({**base, 'CCCUSDT': None}))
    module2 = _load_at('48h_test_b', BACKTEST / '48h_test.py', tree / 'backtest' / '48h_test.py')
    assert module is not None and module2 is not None


def test_dashboard_attribution_branches():
    from backtest.dashboard_attribution import reconstruct_attribution
    n = 40
    closes = [100.0 + i * 0.1 for i in range(n)]
    equity = [1000.0 + i * 0.5 for i in range(n)]

    # legacy constant funding with open positions + cooldown mark-to-close + siphoning
    fills = [
        {'bar': 2, 'side': 'BUY', 'leg': 'BASE_LONG', 'qty': 1.0, 'price': 100.5, 'mid': 100.0,
         'commission': 0.05, 'realized_pnl': 0.0, 'tpv': 1000.0},
        {'bar': 20, 'side': 'SELL', 'leg': 'BASE_LONG', 'qty': 2.0, 'price': 102.0, 'mid': 101.8,
         'commission': 0.04, 'realized_pnl': -1.0, 'tpv': 1008.0},  # unmatched close (2 > 1)
        {'bar': 30, 'side': 'BUY', 'leg': 'BASE_SHORT', 'qty': 0.0, 'price': 100.0,
         'commission': 0.0},  # zero qty
        {'bar': 999, 'side': 'BUY', 'leg': 'BASE_LONG', 'qty': 1.0, 'price': 100.0,
         'commission': 0.0},  # out of range, ignored
        {'bar': 31, 'side': 'BUY', 'leg': 'BASE_LONG', 'qty': 1.0, 'price': 100.0,
         'mid': None, 'commission': 0.0},  # mid None -> price
    ]
    events = [
        {'bar': 5, 'kind': 'cooldown_start', 'until_bar': 8, 'reason': 'dd'},
        {'bar': 8, 'kind': 'cooldown_end', 'reserve_reset': 2.0},
        {'bar': 3, 'kind': 'funding', 'funding_amount': 0.5},
        {'bar': 4, 'kind': 'funding', 'detail': 'id amount=0.25'},
        {'bar': 5, 'kind': 'funding', 'detail': 'id amount=not-a-number'},
    ]
    out = reconstruct_attribution(closes, equity, fills, initial_capital=1000.0,
                                   final_equity=equity[-1], funding_rate_per_bar=0.0001,
                                   funding_model='legacy_constant', funding_source='portfolio',
                                   events=events, siphoning_log=[{'bar': 6, 'amount': 3.0}],
                                   sample_indices={0, 5, 9, 19, 29, 35, 39})
    checks = out['checks']
    assert checks['unmatched_close_qty'] == 2.0  # bar-20 SELL of 2.0 with 0 open long
    assert checks['open_long'] == 1.0  # re-opened at bar 31 after the cooldown close
    assert out['totals']['reserve'] == pytest.approx(1.0)  # 3.0 siphon - 2.0 cooldown reset
    assert len(out['series']['idx']) == 7

    # signed funding: detail fallback is parsed, unparseable detail skipped;
    # the stored funding cost is the negated signed amount
    out2 = reconstruct_attribution(closes, equity, fills, initial_capital=1000.0,
                                   funding_model='signed_actual', funding_source='portfolio',
                                   events=events)
    assert out2['totals']['funding'] == pytest.approx(-0.75)

    # zero-window contract: final_unrealized is initialized before the loop, so an empty
    # window (n == 0) returns unrealized=0.0 and residual=0.0 without UnboundLocalError
    out = reconstruct_attribution([], [], [], initial_capital=1000.0, final_equity=1000.0)
    assert out['totals']['unrealized'] == 0.0
    assert out['totals']['residual'] == 0.0
    assert out['totals']['final_equity'] == 1000.0
