from copy import deepcopy
from decimal import Decimal as D
import json
import sqlite3

import pytest

from rebalance.binance_readonly import ReadError
from rebalance.durable import pack
from rebalance.public_paper import Paper
from rebalance.public_session import Session, SessionReader, exclusive
from rebalance.scenarios.test_public_paper import sample, inputs


class Clock:
    def __init__(self): self.now=0
    def monotonic(self): return self.now
    def sleep(self,seconds): self.now+=seconds


class Feed:
    def __init__(self,values,funding=None):
        self.values=iter(values)
        self.rate=funding
        self.calls=0
    def capture(self,symbol):
        self.calls+=1
        value=next(self.values)
        if isinstance(value,Exception): raise value
        return deepcopy(value)
    def funding(self,symbol,stamp):
        if isinstance(self.rate,Exception): raise self.rate
        return deepcopy(self.rate)


def make(tmp_path,inputs,feed):
    p=Paper(tmp_path/'paper.sqlite','BTCUSDT',*inputs)
    clock=Clock()
    return p,Session(p,feed,monotonic=clock.monotonic,sleep=clock.sleep)


def funding(stamp=650000):
    return [dict(symbol='BTCUSDT',fundingTime=stamp,fundingRate='0.001',markPrice='101',rateType='Regular')]


def seeded(tmp_path,inputs):
    p=Paper(tmp_path/'paper.sqlite','BTCUSDT',*inputs)
    first=sample()
    first['mark']['nextFundingTime']=650000
    p.step(first)
    # An independently seeded unequal simulated exposure exercises nonzero cash.
    state,_=p.store.load()
    state.legs['SHORT'].units-=100
    with p.store.connection() as db:
        db.execute('UPDATE portfolio SET body=?',(pack(state),))
    return p


def test_short_read_failure_recovers_and_restart(tmp_path,inputs):
    feed=Feed([sample(),ReadError('public_request_failed'),sample(660000)])
    p,s=make(tmp_path,inputs,feed)
    assert s.run(duration=6,interval=2)['status'] == 'STOPPED'
    assert p.store.load()[1] == 2
    assert len(p.store.load()[0].receipts) == 2
    with p.store.connection() as db:
        assert db.execute('SELECT count(*) FROM session_inputs').fetchone()[0] == 2
    again=Session(Paper(p.store.path,'BTCUSDT',*inputs),Feed([sample(660010)]),
                  monotonic=Clock().monotonic,sleep=lambda _:None)
    # Replay the persisted inbox directly, without contacting the exhausted feed.
    before=p.store.load()
    again.recover()
    assert p.store.load() == before


@pytest.mark.parametrize('committed',[False,True])
def test_pending_input_after_crash_before_and_after_fill(tmp_path,inputs,committed):
    p,s=make(tmp_path,inputs,Feed([]))
    payload=dict(sample=sample(),funding=None)
    identity=s.enqueue(payload)
    assert p.store.load()[1] == 0
    result=p.step(payload['sample']) if committed else None
    restarted=Session(Paper(p.store.path,'BTCUSDT',*inputs),Feed([]))
    restarted.recover()
    assert p.store.load()[1] == 1
    with p.store.connection() as db:
        saved=json.loads(db.execute('SELECT result FROM session_inputs WHERE id=?',(identity,)).fetchone()[0])
        assert saved == result if committed else len(saved['fills']) == 2
    assert len(p.store.load()[0].receipts) == 2


def test_gap_freezes_durably_without_reset(tmp_path,inputs):
    p,s=make(tmp_path,inputs,Feed([sample(),sample(720001)]))
    assert s.run(duration=4,interval=2)['status'] == 'FROZEN'
    before=p.store.load()
    restarted=Session(Paper(p.store.path,'BTCUSDT',*inputs),Feed([sample(660000)]))
    with pytest.raises(ValueError,match='FROZEN'):
        restarted.run(duration=1)
    assert p.store.load() == before


def test_funding_atomic_nonzero_and_duplicate(tmp_path,inputs):
    p=seeded(tmp_path,inputs)
    before,_=p.store.load()
    second=sample(650001) # same decision minute as the first
    rows=funding()
    result=p.step(second,funding=rows)
    expected=(before.legs['SHORT'].units-before.legs['LONG'].units)*before.config.step*D('101')*D('.001')
    assert D(result['funding_delta']) == expected != 0
    after,revision=p.store.load()
    assert after.wallet-before.wallet == after.funding-before.funding == expected
    assert p.step(second,funding=rows) == result
    assert p.store.load()[1] == revision
    with p.store.connection() as db:
        assert db.execute('SELECT funding_due FROM public_paper').fetchone()[0] == 3600000


def test_funding_fetch_refreshes_prices(tmp_path,inputs):
    p=seeded(tmp_path,inputs)
    feed=Feed([sample(650001),sample(650010)],funding())
    clock=Clock()
    s=Session(p,feed,monotonic=clock.monotonic,sleep=clock.sleep)
    assert s.run(duration=1)['status'] == 'STOPPED'
    assert feed.calls == 2
    with p.store.connection() as db:
        body=json.loads(db.execute('SELECT payload FROM session_inputs').fetchone()[0])
        assert body['sample']['after'] == 650010


@pytest.mark.parametrize('change',[
    lambda r:r.clear(),
    lambda r:r.append(deepcopy(r[0])),
    lambda r:r[0].update(fundingTime=649999),
    lambda r:r[0].update(symbol='ETHUSDT'),
    lambda r:r[0].update(fundingRate='NaN'),
    lambda r:r[0].update(markPrice='0'),
    lambda r:r[0].update(rateType='Special'),
])
def test_bad_funding_cannot_change_portfolio(tmp_path,inputs,change):
    p=seeded(tmp_path,inputs)
    before=p.store.load()
    rows=funding()
    change(rows)
    with pytest.raises(ValueError):
        p.step(sample(650001),funding=rows)
    assert p.store.load() == before


def test_funding_and_journal_rollback_together(tmp_path,inputs):
    p=seeded(tmp_path,inputs)
    before=p.store.load()
    with p.store.connection() as db:
        db.execute("CREATE TRIGGER crash BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT,'crash'); END")
    with pytest.raises(sqlite3.IntegrityError):
        p.step(sample(650001),funding=funding())
    assert p.store.load() == before
    with p.store.connection() as db:
        assert db.execute('SELECT funding_due FROM public_paper').fetchone()[0] == 650000


def test_unknown_funding_is_not_assumed_zero(tmp_path,inputs):
    p=seeded(tmp_path,inputs)
    feed=Feed([sample(650001)],ReadError('funding_not_published'))
    clock=Clock()
    s=Session(p,feed,monotonic=clock.monotonic,sleep=clock.sleep)
    before=p.store.load()
    assert s.run(duration=1)['status'] == 'FROZEN'
    assert p.store.load() == before


def test_immutable_inbox_and_exclusive_lock(tmp_path,inputs):
    p,s=make(tmp_path,inputs,Feed([]))
    s.enqueue(dict(sample=sample(),funding=None))
    bad=sample()
    bad['book']['askPrice']='101'
    with pytest.raises(ValueError,match='conflicting'):
        s.enqueue(dict(sample=bad,funding=None))
    with p.store.connection() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE session_inputs SET payload='{}'")
    with exclusive(p.store.path):
        with pytest.raises(ValueError,match='another'):
            with exclusive(p.store.path): pass


def test_funding_transport_exact_get_no_credentials(monkeypatch):
    reader=SessionReader()
    requests=[]
    class Response:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self): return json.dumps(funding()).encode()
    def open_request(request,timeout):
        requests.append(request)
        return Response()
    monkeypatch.setattr(reader.opener,'open',open_request)
    assert reader.funding('BTCUSDT',650000) == funding()
    request=requests[0]
    assert request.method == 'GET' and request.headers == {}
    assert 'startTime=650000&endTime=650000' in request.full_url
    with pytest.raises(ValueError): reader.funding('ETHUSDT',650000)
