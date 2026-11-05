"""
Coverage push: supervisor.py 66% -> 85%+
Targets: get_pm2_processes, reconcile_swarm_state, stop_bot exceptions,
enforce_invariant_gate, reset_bot_state_files, start_bot, _ensure_real_bots_alive,
Amnesty, Reaper Guard, Authoritative Cleanup, manage_swarm real rotation.
"""
import pytest
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

M = "futures_portfolio.supervisor.swarm_manager"


# =====================================================================
# get_pm2_processes (lines 85-94)
# =====================================================================

@pytest.mark.asyncio
async def test_get_pm2_processes_empty_stdout():
    """Lines 89-90: empty stdout -> returns []"""
    from futures_portfolio.supervisor.swarm_manager import get_pm2_processes
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"", b"")
    with patch(f"{M}.asyncio.create_subprocess_shell", return_value=mock_proc):
        result = await get_pm2_processes()
    assert result == []


@pytest.mark.asyncio
async def test_get_pm2_processes_valid_json():
    """Line 91: valid JSON stdout -> parsed list"""
    from futures_portfolio.supervisor.swarm_manager import get_pm2_processes
    data = [{"name": "paper-aliceusdt", "pm2_env": {"status": "online"}}]
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (json.dumps(data).encode(), b"")
    with patch(f"{M}.asyncio.create_subprocess_shell", return_value=mock_proc):
        result = await get_pm2_processes()
    assert result == data


@pytest.mark.asyncio
async def test_get_pm2_processes_exception():
    """Lines 92-94: exception -> returns []"""
    from futures_portfolio.supervisor.swarm_manager import get_pm2_processes
    with patch(f"{M}.asyncio.create_subprocess_shell", side_effect=FileNotFoundError("pm2 not found")):
        result = await get_pm2_processes()
    assert result == []


@pytest.mark.asyncio
async def test_get_pm2_processes_json_decode_error():
    """Line 92: JSON decode error -> returns []"""
    from futures_portfolio.supervisor.swarm_manager import get_pm2_processes
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"NOT JSON {{{", b"")
    with patch(f"{M}.asyncio.create_subprocess_shell", return_value=mock_proc):
        result = await get_pm2_processes()
    assert result == []


# =====================================================================
# reconcile_swarm_state (lines 96-145)
# =====================================================================

@pytest.mark.asyncio
async def test_reconcile_swarm_state_no_orphans():
    from futures_portfolio.supervisor.swarm_manager import reconcile_swarm_state
    processes = [{'name': 'paper-alice', 'pm2_env': {'status': 'online'}}]
    with patch(f'{M}.get_pm2_processes', AsyncMock(return_value=processes)), \
         patch(f'{M}.stop_bot', AsyncMock()) as stop, \
         patch(f'{M}.asyncio.create_subprocess_shell', AsyncMock()) as shell:
        await reconcile_swarm_state(['ALICEUSDT'], 'paper')
    stop.assert_not_awaited()
    shell.assert_not_awaited()



@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['paper', 'real'])
async def test_reconcile_swarm_state_kills_orphans(tmp_path, mode):
    from futures_portfolio.supervisor.swarm_manager import reconcile_swarm_state
    (tmp_path/'paper_budget.sqlite').touch()
    processes = [{'name': f'{mode}-{name}', 'pm2_env': {'status': 'online'}} for name in ['alice', 'orphon']]
    process = AsyncMock(wait=AsyncMock(return_value=0))
    with patch(f'{M}.CORE_PATH', tmp_path), \
         patch(f'{M}.get_pm2_processes', AsyncMock(return_value=processes)), \
         patch(f'{M}.stop_bot', AsyncMock()) as stop, \
         patch(f'{M}.asyncio.create_subprocess_shell', AsyncMock(return_value=process)) as shell:
        await reconcile_swarm_state(['ALICEUSDT'], mode)
    stop.assert_awaited_once_with('ORPHONUSDT', is_paper=mode == 'paper')
    assert shell.call_args.args[0] == 'pm2 save'



@pytest.mark.asyncio
async def test_reconcile_swarm_state_empty_pm2():
    """Lines 108-115: empty PM2 -> no orphans"""
    from futures_portfolio.supervisor.swarm_manager import reconcile_swarm_state
    with patch(f"{M}.get_pm2_processes", return_value=[]):
        await reconcile_swarm_state(["ALICEUSDT"], "paper")


# =====================================================================
# stop_bot exception paths (lines 152-157)
# =====================================================================

@pytest.mark.asyncio
@pytest.mark.parametrize('is_paper,error', [(False, FileNotFoundError), (True, OSError), (False, RuntimeError)])
async def test_stop_bot_process_failure_propagates_without_releasing_budget(tmp_path, is_paper, error):
    from futures_portfolio.supervisor.swarm_manager import stop_bot
    pool = tmp_path/'paper_budget.sqlite'; pool.write_bytes(b'reserved-capital')
    with patch(f'{M}.CORE_PATH', tmp_path), \
         patch(f'{M}.asyncio.create_subprocess_shell', AsyncMock(side_effect=error('launch failed'))), \
         patch(f'{M}.asyncio.create_subprocess_exec', AsyncMock()) as close:
        with pytest.raises(error):
            await stop_bot('BTCUSDT', is_paper)
    close.assert_not_awaited()
    assert pool.read_bytes() == b'reserved-capital'









# =====================================================================
# enforce_invariant_gate (lines 258-284)
# =====================================================================

@pytest.mark.asyncio
async def test_enforce_invariant_gate_adds_exposed_ticker():
    """Lines 263-282: position with notional >= threshold -> forced into live_swarm"""
    from futures_portfolio.supervisor.swarm_manager import enforce_invariant_gate
    connector = MagicMock()
    connector.get_positions = AsyncMock(return_value={
        "NEWCOINUSDT_LONG": {"qty": "10.0", "mark_price": "10.0"}
    })
    config = {"live_swarm": ["BTCUSDT"], "min_notional_usdt": 5.0}
    await enforce_invariant_gate(connector, config)
    assert "NEWCOINUSDT" in config["live_swarm"]
    assert "BTCUSDT" in config["live_swarm"]


@pytest.mark.asyncio
async def test_enforce_invariant_gate_dust_filtered():
    """Lines 275-276: position below dust threshold -> NOT added"""
    from futures_portfolio.supervisor.swarm_manager import enforce_invariant_gate
    connector = MagicMock()
    connector.get_positions = AsyncMock(return_value={
        "DUSTUSDT_LONG": {"qty": "0.01", "mark_price": "1.0"}
    })
    config = {"live_swarm": [], "min_notional_usdt": 5.0}
    await enforce_invariant_gate(connector, config)
    assert "DUSTUSDT" not in config["live_swarm"]


@pytest.mark.asyncio
async def test_enforce_invariant_gate_empty_positions():
    """Lines 260-261: no positions -> early return"""
    from futures_portfolio.supervisor.swarm_manager import enforce_invariant_gate
    connector = MagicMock()
    connector.get_positions = AsyncMock(return_value=None)
    config = {"live_swarm": []}
    await enforce_invariant_gate(connector, config)
    assert config["live_swarm"] == []


@pytest.mark.asyncio
async def test_enforce_invariant_gate_zero_qty_filtered():
    """Line 269: qty=0 -> skip"""
    from futures_portfolio.supervisor.swarm_manager import enforce_invariant_gate
    connector = MagicMock()
    connector.get_positions = AsyncMock(return_value={
        "ZEROUSDT_LONG": {"qty": "0.0", "mark_price": "100.0"}
    })
    config = {"live_swarm": [], "min_notional_usdt": 5.0}
    await enforce_invariant_gate(connector, config)
    assert "ZEROUSDT" not in config["live_swarm"]


@pytest.mark.asyncio
async def test_enforce_invariant_gate_exception():
    """Lines 283-284: exception -> logged, not raised"""
    from futures_portfolio.supervisor.swarm_manager import enforce_invariant_gate
    connector = MagicMock()
    connector.get_positions = AsyncMock(side_effect=RuntimeError("exchange down"))
    config = {"live_swarm": []}
    await enforce_invariant_gate(connector, config)


# =====================================================================
# reset_bot_state_files (lines 286-361)
# =====================================================================

@pytest.mark.asyncio
async def test_reset_bot_state_files_rebases_on_trailing_stop(tmp_path, monkeypatch):
    """A completed session cannot compound beyond the configured capital cap."""
    from futures_portfolio.supervisor.swarm_manager import reset_bot_state_files
    monkeypatch.setattr(f"{M}.BASE_PATH", tmp_path)
    monkeypatch.setattr(f"{M}.CORE_PATH", tmp_path)
    old_state = {"trailing_stop_triggered": True, "last_tpv": 200.0}
    (tmp_path / "paper_state_BTCUSDT.json").write_text(json.dumps(old_state))
    (tmp_path / "paper_shadow_BTCUSDT.json").write_text(json.dumps({
        "balance": 100, "positions": {"BTCUSDT_LONG": 0, "BTCUSDT_SHORT": 0}}))
    config = {"portfolios": [{"paper_initial_capital": 100.0}]}
    await reset_bot_state_files("BTCUSDT", is_paper=True, config=config)
    new_state = json.loads((tmp_path / "paper_state_BTCUSDT.json").read_text())
    assert new_state["balance"] == 100.0


@pytest.mark.asyncio
async def test_reset_bot_state_files_no_state_uses_config(tmp_path, monkeypatch):
    """Lines 307-310: no old state -> uses config_capital"""
    from futures_portfolio.supervisor.swarm_manager import reset_bot_state_files
    monkeypatch.setattr(f"{M}.BASE_PATH", tmp_path)
    monkeypatch.setattr(f"{M}.CORE_PATH", tmp_path)
    config = {"portfolios": [{"paper_initial_capital": 150.0, "initial_capital": 80.0}]}
    await reset_bot_state_files("ETHUSDT", is_paper=True, config=config)
    new_state = json.loads((tmp_path / "paper_state_ETHUSDT.json").read_text())
    assert new_state["balance"] == 150.0


# =====================================================================
# start_bot (lines 363-378)
# =====================================================================

@pytest.mark.asyncio
async def test_start_bot_real_missing_snapshot_requires_allocation_proof(tmp_path):
    from futures_portfolio.supervisor.swarm_manager import start_bot
    connector = MagicMock(api_key='', testnet=True, verify_connection=AsyncMock())
    config = {'max_bots': 9, 'paper_mode_bots': 8, 'portfolios': [{'initial_capital': 100}]}
    with patch(f'{M}.CORE_PATH', tmp_path), patch(f'{M}.BASE_PATH', tmp_path), \
         patch(f'{M}.BinanceConnector', return_value=connector), \
         patch(f'{M}.get_running_bots_info', AsyncMock(return_value={})), \
         patch(f'{M}.reset_bot_state_files', AsyncMock()) as reset, \
         patch(f'{M}.asyncio.create_subprocess_shell', AsyncMock()) as launch:
        with pytest.raises(ValueError, match='identified exchange credentials'):
            await start_bot('BTCUSDT', False, config)
    reset.assert_not_awaited()
    launch.assert_not_awaited()
    assert not (tmp_path/'real_state_BTCUSDT.json').exists()



@pytest.mark.asyncio
async def test_start_bot_real_incomplete_snapshot_is_not_reset(tmp_path):
    from futures_portfolio.supervisor.swarm_manager import start_bot
    state = tmp_path/'real_state_BTCUSDT.json'; state.write_text('{}')
    with patch(f'{M}.CORE_PATH', tmp_path), patch(f'{M}.BASE_PATH', tmp_path), \
         patch(f'{M}.reset_bot_state_files', AsyncMock()) as reset, \
         patch(f'{M}.asyncio.create_subprocess_shell', AsyncMock()) as launch:
        with pytest.raises(ValueError, match='reconciled snapshot'):
            await start_bot('BTCUSDT', False, {'portfolios': [{'initial_capital': 100}]})
    reset.assert_not_awaited()
    launch.assert_not_awaited()
    assert state.read_text() == '{}'



@pytest.mark.asyncio
async def test_start_bot_paper_invalid_state_does_not_reset(tmp_path):
    from futures_portfolio.supervisor.swarm_manager import start_bot
    state = tmp_path/'paper_state_BTCUSDT.json'; state.write_text('{}')
    with patch(f'{M}.CORE_PATH', tmp_path), patch(f'{M}.BASE_PATH', tmp_path), \
         patch(f'{M}.reset_bot_state_files', AsyncMock()) as reset, \
         patch(f'{M}.asyncio.create_subprocess_shell', AsyncMock()) as launch:
        with pytest.raises(ValueError, match='PAPER resume requires reconciliation'):
            await start_bot('BTCUSDT', True, {'portfolios': [{'paper_initial_capital': 100}]})
    reset.assert_not_awaited()
    launch.assert_not_awaited()
    assert state.read_text() == '{}'



# =====================================================================
# _ensure_real_bots_alive (lines 1043-1066)
# =====================================================================

@pytest.mark.asyncio
async def test_ensure_real_bots_alive_missing_bot_restarts():
    """Lines 1057-1064: live_swarm bot not in PM2 -> restart"""
    from futures_portfolio.supervisor.swarm_manager import _ensure_real_bots_alive
    config = {"live_swarm": ["BTCUSDT"]}
    with patch(f"{M}.get_running_bots_info", AsyncMock(return_value={})), \
         patch(f"{M}.start_bot", AsyncMock()) as mock_start:
        await _ensure_real_bots_alive(config)
    mock_start.assert_called_once_with("BTCUSDT", is_paper=False, config=config)


@pytest.mark.asyncio
async def test_ensure_real_bots_alive_all_running():
    """Lines 1054-1058: all bots running -> no restart"""
    from futures_portfolio.supervisor.swarm_manager import _ensure_real_bots_alive
    config = {"live_swarm": ["BTCUSDT"]}
    running = {"r_BTCUSDT": {"name": "real-btcusdt", "paper": False}}
    with patch(f"{M}.get_running_bots_info", AsyncMock(return_value=running)), \
         patch(f"{M}.start_bot", AsyncMock()) as mock_start:
        await _ensure_real_bots_alive(config)
    mock_start.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_real_bots_alive_empty_swarm():
    """Line 1051: empty live_swarm -> early return"""
    from futures_portfolio.supervisor.swarm_manager import _ensure_real_bots_alive
    config = {"live_swarm": []}
    with patch(f"{M}.get_running_bots_info", AsyncMock()) as mock_info:
        await _ensure_real_bots_alive(config)
    mock_info.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_real_bots_alive_start_exception():
    """Line 1065-1066: start_bot raises -> logged, not raised"""
    from futures_portfolio.supervisor.swarm_manager import _ensure_real_bots_alive
    config = {"live_swarm": ["BTCUSDT"]}
    with patch(f"{M}.get_running_bots_info", AsyncMock(return_value={})), \
         patch(f"{M}.start_bot", AsyncMock(side_effect=RuntimeError("pm2 crash"))):
        await _ensure_real_bots_alive(config)


# =====================================================================
# enforce_swarm_consistency: exception paths (lines 228-243)
# =====================================================================

@pytest.mark.asyncio
async def test_enforce_swarm_consistency_no_positions():
    """Lines 172-173: no active positions -> return empty set"""
    from futures_portfolio.supervisor.swarm_manager import enforce_swarm_consistency
    connector = MagicMock()
    connector.get_positions = AsyncMock(return_value=None)
    config = {"live_swarm": [], "real_whitelist": []}
    result = await enforce_swarm_consistency(connector, config)
    assert result == set()


@pytest.mark.asyncio
async def test_enforce_swarm_consistency_unverified_close_failure_propagates(tmp_path):
    from futures_portfolio.supervisor.swarm_manager import enforce_swarm_consistency
    positions = {'ZECUSDT_LONG': {'qty': '-.5'}}
    connector = MagicMock(get_positions=AsyncMock(return_value=positions))
    with patch(f'{M}.CORE_PATH', tmp_path), \
         patch(f'{M}.get_running_bots_info', AsyncMock(return_value={})), \
         patch(f'{M}.start_bot', AsyncMock()) as start, \
         patch(f'{M}.stop_bot', AsyncMock(side_effect=RuntimeError('unconfirmed close'))) as stop:
        with pytest.raises(RuntimeError, match='unconfirmed close'):
            await enforce_swarm_consistency(connector, {'live_swarm': []})
    stop.assert_awaited_once_with('ZECUSDT', is_paper=False)
    start.assert_not_awaited()
    assert positions == {'ZECUSDT_LONG': {'qty': '-.5'}}

    # Should not raise
