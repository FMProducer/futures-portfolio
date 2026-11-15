"""Coverage for the remaining offline entry points: replay suite, registry and dataset CLIs."""
import importlib.util
import json
import shutil
import sys
import types
from pathlib import Path

import pytest

from rebalance import dataset_catalog, replay, trial_registry

ROOT = Path(__file__).resolve().parent.parent


# ═══════════════════════════════════════════════════════════════════════════
#  rebalance/replay.py
# ═══════════════════════════════════════════════════════════════════════════

def test_replay_suite_runs_three_synthetic_paths():
    results = replay.run()
    assert set(results) == {'constant', 'trend', 'reversals'}
    assert results['constant']['observations'] == 1000
    assert results['constant']['rebalances'] == 0
    assert results['trend']['fees'] != '0'
    assert results['reversals']['observations'] == 400
    assert results['constant']['terminal'] == 'OPEN_MTM'


# ═══════════════════════════════════════════════════════════════════════════
#  rebalance/trial_registry.py
# ═══════════════════════════════════════════════════════════════════════════

def test_trial_registry_cli_starts_finishes_and_reports(tmp_path, monkeypatch, capsys):
    database = tmp_path / 'registry.sqlite'
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'strategy': {}}), encoding='utf-8')
    data = tmp_path / 'candles.csv'
    data.write_text('open_time,open,high,low,close\n0,100,101,99,100\n', encoding='utf-8')
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({
        'files': {'code': str(Path(trial_registry.__file__)), 'config': str(config), 'data': str(data)},
        'metadata': {'output': str(tmp_path), 'engine': 'comparison'},
        'mode': 'PROSPECTIVE'}), encoding='utf-8')
    monkeypatch.setattr('sys.argv', ['trial_registry', '--database', str(database),
                                     'start', '--id', 'fixture', '--manifest', str(manifest)])
    trial_registry.main()
    outcome = tmp_path / 'outcome.json'
    evidence = tmp_path / 'result.json'
    evidence.write_text('{}', encoding='utf-8')
    outcome.write_text(json.dumps({'status': 'COMPLETED', 'reason': 'done',
                                   'evidence': {'result': str(evidence)}}), encoding='utf-8')
    monkeypatch.setattr('sys.argv', ['trial_registry', '--database', str(database),
                                     'finish', '--id', 'fixture', '--outcome', str(outcome)])
    trial_registry.main()
    report = tmp_path / 'report.json'
    monkeypatch.setattr('sys.argv', ['trial_registry', '--database', str(database),
                                     'report', '--output', str(report)])
    trial_registry.main()
    payload = json.loads(report.read_text(encoding='utf-8'))
    assert payload['total'] == 1
    assert payload['trials'][0]['outcome']['status'] == 'COMPLETED'


# ═══════════════════════════════════════════════════════════════════════════
#  rebalance/dataset_catalog.py
# ═══════════════════════════════════════════════════════════════════════════

def _candles(path, count=3, close_time=True):
    header = 'open_time,open,high,low,close' + (',close_time' if close_time else '')
    rows = []
    for index in range(count):
        stamp = index * 60000
        row = f'{stamp},100,101,99,100'
        if close_time:
            row += f',{stamp + 59999}'
        rows.append(row)
    path.write_text(header + '\n' + '\n'.join(rows) + '\n', encoding='utf-8')
    return path


def test_dataset_catalog_cli_inventories_declared_data(tmp_path, monkeypatch, capsys):
    candles = _candles(tmp_path / 'candles.csv')
    output = tmp_path / 'catalog.json'
    monkeypatch.setattr('sys.argv', ['dataset_catalog', '--candles', str(candles),
                                     '--symbol', 'BTCUSDT', '--source', 'vendor',
                                     '--acquired-utc', '2026-01-01T00:00:00Z',
                                     '--output', str(output)])
    dataset_catalog.main()
    payload = json.loads(output.read_text(encoding='utf-8'))
    assert payload['status'] == 'INVENTORIED'
    assert payload['candles']['rows'] == 3
    assert payload['admission_ready'] is False
    assert json.loads(capsys.readouterr().out)['status'] == 'INVENTORIED'


def test_dataset_catalog_cli_fails_on_invalid_rows(tmp_path, monkeypatch, capsys):
    candles = tmp_path / 'candles.csv'
    candles.write_text('open_time,open,high,low,close\n0,100,101,99,100\n0,100,101,99,100\n',
                       encoding='utf-8')
    output = tmp_path / 'catalog.json'
    monkeypatch.setattr('sys.argv', ['dataset_catalog', '--candles', str(candles),
                                     '--symbol', 'BTCUSDT', '--output', str(output)])
    with pytest.raises(SystemExit) as exc:
        dataset_catalog.main()
    assert exc.value.code == 2
    assert json.loads(capsys.readouterr().out)['status'] == 'FAIL'


def test_dataset_catalog_rejects_an_invalid_close_time(tmp_path):
    candles = tmp_path / 'candles.csv'
    candles.write_text('open_time,open,high,low,close,close_time\n0,100,101,99,100,1\n',
                       encoding='utf-8')
    info, data = dataset_catalog.artifact(candles)
    result = dataset_catalog.inspect_candles(data)
    assert result['status'] == 'FAIL'
    assert result['invalid_rows'] == 1
    assert 'invalid 1m close_time' in result['errors'][0]['reason']


def test_dataset_catalog_rejects_an_invalid_symbol():
    with pytest.raises(ValueError, match='invalid symbol or usage'):
        dataset_catalog.catalog('any.csv', symbol='btc usdt')
    with pytest.raises(ValueError, match='invalid symbol or usage'):
        dataset_catalog.catalog('any.csv', symbol='BTCUSDT', usage='reused')


# ═══════════════════════════════════════════════════════════════════════════
#  accounting/scenarios/run.py
# ═══════════════════════════════════════════════════════════════════════════

def test_accounting_evidence_runner_records_reproducibility(tmp_path, monkeypatch, capsys):
    root = tmp_path / 'root'
    shutil.copytree(ROOT / 'accounting', root / 'accounting')
    (root / 'docs' / 'coordination').mkdir(parents=True)
    shutil.copyfile(ROOT / 'docs' / 'coordination' / 'ACCOUNTING_SPEC_V3.md',
                    root / 'docs' / 'coordination' / 'ACCOUNTING_SPEC_V3.md')
    for name in [n for n in list(sys.modules) if n == 'accounting' or n.startswith('accounting.')]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.syspath_prepend(str(root))
    spec = importlib.util.spec_from_file_location('accounting_evidence_runner',
                                                   root / 'accounting' / 'scenarios' / 'run.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'subprocess',
                        types.SimpleNamespace(check_output=lambda *a, **k: 'deadbeef\n'))
    assert module.main() == 0
    manifest = json.loads((root / 'docs/coordination/accounting_stage1_evidence/manifest.json')
                          .read_text(encoding='utf-8'))
    assert manifest['successful'] is True
    assert manifest['tests_run'] > 0
    assert manifest['base_commit'] == 'deadbeef'
    assert manifest['scenarios'][0]['status'] == 'PASS'
    assert (root / 'docs/coordination/accounting_stage1_evidence/scenarios.log').exists()
    assert (root / 'docs/coordination/accounting_stage1_evidence/candidate.patch').exists()
    assert json.loads(capsys.readouterr().out)['successful'] is True
