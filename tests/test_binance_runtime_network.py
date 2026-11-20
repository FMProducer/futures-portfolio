import json
from pathlib import Path
import runpy
import subprocess
import sys


def test_inventory_accepts_utf8_pm2_bytes_without_locale_decoding(monkeypatch):
    payload = json.dumps([{'name': 'paper-jct', 'pid': 123,
        'pm2_env': {'status': 'online', 'pm_cwd': 'C:/Папка/😀',
                   'pm_exec_path': 'core/main.py', 'exec_interpreter': 'python.exe'}}],
        ensure_ascii=False).encode('utf-8')
    captured = {}

    def execute(command, **kwargs):
        assert command == 'pm2.cmd jlist'
        assert not kwargs.get('text') and 'encoding' not in kwargs
        return subprocess.CompletedProcess(command, 0, stdout=payload, stderr=b'')

    def write(path, text, **kwargs):
        captured[path.name] = json.loads(text)

    monkeypatch.setattr(subprocess, 'run', execute)
    monkeypatch.setattr(sys, 'argv', ['binance_runtime_network.py'])
    monkeypatch.setattr(Path, 'mkdir', lambda *a, **k: None)
    monkeypatch.setattr(Path, 'write_text', write)
    runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/binance_runtime_network.py'))
    assert captured['pm2_sanitized.json'][0]['cwd'] == 'C:/Папка/😀'
