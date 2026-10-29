import pytest
import logging

from futures_portfolio.supervisor.swarm_manager import paper_activity_at, rotate_idle


@pytest.fixture(autouse=True)
def isolate_log(monkeypatch):
    monkeypatch.setattr(logging.getLogger('Supervisor'), 'handlers', [logging.NullHandler()])


@pytest.mark.asyncio
@pytest.mark.parametrize('hours,elapsed,expected', [
    (None,10800,False), (6,21599,False), (6,21600,True),
    (3,10800,True), (12,21600,False),
])
async def test_configured_idle_boundary(tmp_path, monkeypatch, hours, elapsed, expected):
    from unittest.mock import AsyncMock
    from futures_portfolio.supervisor import swarm_manager as manager
    monkeypatch.setattr(manager, 'CORE_PATH', tmp_path)
    monkeypatch.setattr(manager, 'BASE_PATH', tmp_path)
    monkeypatch.setattr(manager.time, 'time', lambda: 200 + elapsed)
    monkeypatch.setattr(manager, 'safe_load_json', AsyncMock(return_value={
        'started_at':100, 'initial_build_completed_at':200,
        'last_update':200 + elapsed, 'last_profit':0, 'rebalance_cycles':1}))
    config = {} if hours is None else {'paper_idle_hours':hours}
    assert (await manager.get_bot_efficiency('A', config))['idle'] is expected


@pytest.mark.asyncio
@pytest.mark.parametrize('hours', [0, -1, float('nan'), float('inf')])
async def test_invalid_idle_timeout_is_rejected(monkeypatch, hours):
    from unittest.mock import AsyncMock
    from futures_portfolio.supervisor import swarm_manager as manager
    monkeypatch.setattr(manager, 'safe_load_json', AsyncMock(return_value={'started_at':1}))
    monkeypatch.setattr(manager, 'paper_activity_at', lambda *args: None)
    with pytest.raises(ValueError, match='paper_idle_hours'):
        await manager.get_bot_efficiency('A', {'paper_idle_hours':hours}, is_real=True)


def test_idle_clock_uses_completed_actions_not_heartbeat_or_restart(tmp_path):
    state = {'started_at': 100, 'initial_build_completed_at': 200, 'last_update': 9999}
    assert paper_activity_at(tmp_path, 'A', state) == 200
    state['last_rebalance_completed_at'] = 300
    assert paper_activity_at(tmp_path, 'A', state) == 300
    assert paper_activity_at(tmp_path, 'A', dict(state, initial_build_pending={'missing':['SHORT']})) is None
    assert paper_activity_at(tmp_path, 'A', {'started_at':100, 'last_update':9999}) is None


@pytest.mark.parametrize('net,dd,history,expected', [
    (3, 1, 0, ['NEW']), (2, 1, 0, ['NEW']), (3, 2, 0, ['NEW']),
    (2, 2, 0, ['OLD']), (3, 3, 0, ['OLD']), (1, 1, 0, ['OLD']),
    (3, 1, -10, ['OLD']), (3, 1, -11, ['OLD']),
])
def test_idle_replacement_requires_both_backtest_dominance_and_better_history(net, dd, history, expected):
    candidates = [{'symbol':'OLD', 'backtest':{'pass':True, 'net_pct':2, 'max_dd_pct':2}},
                  {'symbol':'NEW', 'backtest':{'pass':True, 'net_pct':net, 'max_dd_pct':dd}}]
    perf = {'OLD':{'idle':True, 'profit':-10}, 'NEW':{'profit':history}}
    assert rotate_idle(['OLD'], ['OLD'], candidates, perf) == expected


def test_idle_failed_gate_closes_but_data_error_retains_session():
    perf = {'OLD':{'idle':True, 'profit':10}}
    assert rotate_idle(['OLD'], ['OLD'], [{'symbol':'OLD', 'backtest':{'pass':False}}], perf) == []
    assert rotate_idle(['OLD'], ['OLD'], [{'symbol':'OLD', 'backtest':{'pass':False, 'error':'Missing bars'}}], perf) == ['OLD']


def test_legacy_activity_is_recovered_only_inside_current_session(tmp_path):
    path = tmp_path / 'core/logs/paper_A.log'
    path.parent.mkdir(parents=True)
    path.write_text('2026-10-27 01:00:00,000 INFO: Rebalance #1 complete. TPV: 998\n')
    at = paper_activity_at(tmp_path, 'A', {'started_at':1})
    assert at is not None
    assert paper_activity_at(tmp_path, 'A', {'started_at':at+1}) is None
