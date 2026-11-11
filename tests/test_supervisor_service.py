import pytest
import json
import os
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch, mock_open
from futures_portfolio.supervisor.supervisor_service import get_sleep_interval, main


@pytest.mark.asyncio
async def test_wait_resets_once_for_each_confirmed_return():
    from futures_portfolio.supervisor import supervisor_service as service
    clock = [100.0]
    checks = []

    async def advance(seconds):
        clock[0] += seconds

    def returned():
        checks.append(clock[0])
        return 104.0 if clock[0] >= 104 else 102.0 if clock[0] >= 102 else 0.0

    with patch.object(service.time, 'time', side_effect=lambda: clock[0]), \
         patch.object(service, 'latest_return', side_effect=returned), \
         patch('core.paper_budget.reconcile_terminal_allocations', return_value=([], {})), \
         patch.object(service.asyncio, 'sleep', side_effect=advance):
        await service.wait_for_selection(5, 0)
    assert clock[0] == 109
    assert max(checks) == 108

def test_get_sleep_interval_valid():
    config = {"supervisor_interval_days": 1.0}
    with patch("builtins.open", mock_open(read_data=json.dumps(config))), \
         patch("os.path.exists", return_value=True):
        res = get_sleep_interval()
        assert res == 86400

def test_get_sleep_interval_missing_file():
    with patch("os.path.exists", return_value=False):
        res = get_sleep_interval()
        assert res == 3600 # default

def test_get_sleep_interval_floor():
    config = {"supervisor_interval_days": 0.000001}
    with patch("builtins.open", mock_open(read_data=json.dumps(config))), \
         patch("os.path.exists", return_value=True):
        res = get_sleep_interval()
        assert res == 300 # floor

@pytest.mark.asyncio
async def test_supervisor_service_main():
    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate.return_value = (b"success", b"")
    
    with patch("futures_portfolio.supervisor.supervisor_service.get_sleep_interval", return_value=300), \
         patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("asyncio.sleep", side_effect=[None, asyncio.CancelledError]):
        try:
            await main()
        except asyncio.CancelledError:
            pass
        
        assert mock_proc.communicate.called
