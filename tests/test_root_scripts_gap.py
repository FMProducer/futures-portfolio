"""Coverage for the repository-root maintenance scripts (offline, no exchange access)."""
import importlib.machinery
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_root_module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ═══════════════════════════════════════════════════════════════════════════
#  optimize_tickers_10pct.py
# ═══════════════════════════════════════════════════════════════════════════

SETTINGS = {
    'parameters': {
        'leverage': {'type': 'int', 'enabled': True, 'value': 3, 'step': 1},
        'max_bots': {'type': 'int', 'enabled': True, 'value': 8, 'step': 1},
        'limit_order_enabled': {'type': 'categorical', 'enabled': True, 'value': False,
                                 'choices': [False, True]},
    }
}


@pytest.fixture
def ticker_project(tmp_path, monkeypatch):
    module = _load_root_module('optimize_tickers_10pct')
    source = json.loads((ROOT / 'config.json').read_text(encoding='utf-8-sig'))
    project = tmp_path / 'project'
    (project / 'reports').mkdir(parents=True)
    (project / 'config.json').write_text(json.dumps(source), encoding='utf-8')
    (project / 'tickers.txt').write_text('BTCUSDT\nETHUSDT\n', encoding='utf-8')
    template = tmp_path / 'template'
    template.mkdir()
    (template / 'refined_optimizer_config.json').write_text(json.dumps(SETTINGS), encoding='utf-8')
    (template / 'run_local_only.py').write_text(
        "# frozen copy\nprint('FUSE=false. Shared integer leverage follows range_centers.json.')\n",
        encoding='utf-8')
    monkeypatch.setattr(module, 'ROOT', project)
    monkeypatch.setattr(module, 'TEMPLATE', template)
    monkeypatch.setattr('optimization.optimize_universal.load_optimizer_settings',
                        lambda config, path: {'loaded': True})
    return module, project


def test_prepare_freezes_bounded_search_inputs(ticker_project, capsys):
    module, project = ticker_project
    run = project / 'reports' / 'run1'
    module.prepare(run)
    assert (run / 'source_config.json').exists()
    assert (run / 'base_config.json').exists()
    assert (run / 'tickers.txt').read_text() == 'BTCUSDT\nETHUSDT\n'
    base = json.loads((run / 'base_config.json').read_text())
    assert base['funding_model'] == 'legacy_constant'
    assert base['limit_order_enabled'] is False
    centers = json.loads((run / 'range_centers.json').read_text())
    assert centers['leverage'] == {'source_value': 3, 'low': 3, 'high': 3, 'enabled': False}
    assert 'Shared integer leverage follows range_centers.json.' in (run / 'run_local_only.py').read_text()
    assert '400 trials each' in capsys.readouterr().out


def test_prepare_rejects_invalid_ticker_list(ticker_project):
    module, project = ticker_project
    (project / 'tickers.txt').write_text('not-a-symbol\n', encoding='utf-8')
    with pytest.raises(ValueError, match='one USDT symbol per line'):
        module.prepare(project / 'reports' / 'run2')


def test_prepare_rejects_non_numeric_source_value(ticker_project):
    module, project = ticker_project
    settings = json.loads(json.dumps(SETTINGS))
    settings['parameters']['testnet'] = {'type': 'int', 'enabled': True, 'value': 0, 'step': 1}
    (module.TEMPLATE / 'refined_optimizer_config.json').write_text(json.dumps(settings), encoding='utf-8')
    with pytest.raises(ValueError, match='Invalid numeric source value'):
        module.prepare(project / 'reports' / 'run3')


def test_prepare_rejects_leverage_outside_approved_corridor(ticker_project):
    module, project = ticker_project
    source = json.loads((project / 'config.json').read_text())
    source['portfolios'][0]['targets']['BASE_LONG']['leverage'] = 10
    (project / 'config.json').write_text(json.dumps(source), encoding='utf-8')
    with pytest.raises(ValueError, match='approved 3..7 range'):
        module.prepare(project / 'reports' / 'run4')


def test_prepare_only_creates_a_frozen_run(ticker_project, monkeypatch):
    module, project = ticker_project
    monkeypatch.setattr('sys.argv', ['optimize_tickers_10pct', '--prepare-only'])
    module.main()
    runs = list((project / 'reports').glob('ticker_local400_10pct_*'))
    assert len(runs) == 1 and (runs[0] / 'base_config.json').exists()


def test_resume_requires_a_project_run_directory(ticker_project, monkeypatch):
    module, project = ticker_project
    outside = project.parent / 'elsewhere'
    outside.mkdir()
    monkeypatch.setattr('sys.argv', ['optimize_tickers_10pct', '--resume', str(outside)])
    with pytest.raises(ValueError, match='Resume requires an output directory'):
        module.main()


def test_main_downloads_then_runs_the_frozen_worker(ticker_project, monkeypatch, tmp_path):
    module, project = ticker_project
    run = project / 'reports' / 'run5'
    (run / 'frozen').mkdir(parents=True)
    (run / 'run_local_only.py').write_text('# frozen\n', encoding='utf-8')

    class FakeDownloader:
        @staticmethod
        def fetch(url):
            assert url == 'https://fapi.binance.com/fapi/v1/time'
            return {'serverTime': 1767225600000}

    class Loader:
        @staticmethod
        def create_module(spec):
            return types.ModuleType(spec.name)

        @staticmethod
        def exec_module(target):
            def main():
                target.RUN.joinpath('data_manifest.json').write_text(
                    json.dumps({'status': 'completed'}), encoding='utf-8')

            target.RUN = None
            target.DATA = None
            target.fetch = FakeDownloader.fetch
            target.main = main

    monkeypatch.setattr('importlib.util.spec_from_file_location',
                        lambda name, location: importlib.machinery.ModuleSpec(name, Loader()))
    commands = []
    monkeypatch.setattr(module.subprocess, 'run',
                        lambda command, **kwargs: commands.append(command))
    monkeypatch.setattr('sys.argv', ['optimize_tickers_10pct', '--resume', str(run)])
    module.main()
    assert len(commands) == 2
    assert commands[0][2].endswith('run_local_only.py')
    assert commands[1][2].endswith('export_local_optuna_graphs.py')
    manifest = json.loads((run / 'data_manifest.json').read_text())
    assert manifest['end_exclusive_utc'] == '2026-01-01T00:00:00Z'
    assert manifest['start_utc'] == '2025-12-30T00:00:00Z'


# ═══════════════════════════════════════════════════════════════════════════
#  export_local_optuna_graphs.py
# ═══════════════════════════════════════════════════════════════════════════

def _write_study(path, name='study', trials=3, varying=('x', 'y'), failed=False, fixed=False):
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    storage = optuna.storages.RDBStorage('sqlite:///' + Path(path).as_posix())
    study = optuna.create_study(study_name=name, storage=storage, directions=['minimize', 'minimize'])
    if failed:
        def boom(trial):
            raise RuntimeError('injected')
        study.optimize(boom, n_trials=1, catch=(RuntimeError,))
    else:
        def objective(trial):
            values = [trial.suggest_float(key, 0, 10) for key in varying]
            while len(values) < 2:
                values.append(values[-1])
            return values[0], values[1]
        if fixed:
            for _ in range(trials):
                study.enqueue_trial({'x': 5.0})
        study.optimize(objective, n_trials=trials)
    storage.remove_session()
    storage.engine.dispose()


def test_export_writes_charts_and_index(tmp_path, capsys):
    module = _load_root_module('export_local_optuna_graphs')
    run = tmp_path / 'run'
    (run / 'per_ticker').mkdir(parents=True)
    _write_study(run / 'per_ticker' / 'BTCUSDT.db')
    module.export(run)
    folder = run / 'optuna_visuals' / 'BTCUSDT'
    assert (folder / 'history_net_pct.html').exists()
    assert (folder / 'pareto.html').exists()
    index = (run / 'optuna_visuals' / 'index.html').read_text(encoding='utf-8')
    assert 'BTCUSDT' in index and 'Individual Optuna studies' in index
    assert json.loads((run / 'optuna_visuals' / 'errors.json').read_text()) == []
    assert '1/1 BTCUSDT' in capsys.readouterr().out


def test_export_requires_per_ticker_databases(tmp_path):
    module = _load_root_module('export_local_optuna_graphs')
    run = tmp_path / 'empty'
    run.mkdir()
    with pytest.raises(ValueError, match='No per-ticker databases'):
        module.export(run)


def test_export_records_incomplete_and_failing_charts(tmp_path, monkeypatch):
    import optuna
    module = _load_root_module('export_local_optuna_graphs')
    run = tmp_path / 'run'
    (run / 'per_ticker').mkdir(parents=True)
    _write_study(run / 'per_ticker' / 'FAILED.db', failed=True)
    _write_study(run / 'per_ticker' / 'BTCUSDT.db')
    monkeypatch.setattr(optuna.visualization, 'plot_contour',
                        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('no contour')))
    module.export(run)
    errors = json.loads((run / 'optuna_visuals' / 'errors.json').read_text())
    assert any('no feasible completed trials' in e for e in errors)
    assert any('contour' in e and 'no contour' in e for e in errors)


def test_export_records_pareto_failure(tmp_path, monkeypatch):
    import optuna
    module = _load_root_module('export_local_optuna_graphs')
    run = tmp_path / 'run'
    (run / 'per_ticker').mkdir(parents=True)
    _write_study(run / 'per_ticker' / 'BTCUSDT.db')
    monkeypatch.setattr(optuna.visualization, 'plot_pareto_front',
                        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('no pareto')))
    module.export(run)
    errors = json.loads((run / 'optuna_visuals' / 'errors.json').read_text())
    assert any('pareto' in e for e in errors)


# ═══════════════════════════════════════════════════════════════════════════
#  run_zec_test.py / run_zec_backtest.py
# ═══════════════════════════════════════════════════════════════════════════

BACKTEST_RESULT = {
    'status': 'OK', 'liquidations': 1, 'trailing_stops': 2, 'max_dd_pct': 3.5,
    'profit_pct': 12.25, 'cycles': 7,
    'events': [{'bar': 10, 'kind': 'liquidation', 'tpv': 100.0, 'detail': 'x' * 120},
               {'bar': 11, 'kind': 'cooldown_start', 'tpv': 99.0, 'detail': 'y'},
               {'bar': 12, 'kind': 'other', 'tpv': 98.0, 'detail': 'z'}],
    'fills': [{'trigger': 'liquidation', 'bar': 10, 'side': 'BUY', 'leg': 'BASE_LONG',
               'qty': 1.5, 'price': 100.0, 'tpv': 100.0}],
}


@pytest.fixture
def fake_backtest(monkeypatch):
    calls = []
    module = types.ModuleType('backtest.backtest_rebalance')

    async def run_backtest(**kwargs):
        calls.append(kwargs)
        return dict(BACKTEST_RESULT)

    module.run_backtest = run_backtest
    monkeypatch.setitem(sys.modules, 'backtest.backtest_rebalance', module)
    return calls


def test_run_zec_test_reports_the_backtest_result(fake_backtest, capsys):
    module = _load_root_module('run_zec_test')
    out = capsys.readouterr().out
    assert 'Status: OK' in out
    assert 'Liquidations: 1' in out
    assert 'liquidation' in out
    assert fake_backtest[0]['ticker_override'] == 'ZECUSDT'


def test_run_zec_backtest_reports_the_backtest_result(fake_backtest, capsys):
    import asyncio
    module = _load_root_module('run_zec_backtest')
    asyncio.run(module.main())
    out = capsys.readouterr().out
    assert 'Status: OK' in out
    assert 'Event: bar=10 kind=liquidation' in out
    assert fake_backtest[0]['since_utc'] == '2025-10-20 00:00'


def test_run_zec_test_survives_an_empty_result(fake_backtest, monkeypatch, capsys):
    async def empty(**kwargs):
        return None

    monkeypatch.setitem(sys.modules, 'backtest.backtest_rebalance',
                        types.ModuleType('backtest.backtest_rebalance'))
    sys.modules['backtest.backtest_rebalance'].run_backtest = empty
    _load_root_module('run_zec_test')
    assert 'RESULT SUMMARY' not in capsys.readouterr().out
