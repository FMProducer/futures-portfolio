from copy import deepcopy
from decimal import Decimal as D
import hashlib
import sqlite3

import pytest

from rebalance.historical import replay
from rebalance.kernel import Config
from rebalance.paper import Runner


def settings():
    return dict(capital='10000',spread_bps='2',slippage_bps='1',strategy=dict(
        step='.001',min_notional='5.3',leverage='7',fee_rate='.0004',surplus_pp='.7',deficit_pp='.7',trail_pp='.09'))


def bars():
    prices = [D(100)+D(i)/10 for i in range(30)]+[D(103)-D(i)/10 for i in range(60)]
    return [dict(time=i*60000,open=p,high=p,low=p,close=p) for i,p in enumerate(prices)]


def reference(rows,config,funding=()):
    return replay(rows,Config(**config['strategy']),config['capital'],config['spread_bps'],config['slippage_bps'],funding)


def test_restart_matches_uninterrupted_and_reference(tmp_path):
    path = tmp_path/'paper.sqlite'
    rows,config = bars(),settings()
    with Runner(path,rows,config) as r:
        r.run(35)
        assert r.progress()['next_index'] == 35
    with Runner(path,rows,config) as r:
        assert r.progress()['next_index'] == 35
        r.run()
        assert r.report() == reference(rows,config)
        before = r.store.load()
        r.run()
        assert r.store.load() == before


def test_replayed_observation_never_double_posts(tmp_path):
    with Runner(tmp_path/'paper.sqlite',bars(),settings()) as r:
        original = r.step(0)
        before = r.store.load()
        assert r.step(0) == original
        assert r.store.load() == before and r.progress()['next_index'] == 1


def test_cursor_fills_extrema_and_funding_rollback_together(tmp_path):
    path = tmp_path/'paper.sqlite'
    funding = [dict(time=60000,mark=D(100),rate=D('.001')),
               dict(time=2700000,mark=D(100),rate=D('.001'))]
    with Runner(path,bars(),settings(),funding,funding_mode='CSV_RATES') as r:
        r.run(1)
        before,progress = r.store.load(),r.progress()
        with r.store.connection() as db:
            db.execute("CREATE TRIGGER injected BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT,'fault'); END")
        with pytest.raises(sqlite3.IntegrityError):
            r.step(1)
        assert r.store.load() == before and r.progress() == progress
        with r.store.connection() as db:
            db.execute('DROP TRIGGER injected')
        r.run()
        assert r.report() == reference(bars(),settings(),funding)
        assert D(r.report()[0]['funding']) != 0


@pytest.mark.parametrize('change',['capital','step','candles','costs','funding'])
def test_modified_inputs_cannot_resume_old_state(tmp_path,change):
    path = tmp_path/'paper.sqlite'
    with Runner(path,bars(),settings()) as r:
        r.run(2)
        before = r.store.load()
    rows,config,funding = bars(),settings(),[]
    if change == 'capital': config['capital'] = '20000'
    if change == 'step': config['strategy']['step'] = '.002'
    if change == 'candles': rows[-1]['high'] += 1
    if change == 'costs': config['spread_bps'] = '3'
    if change == 'funding': funding = [dict(time=60000,mark=D(100),rate=D('.001'))]
    with pytest.raises(ValueError,match='changed'):
        Runner(path,rows,config,funding,funding_mode='CSV_RATES' if funding else 'ASSUMED_ZERO')
    with Runner(path,bars(),settings()) as r:
        assert r.store.load() == before and r.progress()['next_index'] == 2


def test_foreign_observer_or_live_database_is_not_modified(tmp_path):
    path = tmp_path/'observer.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE observer_binding(id INTEGER)')
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='not a paper'):
        Runner(path,bars(),settings())
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_small_capital_entry_failure_is_atomic(tmp_path):
    config = settings()
    config['capital'] = '14'
    with Runner(tmp_path/'paper.sqlite',bars(),config) as r:
        before = r.store.load()
        with pytest.raises(ValueError,match='entry blocked'):
            r.step(0)
        assert r.store.load() == before and r.progress()['next_index'] == 0


def test_missing_bar_is_rejected_before_start(tmp_path):
    rows = bars()
    del rows[1]
    with pytest.raises(ValueError,match='sequence'):
        Runner(tmp_path/'paper.sqlite',rows,settings())


def test_only_next_bar_can_be_executed(tmp_path):
    with Runner(tmp_path/'paper.sqlite',bars(),settings()) as r:
        with pytest.raises(ValueError,match='non-sequential'):
            r.step(2)
        assert r.progress()['next_index'] == 0


def test_paper_does_not_access_network_or_real_credentials(tmp_path,monkeypatch):
    import socket
    def forbidden(*a,**k):
        raise AssertionError('network access in paper mode')
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    with Runner(tmp_path/'paper.sqlite',bars(),settings()) as r:
        r.run(2)
        assert r.binding['mode'] == 'PAPER_CSV_V1'


def test_wait_observation_is_saved_and_bound_input_is_independent(tmp_path):
    rows = [dict(time=i*60000,open=D(100),high=D(100),low=D(100),close=D(100)) for i in range(5)]
    with Runner(tmp_path/'paper.sqlite',rows,settings()) as r:
        rows[2]['close'] = D(999)
        r.run()
        result,fills,curve = r.report()
        assert len(fills) == 2 and len(curve) == 5 and result['rebalances'] == 0
        assert r.store.load()[0].extrema
        with r.store.connection() as db:
            assert db.execute('SELECT count(*) FROM paper_bars').fetchone()[0] == 5
