"""Direct, deterministic coverage for dashboard/data_collector.py function bodies.

Each test loads the real module file (so coverage attributes to it) and points the
module path constants at tmp fixtures; no real PM2/network access.
"""
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD = ROOT / "dashboard"


@pytest.fixture(scope="module")
def collector():
    import importlib
    importlib.import_module("dashboard")  # pin namespace pkg so dashboard/ path entry cannot shadow it
    inserted = False
    if str(DASHBOARD) not in sys.path:
        sys.path.insert(0, str(DASHBOARD))
        inserted = True
    spec = importlib.util.spec_from_file_location("data_collector_gap", DASHBOARD / "data_collector.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop("data_collector_gap", None)
    if inserted:
        try:
            sys.path.remove(str(DASHBOARD))
        except ValueError:
            pass


@pytest.fixture
def paths(tmp_path, collector, monkeypatch):
    state = tmp_path / "core"
    logs = tmp_path / "logs"
    pm2 = tmp_path / "pm2logs"
    project = tmp_path / "project"
    for d in (state, logs, pm2, project):
        d.mkdir()
    monkeypatch.setattr(collector, "STATE_PATH", state)
    monkeypatch.setattr(collector, "LOGS_PATH", logs)
    monkeypatch.setattr(collector, "PM2_LOGS_PATH", pm2)
    monkeypatch.setattr(collector, "PROJECT_PATH", project)
    return {"state": state, "logs": logs, "pm2": pm2, "project": project}


def test_parse_log_timestamp(collector):
    assert collector._parse_log_timestamp("2026-01-02 03:04:05 x") == datetime(2026, 1, 2, 3, 4, 5)
    assert collector._parse_log_timestamp("garbage") is None
    assert collector._parse_log_timestamp("") is None


def test_read_json(collector, tmp_path):
    good = tmp_path / "g.json"
    good.write_text(json.dumps({"a": 1}))
    bad = tmp_path / "b.json"
    bad.write_text("{not json")
    assert collector._read_json(good) == {"a": 1}
    assert collector._read_json(tmp_path / "missing.json") is None
    assert collector._read_json(bad) is None


def test_find_state_files(collector, paths):
    state = paths["state"]
    (state / "paper_state_AAA.json").write_text("{}")
    (state / "paper_state_BBB.json").write_text("{}")
    (state / "real_state_CCC.json").write_text("{}")
    assert set(collector._find_state_files("paper")) == {"AAA", "BBB"}
    assert collector._find_state_files("real") == ["CCC"]
    assert collector._find_state_files("nope") == []


def test_check_heartbeat(collector, paths):
    logs = paths["logs"]
    fresh = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stale = (datetime.now() - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")
    (logs / "paper_FRESH.log").write_text(f"{fresh} Heartbeat ok\n")
    (logs / "paper_STALE.log").write_text(f"{stale} Heartbeat ok\n")
    ok, age = collector._check_heartbeat("FRESH", "paper")
    assert ok is True and age >= 0
    ok, age = collector._check_heartbeat("STALE", "paper")
    assert ok is False
    ok, age = collector._check_heartbeat("MISSING", "paper")
    assert ok is False and age is None


def test_get_ticker_summary(collector, paths):
    state, logs = paths["state"], paths["logs"]
    (state / "paper_state_P1.json").write_text(json.dumps(
        {"last_tpv": 1200, "initial_tpv": 1000, "last_profit": 200, "rebalance_cycles": 3}))
    (state / "paper_shadow_P1.json").write_text(json.dumps({"positions": {"P1_LONG": 1.0}}))
    (logs / "paper_P1.log").write_text(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " Heartbeat\n")
    summary = collector._get_ticker_summary("P1", "paper")
    assert summary["tpv"] == 1200 and summary["pnl"] == 200
    assert summary["positions"] == {"P1_LONG": 1.0}
    assert summary["status"] == "ok"
    # no-state branch
    assert collector._get_ticker_summary("GHOST", "paper")["status"] == "no_state"
    # pnl_guard branch
    (state / "real_state_G.json").write_text(json.dumps(
        {"last_tpv": 100, "initial_tpv": 200, "last_profit": -100, "pnl_guard_active": True}))
    (logs / "real_G.log").write_text((datetime.now() - timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S") + " Heartbeat\n")
    assert collector._get_ticker_summary("G", "real")["status"] == "pnl_guard"


def test_get_alerts(collector):
    real_stale = {"ticker": "AAA", "status": "stale", "heartbeat_age_sec": 999, "pnl_pct": -20.0}
    alerts = collector._get_alerts([], [real_stale], [])
    messages = [a["message"] for a in alerts]
    assert any("Supervisor" in m for m in messages)
    assert any("heartbeat stale" in m for m in messages)
    assert any("PnL" in m for m in messages)
    online = [{"name": "supervisor-service", "status": "online"}]
    assert collector._get_alerts([], [], online) == []
    offline = [{"name": "supervisor-service", "status": "offline"}]
    assert any("Supervisor" in a["message"] for a in collector._get_alerts([], [], offline))


def _pm2_fake(collector, monkeypatch, returncode=0, payload=None, raise_exc=None):
    def fake_run(cmd, *args, **kwargs):
        if raise_exc:
            raise raise_exc
        return type("R", (), {"returncode": returncode,
                               "stdout": payload if payload is not None else "[]", "stderr": "PM2 unavailable"})()
    monkeypatch.setattr(collector.subprocess, "run", fake_run)


def test_get_pm2_processes_success(collector, monkeypatch):
    payload = json.dumps([{"name": "paper-btc", "pm2_env": {"status": "online",
                                                             "restart_time": 2, "pm_uptime": 100},
                           "monit": {"memory": 5, "cpu": 1}}])
    _pm2_fake(collector, monkeypatch, returncode=0, payload=payload)
    procs = collector.get_pm2_processes()
    assert procs[0]["name"] == "paper-btc" and procs[0]["status"] == "online"


def test_get_pm2_processes_failure_and_exception(collector, monkeypatch):
    _pm2_fake(collector, monkeypatch, returncode=1)
    assert collector.get_pm2_processes() == []
    _pm2_fake(collector, monkeypatch, raise_exc=subprocess.TimeoutExpired("pm2", 10))
    assert collector.get_pm2_processes() == []


def test_get_system_overview(collector, paths, monkeypatch):
    state, project = paths["state"], paths["project"]
    (state / "paper_state_A.json").write_text(json.dumps({"last_tpv": 500, "initial_tpv": 400}))
    (state / "real_state_R.json").write_text(json.dumps({"last_tpv": 900, "initial_tpv": 1000,
                                                        "last_profit": -100, "rebalance_cycles": 1}))
    (project / "config.json").write_text(json.dumps({"initial_capital": 1000}))
    payload = json.dumps([{"name": "supervisor-service", "pm2_env": {"status": "online",
                                                                    "restart_time": 0, "pm_uptime": 1}},
                          {"name": "real-r", "pm2_env": {"status": "online",
                                                          "restart_time": 0, "pm_uptime": 1}}])
    _pm2_fake(collector, monkeypatch, returncode=0, payload=payload)
    overview = collector.get_system_overview()
    assert overview["summary"]["real_count"] == 1
    assert overview["supervisor"] == "online"
    assert overview["paper_curve"]["points"] == []


def test_get_ticker_details(collector, paths, monkeypatch):
    state, logs = paths["state"], paths["logs"]
    (state / "paper_state_T.json").write_text(json.dumps({"last_tpv": 1, "initial_tpv": 1}))
    (state / "paper_shadow_T.json").write_text(json.dumps({"positions": {"T_LONG": 2.0}}))
    (logs / "paper_T.log").write_text("line1\nline2\nline3\n")
    payload = json.dumps([{"name": "paper-t", "pm2_env": {"status": "online",
                                                          "restart_time": 0, "pm_uptime": 1}}])
    _pm2_fake(collector, monkeypatch, returncode=0, payload=payload)
    details = collector.get_ticker_details("paper", "T")
    assert details["pm2"]["name"] == "paper-t"
    assert details["shadow"] == {"positions": {"T_LONG": 2.0}}
    assert details["log_tail"][-1] == "line3"
    assert "not found" in collector.get_ticker_details("paper", "GHOST")["error"]


def test_get_history_data(collector, paths):
    state = paths["state"]
    (state / "real_state_H.json").write_text(json.dumps({"last_tpv": 1500, "initial_tpv": 1000,
                                                        "last_profit": 500}))
    (state / "real_state_EMPTY.json").write_text("")  # unreadable -> skipped
    history = collector.get_history_data()["history"]
    assert [h["ticker"] for h in history] == ["H"]
    assert history[0]["pnl"] == 500


def test_get_log_tail(collector, paths):
    logs, pm2 = paths["logs"], paths["pm2"]
    (logs / "paper_L.log").write_text("a\nb\nc\nd\n")
    result = collector.get_log_tail("paper", "L", lines=2)
    assert result["lines"] == ["c", "d"] and result["total"] == 4
    # offset branch
    offset = collector.get_log_tail("paper", "L", lines=2, offset=2)
    assert offset["lines"] == ["a", "b"]
    # pm2 log fallback
    (pm2 / "real-m.log").write_text("pm2 line\n")
    fallback = collector.get_log_tail("real", "M")
    assert fallback["lines"] == ["pm2 line"]
    # not found
    missing = collector.get_log_tail("real", "Z")
    assert missing["lines"] == [] and "not found" in missing["error"]


def test_get_config_summary(collector, paths):
    project = paths["project"]
    (project / "config.json").write_text(json.dumps({"initial_capital": 2000, "max_bots": 5,
                                                    "secret": "hidden"}))
    summary = collector.get_config_summary()
    assert summary["initial_capital"] == 2000 and summary["max_bots"] == 5
    assert "secret" not in summary
    (project / "config.json").unlink()
    assert "error" in collector.get_config_summary()
