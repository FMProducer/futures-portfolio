from copy import deepcopy
import sqlite3

import pytest

from rebalance.public_paper import Paper, PublicReader, rules, validate


def sample(stamp=600000):
    return dict(symbol='BTCUSDT',before=stamp-50,after=stamp,
        mark=dict(symbol='BTCUSDT',time=stamp-40,markPrice='100',nextFundingTime=3600000),
        book=dict(symbol='BTCUSDT',time=stamp-20,bidPrice='99.9',askPrice='100.1',bidQty='100',askQty='100'))


@pytest.fixture
def inputs():
    settings=dict(capital='1000',slippage_bps='1',strategy=dict(step='0.001',min_notional='5',
        leverage='7',fee_rate='0.0004',surplus_pp='0.7',deficit_pp='0.7',trail_pp='0.09'))
    filters={key:dict(minQty='0.001',maxQty='100',stepSize='0.001') for key in ('LOT_SIZE','MARKET_LOT_SIZE')}
    filters['MIN_NOTIONAL']=dict(notional='5')
    return settings,filters


def test_restart_duplicate_and_minute_gate(tmp_path,inputs):
    path=tmp_path/'paper.sqlite'
    p=Paper(path,'BTCUSDT',*inputs)
    first=p.step(sample())
    assert len(first['fills']) == 2
    before=p.store.load()[0]
    assert Paper(path,'BTCUSDT',*inputs).step(sample()) == first
    assert p.store.load()[1] == 1
    assert p.step(sample(600010))['status'] == 'SAME_MINUTE'
    assert p.store.load()[0] == before
    assert p.step(sample(660000))['status'] == 'APPLIED'
    assert p.store.orders() == []


@pytest.mark.parametrize('mutate',[
    lambda s:s.update(after=s['before']-1),
    lambda s:s['book'].update(time=s['after']-10001),
    lambda s:s['mark'].update(time=s['after']+1),
    lambda s:s['mark'].update(symbol='ETHUSDT'),
    lambda s:s['mark'].update(markPrice='NaN'),
    lambda s:s['book'].update(askPrice='99'),
    lambda s:s['book'].update(bidQty='0'),
    lambda s:s['mark'].update(nextFundingTime=s['after']),
])
def test_invalid_sample(mutate):
    s=sample()
    mutate(s)
    with pytest.raises(ValueError):
        validate(s,'BTCUSDT')


def test_rollback_second_leg_and_event_insert(tmp_path,inputs):
    p=Paper(tmp_path/'paper.sqlite','BTCUSDT',*inputs)
    original=p.store.load()
    s=sample()
    s['book']['bidQty']='0.001' # first LONG fits; second SHORT fails
    with pytest.raises(ValueError,match='volume'):
        p.step(s)
    assert p.store.load() == original
    with p.store.connection() as db:
        assert db.execute('SELECT count(*) FROM events').fetchone()[0] == 0
        db.execute("CREATE TRIGGER fail_event BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT,'crash'); END")
    with pytest.raises(sqlite3.IntegrityError):
        p.step(sample())
    assert p.store.load() == original
    with p.store.connection() as db:
        assert db.execute('SELECT last_time FROM public_paper').fetchone()[0] is None
        db.execute('DROP TRIGGER fail_event')
    assert len(p.step(sample())['fills']) == 2


def test_gap_funding_and_conflicting_duplicate(tmp_path,inputs):
    p=Paper(tmp_path/'paper.sqlite','BTCUSDT',*inputs)
    s=sample()
    s['mark']['nextFundingTime']=670000
    p.step(s)
    before=p.store.load()
    with pytest.raises(ValueError,match='funding UNKNOWN'):
        p.step(sample(900000))
    assert p.store.load() == before


def test_gap_order_and_binding(tmp_path,inputs):
    path=tmp_path/'paper.sqlite'
    p=Paper(path,'BTCUSDT',*inputs)
    p.step(sample())
    for stamp,reason in ((720001,'gap'),(599999,'out-of-order')):
        with pytest.raises(ValueError,match=reason):
            p.step(sample(stamp))
    changed=sample()
    changed['mark']['markPrice']='101'
    with pytest.raises(ValueError,match='conflicting'):
        p.step(changed)
    settings,filters=deepcopy(inputs)
    settings['capital']='2000'
    with pytest.raises(ValueError,match='binding'):
        Paper(path,'BTCUSDT',settings,filters)


def test_filters_and_foreign_database(tmp_path,inputs):
    settings,filters=deepcopy(inputs)
    filters['MARKET_LOT_SIZE']['stepSize']='100'
    p=Paper(tmp_path/'paper.sqlite','BTCUSDT',settings,filters)
    with pytest.raises(ValueError,match='quantity'):
        p.step(sample())
    filters['MARKET_LOT_SIZE']['stepSize']='0.001'
    filters['MIN_NOTIONAL']['notional']='10000'
    p=Paper(tmp_path/'other.sqlite','BTCUSDT',settings,filters)
    with pytest.raises(ValueError,match='notional'):
        p.step(sample())
    foreign=tmp_path/'foreign.sqlite'
    with sqlite3.connect(foreign) as db:
        db.execute('CREATE TABLE personal (body TEXT)')
    original=foreign.read_bytes()
    with pytest.raises(ValueError,match='foreign'):
        Paper(foreign,'BTCUSDT',*inputs)
    assert foreign.read_bytes() == original


def test_public_transport_has_no_credentials_or_order_route(monkeypatch):
    reader=PublicReader()
    with pytest.raises(ValueError):
        reader.get('/fapi/v1/order','BTCUSDT')
    calls=[]
    class Response:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self): return b'{"serverTime":600000}'
    def open_request(request,timeout):
        calls.append(request)
        return Response()
    monkeypatch.setattr(reader.opener,'open',open_request)
    assert reader.get('/fapi/v1/time')['serverTime'] == 600000
    assert calls[0].method == 'GET'
    assert calls[0].headers == {}
