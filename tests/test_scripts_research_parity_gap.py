"""Offline gap coverage for scripts/annual_parity_report.py, scripts/research_agt_drawdown.py,
scripts/verify_agt_rebased_drawdown.py and scripts/run_parity_case.py (no live/exchange access)."""
import asyncio
import hashlib
import importlib.util
import json
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── scripts/annual_parity_report.py ──────────────────────────────────────────

def _case_value(mode_count=3, failures=None, ticker="ZECUSDT", scenario="disabled"):
    modes = {}
    for engine in ("backtest", "paper", "real")[:mode_count]:
        modes[engine] = dict(profit_pct=1.0, profit_after_funding_pct=0.5, max_dd_pct=2.0,
                             final_tpv=1010.0, commission=0.5, funding_paid=-1.0,
                             reserve_end=4999.0, cycles=4, stops=1, market_fills=9,
                             minimum_tpv=995.0, positions={"LONG": 1.0, "SHORT": 0.0})
    return dict(status="PASS", ticker=ticker, scenario=scenario,
                failures=list(failures or []), modes=modes)


def _write_parity_tree(directory, *, running_pending=False, cash_failure=False,
                       historical=False, logs=False, monitoring=False, changed_sources=False):
    directory.mkdir(parents=True, exist_ok=True)
    source = ROOT / "core" / "funding.py"
    digest = "0" * 64 if changed_sources else hashlib.sha256(source.read_bytes()).hexdigest().upper()
    (directory / "inputs.json").write_text(json.dumps(
        dict(base_commit="abc123", period=["2025-10-20", "2026-10-19"], bars=525600,
             cases=[dict(ticker="ZECUSDT", mode="disabled"),
                    dict(ticker="ZECUSDT", mode="historical")])), encoding="utf-8")
    exit_codes = {}
    if not running_pending:
        exit_codes["ZECUSDT_disabled"] = 0
    (directory / "run_status.json").write_text(
        json.dumps(dict(status="RUNNING" if running_pending else "COMPLETED", exit_codes=exit_codes)),
        encoding="utf-8-sig")
    (directory / "source_hashes.json").write_text(json.dumps({"core/funding.py": digest}),
                                                  encoding="utf-8-sig")
    case = directory / "ZECUSDT_disabled"
    case.mkdir()
    value = _case_value(failures=["negative CASH or VIRTUAL balance"] if cash_failure else None)
    value["equity_errors"] = {"paper": 1.0e-09, "real": 3.0e-09}
    value["native_prefix"] = {"status": "PASS", "bars": 180}
    (case / "comparison.json").write_text(json.dumps(value), encoding="utf-8")
    for engine in ("paper", "real"):
        (case / f"{engine}.json").write_text(json.dumps(dict(min_cash="0.12", min_virtual="3.4")),
                                             encoding="utf-8")
    hist = directory / "ZECUSDT_historical"
    hist.mkdir()
    if running_pending:
        # no comparison.json: the report derives the RUNNING view itself
        pass
    elif historical:
        hist_value = _case_value(ticker="ZECUSDT", scenario="historical")
        hist_value["status"] = "FAILED_TO_COMPLETE"
        hist_value["failures"] = ["Case worker did not produce a comparison"]
        (hist / "comparison.json").write_text(json.dumps(hist_value), encoding="utf-8")
    else:
        (hist / "comparison.json").write_text(json.dumps(_case_value(ticker="ZECUSDT",
                                                                     scenario="historical")),
                                              encoding="utf-8")
    for filename, rows in (("backtest_rates.json", [dict(fundingTime=1700000000000, signed_amount="-1.5"),
                                                     dict(fundingTime=1700028800000, signed_amount="0")]),
                            ("paper_funding.json", [dict(event_time=1700000000.0, signed_amount="-1.5")]),
                            ("real_funding_receipts.json", [dict(event_time=1700000060.0, signed_amount="-1.5")])):
        (hist / filename).write_text(json.dumps(rows), encoding="utf-8")
    if logs:
        (directory / "adapter_tests_accepted.log").write_text("12 passed, 1 failed\n", encoding="utf-8")
        (directory / "case_runner_tests.log").write_text("7 passed\n", encoding="utf-8")
        (directory / "full_tests.log").write_text("2000 passed, 0 failed\n", encoding="utf-8")
    if monitoring:
        (directory / "monitoring_failures_isolated.log").write_text("reproduced", encoding="utf-8")


def test_annual_parity_report_publishes_a_pass_summary(tmp_path):
    directory = tmp_path / "case"
    _write_parity_tree(directory, cash_failure=True, historical=True, logs=True, monitoring=True)
    mod = _load("annual_parity_report", ROOT / "scripts" / "annual_parity_report.py")
    mod.report(directory)
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    assert summary["all_cases_pass"] is False
    assert summary["source_changed"] == []
    assert [c["status"] for c in summary["cases"]] == ["PASS", "FAILED_TO_COMPLETE"]
    assert summary["cases"][0]["nonnegative_cash_virtual"] == "FAIL"
    assert summary["cases"][0]["financial_parity"] == "PASS"
    report_md = (directory / "REPORT.md").read_text(encoding="utf-8")
    assert "Адаптеры funding: 12 passed, 1 failed" in report_md
    assert "Два сбоя мониторинга" in report_md
    assert "| ZECUSDT | historical | " in report_md


def test_annual_parity_report_flags_source_change(tmp_path):
    directory = tmp_path / "case"
    _write_parity_tree(directory, changed_sources=True)
    mod = _load("annual_parity_report", ROOT / "scripts" / "annual_parity_report.py")
    mod.report(directory)
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    assert summary["source_changed"] == ["core/funding.py"]
    assert summary["all_cases_pass"] is False
    assert all(c["status"] == "SOURCE_CHANGED_NOT_ACCEPTED" for c in summary["cases"])
    assert "Исходный код изменился во время запуска" in (directory / "REPORT.md").read_text(encoding="utf-8")


def test_annual_parity_report_marks_a_running_case_not_verified(tmp_path):
    directory = tmp_path / "case"
    _write_parity_tree(directory, running_pending=True)
    mod = _load("annual_parity_report", ROOT / "scripts" / "annual_parity_report.py")
    mod.report(directory)
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    running = [c for c in summary["cases"] if c["status"] == "RUNNING"]
    assert running and running[0]["financial_parity"] == "NOT_VERIFIED"
    assert running[0]["nonnegative_cash_virtual"] == "NOT_VERIFIED"


# ── scripts/research_agt_drawdown.py ─────────────────────────────────────────

def test_research_agt_drawdown_observables_and_analyze(tmp_path):
    import backtest.backtest_rebalance as bt
    mod = _load("research_agt_drawdown", ROOT / "scripts" / "research_agt_drawdown.py")
    mod.OUT = tmp_path / "out"
    mod.OUT.mkdir()

    self = SimpleNamespace(events=[], MAX_FILLS=8)
    before = mod.session
    mod.log_event(self, 1, "cooldown_end", 100.0, "x")
    assert mod.session == before + 1 and len(self.events) == 1
    mod.log_event(self, 2, "fill", 100.0, "y")
    assert mod.session == before + 1 and len(self.events) == 2

    state = SimpleNamespace(get_tpv=lambda price: 100.0, siphoning_reserve=0.0,
                            unrealized=lambda price: 5.0, pos_long=1.0, pos_short=0.0,
                            initial_capital=100.0)
    sim = SimpleNamespace(execution_price=lambda side, price: price * (0.999 if side == "SELL" else 1.001),
                          commission_pct=0.0005)
    mod.observe("before_orders", dict(state=state, mid_price=100.0, sim=sim, i=7))
    assert mod.records["before_orders"][-1][0] == 7

    rows = [[0, 0, 100.0, 95.0, 100.0, 99.0, 100.0],
            [1, 0, 110.0, 104.0, 100.0, 109.0, 101.0],
            [2, 1, 105.0, 100.0, 100.0, 104.0, 100.5],
            [3, 1, 95.0, 92.0, 100.0, 94.0, 99.0]]
    results = mod.analyze("before_orders", rows, np.zeros(4), 100.0)
    assert results and {r["stage"] for r in results} == {"before_orders"}
    row = results[0]
    for key in ("anchor", "horizon", "measure", "limits", "envelope", "best_static_limit_hindsight"):
        assert key in row
    assert (mod.OUT / "before_orders.npz").exists()


def test_research_agt_drawdown_main_fails_closed_on_current_engine(tmp_path):
    mod = _load("research_agt_drawdown", ROOT / "scripts" / "research_agt_drawdown.py")
    with pytest.raises(ValueError, match="original portfolio-funded revision"):
        asyncio.run(mod.main())


# ── scripts/verify_agt_rebased_drawdown.py (module level only) ───────────────

def test_verify_agt_rebased_drawdown_exposes_its_cli(tmp_path):
    mod = _load("verify_agt_rebased_drawdown", ROOT / "scripts" / "verify_agt_rebased_drawdown.py")
    assert asyncio.iscoroutinefunction(mod.main)


# ── scripts/run_parity_case.py ────────────────────────────────────────────────

def _write_parity_case(directory, *, bars=3, paper_missing=False, negative_cash=False,
                       short_curve=False, mismatched_curve=False, mismatched_fill=False,
                       funding_mismatch=False, session_mismatch=False):
    directory.mkdir(parents=True, exist_ok=True)
    info = dict(ticker="ZECUSDT", mode="historical", initial_capital=1000, reserve=5000, bars=bars)
    (directory / "case.json").write_text(json.dumps(info), encoding="utf-8")
    bt = dict(status="completed", equity_curve=[1000.0, 1010.0, 1005.0],
              fills=[dict(leg="BASE_LONG", bar=0, side="BUY", qty="1", price="100",
                          commission="0.04", realized_pnl="0.5")],
              events=[dict(kind="cooldown_start", bar=1), dict(kind="cooldown_end", bar=2)],
              commission_paid=0.04, funding_paid=-1.5, cash_reserve_usdt=4998.5,
              cycles=2, trailing_stops=1, open_positions={"long": 1.0, "short": 0.0})
    (directory / "backtest.json").write_text(json.dumps(bt), encoding="utf-8")
    curve = [1000.0, 1010.0, 1005.0]
    if short_curve:
        curve = curve[:2]
    if mismatched_curve:
        curve = [1000.0, 1011.0, 1005.0]
    for mode, fill_keys in (("paper", ("type", "trade_pnl")), ("real", ("symbol", "pnl"))):
        if paper_missing and mode == "paper":
            continue
        fills = [dict(bar=0, side="BUY", **{fill_keys[0]: "LONG"}, qty="1", price="100",
                      commission="0.04", **{fill_keys[1]: "0.5"})]
        if mismatched_fill and mode == "paper":
            fills[0]["qty"] = "2"
        (directory / f"{mode}_curve.json").write_text(json.dumps(curve), encoding="utf-8")
        (directory / f"{mode}_fills.json").write_text(json.dumps(fills), encoding="utf-8")
        value = dict(funding=-1.5, funding_reserve="4998.5", cycles=2, stops=1,
                     min_cash="-1" if negative_cash else "0", min_virtual="0",
                     positions={f"ZECUSDT_LONG": 1.0, f"ZECUSDT_SHORT": 0.0})
        (directory / f"{mode}.json").write_text(json.dumps(value), encoding="utf-8")
    (directory / "backtest_rates.json").write_text(
        json.dumps([dict(fundingTime=1700000000000, signed_amount="-1.5"),
                    dict(fundingTime=1700028800000, signed_amount="0")]), encoding="utf-8")
    paper_amount = "-1.5" if not funding_mismatch else "-2.0"
    (directory / "paper_funding.json").write_text(
        json.dumps([dict(event_time=1700000000.0, signed_amount=paper_amount)]), encoding="utf-8")
    (directory / "real_funding_receipts.json").write_text(
        json.dumps([dict(event_time=1700000060.0, signed_amount=paper_amount)]), encoding="utf-8")
    stops = [dict(bar=1, reason="equity_trailing_stop")] if not session_mismatch else []
    restarts = [dict(start_bar=2)] if not session_mismatch else [dict(start_bar=9)]
    sessions = stops + restarts
    for mode in ("paper", "real"):
        (directory / f"{mode}_sessions.json").write_text(json.dumps(sessions), encoding="utf-8")
    return info


def test_run_parity_case_compare_passes_a_consistent_tree(tmp_path):
    directory = tmp_path / "case"
    info = _write_parity_case(directory)
    mod = _load("run_parity_case", ROOT / "scripts" / "run_parity_case.py")
    result = mod.compare(directory, info)
    assert result["status"] == "PASS" and result["failures"] == []
    assert set(result["modes"]) == {"backtest", "paper", "real"}
    assert D(result["modes"]["paper"]["reserve_end"]) == D("4998.5")
    assert result["funding_events"] == 1
    assert (directory / "comparison.json").exists()


@pytest.mark.parametrize("scenario,match", [
    ("paper_missing", "paper: replay did not complete"),
    ("negative_cash", "paper: negative CASH or VIRTUAL"),
    ("short_curve", "incomplete equity curve"),
    ("mismatched_curve", "first equity mismatch at bar"),
    ("mismatched_fill", "first fill mismatch at index"),
    ("funding_mismatch", "historical funding cashflow mismatch"),
    ("session_mismatch", "stop/restart boundary mismatch"),
])
def test_run_parity_case_compare_reports_each_failure(tmp_path, scenario, match):
    directory = tmp_path / "case"
    kwargs = {scenario: True}
    info = _write_parity_case(directory, **kwargs)
    mod = _load("run_parity_case", ROOT / "scripts" / "run_parity_case.py")
    result = mod.compare(directory, info)
    assert result["status"] == "FAIL"
    assert any(match in failure for failure in result["failures"]), result["failures"]


def test_run_parity_case_preserves_an_existing_run(tmp_path):
    directory = tmp_path / "case"
    directory.mkdir()
    (directory / "case.json").write_text(json.dumps(dict(ticker="ZECUSDT", mode="disabled")),
                                         encoding="utf-8")
    (directory / "status.json").write_text(json.dumps(dict(status="RUNNING")), encoding="utf-8")
    mod = _load("run_parity_case", ROOT / "scripts" / "run_parity_case.py")
    with pytest.raises(ValueError, match="Preserve the existing run"):
        asyncio.run(mod.run_case(directory))


def test_verify_agt_external_funding_results_checks_curve_identity(tmp_path):
    mod = _load("verify_agt_external_funding_results",
                ROOT / "scripts" / "verify_agt_external_funding_results.py")
    left = np.zeros(525600)
    assert mod.maximum_error(left, left.copy()) == 0.0
    right = left.copy()
    right[10] = 1.0
    with pytest.raises(AssertionError):
        mod.maximum_error(left, right)
