"""Independent verification of the four-bug-fix candidate (2026-11-17).

Covers, without touching production files:
- dashboard/dashboard.py: time import + PM2 restart rate limit (mocked PM2)
- backtest/dashboard_attribution.py: empty-window reconciliation contract, nonempty unchanged
- core/paper_budget.py: sqlite close/rollback semantics (success, exception, early return,
  read-only, missing DB, persisted rollback of uncommitted writes)
- optimization/optimize_trend_guard.py: patched-backtester path isolation without real study
"""
import importlib
import json
import re
import shutil
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ── 1. dashboard: time import + PM2 restart rate limit ──────────────────────

@pytest.fixture(scope="module")
def dashboard_mod():
    dashboard_dir = ROOT / "dashboard"
    if str(dashboard_dir) not in sys.path:
        sys.path.insert(0, str(dashboard_dir))
    import importlib.util
    spec = importlib.util.spec_from_file_location("bugfix4_dashboard", dashboard_dir / "dashboard.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module


def test_dashboard_time_import_present(dashboard_mod):
    # the PM2 restart rate limiter calls time.time(); the module must bind the real time module
    import time as real_time
    assert dashboard_mod.time is real_time


def test_dashboard_restart_rate_limit_with_mocked_pm2(dashboard_mod, monkeypatch):
    import time
    app = dashboard_mod.create_app()
    app.config["TESTING"] = True
    client = app.test_client()
    from tests.test_runtime_surface_gap import _login
    token = _login(client)

    target = "paper-bugfix4-target"
    counter = {"now": 100.0}
    fake_time = SimpleNamespace(time=lambda: counter["now"])
    monkeypatch.setattr(dashboard_mod, "time", fake_time)
    executed = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, *args, **kwargs):
        executed.append(cmd[0:2])
        return Result()

    monkeypatch.setattr(dashboard_mod.subprocess, "run", fake_run)
    try:
        response = client.post("/api/control/pm2",
                               json={"action": "pm2_restart", "target": target},
                               headers={"X-CSRF-Token": token})
        assert response.status_code == 200
        assert response.get_json()["success"] is True
        assert executed == [["pm2", "restart"]]

        counter["now"] += 5.0  # inside the 30s cooldown
        response = client.post("/api/control/pm2",
                               json={"action": "pm2_restart", "target": target},
                               headers={"X-CSRF-Token": token})
        assert response.status_code == 200
        assert "Rate limited" in response.get_json()["error"]
        assert len(executed) == 1  # second call must not reach the subprocess

        counter["now"] += 60.0  # past the cooldown
        response = client.post("/api/control/pm2",
                               json={"action": "pm2_restart", "target": target},
                               headers={"X-CSRF-Token": token})
        assert response.get_json()["success"] is True
        assert len(executed) == 2
    finally:
        dashboard_mod._last_pm2_restart.pop(target, None)


# ── 2. attribution: empty-window contract, nonempty reconciliation ──────────

def test_attribution_empty_window_contract():
    from backtest.dashboard_attribution import reconstruct_attribution
    out = reconstruct_attribution([], [], [], initial_capital=1000.0, final_equity=1000.0)
    assert out["totals"]["unrealized"] == 0.0
    assert out["totals"]["residual"] == 0.0
    assert out["totals"]["final_equity"] == 1000.0
    assert out["series"]["idx"] == []
    assert out["checks"]["open_long"] == 0.0
    # empty window without an explicit final equity must not crash either
    out2 = reconstruct_attribution([], [], [], initial_capital=1000.0)
    assert out2["totals"]["final_equity"] == 0.0


def test_attribution_nonempty_window_unchanged_contract():
    from backtest.dashboard_attribution import reconstruct_attribution
    closes = [100.0, 101.0, 102.0, 103.0]
    equity = [999.96, 1000.96, 1001.96, 1002.92]
    fills = [
        {"bar": 0, "side": "BUY", "leg": "BASE_LONG", "qty": 1.0, "price": 100.0,
         "mid": 100.0, "commission": 0.04},
        {"bar": 3, "side": "SELL", "leg": "BASE_LONG", "qty": 1.0, "price": 103.0,
         "mid": 103.0, "commission": 0.04},
    ]
    out = reconstruct_attribution(closes, equity, fills, initial_capital=1000.0,
                                  final_equity=equity[-1])
    totals = out["totals"]
    assert totals["unrealized"] == 0.0  # flat at the end
    assert totals["realized"] == pytest.approx(3.0)
    assert totals["commissions"] == pytest.approx(0.08)
    assert totals["residual"] == pytest.approx(0.0, abs=1e-9)
    assert len(out["series"]["idx"]) == 4


# ── 3. paper_budget sqlite close/rollback semantics ──────────────────────────

def _seeded_root(tmp_path):
    from core import paper_budget
    root = tmp_path / "root"
    (root / "core").mkdir(parents=True)
    paper_budget.initialize(root, 1, 1000, {})
    paper_budget.reserve(root, "T", 100)
    paper_budget.bind(root, "T", {"initial_tpv": 100, "started_at": 5})
    return root


def _closed_state_and_wallet():
    state = {"base_ticker": "T", "started_at": 5, "initial_tpv": 100, "last_tpv": 90,
             "paper_close_complete": True, "paper_closed_at": 100, "rebalance_cycles": 0,
             "siphoning_reserve": 0}
    wallet = {"base_ticker": "T", "balance": 90, "positions": {"T_LONG": 0, "T_SHORT": 0}}
    return state, wallet


def _db_file(root):
    return root / "core" / "paper_budget.sqlite"


def test_paper_budget_success_path_releases_the_windows_lock(tmp_path):
    from core import paper_budget
    root = _seeded_root(tmp_path)
    state, wallet = _closed_state_and_wallet()
    paper_budget.release(root, "T", state, wallet)
    # original bug: the unclosed handle kept the file locked on win32, breaking cleanup
    shutil.rmtree(tmp_path)
    assert not tmp_path.exists()


def test_paper_budget_bind_exception_rolls_back_and_closes(tmp_path):
    from core import paper_budget
    root = _seeded_root(tmp_path)
    with pytest.raises(ValueError, match="PAPER session identity mismatch"):
        paper_budget.bind(root, "T", {"initial_tpv": 100, "started_at": 999})
    with closing(sqlite3.connect(_db_file(root))) as db:
        row = db.execute("SELECT session FROM allocations WHERE ticker='T'").fetchone()
    assert row[0] == "5"  # the failed bind must not have changed the session
    shutil.rmtree(tmp_path)


def test_paper_budget_release_identity_mismatch_is_persisted_rollback(tmp_path):
    from core import paper_budget
    root = _seeded_root(tmp_path)
    with closing(sqlite3.connect(_db_file(root))) as db:
        cash_before = db.execute("SELECT cash FROM account").fetchone()[0]
    bad_state, wallet = _closed_state_and_wallet()
    bad_state["started_at"] = 999
    with pytest.raises(ValueError, match="PAPER release identity mismatch"):
        paper_budget.release(root, "T", bad_state, wallet)
    with closing(sqlite3.connect(_db_file(root))) as db:
        cash_after = db.execute("SELECT cash FROM account").fetchone()[0]
        released = db.execute("SELECT released FROM allocations WHERE ticker='T'").fetchone()[0]
    assert cash_after == cash_before and released == 0
    shutil.rmtree(tmp_path)


def test_paper_budget_reserve_early_returns_rollback_and_release(tmp_path, monkeypatch):
    from core import paper_budget
    root = _seeded_root(tmp_path)
    capital, granted, session = paper_budget.reserve(root, "T", 500)
    assert (capital, granted, session) == (100.0, False, "5")  # re-reserve of an open intent
    # unaffordable reserve of a second ticker: no account mutation must persist
    paper_budget.reserve(root, "U", 5000)
    with closing(sqlite3.connect(_db_file(root))) as db:
        cash = db.execute("SELECT cash FROM account").fetchone()[0]
    assert float(cash) == 900.0  # untouched by the rejected reserve
    shutil.rmtree(tmp_path)


def test_paper_budget_initialize_failure_does_not_persist(tmp_path):
    from core import paper_budget
    root = tmp_path / "root2"
    (root / "core").mkdir(parents=True)
    with pytest.raises(ValueError, match="Cannot adopt unidentified PAPER session"):
        paper_budget.initialize(root, 1, 1000, {"T": {"initial_tpv": 50}})
    # the failed initialize must not leave an account row behind
    with closing(sqlite3.connect(_db_file(root))) as db:
        count = db.execute("SELECT count(*) FROM account").fetchone()[0]
    assert count == 0
    paper_budget.initialize(root, 1, 1000, {})  # a clean initialize now succeeds
    shutil.rmtree(tmp_path)


def test_paper_budget_close_discards_an_uncommitted_transaction(tmp_path):
    # the writing functions rely on close-without-commit discarding open transactions
    from core import paper_budget
    root = _seeded_root(tmp_path)
    with closing(sqlite3.connect(_db_file(root))) as db:
        baseline = db.execute("SELECT cash FROM account").fetchone()[0]
    raw = sqlite3.connect(_db_file(root))
    raw.execute("BEGIN IMMEDIATE")
    raw.execute("UPDATE account SET cash='0.0'")
    with closing(raw):  # close without commit/rollback, exactly like ctx_closing exits
        pass
    with closing(sqlite3.connect(_db_file(root))) as db:
        assert db.execute("SELECT cash FROM account").fetchone()[0] == baseline
    shutil.rmtree(tmp_path)


def test_paper_budget_read_only_and_missing_db_paths(tmp_path):
    from core import paper_budget
    root = _seeded_root(tmp_path)
    history = paper_budget.ticker_history(root, "T")
    assert history["active_session"] == "5"
    budget = paper_budget.read_budget(root)
    assert budget["cash"] == pytest.approx(900.0) and "T" in budget["allocations"]
    returned, blocked = paper_budget.reconcile_terminal_allocations(root)
    assert returned == []
    # no paper_state_T.json exists in this fixture, so T is blocked (not returned)
    assert "T" in blocked and "paper_state_T" in blocked["T"]
    shutil.rmtree(tmp_path)

    empty_root = tmp_path / "empty"
    (empty_root / "core").mkdir(parents=True)
    assert paper_budget.read_budget(empty_root) is None
    assert paper_budget.reconcile_terminal_allocations(empty_root) == ([], {})
    assert paper_budget.selection_ready(empty_root, {}) is True
    shutil.rmtree(empty_root)


# ── 4. optimizer: patched backtester path without real study/import leakage ──

def test_optimizer_patched_backtester_reads_the_repo_source_and_patches(tmp_path, monkeypatch):
    import importlib
    sys.modules.pop("optimization.optimize_trend_guard", None)
    module = importlib.import_module("optimization.optimize_trend_guard")
    try:
        # import-time side effect: a patched copy must exist at the repo root, read from
        # PROJECT_DIR/backtest (the fix), not PROJECT_DIR.parent/backtest (the old bug)
        patched_path = module.PROJECT_DIR / f"{module.patched_module_name}.py"
        assert patched_path.exists()
        patched = patched_path.read_text(encoding="utf-8")
        for marker in ("trend_guard_cfg.get('net_move_window_sec', 60) // 60",
                       '"history": state.history',
                       '"rebalance_log": state.rebalance_log'):
            assert marker in patched, f"regex patch missing: {marker}"
        # known defect (reported to coordinator): the engine now reads the lookback from
        # safety_guards, so the optimizer's "tg_trend_bars = 30" regex is dead and the
        # explored trend_lookback_bars dimension never reaches the engine
        assert "trend_guard_cfg.get('lookback_bars', 30)" not in patched
        # the net_move regex still matches the current engine source, so the patch fires
        source = (ROOT / "backtest" / "backtest_rebalance.py").read_text(encoding="utf-8")
        assert re.search(r"tg_nmg_bars\s*=\s*max\(1,\s*tg_nmg_window\s*//\s*60\)", source)
        assert re.search(r'\"sortino_ratio\":\s*float\(sortino_ratio\)', source)
        # defect characterization: the legacy hardcoded form the lookback regex targets
        # no longer exists in the engine, so that regex is dead code
        assert not re.search(r"tg_trend_bars\s*=\s*30", source)
        # importing the module must not run a real optuna study
        assert not (module.PROJECT_DIR / "optuna_trend_guard.db").exists()
        # cleanup mirrors the module's own atexit handler
        module.cleanup_patched_module()
        assert not patched_path.exists()
        assert module.patched_module_name not in sys.modules

        # direct call of the fixed path resolver
        name = module.generate_patched_backtester()
        generated = module.PROJECT_DIR / f"{name}.py"
        assert generated.exists() and "lookback_bars" in generated.read_text(encoding="utf-8")
        generated.unlink()  # cleanup_patched_module only tracks the import-time name
        assert not generated.exists()
    finally:
        sys.modules.pop("optimization.optimize_trend_guard", None)
        for entry in (str(module.PROJECT_DIR),):
            if entry in sys.path:
                sys.path.remove(entry)


def test_optimizer_missing_tickers_file_fails_closed(tmp_path, monkeypatch):
    import importlib
    sys.modules.pop("optimization.optimize_trend_guard", None)
    module = importlib.import_module("optimization.optimize_trend_guard")
    try:
        monkeypatch.setattr(module, "TICKERS_FILE", tmp_path / "missing.txt")
        with pytest.raises(FileNotFoundError):
            module.load_tickers()
        module.cleanup_patched_module()
    finally:
        sys.modules.pop("optimization.optimize_trend_guard", None)
        for entry in (str(module.PROJECT_DIR),):
            if entry in sys.path:
                sys.path.remove(entry)


# ── 5. D2 reproducer: the trend_lookback_bars dimension is a silent no-op ────
# Documented in reports/BUGFIX_D2_OPTIMIZER_DIMENSION_NOOP_20261117.md.
# Production routing is intentionally NOT changed here; this test characterizes
# the current behavior so the defect cannot regress silently.

def test_optimizer_d2_lookback_dimension_noop_reproducer(tmp_path, monkeypatch):
    import importlib
    import copy
    sys.modules.pop("optimization.optimize_trend_guard", None)
    module = importlib.import_module("optimization.optimize_trend_guard")
    try:
        monkeypatch.setattr(module, "PROJECT_DIR", tmp_path)
        monkeypatch.setattr(module, "DATA_DIR", tmp_path / "data")
        monkeypatch.setattr(module, "CONFIG_PATH", tmp_path / "config.json")
        (tmp_path / "config.json").write_text(json.dumps({"portfolios": [{}]}), encoding="utf-8")
        captured = []

        async def fake_run_backtest(config_path, *args, **kwargs):
            captured.append(copy.deepcopy(json.loads(Path(config_path).read_text())))
            return {"profit_pct": 0.0}

        monkeypatch.setattr(module.patched_backtest, "run_backtest", fake_run_backtest)
        base_params = {"net_move_block_pct": 2.2, "net_move_window_sec": 60,
                       "trend_min_move_pct": 0.7, "trend_eff_threshold": 0.45}
        cfg_in = {"portfolios": [{"safety_guards": {}}]}
        module.evaluate_ticker(cfg_in, "AAAUSDT", 1.0, {**base_params, "trend_lookback_bars": 10})
        module.evaluate_ticker(cfg_in, "AAAUSDT", 1.0, {**base_params, "trend_lookback_bars": 60})
        lo_cfg, hi_cfg = captured
        # the explored dimension lands in a config key the engine never reads...
        assert lo_cfg["trend_guard"]["lookback_bars"] == 10
        assert hi_cfg["trend_guard"]["lookback_bars"] == 60
        # ...while the key the engine actually reads (safety_guards.trend_lookback_bars)
        # is identical in both runs -> the optimizer dimension has no engine effect
        assert lo_cfg["portfolios"][0]["safety_guards"] == hi_cfg["portfolios"][0]["safety_guards"]
        assert "trend_lookback_bars" not in lo_cfg["portfolios"][0]["safety_guards"]
    finally:
        module.cleanup_patched_module()
        sys.modules.pop("optimization.optimize_trend_guard", None)
        for entry in (str(tmp_path),):
            if entry in sys.path:
                sys.path.remove(entry)
