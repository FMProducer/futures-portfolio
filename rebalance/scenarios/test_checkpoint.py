from copy import deepcopy
import sqlite3

import pytest

from rebalance.checkpoint import Observer, identity
from rebalance.binance_readonly import ReadError


def proof(end=1000000,wallet='100',trades=None,incomes=None):
    return dict(binding=dict(account='a'*64,market='USD-M',symbol='BTCUSDT',asset='USDT',purpose='OBSERVER_ONLY_V1'),
                start=700000,before=999000,end=end,trades=trades or [],incomes=incomes or [],
                observation=dict(blocking_reasons=[],rest_bracket_equal=True,active_positions=[],
                    open_orders=0,initial_margin_usdt='0',unrealized_usdt='0',wallet_usdt=wallet))


def flow(id=1,time=1000100,amount='10'):
    return dict(tranId=id,time=time,incomeType='TRANSFER',income=amount,asset='USDT',symbol='',tradeId='')


@pytest.fixture
def observer(tmp_path):
    o = Observer(tmp_path/'observer.sqlite')
    o.initialize(proof())
    return o


def test_uid_binding_does_not_depend_on_api_key_and_never_contains_uid():
    class Reader:
        def get(self,path):
            assert path == '/api/v3/account'
            return dict(uid=12345)
    value = identity(Reader(),'BTCUSDT')
    assert len(value['account']) == 64 and '12345' not in str(value)
    assert identity(Reader(),'BTCUSDT') == value


def test_missing_uid_has_no_key_fingerprint_fallback():
    class Reader:
        def get(self,path):
            return {}
    with pytest.raises(ReadError,match='uid'):
        identity(Reader(),'BTCUSDT')


def test_checkpoint_binding_and_cursor_survive_reopen(observer):
    assert Observer(observer.path).state() == observer.state()
    with pytest.raises(ValueError,match='already bound'):
        observer.initialize(proof())


def test_cannot_bind_observer_to_trading_db(tmp_path):
    path = tmp_path/'trading.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE portfolio(id INTEGER)')
    with pytest.raises(ValueError,match='not an observer'):
        Observer(path)


def test_cash_bridge_and_overlap_do_not_double_count(observer):
    p = proof(end=1001000,wallet='110',incomes=[flow()])
    assert observer.ingest(p,1000000)['new_facts'] == 1
    p['end'] = 1002000
    assert Observer(observer.path).ingest(p,1001000)['new_facts'] == 0
    assert observer.state()[1] == 1002000


def test_late_fact_within_overlap_is_included(observer):
    observer.ingest(proof(end=1001000),1000000)
    result = observer.ingest(proof(end=1002000,wallet='110',incomes=[flow(time=1000100)]),1001000)
    assert result['new_facts'] == 1


@pytest.mark.parametrize('change', ['account','symbol','market'])
def test_wrong_account_or_scope_never_advances_cursor(observer,change):
    p = proof(end=1001000)
    p['binding'][change] = 'wrong'
    with pytest.raises(ValueError,match='mismatch'):
        observer.ingest(p,1000000)
    assert observer.state()[1] == 1000000


def test_history_gap_and_stale_revision_are_rejected(observer):
    p = proof(end=1001000)
    p['start'] = 999999
    with pytest.raises(ValueError,match='overlap'):
        observer.ingest(p,1000000)
    with pytest.raises(ValueError,match='stale'):
        observer.ingest(proof(end=1001000),999999)


def test_wallet_mismatch_does_not_advance_or_insert_facts(observer):
    with pytest.raises(ValueError,match='wallet bridge'):
        observer.ingest(proof(end=1001000,wallet='999',incomes=[flow()]),1000000)
    assert observer.state()[1] == 1000000
    with sqlite3.connect(observer.path) as db:
        assert db.execute('SELECT count(*) FROM observer_facts').fetchone()[0] == 0


def test_disappeared_overlap_fact_blocks_ingest(observer):
    observer.ingest(proof(end=1001000,wallet='110',incomes=[flow()]),1000000)
    with pytest.raises(ValueError,match='missing or changed'):
        observer.ingest(proof(end=1002000,wallet='110'),1001000)
    assert observer.state()[1] == 1001000


def test_late_pre_checkpoint_fact_requires_new_investigation(observer):
    with pytest.raises(ValueError,match='late fact'):
        observer.ingest(proof(end=1001000,incomes=[flow(time=999500)]),1000000)


def test_active_account_checkpoint_rejected(tmp_path):
    o = Observer(tmp_path/'observer.sqlite')
    p = proof()
    p['observation']['active_positions'] = [dict(symbol='BTCUSDT')]
    with pytest.raises(ValueError,match='flat'):
        o.initialize(p)


def test_events_during_initial_read_invalidate_checkpoint(tmp_path):
    o = Observer(tmp_path/'observer.sqlite')
    with pytest.raises(ValueError,match='activity'):
        o.initialize(proof(incomes=[flow(time=999500)]))


def test_cursor_and_facts_rollback_on_sqlite_failure(observer):
    with sqlite3.connect(observer.path) as db:
        db.execute("CREATE TRIGGER fail_check BEFORE INSERT ON observer_checks BEGIN SELECT RAISE(ABORT,'fault'); END")
    with pytest.raises(sqlite3.IntegrityError):
        observer.ingest(proof(end=1001000,wallet='110',incomes=[flow()]),1000000)
    assert observer.state()[1] == 1000000
    with sqlite3.connect(observer.path) as db:
        assert db.execute('SELECT count(*) FROM observer_facts').fetchone()[0] == 0


def test_flat_roundtrip_wallet_bridge_counts_fees_only_once(observer):
    opening = dict(symbol='BTCUSDT',id=1,orderId=1,positionSide='LONG',side='BUY',
                   qty='.4',price='100',commission='.04',commissionAsset='USDT',realizedPnl='0',time=1000100)
    closing = dict(opening,id=2,orderId=2,side='SELL',price='110',commission='.044',realizedPnl='4',time=1000200)
    rows = [dict(incomeType='COMMISSION',tranId=1,tradeId='1',symbol='BTCUSDT',asset='USDT',income='-.04',time=1000100),
            dict(incomeType='COMMISSION',tranId=2,tradeId='2',symbol='BTCUSDT',asset='USDT',income='-.044',time=1000200),
            dict(incomeType='REALIZED_PNL',tranId=3,tradeId='2',symbol='BTCUSDT',asset='USDT',income='4',time=1000200)]
    result = observer.ingest(proof(end=1001000,wallet='103.916',trades=[opening,closing],incomes=rows),1000000)
    assert result['new_facts'] == 5


def test_uid_endpoint_is_signed_get_on_exact_spot_host():
    from io import BytesIO
    from rebalance.binance_readonly import Reader
    class Reply(BytesIO):
        def __enter__(self): return self
        def __exit__(self,*args): self.close()
    class Opener:
        def open(self,request,timeout):
            assert request.method == 'GET'
            assert request.full_url.startswith('https://api.binance.com/api/v3/account?')
            assert 'signature=' in request.full_url
            return Reply(b'{"uid":12345}')
    reader = Reader('fixture-key','fixture-secret')
    reader._opener = Opener()
    assert len(identity(reader,'BTCUSDT')['account']) == 64
