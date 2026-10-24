"""Run the real replay verdict in subprocesses against corrupted engine results."""
import copy
from pathlib import Path
import pickle
import shutil
import subprocess
import sys

import pytest


@pytest.fixture(scope='module')
def replay_payload(tmp_path_factory):
    root = Path(__file__).resolve().parents[1]
    script = root / 'scripts/run_zec_replay.py'
    source = script.read_text(encoding='utf-8')
    prefix, tail = source.split('# --- 5. Compare determinism ---', 1)
    isolated = tmp_path_factory.mktemp('replay_inputs')
    for directory in ('core', 'backtest'):
        for path in (root / directory).glob('*.py'):
            destination = isolated / directory / path.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
    for relative in ('config.json', 'data/ZECUSDT_12m.feather'):
        destination = isolated / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / relative, destination)
    scope = {'__file__': str(isolated / 'scripts/run_zec_replay.py'), '__name__': 'replay_fixture'}
    old_path = sys.path[:]
    try:
        exec(compile(prefix, str(script), 'exec'), scope)
    finally:
        sys.path[:] = old_path
    keys = ('ROOT', 'SNAPSHOTS', 'events', 'scenario_path', 'RESULTS', 'df')
    payload = {key: scope[key] for key in keys}
    payload['tail'] = tail
    return payload, tmp_path_factory.mktemp('replay_negative')


@pytest.mark.parametrize('fault', ['valid', 'determinism', 'accounting', 'equity',
    'missing', 'duplicate', 'bar', 'amount', 'halt_increase', 'disguised_increase'])
def test_replay_gate_rejects_corrupted_result(replay_payload, fault):
    payload, directory = replay_payload
    data = copy.deepcopy(payload)
    for result in data['RESULTS']:
        funding = [e for e in result['events'] if e['kind'] == 'funding']
        halt = next(e['bar'] for e in result['events'] if e['kind'] == 'funding_shortfall')
        if fault == 'accounting':
            result['accounting_ok'] = False
        elif fault == 'equity':
            result['equity_curve'][-1] += 1
        elif fault == 'missing':
            result['events'].remove(funding[0])
        elif fault == 'duplicate':
            result['events'].append(copy.deepcopy(funding[0]))
        elif fault == 'bar':
            funding[0]['bar'] += 1
        elif fault == 'amount':
            funding[0]['funding_amount'] += 1
        elif fault in ('halt_increase', 'disguised_increase'):
            result['fills'].append({'bar': halt, 'leg': 'BASE_LONG', 'side': 'BUY',
                'qty': 0.000001, 'price': 1.0, 'commission': 0.0,
                'trigger': 'funding_shortfall' if fault == 'disguised_increase' else 'rebalance'})
    if fault == 'determinism':
        data['RESULTS'][1]['cycles'] += 1
    datafile = directory / (fault + '.pickle')
    datafile.write_bytes(pickle.dumps(data))
    child = '''
import hashlib, json, os, pickle, sys
import numpy as np
import pandas as pd
data = pickle.load(open(sys.argv[1], 'rb'))
tail = data.pop('tail')
data.update(json=json, os=os, sys=sys, np=np, pd=pd,
    sha256_file=lambda p: hashlib.sha256(open(p, 'rb').read()).hexdigest())
exec(compile(tail, 'run_zec_replay_gate', 'exec'), data)
'''
    process = subprocess.run([sys.executable, '-c', child, str(datafile)],
        cwd=payload['ROOT'], capture_output=True, text=True, timeout=30)
    (directory / (fault + '.log')).write_text(process.stdout + process.stderr, encoding='utf-8')
    expected = 'OVERALL: PASS' if fault == 'valid' else 'OVERALL: FAIL'
    assert expected in process.stdout, process.stdout + process.stderr
    assert process.returncode == (0 if fault == 'valid' else 1)
