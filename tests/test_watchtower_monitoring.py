import asyncio
import os
import time
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from core.notifier import TelegramNotifier
from core.executor import PortfolioExecutor
from monitoring import health_check, send_monitor_report


def test_monitoring_dynamic_project_dir():
    """Verify that monitoring scripts use dynamic repo paths and not hardcoded Windows paths."""
    expected = Path(__file__).resolve().parent.parent
    assert health_check.PROJECT_DIR == expected
    assert send_monitor_report.PROJECT_DIR == expected
    assert not str(health_check.PROJECT_DIR).startswith("C:\\")


@pytest.mark.asyncio
async def test_alert_deduplication_cooldown():
    """Verify that duplicate alerts are suppressed within the cooldown interval."""
    notifier = TelegramNotifier()
    notifier.enabled = True
    notifier.use_queue = False
    notifier.alert_cooldown_sec = 60.0

    with patch.object(notifier, "send_message", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = True

        # First alert sent
        res1 = await notifier.send_alert("HIGH_DRAWDOWN", "Drawdown exceeds 10%")
        assert res1 is True
        assert mock_send.call_count == 1

        # Duplicate alert sent immediately -> should be suppressed
        res2 = await notifier.send_alert("HIGH_DRAWDOWN", "Drawdown exceeds 10%")
        assert res2 is False
        assert mock_send.call_count == 1

        # Different alert title/message -> should be allowed
        res3 = await notifier.send_alert("MARGIN_WARN", "Margin ratio warning")
        assert res3 is True
        assert mock_send.call_count == 2

        # After cooldown expiry -> should be allowed
        notifier._last_alert_time["HIGH_DRAWDOWN:Drawdown exceeds 10%"] = time.time() - 61.0
        res4 = await notifier.send_alert("HIGH_DRAWDOWN", "Drawdown exceeds 10%")
        assert res4 is True
        assert mock_send.call_count == 3


@pytest.mark.asyncio
async def test_dead_leg_detection_in_executor():
    """Verify that asymmetric rebalance results across paired legs are flagged as dead-leg."""
    connector = MagicMock()
    executor = PortfolioExecutor(connector)

    actions = [
        {"base_symbol": "BTCUSDT", "position_side": "LONG", "type": "ORDER", "diff_usdt": -50.0, "is_reduction": True},
        {"base_symbol": "BTCUSDT", "position_side": "SHORT", "type": "ORDER", "diff_usdt": 50.0, "is_reduction": False},
    ]

    results = [
        {"type": "LONG", "symbol": "BTCUSDT_LONG", "status": "SUCCESS", "qty": 0.001},
        {"type": "SHORT", "symbol": "BTCUSDT_SHORT", "status": "ERROR", "message": "Order failed: Insufficient margin"},
    ]

    executor._detect_dead_legs(actions, results)

    assert results[0].get("dead_leg_detected") is True
    assert results[1].get("dead_leg_detected") is True
