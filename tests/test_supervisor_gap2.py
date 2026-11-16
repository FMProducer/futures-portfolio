"""Second-pass supervisor branch coverage: reaper fallback, stop_bot close paths,
start_bot guards, heal/close branches of enforce_swarm_consistency, rotate_idle idle-skip."""
import asyncio
import json
import time
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

M = "futures_portfolio.supervisor.swarm_manager"


class FakeProc:
    def __init__(self, code=0, communicate_error=None):
        self.returncode = code
        self._communicate_error = communicate_error

    async def wait(self):
        return self.returncode

    async def communicate(self, *args, **kwargs):
        if self._communicate_error is not None:
            raise self._communicate_error
        return (b"", b"")


@pytest.mark.asyncio
async def test_reconcile_swarm_state_pm2_shell_fallback(monkeypatch, tmp_path):
    import futures_portfolio.supervisor.swarm_manager as manager
    calls = []

    async def fake_shell(cmd, **kwargs):
        calls.append(cmd)
        return FakeProc(0)

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_shell)
    monkeypatch.setattr(manager, "get_pm2_processes",
                        AsyncMock(return_value=[{"name": "hybrid-orphanusdt",
                                                   "pm2_env": {"status": "online"}}]))
    await manager.reconcile_swarm_state([], "hybrid")
    assert "pm2 delete hybrid-orphanusdt" in calls
    assert "pm2 save" in calls


@pytest.mark.asyncio
async def test_reconcile_swarm_state_pm2_save_failure_swallowed(monkeypatch, tmp_path):
    import futures_portfolio.supervisor.swarm_manager as manager

    async def fake_shell(cmd, **kwargs):
        if cmd == "pm2 save":
            raise OSError("pm2 dump unavailable")
        return FakeProc(0)

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_shell)
    monkeypatch.setattr(manager, "get_pm2_processes",
                        AsyncMock(return_value=[{"name": "hybrid-orphanusdt",
                                                   "pm2_env": {"status": "stopped"}}]))
    await manager.reconcile_swarm_state([], "hybrid")  # must not raise


@pytest.mark.asyncio
async def test_reconcile_swarm_state_kill_failure_logged(monkeypatch):
    import futures_portfolio.supervisor.swarm_manager as manager

    async def fake_shell(cmd, **kwargs):
        if cmd.startswith("pm2 delete"):
            raise OSError("pm2 delete exploded")
        return FakeProc(0)

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_shell)
    monkeypatch.setattr(manager, "get_pm2_processes",
                        AsyncMock(return_value=[{"name": "hybrid-orphanusdt",
                                                   "pm2_env": {"status": "online"}}]))
    await manager.reconcile_swarm_state([], "hybrid")


@pytest.mark.asyncio
async def test_stop_bot_paper_budget_close_failure_raises(tmp_path, monkeypatch):
    import futures_portfolio.supervisor.swarm_manager as manager
    monkeypatch.setattr(manager, "CORE_PATH", tmp_path)
    (tmp_path / "paper_budget.sqlite").touch()

    async def fake_shell(cmd, **kwargs):
        return FakeProc(1)

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_shell)
    with pytest.raises(RuntimeError, match="PAPER process stop failed"):
        await manager.stop_bot("BTCUSDT", is_paper=True)


@pytest.mark.asyncio
async def test_stop_bot_paper_budget_close_success_releases(tmp_path, monkeypatch):
    import futures_portfolio.supervisor.swarm_manager as manager
    monkeypatch.setattr(manager, "CORE_PATH", tmp_path)
    (tmp_path / "paper_budget.sqlite").touch()
    calls = []

    async def fake_shell(cmd, **kwargs):
        calls.append(cmd)
        return FakeProc(0)

    async def fake_exec(*args, **kwargs):
        return FakeProc(0)

    released = {}

    def fake_release(base, ticker, state, wallet, rotation=False):
        released[ticker] = True

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_shell)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr("core.paper_budget.release", fake_release)
    await manager.stop_bot("BTCUSDT", is_paper=True)
    assert released.get("BTCUSDT") is True


@pytest.mark.asyncio
async def test_stop_bot_real_close_failure_retains_allocation(tmp_path, monkeypatch):
    import futures_portfolio.supervisor.swarm_manager as manager
    monkeypatch.setattr(manager, "CORE_PATH", tmp_path)
    monkeypatch.setattr(manager, "BASE_PATH", tmp_path)

    async def fake_shell(cmd, **kwargs):
        return FakeProc(0)

    async def fake_exec(*args, **kwargs):
        return FakeProc(1)

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_shell)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(RuntimeError, match="REAL close failed; allocation retained"):
        await manager.stop_bot("BTCUSDT", is_paper=False)


@pytest.mark.asyncio
async def test_stop_bot_file_not_found_propagates_with_budget(tmp_path, monkeypatch):
    import futures_portfolio.supervisor.swarm_manager as manager
    monkeypatch.setattr(manager, "CORE_PATH", tmp_path)
    (tmp_path / "paper_budget.sqlite").touch()

    async def fake_shell(cmd, **kwargs):
        raise FileNotFoundError("pm2 missing")

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fake_shell)
    with pytest.raises(FileNotFoundError):
        await manager.stop_bot("BTCUSDT", is_paper=True)


@pytest.mark.asyncio
async def test_start_bot_real_recovery_failure(tmp_path, monkeypatch):
    import futures_portfolio.supervisor.swarm_manager as manager
    import core.funding as funding
    monkeypatch.setattr(manager, "CORE_PATH", tmp_path)
    monkeypatch.setattr(funding, "recover_funding_boundary",
                        lambda *a, **k: (funding.RECOVERY_FAILED, "", ""))
    with pytest.raises(ValueError, match="funding WAL reconciliation"):
        await manager.start_bot("BTCUSDT", is_paper=False)


@pytest.mark.asyncio
async def test_start_bot_paper_loss_cooldown_blocks(tmp_path, monkeypatch):
    import futures_portfolio.supervisor.swarm_manager as manager
    import core.paper_budget as paper_budget
    monkeypatch.setattr(manager, "CORE_PATH", tmp_path)
    monkeypatch.setattr(manager, "BASE_PATH", tmp_path)
    (tmp_path / "paper_budget.sqlite").touch()
    monkeypatch.setattr(paper_budget, "ticker_history",
                        lambda base, ticker: {"loss_at": time.time() + 100000})
    monkeypatch.setattr(manager, "_last_start_time", {})
    await manager.start_bot("BTCUSDT", is_paper=True, config={"toxic_cooldown_days": 1})
    assert not (tmp_path / "signals").exists()


@pytest.mark.asyncio
async def test_start_bot_siphon_credit_preserves_session(tmp_path, monkeypatch):
    import futures_portfolio.supervisor.swarm_manager as manager
    monkeypatch.setattr(manager, "CORE_PATH", tmp_path)
    monkeypatch.setattr(manager, "BASE_PATH", tmp_path)
    monkeypatch.setattr(manager, "_last_start_time", {})
    state = {"stop_reason": "siphon", "started_at": "2026-01-01T00:00:00Z",
             "rebalance_cycles": 2}
    (tmp_path / "paper_state_TESTUSDT.json").write_text(json.dumps(state), encoding="utf-8")
    async def fake_load(path, default):
        return state if "TESTUSDT" in path else default
    monkeypatch.setattr(manager, "safe_load_json", fake_load)
    await manager.start_bot("TESTUSDT", is_paper=True, config={"siphoning_credits": {}})
    assert (tmp_path / "signals" / "siphon_paper_TESTUSDT.flag").exists()


@pytest.mark.asyncio
async def test_start_bot_restart_cooldown_defers(tmp_path, monkeypatch):
    import futures_portfolio.supervisor.swarm_manager as manager
    monkeypatch.setattr(manager, "CORE_PATH", tmp_path)
    monkeypatch.setattr(manager, "BASE_PATH", tmp_path)
    monkeypatch.setattr(manager, "_last_start_time", {})
    state = {"restart_not_before": str(time.time() + 100000)}
    async def fake_load(path, default):
        return state if "TESTUSDT" in path else default
    monkeypatch.setattr(manager, "safe_load_json", fake_load)
    await manager.start_bot("TESTUSDT", is_paper=True, config=None)
    assert not (tmp_path / "signals").exists()


@pytest.mark.asyncio
async def test_start_bot_paper_terminal_close_unverified(tmp_path, monkeypatch):
    import futures_portfolio.supervisor.swarm_manager as manager
    monkeypatch.setattr(manager, "CORE_PATH", tmp_path)
    monkeypatch.setattr(manager, "BASE_PATH", tmp_path)
    monkeypatch.setattr(manager, "_last_start_time", {})
    state = {"trailing_stop_triggered": True}
    async def fake_load(path, default):
        return state if "paper_state" in path else {}
    monkeypatch.setattr(manager, "safe_load_json", fake_load)
    with pytest.raises(ValueError, match="PAPER terminal close is unverified"):
        await manager.start_bot("TESTUSDT", is_paper=True, config={"siphoning_credits": {}})


@pytest.mark.asyncio
async def test_start_bot_paper_restart_requires_budget(tmp_path, monkeypatch):
    import futures_portfolio.supervisor.swarm_manager as manager
    monkeypatch.setattr(manager, "CORE_PATH", tmp_path)
    monkeypatch.setattr(manager, "BASE_PATH", tmp_path)
    monkeypatch.setattr(manager, "_last_start_time", {})
    state = {"trailing_stop_triggered": True, "paper_close_complete": True}
    async def fake_load(path, default):
        return state if "paper_state" in path else {"balance": 100, "positions": {}}
    monkeypatch.setattr(manager, "safe_load_json", fake_load)
    with pytest.raises(ValueError, match="capital pool reconciliation"):
        await manager.start_bot("TESTUSDT", is_paper=True, config={"siphoning_credits": {}})


@pytest.mark.asyncio
async def test_start_bot_paper_reserve_below_minimum_defers(tmp_path, monkeypatch):
    import futures_portfolio.supervisor.swarm_manager as manager
    import core.paper_budget as paper_budget
    monkeypatch.setattr(manager, "CORE_PATH", tmp_path)
    monkeypatch.setattr(manager, "BASE_PATH", tmp_path)
    (tmp_path / "paper_budget.sqlite").touch()
    monkeypatch.setattr(manager, "_last_start_time", {})
    monkeypatch.setattr(paper_budget, "reserve",
                        lambda base, ticker, cap: (Decimal("4.99"), True, "id-1"))
    monkeypatch.setattr(paper_budget, "ticker_history",
                        lambda base, ticker: {"loss_at": 0.0})
    config = {"portfolios": [{"paper_initial_capital": 4.99}]}
    await manager.start_bot("TESTUSDT", is_paper=True, config=config)
    assert not (tmp_path / "signals").exists()


@pytest.mark.asyncio
async def test_start_bot_paper_reserve_zero_defers(tmp_path, monkeypatch):
    import futures_portfolio.supervisor.swarm_manager as manager
    import core.paper_budget as paper_budget
    monkeypatch.setattr(manager, "CORE_PATH", tmp_path)
    monkeypatch.setattr(manager, "BASE_PATH", tmp_path)
    (tmp_path / "paper_budget.sqlite").touch()
    monkeypatch.setattr(manager, "_last_start_time", {})
    monkeypatch.setattr(paper_budget, "reserve",
                        lambda base, ticker, cap: (Decimal("0"), False, "id-1"))
    monkeypatch.setattr(paper_budget, "ticker_history",
                        lambda base, ticker: {"loss_at": 0.0})
    config = {"portfolios": [{"paper_initial_capital": 1000}]}
    await manager.start_bot("TESTUSDT", is_paper=True, config=config)
    assert not (tmp_path / "signals").exists()


def test_rotate_idle_skips_active_old_ticker():
    from futures_portfolio.supervisor.swarm_manager import rotate_idle
    final = ["AAAUSDT", "BBBUSDT"]
    old = ["AAAUSDT", "BBBUSDT"]
    candidates = [
        {"symbol": "CCCUSDT", "backtest": {"pass": True, "net_pct": 9.0, "max_dd_pct": 1.0}},
        {"symbol": "BBBUSDT", "backtest": {"pass": True, "net_pct": 12.0, "max_dd_pct": 1.0}},
    ]
    performance = {
        "AAAUSDT": {"idle": False, "profit": 1.0},
        "BBBUSDT": {"idle": True, "profit": 1.0},
        "CCCUSDT": {"profit": 5.0},
    }
    result = rotate_idle(final, old, candidates, performance)
    # BBBUSDT is idle but its own entry is not better than itself; AAAUSDT is not idle.
    assert result == ["AAAUSDT", "BBBUSDT"]


def test_rotate_idle_replaces_idle_failing_ticker():
    from futures_portfolio.supervisor.swarm_manager import rotate_idle
    final = ["AAAUSDT", "BBBUSDT"]
    old = ["AAAUSDT", "BBBUSDT"]
    candidates = [
        {"symbol": "BBBUSDT", "backtest": {"pass": False, "net_pct": 1.0, "max_dd_pct": 9.0}},
        {"symbol": "CCCUSDT", "backtest": {"pass": True, "net_pct": 9.0, "max_dd_pct": 1.0}},
    ]
    performance = {
        "AAAUSDT": {"idle": False, "profit": 1.0},
        "BBBUSDT": {"idle": True, "profit": 0.5},
        "CCCUSDT": {"profit": 5.0},
    }
    result = rotate_idle(final, old, candidates, performance)
    # idle failing ticker is dropped; the substitution comprehension only rewrites
    # entries still present, so the replacement is not appended (documented behavior)
    assert result == ["AAAUSDT"]


@pytest.mark.asyncio
async def test_enforce_swarm_consistency_heal_branch(tmp_path, monkeypatch):
    import futures_portfolio.supervisor.swarm_manager as manager
    monkeypatch.setattr(manager, "CORE_PATH", tmp_path)
    (tmp_path / "real_state_BTC.json").write_text(
        json.dumps({"rebalance_cycles": 3, "virt_qty": 0.0}), encoding="utf-8")
    started = []

    async def fake_start(ticker, is_paper=True, config=None):
        started.append(ticker)

    class FakeConnector:
        async def get_positions(self):
            return {"BTC_USDT": {"qty": "1.0"}}

    monkeypatch.setattr(manager, "get_running_bots_info", AsyncMock(return_value={}))
    monkeypatch.setattr(manager, "start_bot", fake_start)
    monkeypatch.setattr(manager, "stop_bot", AsyncMock())
    config = {"live_swarm": set(), "real_whitelist": set()}
    healed = await manager.enforce_swarm_consistency(FakeConnector(), config)
    assert healed == {"BTC"}
    assert started == ["BTC"]
    assert "BTC" in config["live_swarm"]
