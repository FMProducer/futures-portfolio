import importlib.util
import json
from pathlib import Path

import pytest


@pytest.mark.parametrize("mode", ["paper", "real"])
def test_dashboard_reads_current_worker_state(tmp_path, monkeypatch, mode):
    source = Path(__file__).parents[1] / "dashboard" / "data_collector.py"
    spec = importlib.util.spec_from_file_location("swarm_dashboard_collector", source)
    collector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(collector)
    monkeypatch.setattr(collector, "STATE_PATH", tmp_path)
    monkeypatch.setattr(collector, "_check_heartbeat", lambda *args: (True, 0))
    monkeypatch.setattr(collector, "get_log_tail", lambda *args: {"lines": []})
    monkeypatch.setattr(collector, "_get_pm2_list", lambda: [{"name": f"{mode}-zec"}])
    state = {"last_tpv": 998.6, "initial_tpv": 1000, "last_profit": -1.4,
             "rebalance_cycles": 1}
    shadow = {"positions": {"ZECUSDT_LONG": 1.4}}
    (tmp_path / f"{mode}_state_ZECUSDT.json").write_text(json.dumps(state))
    shadow_name = "paper_shadow_ZECUSDT.json" if mode == "paper" else "shadow_state_ZECUSDT.json"
    (tmp_path / shadow_name).write_text(json.dumps(shadow))
    assert collector._find_state_files(mode) == ["ZECUSDT"]
    summary = collector._get_ticker_summary("ZECUSDT", mode)
    assert (summary["tpv"], summary["initial_capital"], summary["pnl"]) == (998.6, 1000, -1.4)
    details = collector.get_ticker_details(mode, "ZECUSDT")
    assert details["shadow"] == shadow
    assert details["pm2"]["name"] == f"{mode}-zec"
