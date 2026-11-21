"""Owned focused boundary verification (2026-11-21).

PM2 status contract (core.pm2_status) and Telegram fake-credential worker, fully
offline: no live PM2, no real secrets, no network. Deterministic
available/unavailable cases preserving fail-closed safety. No production edits;
all boundary values are injected per-test via monkeypatch.
"""
import importlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_SWARM_MOD = "futures_portfolio.supervisor.swarm_manager"
_TS_MOD = "futures_portfolio.monitoring.telegram_sender"


def _swarm():
    return importlib.import_module(_SWARM_MOD)


# ── PM2 boundary: deterministic available / unavailable (fail-closed) ───────

async def test_pm2_boundary_available_empty(monkeypatch):
    swarm = _swarm()
    monkeypatch.setattr(swarm, "read_pm2_status", lambda: [])
    assert await swarm.get_pm2_processes() == []


async def test_pm2_boundary_unavailable_fails_closed(monkeypatch):
    swarm = _swarm()

    def unavailable():
        raise RuntimeError("PM2 status unavailable: EPERM")

    monkeypatch.setattr(swarm, "read_pm2_status", unavailable)
    with pytest.raises(RuntimeError, match="PM2 status unavailable"):
        await swarm.get_pm2_processes()


async def test_reconcile_swarm_state_fails_closed_when_pm2_unavailable(monkeypatch):
    """Unknown liveness must abort reconciliation BEFORE any stop/start action."""
    swarm = _swarm()

    def unavailable():
        raise RuntimeError("PM2 status unavailable: EPERM")

    monkeypatch.setattr(swarm, "read_pm2_status", unavailable)
    monkeypatch.setattr(swarm, "stop_bot", AsyncMock())
    with pytest.raises(RuntimeError):
        await swarm.reconcile_swarm_state(["BTCUSDT"], "paper")
    swarm.stop_bot.assert_not_called()


# ── core.pm2_status contract: the real module's error paths, separately ─────

def test_read_pm2_status_missing_installation_fails_closed(monkeypatch):
    import core.pm2_status as ps

    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(FileNotFoundError):
        ps.read_pm2_status()


def test_read_pm2_status_daemon_error_fails_closed(monkeypatch):
    import core.pm2_status as ps

    monkeypatch.setattr("shutil.which", lambda name: "X:\\fake" + (".cmd" if name == "pm2" else ".exe"))

    def fake_run(cmd, **kwargs):
        result = MagicMock()
        result.returncode = 1
        result.stderr = "EPERM"
        result.stdout = ""
        return result

    monkeypatch.setattr("subprocess.run", fake_run)
    with pytest.raises(RuntimeError, match="PM2 status unavailable"):
        ps.read_pm2_status()


def test_read_pm2_status_valid_list_contract(monkeypatch):
    import core.pm2_status as ps

    monkeypatch.setattr("shutil.which", lambda name: "X:\\fake" + (".cmd" if name == "pm2" else ".exe"))
    payload = json.dumps([{"name": "paper-btcusdt", "pid": 1, "pm_id": 1, "monit": {},
                           "pm2_env": {"status": "online"}}])

    def fake_run(cmd, **kwargs):
        result = MagicMock()
        result.returncode = 0
        result.stdout = payload
        result.stderr = ""
        return result

    monkeypatch.setattr("subprocess.run", fake_run)
    assert ps.read_pm2_status()[0]["name"] == "paper-btcusdt"


def test_read_pm2_status_invalid_shape_rejected(monkeypatch):
    import core.pm2_status as ps

    monkeypatch.setattr("shutil.which", lambda name: "X:\\fake" + (".cmd" if name == "pm2" else ".exe"))

    def fake_run(cmd, **kwargs):
        result = MagicMock()
        result.returncode = 0
        result.stdout = json.dumps([{"name": 123}])
        result.stderr = ""
        return result

    monkeypatch.setattr("subprocess.run", fake_run)
    with pytest.raises(ValueError, match="Invalid PM2 process list"):
        ps.read_pm2_status()


# ── Telegram worker: fake credentials in a monkeypatched env, mocked HTTP ───

def _telegram():
    return importlib.import_module(_TS_MOD)


async def test_telegram_worker_success_fake_token_mocked_http(monkeypatch, tmp_path):
    ts = _telegram()
    queue = tmp_path / "queue"
    queue.mkdir()
    (queue / "msg_1.json").write_text(json.dumps({"text": "msg"}), encoding="utf-8")

    # fake credentials: never read the real .env, never send real HTTP
    monkeypatch.setattr(ts, "TOKEN", "FAKE-BOT-9f8e7d6c5b4a")
    monkeypatch.setattr(ts, "CHAT_ID", "42")
    monkeypatch.setattr(ts, "QUEUE_DIR", queue)
    monkeypatch.setattr(f"{_TS_MOD}.ProxyConnector.from_url", MagicMock())
    monkeypatch.setattr("aiohttp.ClientSession", MagicMock())
    monkeypatch.setattr(f"{_TS_MOD}.send_to_telegram", AsyncMock(return_value=True))
    with patch("asyncio.sleep", side_effect=[None, _make_cancelled()]):
        try:
            await ts.worker()
        except BaseException:
            pass
    assert not (queue / "msg_1.json").exists(), "worker must remove the message after a successful send"


def _make_cancelled():
    import asyncio
    return asyncio.CancelledError()


async def test_telegram_worker_refuses_placeholder_credentials(monkeypatch, tmp_path):
    """Production guard: 'test_token' trips the _is_placeholder indicators ('test').

    This documents why the pre-existing test fixture that patches TOKEN='test_token'
    cannot reach the success path - a fixture bug, not a production defect."""
    ts = _telegram()
    monkeypatch.setattr(ts, "TOKEN", "test_token")
    monkeypatch.setattr(ts, "CHAT_ID", "test_chat")
    monkeypatch.setattr(ts, "QUEUE_DIR", tmp_path / "queue")
    send_spy = AsyncMock()
    session_spy = MagicMock()
    monkeypatch.setattr(f"{_TS_MOD}.send_to_telegram", send_spy)
    monkeypatch.setattr("aiohttp.ClientSession", MagicMock(return_value=session_spy))
    await ts.worker()  # returns early; no hang, no network
    send_spy.assert_not_called()
    session_spy.__aenter__.assert_not_called()
