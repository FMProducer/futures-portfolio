import sys
import os
import json
import subprocess
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

# Добавляем родительскую директорию (futures_portfolio/) в sys.path,
# чтобы тесты могли импортировать main, supervisor и т.д.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

# Pin the `dashboard` namespace package before any test inserts the dashboard/
# directory into sys.path: otherwise `import dashboard` may resolve to
# dashboard/dashboard.py (a module), which shadows the package and breaks
# `dashboard.<submodule>` imports order-dependently.
import importlib
importlib.import_module('dashboard')


# ═══════════════════════════════════════════════════════════════════════
# SAFETY NET: обнаружение живой торговой системы
# ═══════════════════════════════════════════════════════════════════════

from core.pm2_status import read_pm2_status


def _get_running_bot_names() -> list[str]:
    return [p['name'] for p in read_pm2_status()
            if p['pm2_env'].get('status') == 'online'
            and p['name'].startswith(('real-', 'paper-', 'supervisor'))]


def pytest_addoption(parser):
    parser.addoption(
        "--ignore-safety",
        action="store_true",
        default=False,
        help="Пропустить проверку безопасности (тесты при живой системе)",
    )


def pytest_configure(config):
    """Проверка при конфигурации — ещё до сбора тестов."""
    if config.getoption("--ignore-safety"):
        return

    try:
        bots = _get_running_bot_names()
    except Exception as error:
        pytest.exit(f'PM2 status unavailable; refusing tests: {error}. '
                    'Use --ignore-safety only for explicitly isolated tests.', returncode=1)
    if bots:
        msg = (
            "\n"
            "═══════════════════════════════════════════════════════════════\n"
            "⚠️  ОБНАРУЖЕНА ЖИВАЯ ТОРГОВАЯ СИСТЕМА (PM2 активен)\n"
            f"   Активные процессы: {', '.join(bots)}\n"
            "\n"
            "   Тесты НЕ запущены для защиты реальных ботов.\n"
            "\n"
            "   Варианты:\n"
            "   1. Остановить систему:  pm2 stop all\n"
            "   2. Принудительно:       pytest --ignore-safety\n"
            "═══════════════════════════════════════════════════════════════\n"
        )
        # Используем pytest.exit чтобы остановить весь прогон
        pytest.exit(msg, returncode=1)


# ═══════════════════════════════════════════════════════════════════════
# ФИКСТУРЫ (mock-слой — каждый тест изолирован от реальности)
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture
def mock_config():
    return {
        "binance_api_key": "test_key",
        "binance_api_secret": "test_secret",
        "telegram_token": "test_token",
        "telegram_chat_id": "test_chat_id",
        "initial_capital": 1000.0,
        "leverage": 1,
        "rebalance_threshold": 0.01,
        "rebalance_threshold_surplus": 0.01,
        "rebalance_threshold_deficit": 0.01,
        "tickers": ["BTCUSDT", "ETHUSDT"],
        "share_long": 40.0,
        "share_short": 40.0,
        "share_virt": 20.0,
        "min_cycles_for_rank": 10,
        "max_replace_per_cycle": 2,
        "tpv_safety_floor": 10.0,
        "limit_order_enabled": False,
        "limit_offset_pct": 0.01,
        "limit_timeout_sec": 60,
        "ticker_thresholds": {}
    }

@pytest.fixture
def mock_targets():
    return {
        "long": 40.0,
        "short": 40.0,
        "virt": 20.0
    }

@pytest.fixture
def mock_connector():
    connector = MagicMock()
    connector.client = MagicMock()
    connector.verify_connection = AsyncMock(return_value=True)
    connector.get_mark_prices = AsyncMock(return_value={"BTCUSDT": 50000.0, "ETHUSDT": 3000.0})
    connector.get_futures_balance = AsyncMock(return_value=1000.0)
    connector.get_futures_positions = AsyncMock(return_value=[])
    return connector

@pytest.fixture(autouse=True)
def mock_binance_client():
    """ГЛОБАЛЬНЫЙ мок: предотвращает любые реальные Binance API-вызовы."""
    from contextlib import ExitStack
    with patch("binance.client.Client") as mock, ExitStack() as stack:
        for name in ('core.connector', 'futures_portfolio.core.connector'):
            module = sys.modules.get(name)
            if module is not None:
                stack.enter_context(patch.object(module, 'Client', mock))
        yield mock


@pytest.fixture(autouse=True)
def guard_funding_reserve_writes(monkeypatch):
    from pathlib import Path
    import core.funding as funding
    original = funding.settle_funding_reserve
    workspace = Path(__file__).resolve().parents[1]

    def guarded(config_path, *args, **kwargs):
        if Path(config_path).resolve().is_relative_to(workspace):
            raise AssertionError("Funding tests must use a temporary config or an explicit mock")
        return original(config_path, *args, **kwargs)

    monkeypatch.setattr(funding, "settle_funding_reserve", guarded)
    from core import funding_store
    original_archive = funding_store.archive_events

    def guarded_archive(config_path, *args, **kwargs):
        if Path(config_path).resolve().is_relative_to(workspace):
            raise AssertionError("Funding archive tests must use a temporary config or explicit mock")
        return original_archive(config_path, *args, **kwargs)

    monkeypatch.setattr(funding_store, 'archive_events', guarded_archive)


@pytest.fixture(autouse=True)
def guard_worker_cli_children(monkeypatch):
    import asyncio
    from pathlib import Path
    original = asyncio.create_subprocess_exec
    worker = (Path(__file__).resolve().parents[1] / 'core/main.py').resolve()

    async def guarded(*args, **kwargs):
        if any(isinstance(arg, (str, os.PathLike)) and Path(arg).name == 'main.py'
               and Path(arg).resolve() == worker for arg in args):
            raise AssertionError('Unit tests must mock the trading worker subprocess')
        return await original(*args, **kwargs)

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', guarded)


# Offline deterministic PM2 status boundary: the dirty-PM2 work added a direct
# get_pm2_processes() call site in manage_swarm/reconcile_swarm_state that the
# per-test mocks (get_running_bots_info etc.) do not cover; without this the real
# `pm2 status` subprocess runs and the tests fail with EPERM in offline sandboxes.
# Scoped to exactly the supervisor test modules; available+empty only - the
# fail-closed (unavailable) behaviour is asserted by
# tests/test_pm2_telegram_boundary_20261121.py, and production read_pm2_status
# / its error handling are unchanged.
_PM2_OFFLINE_TEST_MODULES = {
    "test_supervisor", "test_supervisor_new", "test_supervisor_rotation_whitelist",
}


@pytest.fixture(autouse=True)
def pm2_status_boundary_offline(monkeypatch, request):
    if getattr(request.module, "__name__", "").rsplit(".", 1)[-1] not in _PM2_OFFLINE_TEST_MODULES:
        yield
        return
    import importlib
    swarm = importlib.import_module("futures_portfolio.supervisor.swarm_manager")
    monkeypatch.setattr(swarm, "read_pm2_status", lambda: [])
    yield
