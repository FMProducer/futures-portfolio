import pytest

from futures_portfolio.core.paper_budget import initialize, reserve, bind, release, read_budget


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
    assert reserve(tmp_path,'NEW',1000) == (900, True, None)
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
    capital, _, _ = reserve(tmp_path, 'LAST', 1000)
    bind(tmp_path, 'LAST', {'initial_tpv':capital,'started_at':789})
    assert capital == 854.3544
