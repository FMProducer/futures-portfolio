"""Compare the runtime baseline and candidate on a frozen, local-only snapshot."""
import hashlib
import importlib.util
import json
import shutil
import sqlite3
import statistics
import sys
import time
from pathlib import Path

workspace = Path(__file__).resolve().parents[2]
runtime = Path(r'C:\Python\Prosperous_Bot\futures_portfolio')
report = Path(__file__).parent
fixture = report / 'inputs'
fixture.mkdir(exist_ok=True)
sys.path.insert(0, str(workspace))

for pattern in ('history/*.json', 'reports/paper_swarm_monitor_12h/20*.json',
                'core/paper_state_*.json', 'core/paper_shadow_*.json', 'core/logs/paper_*.log'):
    for source in runtime.glob(pattern):
        target = fixture / source.relative_to(runtime)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copyfile(source, target)
for relative in ('core/paper_budget.sqlite', 'reports/paper_swarm_continuous/observations.sqlite'):
    source, target = runtime / relative, fixture / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        continue
    with sqlite3.connect(source.as_uri()+'?mode=ro', uri=True) as src, sqlite3.connect(target) as dst:
        if source.name == 'observations.sqlite':
            dst.execute('CREATE TABLE observations(ts REAL PRIMARY KEY,gap_seconds REAL,summary TEXT)')
            dst.executemany('INSERT INTO observations VALUES(?,?,?)', src.execute('SELECT ts,gap_seconds,summary FROM observations'))
        else:
            src.backup(dst)

def load(name, source):
    spec = importlib.util.spec_from_file_location(name, source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

from dashboard import swarm_history, swarm_curve, data_collector
old_history = load('baseline_history', runtime/'dashboard/swarm_history.py')
old_curve = load('baseline_curve', runtime/'dashboard/swarm_curve.py')
old_collector = load('baseline_collector', runtime/'dashboard/data_collector.py')
for module in (old_collector, data_collector):
    module.LOGS_PATH = fixture/'core/logs'

output = {'utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'python': sys.version,
          'sources': {}, 'benchmarks': {}, 'parity': {}}
for relative in ('dashboard/swarm_history.py','dashboard/swarm_curve.py','dashboard/data_collector.py',
                 'dashboard/log_reader.py','dashboard/static/app.js','dashboard/static/ticker.js','dashboard/static/logs.js'):
    output['sources'][relative] = {'candidate': hashlib.sha256((workspace/relative).read_bytes()).hexdigest()}
    if (runtime/relative).exists():
        output['sources'][relative]['baseline'] = hashlib.sha256((runtime/relative).read_bytes()).hexdigest()
for label, before, after in (
    ('history', lambda: old_history.reconstruct(fixture, None), lambda: swarm_history.reconstruct(fixture, None)),
    ('curve', lambda: old_curve.read_curve(fixture, initial=10000), lambda: swarm_curve.read_curve(fixture, initial=10000)),
    ('tail', lambda: old_collector.get_log_tail('paper','AKEUSDT',150), lambda: data_collector.get_log_tail('paper','AKEUSDT',150)),
):
    results = []
    for version, call in (('before', before), ('after', after)):
        cpu, wall = [], []
        for _ in range(4):
            c, w = time.process_time(), time.perf_counter()
            result = call()
            cpu.append(time.process_time()-c)
            wall.append(time.perf_counter()-w)
        results.append(result)
        output['benchmarks'][label+'_'+version] = {'cpu_s':cpu,'wall_s':wall,
            'warm_cpu_median_s':statistics.median(cpu[1:]),'warm_wall_median_s':statistics.median(wall[1:])}
    before, after = results
    if label == 'curve':
        lookup = {p[0]:p for p in before['points']}
        assert all(lookup[p[0]] == p for p in after['points'])
        assert {k:v for k,v in before.items() if k != 'points'} == {k:v for k,v in after.items() if k != 'points'}
        output['curve_points'] = [len(before['points']),len(after['points'])]
    else:
        assert before == after, label
    output['parity'][label] = 'PASS'
output['input_hashes'] = {str(p.relative_to(fixture)):hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in fixture.rglob('*') if p.is_file()}
(report/'result.json').write_text(json.dumps(output,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in output.items() if k != 'input_hashes'},indent=2))
