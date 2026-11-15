"""Offline branch coverage for the asymmetric threshold optimizer and research runner."""
import asyncio
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _two_row_df(growth_factor):
    import pandas as pd
    return pd.DataFrame({'close': [100.0, 100.0 * growth_factor]})


def _patch_read_feather(monkeypatch, by_stem):
    import pandas as pd

    def fake_read_feather(path, *args, **kwargs):
        return _two_row_df(by_stem[Path(path).stem])

    monkeypatch.setattr(pd, 'read_feather', fake_read_feather)


def _patch_executor(monkeypatch, ot, n_tickers, outcomes):
    """Emulate main()'s per-pair/per-ticker run_in_executor call order."""
    counter = {'n': 0}

    class FakeExecutor:
        def __init__(self, max_workers=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def counting_run_in_executor(executor, func, *args, **kwargs):
        n = counter['n']
        counter['n'] += 1
        pair = ot.THRESHOLD_PAIRS[n // n_tickers]
        results = outcomes.get(pair, [])
        value = results[n % n_tickers] if n % n_tickers < len(results) else None
        fut = asyncio.get_running_loop().create_future()
        fut.set_result(value)
        return fut

    monkeypatch.setattr(ot, 'ProcessPoolExecutor', FakeExecutor)
    monkeypatch.setattr(asyncio.BaseEventLoop, 'run_in_executor', counting_run_in_executor)


def test_optimize_threshold_missing_config_returns(monkeypatch, tmp_path):
    import optimization.optimize_threshold as ot
    monkeypatch.setattr('sys.argv', ['optimize_threshold', '--config', str(tmp_path / 'nope.json')])
    asyncio.run(ot.main())
    assert not (tmp_path / 'nope.json').exists()


def test_optimize_threshold_no_tickers_returns(monkeypatch, tmp_path):
    import optimization.optimize_threshold as ot
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'tickers': []}), encoding='utf-8')
    monkeypatch.setattr('sys.argv', ['optimize_threshold', '--config', str(config)])
    asyncio.run(ot.main())


def test_optimize_threshold_non_debug_writes_config(monkeypatch, tmp_path):
    import optimization.optimize_threshold as ot
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'tickers': ['AAAUSDT', 'BBBUSDT'],
                                  'portfolios': [{'rebalance_threshold': 0.02,
                                                   'ticker_thresholds': {'AAAUSDT': 0.01}}]}),
                      encoding='utf-8')
    monkeypatch.setattr('sys.argv', ['optimize_threshold', '--config', str(config)])
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    monkeypatch.setattr(ot, '__file__', str(data_dir / 'optimize_threshold.py'))
    _patch_read_feather(monkeypatch, {'AAAUSDT_live': 1.05, 'BBBUSDT_live': 1.02})

    outcomes = {(0.008, 0.01): [{'profit_pct': 5.0, 'cycles': 12, 'max_dd_pct': 3.0},
                                {'profit_pct': 4.0, 'cycles': 10, 'max_dd_pct': 4.0}]}
    _patch_executor(monkeypatch, ot, 2, outcomes)
    asyncio.run(ot.main())
    written = json.loads(config.read_text(encoding='utf-8'))
    portfolio = written['portfolios'][0]
    assert portfolio['rebalance_threshold_surplus'] == 0.008
    assert portfolio['rebalance_threshold_deficit'] == 0.01
    assert 'rebalance_threshold' not in portfolio
    assert portfolio['ticker_thresholds']['BBBUSDT'] == {'surplus': 0.008, 'deficit': 0.01}


def test_optimize_threshold_all_null_results_returns(monkeypatch, tmp_path):
    import optimization.optimize_threshold as ot
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'tickers': ['AAAUSDT'], 'portfolios': [{}]}), encoding='utf-8')
    monkeypatch.setattr('sys.argv', ['optimize_threshold', '--config', str(config)])
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    monkeypatch.setattr(ot, '__file__', str(data_dir / 'optimize_threshold.py'))
    _patch_read_feather(monkeypatch, {'AAAUSDT_live': 1.0})
    _patch_executor(monkeypatch, ot, 1, {})
    asyncio.run(ot.main())
    assert 'rebalance_threshold_surplus' not in json.loads(config.read_text(encoding='utf-8'))['portfolios'][0]


def test_research_runner_validation_and_worker_guard(tmp_path):
    import rebalance.research_runner as rr
    candles = tmp_path / 'c.csv'
    candles.write_text("open_time,open,high,low,close\n"
                       "1699999980000,100,101,99,100\n"
                       "1700000040000,100,102,99,101\n", encoding='utf-8')
    config = tmp_path / 'c.json'
    config.write_text(json.dumps({"strategy": {}, "capital": "100",
                                  "spread_bps": "0", "slippage_bps": "0"}), encoding='utf-8')
    funding = tmp_path / 'funding.csv'
    funding.write_text("timestamp,rate,mark_price\n1699999980000,0.0001,100\n", encoding='utf-8')
    with pytest.raises(ValueError, match='terminal_closeout must be boolean'):
        rr.run_study(tmp_path / 'db.sqlite', 't', candles, config, tmp_path / 'out1',
                     funding=None, funding_zero=False, terminal_closeout='yes')
    with pytest.raises(ValueError, match='choose funding'):
        rr.run_study(tmp_path / 'db.sqlite', 't', candles, config, tmp_path / 'out2',
                     funding=funding, funding_zero=True, terminal_closeout=False)
    with pytest.raises(ValueError, match='choose funding'):
        rr.run_study(tmp_path / 'db.sqlite', 't', candles, config, tmp_path / 'out3',
                     funding=None, funding_zero=False, terminal_closeout=False)
    with pytest.raises(ValueError, match='isolated snapshot import'):
        rr.worker(tmp_path / 'db.sqlite', 't', tmp_path / 'out4')


def _study_inputs(tmp_path, strategy):
    candles = tmp_path / 'candles.csv'
    candles.write_text("open_time,open,high,low,close\n"
                       "1699999980000,100,101,99,100\n"
                       "1700000040000,100,102,99,101\n"
                       "1700000100000,101,103,100,102\n", encoding='utf-8')
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({"strategy": strategy, "capital": "10000",
                                  "spread_bps": "5", "slippage_bps": "5"}), encoding='utf-8')
    return candles, config


def test_research_runner_failed_study(tmp_path):
    # Invalid strategy dict makes the isolated worker subprocess raise -> FAILED outcome.
    import rebalance.research_runner as rr
    candles, config = _study_inputs(tmp_path, {})
    outcome = rr.run_study(tmp_path / 'db.sqlite', 'bad1', candles, config,
                           tmp_path / 'out_bad', funding=None, funding_zero=True,
                           terminal_closeout=False)
    assert outcome['status'] == 'FAILED'
    failure = json.loads((tmp_path / 'out_bad' / 'failure.json').read_text(encoding='utf-8'))
    assert failure['status'] == 'FAILED'
    assert failure['trading_authorized'] is False


def test_research_runner_completed_study(tmp_path):
    import rebalance.research_runner as rr
    strategy = {"step": "1", "min_notional": "10", "leverage": "10", "fee_rate": "0.001",
                "surplus_pp": "5", "deficit_pp": "10", "trail_pp": "3"}
    candles, config = _study_inputs(tmp_path, strategy)
    outcome = rr.run_study(tmp_path / 'db.sqlite', 'good1', candles, config,
                           tmp_path / 'out_good', funding=None, funding_zero=True,
                           terminal_closeout=False)
    assert outcome['status'] == 'COMPLETED'
    assert (tmp_path / 'out_good' / 'comparison.json').exists()
    report = json.loads((tmp_path / 'out_good' / 'registry_report.json').read_text(encoding='utf-8'))
    trial = next(t for t in report['trials'] if t['id'] == 'good1')
    assert trial['outcome']['status'] == 'COMPLETED'


def test_research_runner_main_worker_cli(tmp_path):
    import rebalance.research_runner as rr
    monkey = pytest.MonkeyPatch()
    monkey.setattr('sys.argv', ['research_runner', 'worker',
                                '--database', str(tmp_path / 'db.sqlite'),
                                '--id', 't', '--output', str(tmp_path / 'out')])
    try:
        with pytest.raises(ValueError, match='isolated snapshot import'):
            rr.main()
    finally:
        monkey.undo()


def test_optimize_threshold_debug_path_exits(monkeypatch, tmp_path):
    import optimization.optimize_threshold as ot
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'tickers': ['AAAUSDT']}), encoding='utf-8')
    monkeypatch.setattr('sys.argv', ['optimize_threshold', '--config', str(config),
                                     '--debug-ticker', 'AAAUSDT'])
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    monkeypatch.setattr(ot, '__file__', str(data_dir / 'optimize_threshold.py'))
    _patch_read_feather(monkeypatch, {'AAAUSDT_live': 1.05})

    async def fake_run_backtest(**kwargs):
        return {'profit_pct': 1.0, 'cycles': 2, 'max_dd_pct': 1.0}

    monkeypatch.setattr(ot, 'run_backtest', fake_run_backtest)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        asyncio.run(ot.main())
    assert (tmp_path / 'optimization_debug.log').exists()
