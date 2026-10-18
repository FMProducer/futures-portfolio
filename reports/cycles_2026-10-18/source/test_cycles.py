from copy import deepcopy
from decimal import Decimal as D

import pytest

from rebalance.closeout import closeout
from rebalance.comparison import cycles
from rebalance.kernel import Action, Config, Portfolio


def fixture(actions,*,fee='0.001',funding=(),terminal=False):
    config=Config('1','0','7',fee,'0.7','0.7','0.09')
    p=Portfolio(config,'1000')
    trades=[]
    rows=[dict(time=i*60000,open=D(100),close=D(100)) for i in range(max(len(actions),2))]
    index=0
    for i,(kind,side,units,price) in enumerate(actions):
        stamp=i*60000
        while index<len(funding) and funding[index]['time']<=stamp:
            event=funding[index]
            p.cash_event('funding-'+str(index),(p.legs['SHORT'].units-p.legs['LONG'].units)*
                         D(event['mark'])*D(event['rate']),'FUNDING')
            index+=1
        cost=units*D(price)*D(fee)
        p.fill(str(i),Action(kind,side,units),price,cost,'100')
        trades.append(dict(time=stamp,kind=kind,side=side,units=units,price=str(price),fee=str(cost)))
    while index<len(funding):
        event=funding[index]
        p.cash_event('funding-'+str(index),(p.legs['SHORT'].units-p.legs['LONG'].units)*
                     D(event['mark'])*D(event['rate']),'FUNDING')
        index+=1
    end=rows[-1]['time']+59999
    result=dict(start=0,end=end,initial_equity='1000',equity=str(p.snapshot('100')['equity']),
        fees=str(p.fees),funding=str(p.funding),funding_events=len(funding),
        quantities={s:str(leg.units) for s,leg in p.legs.items()})
    exit=closeout(p,'100','2','1','1000',[dict(time=end,equity=result['equity'])]) if terminal else None
    before=deepcopy((trades,result,funding,rows,exit))
    value=cycles(trades,config,result,funding,rows,exit)
    assert before==(trades,result,funding,rows,exit)
    return value


def episode(exit_price):
    return [('ADD','LONG',1,'100'),('ADD','SHORT',1,'100'),
            ('REDUCE','LONG',1,exit_price),('REDUCE','SHORT',1,'100')]


def test_winner_loser_pf_includes_all_fees_once():
    value=fixture(episode('110')+episode('90'))
    assert value['completed_cycles']==2 and value['natural_simulated_cycles']==2
    assert D(value['gross_profit'])==D('9.59') and D(value['gross_loss'])==D('10.39')
    assert D(value['profit_factor'])==D('9.59')/D('10.39')
    assert value['status']=='DEFINED' and value['real_quarantine_cycles']==0
    assert sum(D(e['net']) for e in value['episodes'])==D(value['terminal_net'])


@pytest.mark.parametrize('actions,fee,status,pf', [
    ([], '0.001','NO_CYCLES',None),
    (episode('110'),'0.001','NO_LOSS',None),
    (episode('90'),'0.001','NO_PROFIT','0'),
    (episode('100'),'0','ZERO_ONLY',None)])
def test_pf_edge_cases_no_automatic_admission(actions,fee,status,pf):
    value=fixture(actions,fee=fee)
    assert value['status']==status and value['profit_factor']==pf
    assert value['admission_ready'] is False and value['trading_authorized'] is False


def test_open_inventory_blocks_pf_despite_closed_winner():
    value=fixture(episode('110')+[('ADD','LONG',2,'100'),('REDUCE','LONG',1,'101')])
    assert value['completed_cycles']==1 and value['open_cycles']==1
    assert value['status']=='OPEN_PORTFOLIO' and value['profit_factor'] is None


def test_two_synthetic_exits_are_one_research_cycle():
    funding=[dict(time=0,mark=D(100),rate=D('.9')),dict(time=90000,mark=D(100),rate=D('.01'))]
    value=fixture([('ADD','LONG',2,'100'),('ADD','SHORT',1,'100')],funding=funding,terminal=True)
    assert value['synthetic_cycles']==1 and value['natural_simulated_cycles']==0
    assert value['real_quarantine_cycles']==0 and value['completed_cycles']==1
    assert D(value['episodes'][0]['funding'])==-1
    assert D(value['episodes'][0]['fees'])==D('0.59998')
    assert D(value['episodes'][0]['net'])==D('-1.65998')
    assert D(value['terminal_net'])==D(value['episodes'][0]['net'])


def test_splitting_exit_cannot_inflate_cycle_count():
    value=fixture([('ADD','LONG',2,'100'),('ADD','SHORT',1,'100'),
                   ('REDUCE','LONG',1,'110'),('REDUCE','LONG',1,'110'),('REDUCE','SHORT',1,'100')])
    assert value['completed_cycles']==1 and value['natural_simulated_cycles']==1


def test_zero_cycle_reported_separately():
    value=fixture(episode('100')+episode('110')+episode('90'),fee='0')
    assert value['zero_cycles']==1 and value['completed_cycles']==3
    assert D(value['profit_factor'])==1
