"""Keep unit tests from starting a real trading worker child."""
import asyncio
from pathlib import Path
import sys

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['--real', '--paper'])
async def test_unmocked_trading_worker_child_is_refused(mode):
    worker = Path(__file__).resolve().parents[1] / 'core/main.py'
    with pytest.raises(AssertionError, match='must mock'):
        await asyncio.create_subprocess_exec(sys.executable, str(worker), '--stop', mode)


@pytest.mark.asyncio
async def test_isolated_python_probe_is_allowed():
    child = await asyncio.create_subprocess_exec(sys.executable, '-c', "print('isolated')",
                                                stdout=asyncio.subprocess.PIPE)
    output, _ = await child.communicate()
    assert child.returncode == 0 and output.strip() == b'isolated'


@pytest.mark.asyncio
@pytest.mark.parametrize('module_name', ['core.connector', 'futures_portfolio.core.connector'])
async def test_bound_binance_client_is_mocked(module_name, mock_binance_client):
    import importlib
    module = importlib.import_module(module_name)
    connection = module.BinanceConnector('isolated-test', 'isolated-test')
    await connection.verify_connection()
    assert connection.client is mock_binance_client.return_value
    mock_binance_client.assert_called_once()
