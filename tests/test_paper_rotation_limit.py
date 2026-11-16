import pytest
from supervisor.swarm_manager import selective_merge_incubator
from supervisor import swarm_manager
from unittest.mock import AsyncMock


@pytest.mark.asyncio
@pytest.mark.parametrize('limit,expected_new', [(0, 0), (1, 1), (2, 2)])
async def test_replacement_limit(limit, expected_new):
    config = {'max_bots': 2, 'max_replace_per_cycle': limit,
              'min_cycles_for_rotation': 6, 'min_cycles_for_rank': 15}
    performance = {t: {'profit': -112, 'cycles': 6} for t in ['SOPHUSDT', 'OLD']}
    result = await selective_merge_incubator(['SOPHUSDT', 'OLD'],
        [{'symbol': 'NEW1'}, {'symbol': 'NEW2'}], config, performance)
    assert len(result) == 2
    assert len(set(result) & {'NEW1', 'NEW2'}) == expected_new


@pytest.mark.asyncio
async def test_zero_replacement_fills_vacancies_and_keeps_quarantine():
    config = {'max_bots': 2, 'max_replace_per_cycle': 0,
              'toxic_blacklist_paper': {'TOXIC': 1}}
    result = await selective_merge_incubator(['TOXIC', 'SOPHUSDT'],
        [{'symbol': 'NEW'}, {'symbol': 'SOPHUSDT'}], config,
        {'SOPHUSDT': {'profit': -112, 'cycles': 6}})
    assert set(result) == {'SOPHUSDT', 'NEW'}


@pytest.mark.asyncio
async def test_zero_replacement_allows_initial_start():
    result = await selective_merge_incubator([], [{'symbol': 'SOPHUSDT'}],
        {'max_bots': 1, 'max_replace_per_cycle': 0}, {})
    assert result == ['SOPHUSDT']


@pytest.mark.asyncio
@pytest.mark.parametrize('positions,closed,triggered,expected', [
    ({'SOPHUSDT_LONG': 914209, 'SOPHUSDT_SHORT': 217625}, False, False, True),
    ({'SOPHUSDT_LONG': 0, 'SOPHUSDT_SHORT': 0}, False, False, False),
    ({'SOPHUSDT_LONG': 1}, False, False, True),
    (None, False, False, True),
    ({'SOPHUSDT_LONG': 1, 'SOPHUSDT_SHORT': 0}, True, False, False),
    ({'SOPHUSDT_LONG': 1, 'SOPHUSDT_SHORT': 0}, False, True, False),
])
async def test_losing_open_session_protection(tmp_path, monkeypatch, positions, closed, triggered, expected):
    monkeypatch.setattr(swarm_manager, 'CORE_PATH', tmp_path)
    state = {'last_profit': -112.3, 'rebalance_cycles': 6, 'paper_close_complete': closed,
             'trailing_stop_triggered': triggered}
    reader = AsyncMock(side_effect=[state, {'positions': positions}])
    monkeypatch.setattr(swarm_manager, 'safe_load_json', reader)
    metrics = await swarm_manager.get_bot_efficiency('SOPHUSDT', {'paper_idle_hours': 0})
    assert metrics['rotation_protected'] is expected
    result = await selective_merge_incubator(['SOPHUSDT'], [{'symbol': 'NEW'}],
        {'max_bots': 1, 'max_replace_per_cycle': 1}, {'SOPHUSDT': metrics})
    assert ('SOPHUSDT' in result) is expected


def test_idle_rotation_does_not_close_protected_session():
    result = swarm_manager.rotate_idle(['SOPHUSDT'], ['SOPHUSDT'],
        [{'symbol': 'SOPHUSDT', 'backtest': {'pass': False}}],
        {'SOPHUSDT': {'rotation_protected': True, 'idle': True}})
    assert result == ['SOPHUSDT']


@pytest.mark.asyncio
async def test_rotation_protection_does_not_override_quarantine():
    result = await selective_merge_incubator(['SOPHUSDT'], [{'symbol': 'NEW'}],
        {'max_bots': 1, 'max_replace_per_cycle': 1, 'toxic_blacklist_paper': {'SOPHUSDT': 1}},
        {'SOPHUSDT': {'profit': -112, 'cycles': 6, 'rotation_protected': True}})
    assert result == ['NEW']


@pytest.mark.asyncio
async def test_invalid_position_cannot_be_treated_as_flat(tmp_path, monkeypatch):
    monkeypatch.setattr(swarm_manager, 'CORE_PATH', tmp_path)
    monkeypatch.setattr(swarm_manager, 'safe_load_json', AsyncMock(side_effect=[
        {'last_profit': -112, 'rebalance_cycles': 6},
        {'positions': {'SOPHUSDT_LONG': 'NaN', 'SOPHUSDT_SHORT': 0}}]))
    with pytest.raises(ValueError, match='Invalid PAPER hedge quantity'):
        await swarm_manager.get_bot_efficiency('SOPHUSDT', {'paper_idle_hours': 0})
