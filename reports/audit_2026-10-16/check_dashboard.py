"""Exercise original dashboard with local fixtures and a mocked PM2 boundary."""
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

OUT = Path(__file__).resolve().parent
ROOT = OUT / "_sandbox" / "futures_portfolio"
sys.path.insert(0, str(ROOT / "dashboard"))
import dashboard as web
import data_collector as dc

fixture = OUT / "_sandbox" / "dashboard_fixture"
fixture.mkdir(exist_ok=True)
(fixture / "core").mkdir(exist_ok=True)
native = dict(last_tpv=125, initial_tpv=100, last_profit=25, rebalance_cycles=3)
(fixture / "core" / "real_state_BTCUSDT.json").write_text(json.dumps(native))
(fixture / "core" / "shadow_state_BTCUSDT.json").write_text(json.dumps(dict(balance=100, positions={"BTCUSDT_LONG": 0.01})))
(fixture / "logs").mkdir(exist_ok=True)
(fixture / "logs" / "real_BTCUSDT.log").write_text(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " Heartbeat audit fixture\n")
results = dict(original_project_path=str(web.PROJECT_PATH), checked_source="44db0bb9d1b1675868ef351de24422ed6c7ab817")
# Neither original absolute paths nor real PM2 are used below.
dc.PROJECT_PATH = fixture
dc.LOGS_PATH = fixture / "logs"
dc.PM2_LOGS_PATH = fixture / "logs"
web.PROJECT_PATH = fixture
web.subprocess.run = lambda *a, **kw: SimpleNamespace(returncode=0, stdout="[]", stderr="")
dc._get_pm2_list = lambda: []
results["native_core_state_discovery"] = dc.get_system_overview()["summary"]
dc.PROJECT_PATH = fixture / "core"
results["native_state_schema_summary"] = dc.get_system_overview()["summary"]
try:
    results["native_shadow_details"] = dc.get_ticker_details("real", "BTCUSDT")
except Exception as e:
    results["native_shadow_details_error"] = repr(e)
app = web.create_app()
app.config["TESTING"] = False
client = app.test_client()
results["anonymous_overview_status"] = client.get("/api/overview").status_code
with client.session_transaction() as s:
    s.update(authenticated=True, username="audit", csrf_token="audit-csrf")
results["page_status"] = {path: client.get(path).status_code for path in
    ("/", "/history", "/settings", "/ticker/real/BTCUSDT", "/logs/real/BTCUSDT", "/api/overview", "/api/history")}
html = client.get("/ticker/real/BTCUSDT").text
results["ticker_has_invalid_csrf_script"] = "const CSRF_TOKEN=***" in html
results["ticker_onclick_handlers"] = re.findall('onclick="([^"]+)"', html)
calls = []
def pm2_stub(cmd, **kw):
    calls.append(cmd)
    return SimpleNamespace(returncode=1, stdout="", stderr="audit: simulated PM2 failure")
with patch.object(web.subprocess, "run", side_effect=pm2_stub):
    web.config["control"]["enabled"] = False
    result = client.post("/api/control/emergency-stop", json=dict(ticker="BTCUSDT", mode="real"), headers={"X-CSRF-Token": "audit-csrf"})
    results["disabled_emergency"] = dict(status=result.status_code, body=result.json, mocked_calls=list(calls))
    calls.clear()
    web.config["control"]["enabled"] = True
    result = client.post("/api/control/pm2", json=dict(action="pm2_start", ticker="BTCUSDT"), headers={"X-CSRF-Token": "audit-csrf"})
    results["paper_start_command"] = dict(status=result.status_code, mocked_calls=list(calls))
    calls.clear()
    result = client.post("/api/control/pm2", json=dict(action="pm2_stop", target="all"), headers={"X-CSRF-Token": "audit-csrf"})
    results["arbitrary_target"] = dict(status=result.status_code, mocked_calls=list(calls))
    # A signed cookie made with the shipped source key is accepted without a login.
    forged = app.test_client()
    cookie = app.session_interface.get_signing_serializer(app).dumps(dict(authenticated=True, username="audit-forged"))
    forged.set_cookie("session", cookie)
    results["source_default_key_forged_cookie_status"] = forged.get("/api/overview").status_code
    calls.clear()
    response = forged.post("/api/control/emergency-stop", json=dict(ticker="BTCUSDT"))
    results["forged_session_missing_csrf"] = dict(status=response.status_code, mocked_calls=list(calls))
results["invalid_log_lines_status"] = client.get("/api/logs/real/BTCUSDT?lines=bad").status_code
(OUT / "dashboard_results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(results, ensure_ascii=True, indent=2))

if "--serve" in sys.argv:
    # Fixture-only authentication for browser QA. Runtime config is not written.
    import bcrypt
    web.config["auth"]["username"] = "audit"
    web.config["auth"]["password_hash"] = bcrypt.hashpw(b"audit-local-fixture", bcrypt.gensalt()).decode()
    web.config["control"]["enabled"] = False
    app.run(host="127.0.0.1", port=18765, debug=False, use_reloader=False)
