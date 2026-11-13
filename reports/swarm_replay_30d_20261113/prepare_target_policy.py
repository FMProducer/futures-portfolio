"""Prepare an isolated, explicitly targeted PAPER/selection replay variant."""
import hashlib
import json
from pathlib import Path
from replay import HERE, ROOT


def replace_once(text, old, new):
    assert text.count(old) == 1, old
    return text.replace(old, new)


def main():
    folder = HERE/'target_policy_variant'
    folder.mkdir(exist_ok=False)
    replay = (HERE/'replay.py').read_text(encoding='utf-8')
    replay = replace_once(replay, "ROOT = Path(__file__).resolve().parents[2]", f'ROOT = Path({str(ROOT)!r})')
    replay = replace_once(replay, 'self.config.update(max_bots=9, paper_mode_bots=8)',
        "self.config.update(max_bots=10, paper_mode_bots=8, max_drawdown_limit=99.99, use_real_whitelist=True, real_whitelist=['ONUSDT','HOTUSDT'])")
    replay = replace_once(replay, 'volume < 20000000', 'volume < 15000000')
    replay = replace_once(replay, "'volume_below_20m'", "'volume_below_15m'")
    replay = replace_once(replay, 'min(8, os.cpu_count() or 1)', 'min(2, os.cpu_count() or 1)')
    replay = replace_once(replay, "write(self.folder/'selection'/f'{self.selection:05d}.scanner.json'",
        "results = [r for r in results if r['symbol'] in self.config['real_whitelist']]\n        write(self.folder/'selection'/f'{self.selection:05d}.scanner.json'")
    old = "functions = [n for n in ast.parse(source.read_text(encoding='utf-8')).body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]"
    new = "from policy import instrument, cumulative_cycles, observe\n        functions = [n for n in ast.parse(instrument(source.read_text(encoding='utf-8'))).body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]"
    replay = replace_once(replay, old, new)
    replay = replace_once(replay, "self.manager.update(get_running_bots_info=self.running, get_pm2_processes=self.pm2)",
        "self.manager.update(get_running_bots_info=self.running, get_pm2_processes=self.pm2, research_observe=lambda *args:observe(self,*args))")
    replay = replace_once(replay, "encoded = json.dumps(state, sort_keys=True).encode()",
        "if not is_real:\n                result['session_cycles'] = result['cycles']\n                result['cycles'] = cumulative_cycles(self.folder, ticker, state)\n            encoded = json.dumps(state, sort_keys=True).encode()")
    replay = replace_once(replay, "effective_changes={'max_bots':9,'paper_mode_bots':8}",
        "effective_changes={'max_bots':10,'paper_mode_bots':8,'max_drawdown_limit':99.99,'gate_hours':[48,36],'modeled_volume_min':15000000,'target_universe':['ONUSDT','HOTUSDT'],'cycles':'unique closed PAPER sessions plus current','REAL_execution':'disabled; record fresh-slot decisions and sticky identity reservations only'}")
    worker = (HERE/'gate_worker.py').read_text(encoding='utf-8')
    worker = replace_once(worker, 'for hours in (48,24):', 'for hours in (48,36):')
    worker = replace_once(worker, "row.update(row['windows']['24'])", "row.update(row['windows']['36'])")
    for name, text in [('replay.py',replay),('gate_worker.py',worker)]:
        (folder/name).write_text(text, encoding='utf-8')
    for name in ('config.source.json','manifest.json'):
        (folder/name).write_bytes((HERE/name).read_bytes())
    (folder/'policy.py').write_bytes((HERE/'target_policy.py').read_bytes())
    hashes = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir() if p.is_file()}
    (folder/'variant.json').write_text(json.dumps(dict(files=hashes,
        base_driver_sha256=hashlib.sha256((HERE/'replay.py').read_bytes()).hexdigest(),
        scope='Continuous native PAPER workers and shared 8000 budget; targeted admission; native fresh-slot decisions; no REAL execution or account model'),indent=2))
    print(json.dumps(dict(folder=str(folder), files=hashes),indent=2))


if __name__ == '__main__':
    main()
