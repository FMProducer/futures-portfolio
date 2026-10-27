import pytest

from futures_portfolio.core.paper_budget import initialize, reserve, bind, release, read_budget


def test_rotation_loss_history_survives_reentry_and_retry(tmp_path):
    from futures_portfolio.core.paper_budget import ticker_history
    (tmp_path / 'core').mkdir()
    initialize(tmp_path, 1, 1000, {})
    reserve(tmp_path, 'A', 1000)
    bind(tmp_path, 'A', {'initial_tpv': 1000, 'started_at': 1})
    state = {'started_at': 1, 'paper_close_complete': True, 'last_tpv': 900}
    wallet = {'balance': 900, 'positions': {'A_LONG': 0}}
    release(tmp_path, 'A', state, wallet, rotation=True)
    first = ticker_history(tmp_path, 'A')
    release(tmp_path, 'A', state, wallet, rotation=True)
    assert ticker_history(tmp_path, 'A') == first
    assert first['closed_net'] == -100 and first['loss_at'] > 0
    assert first['active_session'] is None
    reserve(tmp_path, 'A', 900)
    bind(tmp_path, 'A', {'initial_tpv': 900, 'started_at': 2})
    assert ticker_history(tmp_path, 'A')['active_session'] == '2'
    release(tmp_path, 'A', dict(state, started_at=2, last_tpv=950),
            {'balance': 950, 'positions': {'A_LONG': 0}}, rotation=True)
    assert ticker_history(tmp_path, 'A')['closed_net'] == -50
    assert ticker_history(tmp_path, 'A')['loss_at'] == first['loss_at']


def test_budget_return_retry_and_reallocation_preserve_losses(tmp_path):
    (tmp_path / 'core').mkdir()
    state = {'initial_tpv':1000, 'started_at':123}
    initialize(tmp_path, 1, 1000, {'OLD':state})
    initialize(tmp_path, 10, 9000, {})
    assert read_budget(tmp_path)['initial'] == 1000
    assert reserve(tmp_path,'NEW',1000)[0] == 0
    closed = dict(state, paper_close_complete=True, last_tpv=900)
    wallet = {'balance':900, 'positions':{'OLD_LONG':1}}
    with pytest.raises(ValueError):
        release(tmp_path,'OLD',closed,wallet)
    wallet['positions']['OLD_LONG'] = 0
    release(tmp_path,'OLD',closed,wallet)
    release(tmp_path,'OLD',closed,wallet)
    assert read_budget(tmp_path)['cash'] == 900
    assert reserve(tmp_path,'NEW',1000) == (0, True, None)
    assert read_budget(tmp_path)['cash'] == 900
    assert 'NEW' not in read_budget(tmp_path)['allocations']
    assert reserve(tmp_path,'NEW',900) == (900, True, None)
    bind(tmp_path,'NEW',{'initial_tpv':900,'started_at':456})
    assert reserve(tmp_path,'NEW',1000) == (900,False,'456')
    assert read_budget(tmp_path)['cash'] == 0


def test_migration_losses_reduce_cash_without_changing_base(tmp_path):
    (tmp_path / 'core').mkdir()
    initialize(tmp_path, 10, 1000, {}, closed_result=-145.645599999999949)
    assert read_budget(tmp_path)['initial'] == 10000
    assert read_budget(tmp_path)['cash'] == 9854.3544
    for i in range(9):
        reserve(tmp_path, str(i), 1000)
    assert reserve(tmp_path, 'LAST', 1000) == (0, True, None)
    assert read_budget(tmp_path)['cash'] == 854.3544
    assert 'LAST' not in read_budget(tmp_path)['allocations']


def test_insufficient_reservation_does_not_mutate_events_or_existing_session(tmp_path):
    import sqlite3
    (tmp_path/'core').mkdir()
    initialize(tmp_path, 1, 1000, {})
    reserve(tmp_path, 'EXISTING', 900)
    bind(tmp_path, 'EXISTING', {'initial_tpv':900, 'started_at':123})
    with sqlite3.connect(tmp_path/'core/paper_budget.sqlite') as db:
        before = db.execute('select * from events').fetchall()
    assert reserve(tmp_path, 'NIL', 1000) == (0, True, None)
    assert reserve(tmp_path, 'EXISTING', 1000) == (900, False, '123')
    with sqlite3.connect(tmp_path/'core/paper_budget.sqlite') as db:
        assert db.execute('select * from events').fetchall() == before
    assert read_budget(tmp_path)['cash'] == 100
