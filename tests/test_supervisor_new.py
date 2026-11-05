import pytest
import math
import json
from unittest.mock import MagicMock, AsyncMock, patch, mock_open
from futures_portfolio import supervisor
from futures_portfolio.supervisor.swarm_manager import calculate_bot_score, _calc_rotation_score, selective_merge_incubator, get_bot_efficiency, reset_bot_state_files, enforce_swarm_consistency, _ensure_real_bots_alive, manage_swarm

# Префикс для всех patch-путей — модуль supervisor как он виден тесту
M = "futures_portfolio.supervisor.swarm_manager"

def test_calculate_bot_score():
    # is_in_drawdown=True -> INF
    assert calculate_bot_score("BTCUSDT", {"profit": 10.0, "cycles": 20}, is_running_real=True, is_in_drawdown=True, min_cycles_required=10) == float('inf')
    
    # cycles < min_cycles_required and not is_running_real -> -INF
    assert calculate_bot_score("BTCUSDT", {"profit": 10.0, "cycles": 5}, is_running_real=False, is_in_drawdown=False, min_cycles_required=10) == -float('inf')
    
    # net_pnl <= 0 -> -INF
    assert calculate_bot_score("BTCUSDT", {"profit": 0.0, "cycles": 20}, is_running_real=True, is_in_drawdown=False, min_cycles_required=10) == -float('inf')
    assert calculate_bot_score("BTCUSDT", {"profit": -5.0, "cycles": 20}, is_running_real=True, is_in_drawdown=False, min_cycles_required=10) == -float('inf')
    
    # Profitable case
    p = {"profit": 100.0, "cycles": 20}
    min_cycles = 10
    expected_base = (100.0 / 20) * math.log1p(20)
    # is_running_real=False
    assert calculate_bot_score("BTCUSDT", p, is_running_real=False, is_in_drawdown=False, min_cycles_required=min_cycles) == pytest.approx(expected_base)
    # is_running_real=True -> +20% bonus
    assert calculate_bot_score("BTCUSDT", p, is_running_real=True, is_in_drawdown=False, min_cycles_required=min_cycles) == pytest.approx(expected_base * 1.2)

def test_calc_rotation_score():
    min_cycles = 10
    # cycles < min_cycles and net_pnl <= 0 -> -INF
    assert _calc_rotation_score("BTCUSDT", {"profit": 0, "cycles": 5}, min_cycles) == -float('inf')
    
    # net_pnl > 0
    p = {"profit": 100.0, "cycles": 20}
    expected_score = (100.0 / 20) * math.log1p(20)
    assert _calc_rotation_score("BTCUSDT", p, min_cycles) == pytest.approx(expected_score)
    
    # net_pnl <= 0 but enough cycles -> net_pnl * 0.01
    assert _calc_rotation_score("BTCUSDT", {"profit": -10.0, "cycles": 20}, min_cycles) == pytest.approx(-0.1)

@pytest.mark.asyncio
async def test_selective_merge_incubator():
    config = {"max_bots": 4, "max_replace_per_cycle": 2, "min_cycles_for_rank": 5}
    scanner_results = [
        {"symbol": "A"}, {"symbol": "B"}, {"symbol": "C"}, {"symbol": "D"}, {"symbol": "E"}
    ]
    
    # First start
    res = await selective_merge_incubator([], scanner_results, config, {})
    assert res == ["A", "B", "C", "D"]
    
    # Rotation
    old_incubator = ["A", "B", "C", "D"]
    perf_map = {
        "A": {"profit": 10.0, "cycles": 10}, # Profitable
        "B": {"profit": 5.0, "cycles": 10},  # Profitable
        "C": {"profit": -10.0, "cycles": 10}, # Unprofitable
        "D": {"profit": -20.0, "cycles": 10}  # Unprofitable
    }
    
    scanner_results_new = [
        {"symbol": "A"}, {"symbol": "B"}, {"symbol": "E"}, {"symbol": "F"}
    ]
    
    res = await selective_merge_incubator(old_incubator, scanner_results_new, config, perf_map)
    assert set(res) == {"A", "B", "E", "F"}

@pytest.mark.asyncio
async def test_get_bot_efficiency(tmp_path, monkeypatch):
    monkeypatch.setattr(f"{M}.BASE_PATH", tmp_path)
    monkeypatch.setattr(f"{M}.CORE_PATH", tmp_path)
    config = {"min_cycles_for_rank": 10}
    ticker = "BTCUSDT"
    
    with patch(f"{M}.safe_load_json", AsyncMock(return_value={})):
        res = await get_bot_efficiency(ticker, config)
        assert res["profit"] == 0.0
        assert res["cycles"] == 0

    state = {
        "last_profit": 100.0,
        "rebalance_cycles": 20,
        "trailing_stop_paper_timeout_end": 123456789.0
    }
    with patch(f"{M}.safe_load_json", AsyncMock(return_value=state)):
        res = await get_bot_efficiency(ticker, config)
        assert res["profit"] == 100.0
        assert res["cycles"] == 20
        assert res["eff"] == 100.0 / 20
        assert res["trailing_stop_paper_timeout_end"] == 123456789.0

@pytest.mark.asyncio
@pytest.mark.parametrize("is_paper,capital", [(False, 200.0), (True, 150.0)])
async def test_reset_bot_state_files_commits_pair_and_archives_originals(tmp_path, is_paper, capital):
    core = tmp_path / 'core'
    core.mkdir()
    state_path = core / ('paper_state_BTCUSDT.json' if is_paper else 'real_state_BTCUSDT.json')
    wallet_path = core / ('paper_shadow_BTCUSDT.json' if is_paper else 'shadow_state_BTCUSDT.json')
    state_path.write_text(json.dumps({'base_ticker': 'BTCUSDT', 'initial_tpv': capital, 'started_at': 1}))
    wallet_path.write_text(json.dumps({'balance': capital, 'positions': {'BTCUSDT_LONG': 0, 'BTCUSDT_SHORT': 0}}))
    originals = [state_path.read_bytes(), wallet_path.read_bytes()]
    config = {'portfolios': [{'initial_capital': 200, 'paper_initial_capital': 150}]}
    with patch(f'{M}.CORE_PATH', core), patch(f'{M}.BASE_PATH', tmp_path):
        await reset_bot_state_files('BTCUSDT', is_paper, config)
    state, wallet = json.loads(state_path.read_text()), json.loads(wallet_path.read_text())
    assert state['initial_tpv'] == state['reference_tpv'] == state['tpv_ath'] == wallet['balance'] == capital
    assert state['rebalance_cycles'] == 0 and not state['trailing_stop_triggered']
    assert not any(wallet['positions'].values())
    archives = list((tmp_path/'history').glob('archive_BTCUSDT_*'))
    assert len(archives) == 2 and sorted(p.read_bytes() for p in archives) == sorted(originals)




@pytest.mark.asyncio
async def test_enforce_swarm_consistency(tmp_path):
    positions = {'BTCUSDT_LONG': {'qty': 1}, 'ETHUSDT_SHORT': {'qty': -1}}
    connector = MagicMock(get_positions=AsyncMock(return_value=positions))
    with patch(f'{M}.CORE_PATH', tmp_path), \
         patch(f'{M}.get_running_bots_info', AsyncMock(return_value={'r_BTCUSDT': {}})), \
         patch(f'{M}.stop_bot', AsyncMock()) as stop, patch(f'{M}.start_bot', AsyncMock()) as start:
        await enforce_swarm_consistency(connector, {'live_swarm': ['BTCUSDT']})
    stop.assert_awaited_once_with('ETHUSDT', is_paper=False)
    start.assert_not_awaited()
    assert positions == {'BTCUSDT_LONG': {'qty': 1}, 'ETHUSDT_SHORT': {'qty': -1}}


@pytest.mark.asyncio
async def test_ensure_real_bots_alive():
    config = {"live_swarm": ["BTCUSDT", "ETHUSDT"]}
    # BTCUSDT is running, ETHUSDT is not
    running_bots = {"r_BTCUSDT": {"name": "real-btcusdt"}}

    with patch(f"{M}.get_running_bots_info", AsyncMock(return_value=running_bots)), \
         patch(f"{M}.start_bot", AsyncMock()) as mock_start, \
         patch(f"{M}.Path.exists", return_value=False):
        await _ensure_real_bots_alive(config)
        # Should restart ETHUSDT
        mock_start.assert_called_once_with("ETHUSDT", is_paper=False, config=config)

@pytest.mark.asyncio
async def test_manage_swarm_toxic_flow(tmp_path):
    from test_supervisor import _run_manage
    signals = tmp_path/'signals'; signals.mkdir()
    (signals/'stop_paper_TRXUSDT.flag').touch()
    saved, _, _ = await _run_manage(tmp_path, config_overrides={
        'paper_mode': True, 'max_bots': 2, 'paper_mode_bots': 2, 'tickers': ['BTCUSDT']},
        scanner=[{'symbol': 'BTCUSDT', 'is_toxic': False}, {'symbol': 'TRXUSDT', 'is_toxic': True}])
    assert 'TRXUSDT' in saved['toxic_blacklist_paper'] and 'TRXUSDT' in saved['black_list']
    assert not list(signals.glob('*.flag'))


@pytest.mark.asyncio
async def test_isolated_blacklists_signal_processing(tmp_path):
    from test_supervisor import _run_manage
    signals = tmp_path/'signals'; signals.mkdir()
    for name in ['stop_paper_BTCUSDT.flag', 'exit_real_ETHUSDT.flag']:
        (signals/name).touch()
    saved, _, _ = await _run_manage(tmp_path, config_overrides={
        'paper_mode': False, 'tickers': ['BTCUSDT', 'ETHUSDT']}, scanner=[{'symbol': 'BTCUSDT'}])
    assert 'BTCUSDT' in saved['toxic_blacklist_paper'] and 'BTCUSDT' in saved['black_list']
    assert 'ETHUSDT' in saved['probation_real']
    assert 'ETHUSDT' not in saved['toxic_blacklist_real'] and 'BTCUSDT' not in saved['toxic_blacklist_real']


@pytest.mark.asyncio
async def test_get_running_bots_info():
    pm2_jlist = [
        {
            "name": "paper-btcusdt",
            "pm2_env": {"args": ["--ticker", "BTCUSDT"]}
        },
        {
            "name": "real-ethusdt",
            "pm2_env": {"args": ["--ticker", "ETHUSDT"]}
        }
    ]
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (json.dumps(pm2_jlist).encode(), None)
    
    with patch(f"{M}.asyncio.create_subprocess_shell", AsyncMock(return_value=mock_proc)):
        res = await supervisor.get_running_bots_info()
        assert "p_BTCUSDT" in res
        assert "r_ETHUSDT" in res
        assert res["p_BTCUSDT"]["paper"] is True
        assert res["r_ETHUSDT"]["paper"] is False

@pytest.mark.asyncio
async def test_stop_bot(tmp_path):
    process = AsyncMock(wait=AsyncMock(return_value=0))
    with patch(f'{M}.CORE_PATH', tmp_path), \
         patch(f'{M}.asyncio.create_subprocess_shell', AsyncMock(return_value=process)) as shell:
        await supervisor.stop_bot('BTCUSDT', is_paper=True)
    assert shell.call_args.args[0] == 'pm2 delete paper-btc'


@pytest.mark.asyncio
async def test_start_bot(tmp_path):
    config = {'portfolios': [{'paper_initial_capital': 100}]}
    process = MagicMock(returncode=0, communicate=AsyncMock(return_value=(b'', b'')))
    with patch(f'{M}.CORE_PATH', tmp_path), patch(f'{M}.BASE_PATH', tmp_path), \
         patch(f'{M}._last_start_time', {}), \
         patch(f'{M}.get_running_bots_info', AsyncMock(return_value={})), \
         patch(f'{M}.asyncio.create_subprocess_shell', AsyncMock(return_value=process)) as launch, \
         patch(f'{M}.reset_bot_state_files', AsyncMock()) as reset:
        from futures_portfolio.supervisor.swarm_manager import start_bot
        await start_bot('BTCUSDT', is_paper=True, config=config)
    reset.assert_awaited_once_with('BTCUSDT', is_paper=True, config=config)
    launch.assert_awaited_once()
    assert 'pm2 start core/main.py' in launch.call_args.args[0]
    assert '--paper' in launch.call_args.args[0]



# ============================================================
# Rotation Guard: immature bots (cycles < min_cycles_for_rotation)
# защищены от замены новыми кандидатами из сканера
# ============================================================

@pytest.mark.asyncio
async def test_rotation_guard_immature_unprofitable_protected():
    """
    Убыточный paper-бот с cycles=3 < min_cycles_for_rotation=6
    НЕ должен быть вытеснен новым кандидатом из сканера.
    """
    config = {
        "max_bots": 4,
        "max_replace_per_cycle": 2,
        "min_cycles_for_rank": 5,
        "min_cycles_for_rotation": 6,
    }
    old_incubator = ["A", "B", "C", "D"]
    perf_map = {
        "A": {"profit": 10.0, "cycles": 10},   # Прибыльный — всегда остаётся
        "B": {"profit": -5.0, "cycles": 3},     # Убыточный, но НЕЗРЕЛЫЙ (3 < 6) — ЗАЩИЩЁН
        "C": {"profit": -10.0, "cycles": 10},   # Убыточный зрелый — кандидат на замену
        "D": {"profit": -20.0, "cycles": 10},   # Убыточный зрелый — кандидат на замену
    }
    scanner_results = [
        {"symbol": "A"}, {"symbol": "B"}, {"symbol": "E"}, {"symbol": "F"},
    ]
    res = await selective_merge_incubator(old_incubator, scanner_results, config, perf_map)
    # A (прибыльный) + B (незрелый защищённый) — оба остаются
    assert "A" in res, "Profitable bot A must stay"
    assert "B" in res, "Immature bot B (cycles=3 < 6) must be PROTECTED from replacement"
    # C и D — зрелые убыточные, конкурируют с E, F
    # Итог: A, B + 2 из {C, D, E, F}
    assert len(res) == 4


@pytest.mark.asyncio
async def test_rotation_guard_immature_profitable_protected():
    """
    Прибыльный paper-бот с cycles=2 < min_cycles_for_rotation=6
    тоже защищён (прибыльные и так всегда остаются,
    но проверяем что логика не сломана).
    """
    config = {
        "max_bots": 3,
        "max_replace_per_cycle": 2,
        "min_cycles_for_rank": 5,
        "min_cycles_for_rotation": 6,
    }
    old_incubator = ["A", "B", "C"]
    perf_map = {
        "A": {"profit": 1.0, "cycles": 2},     # Прибыльный незрелый
        "B": {"profit": -5.0, "cycles": 10},    # Убыточный зрелый
        "C": {"profit": -10.0, "cycles": 10},   # Убыточный зрелый
    }
    scanner_results = [
        {"symbol": "A"}, {"symbol": "D"}, {"symbol": "E"},
    ]
    res = await selective_merge_incubator(old_incubator, scanner_results, config, perf_map)
    assert "A" in res, "Profitable bot A must stay regardless of cycles"
    assert len(res) == 3


@pytest.mark.asyncio
async def test_rotation_guard_mature_unprofitable_replaced():
    """
    Убыточный paper-бот с cycles=8 >= min_cycles_for_rotation=6
    НЕ защищён — может быть вытеснен новым кандидатом.
    """
    config = {
        "max_bots": 3,
        "max_replace_per_cycle": 2,
        "min_cycles_for_rank": 5,
        "min_cycles_for_rotation": 6,
    }
    old_incubator = ["A", "B", "C"]
    perf_map = {
        "A": {"profit": 10.0, "cycles": 10},   # Прибыльный — остаётся
        "B": {"profit": -5.0, "cycles": 8},     # Убыточный ЗРЕЛЫЙ (8 >= 6) — НЕ защищён
        "C": {"profit": -10.0, "cycles": 10},   # Убыточный зрелый — НЕ защищён
    }
    scanner_results = [
        {"symbol": "A"}, {"symbol": "D"}, {"symbol": "E"},
    ]
    res = await selective_merge_incubator(old_incubator, scanner_results, config, perf_map)
    assert "A" in res, "Profitable bot A must stay"
    # B и C — зрелые убыточные, D и E — новые с score=0
    # B имеет score = -5 * 0.01 = -0.05, C = -10 * 0.01 = -0.10
    # D, E имеют score=0 — они вытеснят B и C (0 > -0.05 > -0.10)
    assert "D" in res, "New candidate D should replace mature unprofitable"
    assert "E" in res, "New candidate E should replace mature unprofitable"
    assert "B" not in res, "Mature unprofitable B (cycles=8 >= 6) should be replaced"
    assert "C" not in res, "Mature unprofitable C should be replaced"


@pytest.mark.asyncio
async def test_rotation_guard_default_min_cycles_for_rotation():
    """
    Если min_cycles_for_rotation НЕ указан в config,
    используется дефолт=6 (из .get default).
    """
    config = {
        "max_bots": 3,
        "max_replace_per_cycle": 2,
        "min_cycles_for_rank": 5,
        # min_cycles_for_rotation НЕ задан — дефолт 6
    }
    old_incubator = ["A", "B", "C"]
    perf_map = {
        "A": {"profit": -1.0, "cycles": 5},  # 5 < 6 — незрелый, защищён
        "B": {"profit": -5.0, "cycles": 10}, # зрелый убыточный
        "C": {"profit": -10.0, "cycles": 10},# зрелый убыточный
    }
    scanner_results = [
        {"symbol": "A"}, {"symbol": "D"}, {"symbol": "E"},
    ]
    res = await selective_merge_incubator(old_incubator, scanner_results, config, perf_map)
    assert "A" in res, "Immature bot A (cycles=5 < default 6) must be protected"
    assert len(res) == 3

@pytest.mark.asyncio
async def test_reset_bot_state_files_exceptions():
    config = {"portfolios": [{"initial_capital": 200.0}]}
    ticker = "BTCUSDT"
    with patch(f"{M}.shutil.copy", side_effect=Exception("Copy failed")), \
         patch(f"{M}.os.remove", side_effect=Exception("Remove failed")), \
         patch(f"{M}.Path.exists", return_value=True), \
         patch(f"{M}.safe_load_json", AsyncMock(return_value={})), \
         patch(f"{M}.safe_save_json", AsyncMock()):
        with pytest.raises(Exception, match="Copy failed"):
            await reset_bot_state_files(ticker, is_paper=False, config=config)

@pytest.mark.asyncio
async def test_manage_swarm_rotation_hysteresis_and_cushion(tmp_path):
    from test_supervisor import _run_manage
    _, stop, start = await _run_manage(tmp_path, config_overrides={
        'paper_mode': False, 'max_bots': 2, 'paper_mode_bots': 1, 'tickers': ['ETHUSDT'],
        'live_swarm': ['ETHUSDT'], 'real_whitelist': [], 'use_real_whitelist': False,
        'replacement_efficiency_threshold_pct': 20, 'min_cycles_for_rank': 5},
        scanner=[{'symbol': 'BTCUSDT'}, {'symbol': 'ETHUSDT'}],
        running_bots={'r_ETHUSDT': {'name': 'real-eth', 'paper': False}},
        eff_map={'ETHUSDT': {'profit': 0, 'cycles': 10}, 'BTCUSDT': {'profit': 100, 'cycles': 10}})
    stop.assert_any_await('ETHUSDT', is_paper=False)
    assert any(c.args[0] == 'BTCUSDT' and c.kwargs['is_paper'] is False for c in start.await_args_list)
