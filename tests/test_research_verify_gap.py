"""Gap coverage for rebalance/research_verify.py (offline studies only)."""
import hashlib
import json
import sqlite3

import pytest

from rebalance.research_runner import run_study
from rebalance.research_verify import main as verify_main, verify


def test_verify_accepts_a_csv_rates_study(tmp_path):
    data = tmp_path / "candles.csv"
    data.write_text("open_time,open,high,low,close\n0,100,100,100,100\n60000,100,100,100,100\n",
                    encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps(dict(capital="10000", spread_bps="2", slippage_bps="1",
                                      strategy=dict(step="0.001", min_notional="5.3", leverage="7",
                                                   fee_rate="0.0004", surplus_pp="0.7",
                                                   deficit_pp="0.7", trail_pp="0.09"))),
                      encoding="utf-8")
    funding = tmp_path / "funding.csv"
    funding.write_text("timestamp,rate,mark_price\n60000,0.001,100\n", encoding="utf-8")
    database = tmp_path / "registry.sqlite"
    output = tmp_path / "output"
    outcome = run_study(database, "csv-rates", data, config, output, funding=funding)
    assert outcome["status"] == "COMPLETED"
    result = verify(database, output)
    assert result["status"] == "PASS"
    assert result["checked_snapshot_artifacts"] >= 8


def _retarget_cycle_definition(tmp_path, database, output):
    """Rewrite the verified comparison as an unapproved cycle definition, rehashing evidence."""
    path = output / "comparison.json"
    summary = json.loads(path.read_text())
    summary["cycle_definition"] = "NOT_APPROVED"
    summary["profit_factor"] = None
    summary.pop("cycles", None)
    path.write_text(json.dumps(summary), encoding="utf-8")
    with sqlite3.connect(database) as db:
        body = json.loads(db.execute("SELECT body FROM outcomes").fetchone()[0])
        body["evidence"]["comparison"].update(bytes=path.stat().st_size,
                                              sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        db.execute("DROP TRIGGER outcomes_no_update")
        db.execute("UPDATE outcomes SET body=?", (json.dumps(body),))
    report_path = output / "registry_report.json"
    report = json.loads(report_path.read_text())
    report["trials"][0]["outcome"] = body
    report_path.write_text(json.dumps(report), encoding="utf-8")


def test_verify_accepts_a_not_approved_cycle_definition(tmp_path):
    candles = tmp_path / "candles.csv"
    candles.write_text("open_time,open,high,low,close\n0,100,100,100,100\n60000,100,100,100,100\n",
                       encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps(dict(capital="10000", spread_bps="2", slippage_bps="1",
                                      strategy=dict(step="0.001", min_notional="5.3", leverage="7",
                                                   fee_rate="0.0004", surplus_pp="0.7",
                                                   deficit_pp="0.7", trail_pp="0.09"))),
                      encoding="utf-8")
    database = tmp_path / "registry.sqlite"
    output = tmp_path / "output"
    run_study(database, "fixture", candles, config, output, funding_zero=True)
    _retarget_cycle_definition(tmp_path, database, output)
    assert verify(database, output)["status"] == "PASS"


def test_verify_main_writes_a_pass_report(tmp_path, monkeypatch, capsys):
    candles = tmp_path / "candles.csv"
    candles.write_text("open_time,open,high,low,close\n0,100,100,100,100\n60000,100,100,100,100\n",
                       encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps(dict(capital="10000", spread_bps="2", slippage_bps="1",
                                      strategy=dict(step="0.001", min_notional="5.3", leverage="7",
                                                   fee_rate="0.0004", surplus_pp="0.7",
                                                   deficit_pp="0.7", trail_pp="0.09"))),
                      encoding="utf-8")
    database = tmp_path / "registry.sqlite"
    output = tmp_path / "output"
    run_study(database, "fixture", candles, config, output, funding_zero=True)
    report = tmp_path / "result.json"
    monkeypatch.setattr("sys.argv", ["research_verify", "--database", str(database),
                                     "--study", str(output), "--output", str(report)])
    verify_main()
    assert json.loads(report.read_text())["status"] == "PASS"
    assert json.loads(capsys.readouterr().out)["status"] == "PASS"


def test_verify_main_reports_a_failed_study(tmp_path, monkeypatch, capsys):
    candles = tmp_path / "candles.csv"
    candles.write_text("open_time,open,high,low,close\n0,100,100,100,100\n60000,100,100,100,100\n",
                       encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps(dict(capital="10000", spread_bps="2", slippage_bps="1",
                                      strategy=dict(step="0.001", min_notional="5.3", leverage="7",
                                                   fee_rate="0.0004", surplus_pp="0.7",
                                                   deficit_pp="0.7", trail_pp="0.09"))),
                      encoding="utf-8")
    database = tmp_path / "registry.sqlite"
    output = tmp_path / "output"
    run_study(database, "fixture", candles, config, output, funding_zero=True)
    (output / "hold_equity.json").unlink()
    report = tmp_path / "result.json"
    monkeypatch.setattr("sys.argv", ["research_verify", "--database", str(database),
                                     "--study", str(output), "--output", str(report)])
    with pytest.raises(SystemExit) as exc:
        verify_main()
    assert exc.value.code == 2
    assert json.loads(report.read_text())["status"] == "FAIL"
