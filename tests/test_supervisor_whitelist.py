
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from futures_portfolio.supervisor.swarm_manager import enforce_swarm_consistency

@pytest.mark.asyncio
async def test_enforce_swarm_consistency_respects_whitelist(tmp_path):
    connector = AsyncMock(get_positions=AsyncMock(return_value={
        'BTCUSDT_LONG': {'qty': 1}, 'ETHUSDT_SHORT': {'qty': -2}, 'SOLUSDT_LONG': {'qty': 5}}))
    module = 'futures_portfolio.supervisor.swarm_manager'
    with patch(f'{module}.CORE_PATH', tmp_path), \
         patch(f'{module}.get_running_bots_info', AsyncMock(return_value={'r_BTCUSDT': {}, 'r_ETHUSDT': {}})), \
         patch(f'{module}.stop_bot', AsyncMock()) as stop:
        await enforce_swarm_consistency(connector, {'live_swarm': ['BTCUSDT'], 'real_whitelist': ['ETHUSDT']})
    stop.assert_awaited_once_with('SOLUSDT', is_paper=False)


@pytest.mark.asyncio
async def test_enforce_swarm_consistency_all_allowed():
    connector = AsyncMock()
    connector.get_positions.return_value = {
        "BTCUSDT_LONG": {"qty": 1.0},
        "ETHUSDT_SHORT": {"qty": -2.0}
    }

    config = {
        "live_swarm": ["BTCUSDT"],
        "real_whitelist": ["ETHUSDT"]
    }

    with patch("asyncio.create_subprocess_shell", new_callable=AsyncMock) as mock_shell, \
         patch("futures_portfolio.supervisor.swarm_manager.get_running_bots_info", AsyncMock(return_value={"r_BTCUSDT": {}, "r_ETHUSDT": {}})):
        await enforce_swarm_consistency(connector, config)

        assert mock_shell.call_count == 0
