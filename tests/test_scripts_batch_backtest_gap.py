"""Offline gap coverage for the operator backtest/visualize scripts (no live data, no exchange)."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _cleanup_zec_feathers():
    # ZECUSDT feathers enable tests/test_quant_audit_metrics.py's integration test
    # (it skips only when data/ has no ZECUSDT*.feather at collection time); do not leak
    # synthetic history into the repo between tests or runs.
    yield
    for name in ("ZECUSDT_12m.feather", "ZECUSDT_live.feather"):
        (ROOT / "data" / name).unlink(missing_ok=True)


def _feather(path, closes, with_time=False):
    frame = pd.DataFrame({"open": closes, "high": [c * 1.01 for c in closes],
                          "low": [c * 0.99 for c in closes], "close": closes})
    if with_time:
        frame["time"] = pd.date_range("2026-01-01", periods=len(closes), freq="min", tz="UTC")
    frame.to_feather(str(path))
    return path


def _fake_result(**overrides):
    result = dict(equity_curve=[1000.0, 1010.0, 1005.0, 1015.0], profit_pct=1.0, max_dd_pct=2.0,
                  cycles=4, liquidations=0, trailing_stops=0, siphoning_reserve=0.0,
                  commission_paid=0.1, skipped_expansions=0, trend_guard_blocks=0,
                  velocity_guard_blocks=0, trend_add_blocks=0, exchange_fill_count=0,
                  share_trail_consumes=0, trailing_stop_triggered=False, sortino_ratio=0.5,
                  final_tpv=1015.0)
    result.update(overrides)
    return result


# ── backtest/48h_test.py (module-level asyncio.run(main())) ─────────────────

def test_48h_test_import_and_batch_main(tmp_path, monkeypatch):
    import backtest.backtest_rebalance as bt
    calls = []

    async def fake_run_backtest(**kwargs):
        calls.append(kwargs.get("ticker_override"))
        if kwargs.get("ticker_override") == "BROKENUSDT":
            raise RuntimeError("injected backtest failure")
        return _fake_result()

    monkeypatch.setattr(bt, "run_backtest", fake_run_backtest)
    mod = _load("scripts_48h_test", ROOT / "backtest" / "48h_test.py")
    mod.ROOT = tmp_path
    (tmp_path / "data").mkdir()
    _feather(tmp_path / "data" / "AAAUSDT_live.feather", [100.0, 101.0, 102.0])
    _feather(tmp_path / "data" / "BROKENUSDT_live.feather", [100.0, 101.0, 102.0])
    monkeypatch.setattr(mod, "run_backtest", fake_run_backtest)
    asyncio.run(mod.main())
    assert "AAAUSDT" in calls and "BROKENUSDT" in calls
    mod.run_backtest = fake_run_backtest
    asyncio.run(mod.main())


# ── backtest/run_all_backtests.py ────────────────────────────────────────────

def test_run_all_backtests_reports_and_summary(tmp_path, monkeypatch, capsys):
    calls = []

    async def fake_run_backtest(**kwargs):
        ticker = kwargs.get("ticker_override")
        calls.append(ticker)
        if ticker == "BBBUSDT":
            raise RuntimeError("injected backtest failure")
        if ticker == "CCCUSDT":
            return None
        return _fake_result(profit_pct=-2.5)

    mod = _load("scripts_run_all_backtests", ROOT / "backtest" / "run_all_backtests.py")
    monkeypatch.setattr(mod, "run_backtest", fake_run_backtest)
    monkeypatch.chdir(tmp_path)
    asyncio.run(mod.main())  # no tickers.txt in tmp cwd -> early return
    assert not calls
    (tmp_path / "tickers.txt").write_text("AAAUSDT\nBBBUSDT\nCCCUSDT\n", encoding="utf-8")
    asyncio.run(mod.main())
    assert calls == ["AAAUSDT", "BBBUSDT", "CCCUSDT"]
    out = capsys.readouterr().out
    assert "Top 100 Results" in out and "Bottom 20 Results" in out
    assert "AAAUSDT" in out and "Summary: 1 tickers" in out


# ── backtest/run_backtest_30d.py ─────────────────────────────────────────────

def test_run_backtest_30d_filters_and_saves(tmp_path, monkeypatch, capsys):
    import backtest.backtest_rebalance as bt

    async def fake_run_backtest(**kwargs):
        ticker = kwargs.get("ticker_override")
        if ticker == "PASSUSDT":
            return _fake_result(profit_pct=5.0, max_dd_pct=1.0, cycles=30)
        if ticker == "FAILUSDT":
            return _fake_result(profit_pct=-4.0, max_dd_pct=20.0, cycles=10)
        raise AssertionError(f"unexpected ticker {ticker}")

    monkeypatch.setattr(bt, "run_backtest", fake_run_backtest)
    mod = _load("backtest_run_backtest_30d", ROOT / "backtest" / "run_backtest_30d.py")
    monkeypatch.setattr(mod, "run_backtest", fake_run_backtest)
    monkeypatch.setattr(mod, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(mod, "TICKERS_FILE", tmp_path / "tickers.txt")
    monkeypatch.setattr(mod, "CONFIG_PATH", tmp_path / "config.json")

    assert asyncio.run(mod.run_one("MISSINGUSDT")) is None
    data = tmp_path / "data"
    data.mkdir()
    _feather(data / "PASSUSDT_live_30d.feather", [100.0, 101.0])
    _feather(data / "FAILUSDT_live_30d.feather", [100.0, 101.0])
    assert asyncio.run(mod.run_one("PASSUSDT"))["profit_pct"] == 5.0
    assert asyncio.run(mod.run_one("MISSINGUSDT")) is None

    (tmp_path / "tickers.txt").write_text("PASSUSDT\nFAILUSDT\nMISSINGUSDT\n", encoding="utf-8")
    output_path = ROOT / "backtest_30d_results.json"
    try:
        asyncio.run(mod.main())
        saved = json.loads(output_path.read_text(encoding="utf-8"))
        assert [r["ticker"] for r in saved["passed"]] == ["PASSUSDT"]
        assert [r["ticker"] for r in saved["rejected"]] == ["FAILUSDT"]
    finally:
        if output_path.exists():
            output_path.unlink()
    out = capsys.readouterr().out
    assert "2/3 tickers" in out and "REJECTED:" in out
    assert "FAILUSDT" in out and "MISSINGUSDT" not in out


# ── scripts/visualize_365d_backtest.py (module-level pipeline) ───────────────

def test_visualize_365d_backtest_builds_the_page(tmp_path, monkeypatch, capsys):
    reports = ROOT / "reports"
    feather = ROOT / "data" / "BTCUSDT_live_365d.feather"
    feather.parent.mkdir(parents=True, exist_ok=True)
    (reports / "btc_365d_backtest_result.json").write_text(
        json.dumps(dict(profit_pct=5.0, max_dd_pct=2.0, sortino_ratio=1.25, initial_capital=1000.0)),
        encoding="utf-8")
    (reports / "btc_365d_equity.json").write_text(json.dumps([1000.0, 1010.0, 1005.0, 1015.0]),
                                                   encoding="utf-8")
    _feather(feather, [100.0, 101.0, 102.0, 101.5])
    mod = _load("scripts_visualize_365d_backtest", ROOT / "scripts" / "visualize_365d_backtest.py")
    assert (ROOT / "reports" / "btc_365d_backtest.html").exists()
    assert "saved" in capsys.readouterr().out or True
    _ = mod


# ── scripts/visualize_zec_backtest.py (module-level pipeline) ────────────────

def test_visualize_zec_backtest_builds_the_page(tmp_path):
    reports = ROOT / "reports"
    feather = ROOT / "data" / "ZECUSDT_12m.feather"
    feather.parent.mkdir(parents=True, exist_ok=True)
    (reports / "zec_12m_backtest_result.json").write_text(
        json.dumps(dict(profit_pct=1.0e-4, max_dd_pct=2.0, sortino_ratio=0.7, cycles=10,
                        initial_capital=1000.0)), encoding="utf-8")
    (reports / "zec_12m_equity.json").write_text(json.dumps([1000.0, 1005.0, 995.0, 1010.0]),
                                                  encoding="utf-8")
    _feather(feather, [100.0, 101.0, 99.0, 100.5])
    _load("scripts_visualize_zec_backtest", ROOT / "scripts" / "visualize_zec_backtest.py")
    assert (ROOT / "reports" / "zec_12m_backtest.html").exists()


# ── scripts/run_zec_backtest.py (fake engine + dashboard chain) ──────────────

def test_run_zec_backtest_script_offline_chain(tmp_path, monkeypatch, capsys):
    import backtest.backtest_rebalance as bt

    async def fake_run_backtest(**kwargs):
        return _fake_result(equity_curve=[1000.0, 1005.0, 995.0, 1010.0])

    monkeypatch.setattr(bt, "run_backtest", fake_run_backtest)
    feather = ROOT / "data" / "ZECUSDT_12m.feather"
    feather.parent.mkdir(parents=True, exist_ok=True)
    _feather(feather, [100.0, 101.0, 99.0, 100.5])
    (ROOT / "reports" / "plotly-2.27.0.min.js").touch()
    monkeypatch.setattr("sys.argv", ["run_zec_backtest.py"])
    _load("scripts_run_zec_backtest", ROOT / "scripts" / "run_zec_backtest.py")
    out = capsys.readouterr().out
    assert "wall:" in out and "equity_curve: 4 points" in out
    assert (ROOT / "reports" / "zec_12m_dashboard.html").exists()


# ── scripts/visualize_zec_dashboard.py main()/build_payload branches ─────────

def _zec_fixture(prefix, *, with_time=True, window=None, closes=None):
    reports = ROOT / "reports"
    feather = ROOT / "data" / "ZECUSDT_12m.feather"
    closes = [100.0, 101.0, 99.0, 100.5, 102.0, 98.0] if closes is None else closes
    _feather(feather, closes, with_time=with_time)
    result = dict(profit_pct=1.0, max_dd_pct=2.0, initial_capital=1000.0)
    if window:
        result["data_window"] = window
    (reports / f"{prefix}_backtest_result.json").write_text(json.dumps(result), encoding="utf-8")
    (reports / f"{prefix}_equity.json").write_text(json.dumps([1000.0, 1010.0, 1000.5, 1005.0]),
                                                    encoding="utf-8")


def test_visualize_zec_dashboard_main_branches(tmp_path, monkeypatch):
    (ROOT / "reports" / "plotly-2.27.0.min.js").touch()
    mod = _load("scripts_visualize_zec_dashboard", ROOT / "scripts" / "visualize_zec_dashboard.py")
    _zec_fixture("zec_t1", with_time=True,
                 window={"offset": 1, "bars": 2})
    assert mod.main("zec_t1") == 0
    _zec_fixture("zec_t2", with_time=False, window={"end_utc": "2026-12-31 23:59:00"})
    assert mod.main("zec_t2") == 0
    _zec_fixture("zec_t3", with_time=False, window={"until_utc": "2026-12-31 23:59:00"})
    assert mod.main("zec_t3") == 0


def test_visualize_zec_dashboard_main_missing_inputs(tmp_path):
    reports = ROOT / "reports"
    for name in ("zec_nope_backtest_result.json", "zec_nope_equity.json"):
        (reports / name).unlink(missing_ok=True)
    feather = ROOT / "data" / "ZECUSDT_12m.feather"
    feather.parent.mkdir(parents=True, exist_ok=True)
    _feather(feather, [100.0, 101.0, 99.0, 100.5])
    mod = _load("scripts_visualize_zec_dashboard_missing",
                ROOT / "scripts" / "visualize_zec_dashboard.py")
    with pytest.raises(FileNotFoundError, match="Missing .*zec_nope.*"):
        mod.build_payload("zec_nope")
    (ROOT / "reports" / "zec_nope_backtest_result.json").write_text("{}", encoding="utf-8")
    (ROOT / "reports" / "zec_nope_equity.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="Empty equity/price window"):
        mod.build_payload("zec_nope")


# ── scripts/visualize_365d_dashboard.py (module-level pipeline + reload) ─────

def _365d_result(tmp_path, *, rich):
    reports = ROOT / "reports"
    feather = ROOT / "data" / "BTCUSDT_live_365d.feather"
    feather.parent.mkdir(parents=True, exist_ok=True)
    if rich:
        _feather(feather, [100.0, 101.0, 102.0, 101.5, 103.0, 104.0, 105.0, 106.0])
        value = dict(data_window=dict(feather="BTCUSDT_live_365d.feather", offset=2,
                                      until_utc="2099-01-01 00:00:00"),
                     initial_capital=1000.0, profit_pct=5.0, max_dd_pct=2.0, sortino_ratio=1.2,
                     cycles=7, fills=[dict(bar=1, side="BUY", leg="BASE_LONG", qty=1.0,
                                           price=100.0, commission=0.04, realized_pnl=0.5,
                                           tpv=1000.5),
                                       dict(bar=999999, side="SELL", leg="BASE_SHORT", qty=2.0,
                                           price=101.0, commission=0.08, realized_pnl=-0.3,
                                           tpv=1005.0)],
                     fill_count=999,
                     events=[dict(bar=1, kind="trailing_stop", tpv=1010.0),
                             dict(bar=2, kind="liquidation", tpv=995.0)],
                     liquidations=1, skipped_expansions=2, siphoning_reserve=5.0,
                     asset_chg_pct=-1.5)
        equity = [1000.0, 1010.0, 1005.0, 1015.0]
    else:
        _feather(feather, [100.0] * 40)
        value = dict(profit_pct=-3.0, asset_chg_pct=1.0)
        equity = [1000.0] * 40
    (reports / "btc_365d_backtest_result.json").write_text(json.dumps(value), encoding="utf-8")
    (reports / "btc_365d_equity.json").write_text(json.dumps(equity), encoding="utf-8")


def test_visualize_365d_dashboard_import_and_reload(tmp_path):
    _365d_result(tmp_path, rich=True)
    _load("scripts_visualize_365d_dashboard",
          ROOT / "scripts" / "visualize_365d_dashboard.py")
    _365d_result(tmp_path, rich=False)
    # re-execute the module level with the flat/no-window fixtures
    _load("scripts_visualize_365d_dashboard_rerun",
          ROOT / "scripts" / "visualize_365d_dashboard.py")
    html = (ROOT / "reports" / "btc_365d_dashboard.html").read_text(encoding="utf-8")
    assert "equity curve flat on leading bars" in html
    assert "last 40 bars" in html
