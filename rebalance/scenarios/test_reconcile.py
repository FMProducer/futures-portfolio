from copy import deepcopy
from decimal import Decimal as D
import sqlite3

import pytest

from rebalance.binance_readonly import ReadError
from rebalance.durable import Store
from rebalance.kernel import Action, Config, Portfolio
from rebalance.reconcile import analyze, apply_offline, history


def trade(**changes):
    row = dict(id=1, orderId=10, symbol='BTCUSDT', side='BUY', positionSide='LONG',
               qty='.4', price='100', commission='.04', commissionAsset='USDT', realizedPnl='0', time=100)
    return dict(row, **changes)


def income(**changes):
    row = dict(tranId=1, incomeType='COMMISSION', symbol='BTCUSDT', tradeId='1',
               asset='USDT', income='-.04', time=100)
    return dict(row, **changes)


def evidence(**changes):
    row = dict(clientOrderId='stable', symbol='BTCUSDT', orderId=10, side='BUY',
               positionSide='LONG', origQty='1', executedQty='.4', status='CANCELED')
    return {'stable':dict(row, **changes)}


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path/'state.sqlite')
    s.initialize(Portfolio(Config('.001','5.3','7','.0004','.7','.7','.09'), '10000'))
    s.prepare('prepare','stable',Action('ADD','LONG',1000),'100','1','100',0,entry=True)
    s.transition('dispatch','stable','DISPATCHING')
    s.transition('timeout','stable','UNKNOWN')
    return s


def apply(store, bundle=None, proof=None, key='reconcile'):
    return apply_offline(store,key,bundle or analyze([trade()],[income()]),
        proof or evidence(),symbol='BTCUSDT',expected_revision=store.load()[1])


def test_unknown_partial_cancel_and_fee_once_survive_restart(store):
    result = apply(store)
    p, revision = Store(store.path).load()
    assert result['orders']['stable'] == 'CANCELED'
    assert p.wallet == D('9999.96') and p.fees == D('.04')
    assert p.legs['LONG'].units == p.legs['LONG'].initial_units == 400
    assert p.order_reserve == 0
    # Same request ID is cached; a different overlapping window still does not double-post.
    apply(store,key='overlapping-window')
    assert store.load()[0] == p


def test_funding_and_transfer_posted_as_separate_cash_events(store):
    rows = [income(),income(tranId=2,incomeType='FUNDING_FEE',income='.5',tradeId='',time=101),
            income(tranId=3,incomeType='TRANSFER',income='10',tradeId='',time=102)]
    apply(store,analyze([trade()],rows))
    p, _ = store.load()
    assert p.wallet == D('10010.46') and p.funding == D('.5') and p.fees == D('.04')


@pytest.mark.parametrize('changed', [dict(executedQty='.5'),dict(origQty='2'),
    dict(clientOrderId='other'),dict(positionSide='SHORT'),dict(status='FILLED')])
def test_bad_order_evidence_keeps_unknown_and_reserve(store, changed):
    before = store.load()
    with pytest.raises(ValueError):
        apply(store,proof=evidence(**changed))
    assert store.load() == before and store.orders()[0]['status'] == 'UNKNOWN'


def test_atomic_failure_rolls_back_already_applied_trade_and_cash(store):
    before = store.load()
    rows = [income(),income(tranId=2,incomeType='TRANSFER',income='10',tradeId='',time=99)]
    bundle = analyze([trade(realizedPnl='1')],rows+[income(tranId=3,incomeType='REALIZED_PNL',income='1')])
    with pytest.raises(ValueError, match='basis'):
        apply(store,bundle)
    assert store.load() == before and store.orders()[0]['status'] == 'UNKNOWN'


def test_db_failure_rolls_back_finalization_with_postings(store):
    before = store.load()
    with sqlite3.connect(store.path) as db:
        db.execute("CREATE TRIGGER injected BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT,'fault'); END")
    with pytest.raises(sqlite3.IntegrityError):
        apply(store)
    assert store.load() == before


@pytest.mark.parametrize('row,reason', [
    (income(asset='BNB'),'NON_USDT_INCOME'),
    (income(income='-.05'),'TRADING_INCOME_MISMATCH:COMMISSION'),
    (income(incomeType='SURPRISE'),'UNSUPPORTED_INCOME_TYPE:SURPRISE')])
def test_unclassified_or_unmatched_income_is_not_posted(store,row,reason):
    bundle = analyze([trade()],[row])
    assert reason in bundle['blocking_reasons']
    before = store.load()
    with pytest.raises(ValueError, match='blocked'):
        apply(store,bundle)
    assert store.load() == before


def test_duplicate_trade_not_summed_twice():
    with pytest.raises(ValueError, match='duplicate trade'):
        analyze([trade(),trade()],[income()])


def test_duplicate_income_not_summed_twice():
    with pytest.raises(ValueError, match='duplicate income'):
        analyze([trade()],[income(),income()])


def test_commission_in_non_usdt_is_blocked():
    assert 'NON_USDT_COMMISSION' in analyze([trade(commissionAsset='BNB')],[income()])['blocking_reasons']


def test_cash_and_trade_same_timestamp_is_ambiguous(store):
    bundle = analyze([trade()],[income(),income(tranId=2,incomeType='TRANSFER',income='1',tradeId='')])
    with pytest.raises(ValueError, match='ambiguous'):
        apply(store,bundle)


def test_history_recursively_splits_without_losing_equal_time_records():
    rows = [trade(id=i,time=t) for i,t in enumerate((1,2,2,3,4),1)]
    class Reader:
        def get(self,path,**params):
            return [r for r in rows if params['startTime'] <= r['time'] <= params['endTime']][:params['limit']]
    assert history(Reader(),'/fapi/v1/userTrades',0,5,symbol='BTCUSDT',limit=3) == rows


def test_saturation_in_one_millisecond_blocks_instead_of_skipping():
    class Reader:
        def get(self,path,**params):
            return [trade(id=i,time=1) for i in range(3)]
    with pytest.raises(ReadError, match='saturated'):
        history(Reader(),'/fapi/v1/userTrades',1,1,symbol='BTCUSDT',limit=3)


def test_history_error_and_budget_cannot_claim_complete_coverage():
    class Reader:
        def get(self,path,**params):
            return [trade(id=i,time=params['startTime']) for i in range(3)]
    with pytest.raises(ReadError, match='budget'):
        history(Reader(),'/fapi/v1/userTrades',0,5,symbol='BTCUSDT',limit=3,budget=1)


def test_terminal_evidence_cannot_regress_completed_order(store):
    apply(store)
    before = store.load()
    with pytest.raises(ValueError, match='regresses'):
        apply(store,proof=evidence(status='PARTIALLY_FILLED'),key='regression')
    assert store.load() == before


def test_exchange_rounded_close_pnl_is_posted_without_model_cash_drift(store):
    apply(store)  # .4 LONG opened, then canceled remainder
    store.prepare('reduce','close',Action('REDUCE','LONG',200),'110','1','110',store.load()[1])
    store.transition('dispatch-close','close','DISPATCHING')
    trades = [trade(id=2,orderId=11,side='SELL',qty='.2',price='110',commission='.022',
                    realizedPnl='2.000000001',time=200)]
    flows = [income(tranId=2,tradeId='2',income='-.022',time=200),
             income(tranId=3,tradeId='2',incomeType='REALIZED_PNL',income='2.000000001',time=200)]
    proof = {'close':dict(clientOrderId='close',symbol='BTCUSDT',orderId=11,side='SELL',
                         positionSide='LONG',origQty='.2',executedQty='.2',status='FILLED')}
    apply_offline(store,'close-reconcile',analyze(trades,flows),proof,
                  symbol='BTCUSDT',expected_revision=store.load()[1])
    p,_ = store.load()
    assert p.realized == D('2.000000001') and p.wallet == D('10001.938000001')


def test_request_event_replay_uses_original_revision_without_duplicate_posting(store):
    bundle, proof = analyze([trade()],[income()]), evidence()
    revision = store.load()[1]
    args = dict(symbol='BTCUSDT',expected_revision=revision)
    result = apply_offline(store,'same-event',bundle,proof,**args)
    before = store.load()
    assert apply_offline(Store(store.path),'same-event',bundle,proof,**args) == result
    assert store.load() == before
