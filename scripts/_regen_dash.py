import json, pathlib, subprocess, sys, os
ROOT = pathlib.Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
res = json.loads((ROOT/"reports/zec_12m_backtest_result.json").read_text())
curve = json.loads((ROOT/"reports/zec_12m_equity.json").read_text())
payload = {"equity_curve": curve, **res}
tmp = ROOT/"reports/_dashboard_payload.json"
tmp.write_text(json.dumps(payload))
r = subprocess.run([sys.executable, str(ROOT/"scripts/generate_dashboard_enhanced.py"), "--payload", str(tmp)])
tmp.unlink(missing_ok=True)
sys.exit(r.returncode)
