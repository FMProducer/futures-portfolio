"""Inspect or restart only PAPER networking workers, preserving state snapshots."""
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path('C:/Python/Prosperous_Bot/futures_portfolio')
OUT = Path(__file__).resolve().parents[1] / 'reports/binance_physical_20261117'
OUT.mkdir(parents=True, exist_ok=True)


def inventory():
    result = subprocess.run('pm2.cmd jlist', shell=True, capture_output=True, check=True)
    rows = json.loads(result.stdout)
    return [dict(name=r['name'], pid=r.get('pid'), status=r['pm2_env'].get('status'),
                 cwd=r['pm2_env'].get('pm_cwd'), script=r['pm2_env'].get('pm_exec_path'),
                 interpreter=r['pm2_env'].get('exec_interpreter')) for r in rows]


rows = inventory()
if sys.argv[1:] == ['restart-paper']:
    config = json.loads((ROOT / 'config.json').read_text())
    if config.get('paper_mode') is not True or config.get('live_swarm') or any(
            r['name'].startswith('real-') and r['status'] == 'online' for r in rows):
        raise RuntimeError('Network rollout refuses REAL trading')
    names = [r['name'] for r in rows if r['status'] == 'online' and (
        r['name'].startswith('paper-') or r['name'] == 'supervisor-service')]
    if not names or any(not re.fullmatch('[a-zA-Z0-9-]+', n) for n in names):
        raise RuntimeError('Invalid PAPER process inventory')
    if any(Path(r['cwd']).resolve() != ROOT.resolve() for r in rows if r['name'] in names):
        raise RuntimeError('PAPER process checkout mismatch')
    snapshot = OUT / ('runtime_before_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    snapshot.mkdir()
    for pattern in ['core/paper_state_*.json', 'core/paper_shadow_*.json', 'config.json']:
        for source in ROOT.glob(pattern):
            shutil.copy2(source, snapshot / source.name)
    ledger = ROOT / 'core/paper_budget.sqlite'
    if ledger.exists():
        with sqlite3.connect(ledger.as_uri() + '?mode=ro', uri=True) as source:
            with sqlite3.connect(snapshot / ledger.name) as destination:
                source.backup(destination)
    (snapshot / 'processes.json').write_text(json.dumps(rows, indent=2))
    for name in names:
        subprocess.run(['cmd.exe', '/d', '/c', 'pm2.cmd', 'restart', name],
                       check=True, capture_output=True)
    rows = inventory()
    (OUT / 'restart_completed.json').write_text(json.dumps({'names': names, 'processes': rows}, indent=2))
    print('Restarted PAPER networking:', ', '.join(names))
elif sys.argv[1:]:
    raise ValueError('Expected no arguments or restart-paper')
(OUT / 'pm2_sanitized.json').write_text(json.dumps(rows, indent=2))
print('Sanitized PM2 inventory saved')
