import asyncio
import json
from pathlib import Path
import runpy
import subprocess
import sys
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("ticker,bars,prefix", [
    ("TUSDT", 128869, "t_89d"),
    ("BTCUSDT", 1440, "btc_1d"),
    ("ZECUSDT", 64800, "zec_45d"),
    ("ZECUSDT", 525600, "zec_12m"),
])
def test_cli_uses_full_dashboard_for_every_ticker(tmp_path, monkeypatch, ticker, bars, prefix):
    source = Path(__file__).resolve().parents[1] / "backtest" / "backtest_rebalance.py"
    script = tmp_path / "backtest" / source.name
    script.parent.mkdir()
    script.write_bytes(source.read_bytes())
    result = {"ticker": ticker, "data_window": {"bars": bars}, "equity_curve": [1000, 1001]}

    def fake_run(coroutine):
        coroutine.close()
        return result

    calls = []

    def fake_subprocess(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="dashboard generated")

    monkeypatch.setattr(asyncio, "run", fake_run)
    monkeypatch.setattr(subprocess, "run", fake_subprocess)
    monkeypatch.setattr(sys, "argv", [str(script), "--config", "missing.json"])
    runpy.run_path(str(script), run_name="__main__")

    assert calls == [[sys.executable, str(tmp_path / "scripts" / "visualize_zec_dashboard.py"), prefix]]
    reports = tmp_path / "reports"
    saved = json.loads((reports / f"{prefix}_backtest_result.json").read_text(encoding="utf-8"))
    assert saved["ticker"] == ticker
    assert json.loads((reports / f"{prefix}_equity.json").read_text()) == [1000, 1001]
    assert not (reports / "btc_365d_backtest_result.json").exists()
