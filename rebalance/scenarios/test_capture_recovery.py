import json

import pytest

from rebalance.public_paper import CaptureError, validate
from rebalance.scenarios.test_public_paper import sample, inputs
from rebalance.scenarios.test_public_session import Feed, make


@pytest.mark.parametrize('field,value,kind,retryable',[
    ('before',580000,'capture_span_exceeded',True),
    ('symbol','ETHUSDT','wrong_capture_symbol',False),
    ('before',600001,'capture_time_regression',False),
])
def test_precise_capture_error(field,value,kind,retryable):
    s=sample()
    s[field]=value
    with pytest.raises(CaptureError) as error:
        validate(s,'BTCUSDT')
    assert error.value.kind==kind and error.value.retryable is retryable
    assert error.value.sample==s
    s['symbol']='MUTATED'
    assert error.value.sample['symbol']!='MUTATED'


@pytest.mark.parametrize('stamp,kind,retryable',[
    (580000,'stale_quote',True),(600001,'future_quote',False),
])
def test_quote_error_is_classified(stamp,kind,retryable):
    s=sample()
    s['book']['time']=stamp
    with pytest.raises(CaptureError) as error: validate(s,'BTCUSDT')
    assert (error.value.kind,error.value.retryable)==(kind,retryable)


def slow(stamp=600000):
    s=sample(stamp)
    s['before']=stamp-10001
    return s


def test_discard_slow_then_fresh_no_bad_fill(tmp_path,inputs):
    p,session=make(tmp_path,inputs,Feed([slow(),sample()]))
    assert session.run(duration=4,interval=2)['status']=='STOPPED'
    portfolio,revision=p.store.load()
    assert revision==1 and len(portfolio.receipts)==2
    with p.store.connection() as db:
        assert db.execute('SELECT count(*) FROM session_inputs').fetchone()[0]==1
        logs=[json.loads(r[0]) for r in db.execute('SELECT body FROM session_log')]
    rejected=[r for r in logs if 'rejected_capture' in r]
    assert len(rejected)==1 and rejected[0]['reason']=='capture_span_exceeded'
    assert rejected[0]['rejected_capture']==slow()
    assert p.store.orders()==[]


def test_repeated_slow_freezes_at_existing_retry_limit(tmp_path,inputs):
    p,session=make(tmp_path,inputs,Feed([slow(),slow(),slow()]))
    assert session.run(duration=10,interval=2)['status']=='FROZEN'
    assert p.store.load()[1]==0
    with p.store.connection() as db:
        logs=[json.loads(r[0]) for r in db.execute('SELECT body FROM session_log')]
    assert len([r for r in logs if 'rejected_capture' in r])==3


def test_wrong_symbol_never_retried(tmp_path,inputs):
    s=sample()
    s['symbol']='ETHUSDT'
    feed=Feed([s,sample()])
    p,session=make(tmp_path,inputs,feed)
    assert session.run(duration=4,interval=2)['status']=='FROZEN'
    assert feed.calls==1 and p.store.load()[1]==0


def test_retry_does_not_extend_observation_gap(tmp_path,inputs):
    p,session=make(tmp_path,inputs,Feed([sample(),slow(720001)]))
    assert session.run(duration=4,interval=2)['status']=='FROZEN'
    assert p.store.load()[1]==1
    with p.store.connection() as db:
        last=json.loads(db.execute('SELECT body FROM session_log ORDER BY seq DESC LIMIT 1').fetchone()[0])
        assert last['observation_gap_exceeded'] is True
        assert db.execute('SELECT last_time FROM public_paper').fetchone()[0]==600000
