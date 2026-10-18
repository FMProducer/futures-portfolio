from copy import deepcopy
from decimal import Decimal as D

import pytest

from rebalance.comparison import compare, hold
from rebalance.kernel import Config, Portfolio


def config():
    return Config(step='0.001',min_notional='5.3',leverage='7',fee_rate='0.0004',
                  surplus_pp='0.7',deficit_pp='0.7',trail_pp='1000')


def rows(prices=('100','101','99','100')):
    return [dict(time=i*60000,open=D(p),high=D(p),low=D(p),close=D(p)) for i,p in enumerate(prices)]


def test_no_rebalance_candidate_matches_hold_including_funding():
    data=rows()
    rates=[dict(time=90000,rate=D('.001'),mark=D('101'))]
    before=deepcopy((data,rates))
    report,candidate,control=compare(data,config(),'1000','2','1',rates)
    assert candidate==control
    assert len(control[1])==2 and control[0]['rebalances']==0
    assert D(report['delta_net_mtm'])==0
    assert report['candidate']['terminal']=='OPEN_MTM'
    assert report['profit_factor'] is None and not report['admission_ready']
    assert (data,rates)==before


def test_hold_never_calls_rebalance_planner(monkeypatch):
    def forbidden(*args): raise AssertionError('hold called next_action')
    monkeypatch.setattr(Portfolio,'next_action',forbidden)
    result,trades,curve=hold(rows(),config(),'1000','2','1',[])
    assert len(trades)==2 and len(curve)==4 and result['rebalances']==0
    assert result['realized']=='0'


def test_funding_at_entry_before_and_after_position_exists():
    data=rows(('100','100'))
    rates=[dict(time=0,rate=D('.9'),mark=D('100')),
           dict(time=1,rate=D('.001'),mark=D('100')),
           dict(time=60000,rate=D('-.002'),mark=D('101'))]
    result,fills,_=hold(data,config(),'1000','200','0',rates)
    qty=(fills[1]['units']-fills[0]['units'])*config().step
    expected=qty*(D('100')*D('.001')+D('101')*D('-.002'))
    assert expected!=0 and D(result['funding'])==expected
    assert result['funding_events']==3
    assert D(result['fees'])==sum(D(f['fee']) for f in fills)
    assert D(result['net_mtm'])==D(result['equity'])-D('1000')


@pytest.mark.parametrize('fault', ['gap','nan','negative','funding_duplicate','funding_outside','cost'])
def test_invalid_input_blocks_comparison_before_candidate(monkeypatch,fault):
    data=rows()
    funding=[]
    spread='2'
    if fault=='gap': data[1]['time']=120000
    if fault=='nan': data[0]['close']=D('NaN')
    if fault=='negative': data[0]['low']=D('-1')
    if fault=='funding_duplicate': funding=[dict(time=1,rate=D('.01'),mark=D(100))]*2
    if fault=='funding_outside': funding=[dict(time=999999,rate=D('.01'),mark=D(100))]
    if fault=='cost': spread='-1'
    def forbidden(*args,**kwargs): raise AssertionError('candidate ran before validation')
    monkeypatch.setattr('rebalance.comparison.replay',forbidden)
    with pytest.raises(ValueError):
        compare(data,config(),'1000',spread,'1',funding)


def test_blocked_entry_is_not_a_cash_only_hold():
    with pytest.raises(ValueError,match='entry blocked'):
        hold(rows(),config(),'14','2','1',[])
