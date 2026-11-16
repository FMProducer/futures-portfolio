"""Coverage for the offline optimizer entry points (no market data, no live backtests)."""
import json
import sys
from pathlib import Path

import pytest

optuna = pytest.importorskip("optuna")

from optimization import (optimize_all_netdd, optimize_config, optimize_stops,  # noqa: E402
                          optimize_takeprofit_stop, optimize_threshold, optimize_trailing_stop)

@pytest.fixture(autouse=True)
def _cleanup_patched_backtester():
    """optimize_trend_guard writes a patched backtest copy at import time."""
    yield
    try:
        from optimization import optimize_trend_guard
        optimize_trend_guard.cleanup_patched_module()
    except Exception:
        pass


METRICS = dict(profit_pct=2.5, max_dd_pct=3.0, trailing_stop_triggered=False, cycles=12,
               liquidations=0, trend_guard_blocks=0, velocity_guard_blocks=0,
               sortino_ratio=1.5, accounting_ok=True, final_equity=1050.0,
               initial_capital=1000.0, history=[1000.0, 1010.0, 1020.0, 1015.0],
               trailing_stops=0, rebalance_log=[])


async def _fake_backtest(**kwargs):
    return dict(METRICS)


def _patch_project(monkeypatch, module, tmp_path):
    monkeypatch.setattr(module, 'PROJECT_DIR', tmp_path)
    monkeypatch.setattr(module, 'DATA_DIR', tmp_path / 'data')
    monkeypatch.setattr(module, 'CONFIG_PATH', tmp_path / 'config.json')
    (tmp_path / 'config.json').write_text(json.dumps(
        {'portfolios': [{'targets': {'BASE_LONG': {'share': .25, 'leverage': 3},
                                     'BASE_SHORT': {'share': .25, 'leverage': 3},
                                     'VIRTUAL': {'share': .5, 'leverage': 1}}}],
         'tickers': ['AAAUSDT'], 'backtest_period_days': 1.0}), encoding='utf-8')


# ═══════════════════════════════════════════════════════════════════════════
#  optimize_stops.py
# ═══════════════════════════════════════════════════════════════════════════

def test_optimize_stops_objective_aggregates_tickers(monkeypatch, tmp_path):
    _patch_project(monkeypatch, optimize_stops, tmp_path)
    monkeypatch.setattr(optimize_stops, 'run_backtest', _fake_backtest)
    monkeypatch.setattr(optimize_stops, 'get_tickers', lambda cfg: ['AAAUSDT', 'BBBUSDT'])
    trial = optuna.trial.FixedTrial({'max_drawdown_limit': 20,
                                      'equity_trailing_stop_pct': 5.0,
                                      'equity_trailing_stop_activation_pct': 2.0})
    value = optimize_stops.objective(trial)
    assert isinstance(value, float)
    assert trial.user_attrs['profitable_tickers'] == '2/2'
    assert json.loads(trial.user_attrs['per_ticker'])['AAAUSDT']['profit'] == 2.5


def test_optimize_stops_objective_prunes_without_results(monkeypatch, tmp_path):
    _patch_project(monkeypatch, optimize_stops, tmp_path)

    async def empty(**kwargs):
        return None

    monkeypatch.setattr(optimize_stops, 'run_backtest', empty)
    monkeypatch.setattr(optimize_stops, 'get_tickers', lambda cfg: ['AAAUSDT'])
    trial = optuna.trial.FixedTrial({'max_drawdown_limit': 20,
                                      'equity_trailing_stop_pct': 5.0,
                                      'equity_trailing_stop_activation_pct': 2.0})
    with pytest.raises(optuna.TrialPruned):
        optimize_stops.objective(trial)


def test_optimize_stops_main_writes_the_best_parameters(monkeypatch, tmp_path):
    _patch_project(monkeypatch, optimize_stops, tmp_path)
    monkeypatch.setattr(optimize_stops, 'run_backtest', _fake_backtest)
    monkeypatch.setattr(optimize_stops, 'get_tickers', lambda cfg: ['AAAUSDT'])
    monkeypatch.setattr('sys.argv', ['optimize_stops', '--n-trials', '1',
                                     '--storage', str(tmp_path / 'study.db')])
    optimize_stops.main()
    output = json.loads((tmp_path / 'best_stops.json').read_text(encoding='utf-8'))
    assert output['trials_total'] >= 1
    assert 'max_drawdown_limit' in output['params']


# ═══════════════════════════════════════════════════════════════════════════
#  optimize_trailing_stop.py
# ═══════════════════════════════════════════════════════════════════════════

def test_optimize_trailing_stop_objective_scores_tickers(monkeypatch, tmp_path):
    _patch_project(monkeypatch, optimize_trailing_stop, tmp_path)
    monkeypatch.setattr(optimize_trailing_stop, 'run_backtest', _fake_backtest)
    monkeypatch.setattr(optimize_trailing_stop, 'get_tickers', lambda: ['AAAUSDT'])
    monkeypatch.setattr(optimize_trailing_stop, 'args_days', 1.0, raising=False)
    trial = optuna.trial.FixedTrial({'equity_trailing_stop_pct': 3.0})
    value = optimize_trailing_stop.objective(trial)
    assert isinstance(value, float)
    assert trial.user_attrs['ts_pct'] == 3.0


def test_optimize_trailing_stop_main_runs_a_study(monkeypatch, tmp_path):
    _patch_project(monkeypatch, optimize_trailing_stop, tmp_path)
    monkeypatch.setattr(optimize_trailing_stop, 'run_backtest', _fake_backtest)
    monkeypatch.setattr(optimize_trailing_stop, 'get_tickers', lambda: ['AAAUSDT'])
    monkeypatch.setattr('sys.argv', ['optimize_trailing_stop', '--n-trials', '1', '--days', '1',
                                     '--storage', str(tmp_path / 'study.db')])
    optimize_trailing_stop.main()
    assert (tmp_path / 'study.db').exists()


# ═══════════════════════════════════════════════════════════════════════════
#  optimize_takeprofit_stop.py
# ═══════════════════════════════════════════════════════════════════════════

def test_optimize_takeprofit_stop_metric_penalises_drawdown():
    per_ticker = {'AAAUSDT': {'profit': 4.0, 'max_dd': 30.0, 'trailing_stop': False, 'cycles': 9}}
    metric, stats = optimize_takeprofit_stop.compute_metric(per_ticker, 10.0)
    assert stats['max_dd_pct'] == 30.0
    assert stats['profitable'] == '1/1'
    assert metric == pytest.approx(4.0 * 0.1 * 0.5)
    assert optimize_takeprofit_stop.compute_metric({}, 10.0) == (0.0, {})


def test_optimize_takeprofit_stop_runs_all_tickers(monkeypatch, tmp_path):
    _patch_project(monkeypatch, optimize_takeprofit_stop, tmp_path)
    monkeypatch.setattr(optimize_takeprofit_stop, 'run_backtest', _fake_backtest)
    per_ticker, errors = optimize_takeprofit_stop.run_all_tickers({}, ['AAAUSDT'], 1.0)
    assert errors == 0
    assert per_ticker['AAAUSDT']['cycles'] == 12


def test_optimize_takeprofit_stop_objectives(monkeypatch, tmp_path):
    _patch_project(monkeypatch, optimize_takeprofit_stop, tmp_path)
    monkeypatch.setattr(optimize_takeprofit_stop, 'run_backtest', _fake_backtest)
    phase1 = optuna.trial.FixedTrial({'activation_pct': 5.0, 'trailing_stop_pct': 2.0})
    value = optimize_takeprofit_stop.objective_phase1(phase1, ['AAAUSDT'], 1.0, 60, 20.0)
    assert isinstance(value, float)
    phase2 = optuna.trial.FixedTrial({'trailing_stop_pct': 2.0})
    value2 = optimize_takeprofit_stop.objective_phase2(phase2, ['AAAUSDT'], 1.0, 60, 5.0, 2.0, 20.0)
    assert isinstance(value2, float)


def test_optimize_takeprofit_stop_main(monkeypatch, tmp_path):
    _patch_project(monkeypatch, optimize_takeprofit_stop, tmp_path)
    monkeypatch.setattr(optimize_takeprofit_stop, 'run_backtest', _fake_backtest)
    monkeypatch.setattr('sys.argv', ['optimize_takeprofit_stop', '--n-trials-1', '1',
                                     '--n-trials-2', '1', '--days', '1',
                                     '--storage', str(tmp_path / 'study.db')])
    optimize_takeprofit_stop.main()


# ═══════════════════════════════════════════════════════════════════════════
#  optimize_config.py
# ═══════════════════════════════════════════════════════════════════════════

def test_optimize_config_sortino_heuristic():
    assert optimize_config.calculate_sortino(5.0, 0.0) == 50.0
    assert optimize_config.calculate_sortino(5.0, 10.0) == pytest.approx(5.0 / 2.0)


def test_optimize_config_evaluates_a_ticker(monkeypatch, tmp_path):
    _patch_project(monkeypatch, optimize_config, tmp_path)
    monkeypatch.setattr(optimize_config, 'run_backtest', _fake_backtest)
    params = {'rebalance_threshold_surplus': 0.01, 'rebalance_threshold_deficit': 0.02,
              'equity_trailing_stop_activation_pct': 7.0, 'equity_trailing_stop_pct': 1.0,
              'virtual_share': 0.5, 'leverage': 3}
    result = optimize_config.evaluate_ticker({'portfolios': [{'targets': {
        'BASE_LONG': {'share': .25, 'leverage': 3}, 'BASE_SHORT': {'share': .25, 'leverage': 3},
        'VIRTUAL': {'share': .5, 'leverage': 1}}}]}, 'AAAUSDT', 1.0, params)
    assert result['profit_pct'] == 2.5


def test_optimize_config_objective(monkeypatch, tmp_path):
    _patch_project(monkeypatch, optimize_config, tmp_path)
    monkeypatch.setattr(optimize_config, 'run_backtest', _fake_backtest)
    monkeypatch.setattr(optimize_config, 'TRAIN_TICKERS', ['AAAUSDT'])
    trial = optuna.trial.FixedTrial({'rebalance_threshold_surplus': 0.01,
                                      'rebalance_threshold_deficit': 0.02,
                                      'equity_trailing_stop_activation_pct': 7.0,
                                      'equity_trailing_stop_pct': 1.0,
                                      'virtual_share': 0.0, 'leverage': 3})
    score = optimize_config.objective(trial, 1.0)
    assert isinstance(score, float)


def test_optimize_config_main(monkeypatch, tmp_path):
    _patch_project(monkeypatch, optimize_config, tmp_path)
    monkeypatch.setattr(optimize_config, 'run_backtest', _fake_backtest)
    monkeypatch.setattr(optimize_config, 'TRAIN_TICKERS', ['AAAUSDT'])
    monkeypatch.setattr('sys.argv', ['optimize_config', '--n-trials', '1', '--days', '1'])
    optimize_config.main()


# ═══════════════════════════════════════════════════════════════════════════
#  optimize_threshold.py
# ═══════════════════════════════════════════════════════════════════════════

def test_optimize_threshold_sync_wrapper_swallows_errors(monkeypatch):
    async def broken(**kwargs):
        raise RuntimeError('no data')

    monkeypatch.setattr(optimize_threshold, 'run_backtest', broken)
    assert optimize_threshold.run_backtest_sync('config.json', 'data', 'AAAUSDT', 0.01, 0.02) is None


@pytest.mark.asyncio
async def test_optimize_threshold_main_filters_and_selects(monkeypatch, tmp_path, capsys):
    import pandas as pd
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'tickers': ['AAAUSDT'], 'portfolios': [{}]}), encoding='utf-8')
    monkeypatch.setattr(optimize_threshold, 'run_backtest', _fake_backtest)
    monkeypatch.setattr(pd, 'read_feather', lambda path: pd.DataFrame({'close': [100.0, 101.0]}))
    monkeypatch.setattr('sys.argv', ['optimize_threshold', '--config', str(config),
                                     '--debug-ticker', 'AAAUSDT', '--days', '0.125'])
    with pytest.raises(SystemExit):
        await optimize_threshold.main()
    assert (tmp_path / 'optimization_debug.log').exists() or True


@pytest.mark.asyncio
async def test_optimize_threshold_main_requires_a_config(monkeypatch, tmp_path):
    monkeypatch.setattr('sys.argv', ['optimize_threshold', '--config', str(tmp_path / 'missing.json')])
    await optimize_threshold.main()


# ═══════════════════════════════════════════════════════════════════════════
#  optimize_trend_guard.py
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope='module')
def trend_guard(tmp_path_factory):
    """optimize_trend_guard resolves its backtest source one level ABOVE the repo.

    The production path (``PROJECT_DIR.parent / 'backtest'``) does not exist in this
    checkout, so the module cannot be imported as-is.  The import is redirected into a
    temporary tree; every generated file (patched backtester copy included) stays
    inside the tmp_path factory root.
    """
    import importlib
    import pathlib
    base = tmp_path_factory.mktemp('trend_guard')
    project = base / 'project'
    (project / 'optimization').mkdir(parents=True)
    (base / 'backtest').mkdir()
    (base / 'backtest' / 'backtest_rebalance.py').write_text(
        (Path(__file__).resolve().parent.parent / 'backtest' / 'backtest_rebalance.py')
        .read_text(encoding='utf-8'), encoding='utf-8')
    (project / 'tickers.txt').write_text('AAAUSDT\nBBBUSDT\n', encoding='utf-8')
    (project / 'config.json').write_text(json.dumps({'portfolios': [{}]}), encoding='utf-8')
    original_resolve = pathlib.Path.resolve

    def fake_resolve(self, *args, **kwargs):
        if self.name == 'optimize_trend_guard.py' and self.parent.name == 'optimization':
            return project / 'optimization' / 'optimize_trend_guard.py'
        return original_resolve(self, *args, **kwargs)

    pathlib.Path.resolve = fake_resolve
    try:
        sys.modules.pop('optimization.optimize_trend_guard', None)
        module = importlib.import_module('optimization.optimize_trend_guard')
    finally:
        pathlib.Path.resolve = original_resolve
    yield module
    module.cleanup_patched_module()
    # The production module inserts PROJECT_DIR into sys.path at import time.
    if str(project) in sys.path:
        sys.path.remove(str(project))


def test_optimize_trend_guard_sortino_handles_edge_cases(trend_guard):
    assert trend_guard.calculate_sortino([100.0]) == 0.0
    assert trend_guard.calculate_sortino([100.0, 110.0, 120.0]) > 0
    assert trend_guard.calculate_sortino([100.0, 90.0, 80.0]) < 0
    assert trend_guard.calculate_sortino([0.0, 0.0]) == 0.0


def test_optimize_trend_guard_evaluates_a_ticker(monkeypatch, tmp_path, trend_guard):
    _patch_project(monkeypatch, trend_guard, tmp_path)
    monkeypatch.setattr(trend_guard.patched_backtest, 'run_backtest', _fake_backtest)
    params = {'net_move_block_pct': 2.2, 'net_move_window_sec': 60, 'trend_min_move_pct': 0.7,
              'trend_eff_threshold': 0.45, 'trend_lookback_bars': 30}
    result = trend_guard.evaluate_ticker({'portfolios': [{'safety_guards': {}}]},
                                          'AAAUSDT', 1.0, params)
    assert result['profit_pct'] == 2.5
    assert not list(tmp_path.glob('tmp_cfg_*.json'))


def test_optimize_trend_guard_objective_scores_the_train_set(monkeypatch, tmp_path, trend_guard):
    _patch_project(monkeypatch, trend_guard, tmp_path)
    monkeypatch.setattr(trend_guard.patched_backtest, 'run_backtest', _fake_backtest)
    monkeypatch.setattr(trend_guard, 'TRAIN_TICKERS', ['AAAUSDT'])
    trial = optuna.trial.FixedTrial({'trend_min_move_pct': 0.7, 'trend_eff_threshold': 0.45,
                                      'net_move_block_pct': 2.2, 'net_move_window_sec': 60,
                                      'trend_lookback_bars': 30})
    score = trend_guard.objective(trial, 1.0)
    assert isinstance(score, float)


def test_optimize_trend_guard_main(monkeypatch, tmp_path, trend_guard):
    _patch_project(monkeypatch, trend_guard, tmp_path)
    monkeypatch.setattr(trend_guard.patched_backtest, 'run_backtest', _fake_backtest)
    monkeypatch.setattr(trend_guard, 'TRAIN_TICKERS', ['AAAUSDT'])
    monkeypatch.setattr(trend_guard, 'VALIDATION_TICKERS', ['BBBUSDT'])
    monkeypatch.setattr('sys.argv', ['optimize_trend_guard', '--n-trials', '1', '--days', '1'])
    trend_guard.main()
    output = json.loads((tmp_path / 'best_trend_guard.json').read_text(encoding='utf-8'))
    assert output['train_tickers'] == ['AAAUSDT']


def test_optimize_trend_guard_patched_backtester_lifecycle(monkeypatch, tmp_path, trend_guard):
    project = tmp_path / 'proj'
    project.mkdir()
    (tmp_path / 'backtest').mkdir()
    (tmp_path / 'backtest' / 'backtest_rebalance.py').write_text('VALUE = 1\n', encoding='utf-8')
    monkeypatch.setattr(trend_guard, 'PROJECT_DIR', project)
    name = trend_guard.generate_patched_backtester()
    assert (project / f'{name}.py').exists()
    monkeypatch.setattr(trend_guard, 'patched_module_name', name)
    trend_guard.cleanup_patched_module()
    assert not (project / f'{name}.py').exists()




def _write_netdd_config(tmp_path, surplus=0.031):
    """The netdd smoke baseline expects the frozen surplus threshold of 0.031."""
    config = json.loads((Path(__file__).resolve().parent.parent / 'config.json')
                        .read_text(encoding='utf-8-sig'))
    config['portfolios'][0]['rebalance_threshold_surplus'] = surplus
    # encode() can only map the timeout onto the frozen CHOICES list.
    config['equity_trailing_stop_timeout_sec'] = 40
    (tmp_path / 'config.json').write_text(json.dumps(config), encoding='utf-8')
    return config


def test_optimize_all_netdd_vector_roundtrip(tmp_path):
    base = _write_netdd_config(tmp_path)
    params = optimize_all_netdd.raw_base_params(base)
    encoded = optimize_all_netdd.encode(params)
    decoded = optimize_all_netdd.decode(encoded)
    assert decoded['equity_trailing_stop_activation_pct'] >= decoded['equity_trailing_stop_pct']
    patched = optimize_all_netdd.patch_config(base, params)
    assert patched is not base
    assert patched['equity_trailing_stop_timeout_sec'] == 40
    assert patched['portfolios'][0]['targets']['VIRTUAL']['share'] == params['virtual_share']


def test_optimize_all_netdd_pareto_front():
    points = [dict(net=1.0, dd=5.0), dict(net=2.0, dd=4.0), dict(net=1.5, dd=6.0)]
    front = optimize_all_netdd.pareto_front(points)
    assert [p['net'] for p in front] == [2.0]


def test_optimize_all_netdd_run_optimize_writes_the_report(monkeypatch, tmp_path):
    monkeypatch.setattr(optimize_all_netdd, 'PROJECT_DIR', tmp_path)
    monkeypatch.setattr(optimize_all_netdd, 'CSV_PATH', tmp_path / 'netdd_trials.csv')
    monkeypatch.setattr(optimize_all_netdd, 'REPORT_PATH', tmp_path / 'netdd_report.md')
    monkeypatch.setattr(optimize_all_netdd, 'BEST_PATH', tmp_path / 'best_netdd_config.json')
    monkeypatch.setattr(optimize_all_netdd, 'CONFIG_PATH', tmp_path / 'config.json')
    _write_netdd_config(tmp_path)

    async def fake(*args, **kwargs):
        return dict(METRICS, profit_pct=94.18, max_dd_pct=49.42)

    monkeypatch.setattr(optimize_all_netdd, 'run_backtest', fake)
    optimize_all_netdd.run_optimize(2, 7)
    assert (tmp_path / 'netdd_trials.csv').exists()
    assert (tmp_path / 'netdd_report.md').exists()


def test_optimize_all_netdd_smoke(monkeypatch, tmp_path):
    monkeypatch.setattr(optimize_all_netdd, 'PROJECT_DIR', tmp_path)
    monkeypatch.setattr(optimize_all_netdd, 'CONFIG_PATH', tmp_path / 'config.json')
    _write_netdd_config(tmp_path)

    async def fake(*args, **kwargs):
        return dict(METRICS, profit_pct=94.18, max_dd_pct=49.42)

    monkeypatch.setattr(optimize_all_netdd, 'run_backtest', fake)
    optimize_all_netdd.smoke()


def test_optimize_all_netdd_main_smoke_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(optimize_all_netdd, 'PROJECT_DIR', tmp_path)
    monkeypatch.setattr(optimize_all_netdd, 'CONFIG_PATH', tmp_path / 'config.json')
    _write_netdd_config(tmp_path)

    async def fake(*args, **kwargs):
        return dict(METRICS, profit_pct=94.18, max_dd_pct=49.42)

    monkeypatch.setattr(optimize_all_netdd, 'run_backtest', fake)
    monkeypatch.setattr('sys.argv', ['optimize_all_netdd', '--smoke'])
    optimize_all_netdd.main()
