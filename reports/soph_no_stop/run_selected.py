"""Capture the exact final CLI exit separately from the PowerShell session."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
p = Path(__file__).parent
command = [sys.executable, 'backtest/backtest_rebalance.py', '--config', 'config.json', '--ticker', 'SOPHUSDT']
with (p / 'selected_cli.log').open('w', encoding='utf-8') as output:
    completed = subprocess.run(command, cwd=root, stdout=output, stderr=subprocess.STDOUT,
        env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
(p / 'selected_exit.json').write_text(json.dumps({'command': command, 'exit_code': completed.returncode}, indent=2))
assert completed.returncode == 0
shutil.copy2(root / 'reports/soph_12m_backtest_result.json', p / 'selected_result.json')
shutil.copy2(root / 'reports/soph_12m_equity.json', p / 'selected_equity.json')
print('Final selected CLI exit:', completed.returncode, flush=True)
