"""The CLI must pass the merged HTF arguments without starting a replay."""
import asyncio
from pathlib import Path
import runpy
import sys

import pytest


@pytest.mark.parametrize("mode", [None, "from_virtual"])
def test_backtest_cli_passes_htf_mode(monkeypatch, mode):
    entrypoint = Path(__file__).resolve().parents[1] / "backtest/backtest_rebalance.py"
    argv = [str(entrypoint), "--days", "1"]
    if mode is not None:
        argv.extend(["--htf-mode", mode])
    captured = {}

    def capture(coroutine):
        captured.update(coroutine.cr_frame.f_locals)
        coroutine.close()
        return None

    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(asyncio, "run", capture)
    runpy.run_path(str(entrypoint), run_name="__main__")
    assert captured["htf_mode"] == (mode or "between_legs")
