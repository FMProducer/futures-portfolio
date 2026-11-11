import json
import sqlite3

import pytest

from futures_portfolio.core.paper_budget import initialize, reserve, bind, read_budget, reconcile_terminal_allocations


def terminal(root):
    (root/'core').mkdir()
    (root/'history').mkdir()
    initialize(root, 1, 1000, {})
    reserve(root, 'BEATUSDT', 1000)
    state = {'base_ticker':'BEATUSDT', 'started_at':123, 'initial_tpv':1000,
             'last_tpv':1039.4821, 'rebalance_cycles':7, 'paper_close_complete':True}
    wallet = {'base_ticker':'BEATUSDT', 'balance':1039.4821,
              'positions':{'BEATUSDT_LONG':0, 'BEATUSDT_SHORT':0},
              'share_fill_receipts':{'paper-close:trailing:123:7:LONG':{'event_id':'trailing:123:7', 'symbol':'BEATUSDT_LONG', 'type':'LONG', 'reduce_only':'True'}}}
    bind(root, 'BEATUSDT', state)
    return state, wallet


def save(root, state, wallet):
    (root/'core/paper_state_BEATUSDT.json').write_text(json.dumps(state))
    (root/'core/paper_shadow_BEATUSDT.json').write_text(json.dumps(wallet))
    (root/'history/paper_close_BEATUSDT_123.json').write_text(json.dumps(
        {'state':state, 'paper_state':wallet, 'close_event':'stop:123:7'}))


def test_terminal_return_without_process_is_idempotent_and_equity_neutral(tmp_path):
    state, wallet = terminal(tmp_path)
    save(tmp_path, state, wallet)
    before = read_budget(tmp_path)['cash'] + state['last_tpv']
    assert reconcile_terminal_allocations(tmp_path) == (['BEATUSDT'], {})
    assert read_budget(tmp_path)['cash'] == before
    assert read_budget(tmp_path)['allocations'] == {}
    assert reconcile_terminal_allocations(tmp_path) == ([], {})
    with sqlite3.connect(tmp_path/'core/paper_budget.sqlite') as db:
        assert db.execute("select count(*) from events where kind='return'").fetchone()[0] == 1
    assert json.loads((tmp_path/'core/paper_state_BEATUSDT.json').read_text()) == state


@pytest.mark.parametrize('fault', ['identity','archive','position','missing_leg','inflight','pending','wal','receipts','capital','equity'])
def test_ambiguous_terminal_stays_reserved(tmp_path, fault):
    state, wallet = terminal(tmp_path)
    if fault == 'identity': state['started_at'] = 456
    if fault == 'position': wallet['positions']['BEATUSDT_LONG'] = 1
    if fault == 'missing_leg': wallet['positions'].pop('BEATUSDT_SHORT')
    if fault == 'inflight': state['rebalance_inflight'] = {'id':'uncertain'}
    if fault == 'pending': state['initial_build_pending'] = {'missing':['LONG']}
    if fault == 'receipts': wallet['share_fill_receipts'] = {}
    if fault == 'capital': state['initial_tpv'] = 900
    if fault == 'equity': state['last_tpv'] = 1000
    save(tmp_path, state, wallet)
    if fault == 'archive': (tmp_path/'history/paper_close_BEATUSDT_123.json').unlink()
    if fault == 'wal': (tmp_path/'core/paper_state_BEATUSDT.json.funding_intent').write_text('{}')
    returned, blocked = reconcile_terminal_allocations(tmp_path)
    assert returned == [] and 'BEATUSDT' in blocked
    assert read_budget(tmp_path)['cash'] == 0
    assert 'BEATUSDT' in read_budget(tmp_path)['allocations']


def test_open_identity_is_not_returned(tmp_path):
    state, wallet = terminal(tmp_path)
    state['paper_close_complete'] = False
    save(tmp_path, state, wallet)
    (tmp_path/'history/paper_close_BEATUSDT_123.json').unlink()
    assert reconcile_terminal_allocations(tmp_path) == ([], {})
    assert read_budget(tmp_path)['cash'] == 0


@pytest.mark.parametrize('equity,key', [(1039.4821,'probation_period_days'), (900,'toxic_cooldown_days')])
def test_confirm_close_uses_terminal_profit_and_returns_once(tmp_path, equity, key):
    from futures_portfolio.core.paper_budget import confirm_close
    state, wallet = terminal(tmp_path)
    state['last_tpv'] = wallet['balance'] = equity
    save(tmp_path, state, wallet)
    config = {'probation_period_days':.01, 'toxic_cooldown_days':.09}
    (tmp_path/'config.json').write_text(json.dumps(config))
    confirm_close(tmp_path, 'BEATUSDT')
    closed = json.loads((tmp_path/'core/paper_state_BEATUSDT.json').read_text())
    assert closed['restart_not_before'] - closed['paper_closed_at'] == pytest.approx(config[key]*86400)
    assert read_budget(tmp_path)['cash'] == equity
    confirm_close(tmp_path, 'BEATUSDT')
    assert read_budget(tmp_path)['cash'] == equity
    assert json.loads((tmp_path/'core/paper_state_BEATUSDT.json').read_text()) == closed


def test_selection_waits_after_return_and_survives_new_reader(tmp_path, monkeypatch):
    from futures_portfolio.core import paper_budget as budget
    state, wallet = terminal(tmp_path)
    save(tmp_path, state, wallet)
    monkeypatch.setattr(budget.time, 'time', lambda: 1000)
    budget.reconcile_terminal_allocations(tmp_path)
    cfg = {'supervisor_interval_days': .011}
    assert not budget.selection_ready(tmp_path, cfg)
    monkeypatch.setattr(budget.time, 'time', lambda: 1950.39)
    assert not budget.selection_ready(tmp_path, cfg)
    monkeypatch.setattr(budget.time, 'time', lambda: 1950.4)
    assert budget.selection_ready(tmp_path, cfg)


def test_recover_close_before_archive_and_return(tmp_path):
    state, wallet = terminal(tmp_path)
    save(tmp_path, state, wallet)
    (tmp_path/'config.json').write_text(json.dumps({'probation_period_days':.01}))
    (tmp_path/'history/paper_close_BEATUSDT_123.json').unlink()
    assert reconcile_terminal_allocations(tmp_path, recover=True) == (['BEATUSDT'], {})
    assert read_budget(tmp_path)['cash'] == wallet['balance']
    assert reconcile_terminal_allocations(tmp_path, recover=True) == ([], {})
