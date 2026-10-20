from decimal import Decimal as D

import pytest

from rebalance.comparison import compare
from rebalance.historical import replay
from rebalance.kernel import Config
from rebalance.scenarios.test_research_runner import study


CONFIG=Config('1','0','7','0','0.7','0.7','1000')
POLICY=dict(activation_pct='0.1',drawback_pct='0.05',loss_pct='0.5')


def rows(prices):
    return [dict(time=i*60000,open=D(p),high=D(p),low=D(p),close=D(p)) for i,p in enumerate(prices)]


def test_loss_exit_reentry_on_following_bar_with_reset_state():
    data=rows(['100']*5)
    funding=[dict(time=1,rate=D('0.1'),mark=D('100'))]
    # Unequal initial quantities are needed for net funding; spread changes entry sizing.
    config=Config('1','0','7','0','0.7','0.7','1000')
    result,trades,_=replay(data,config,'1000','1000','0',funding,cycle_policy=POLICY)
    assert result['cycle_exits']>=1
    exit_time=next(t['time'] for t in trades if t.get('phase')=='CYCLE_LOSS')
    entries=[t for t in trades if t.get('phase')=='CYCLE_ENTRY']
    assert entries and entries[0]['time']==exit_time+60000


def test_trailing_activation_peak_and_next_open_execution():
    data=rows(['100']*5)
    # Spread creates unequal hedge quantities; received funding arms the trailing.
    funding=[dict(time=1,rate=D('2'),mark=D('100')),
             dict(time=60001,rate=D('-1'),mark=D('100'))]
    result,trades,_=replay(data,CONFIG,'1000','2000','0',funding,
        cycle_policy=dict(activation_pct='0.1',drawback_pct='0.05',loss_pct='50'))
    exits=[t for t in trades if t.get('phase')=='CYCLE_TRAILING']
    assert exits and exits[0]['time']==120000
    assert all(t['time']!=exits[0]['time'] for t in trades if t.get('phase')=='CYCLE_ENTRY')


def test_no_policy_preserves_single_entry_and_original_results():
    a=replay(rows(['100']*4),CONFIG,'1000','2','1',[])
    b=replay(rows(['100']*4),CONFIG,'1000','2','1',[],cycle_policy=None)
    assert a==b and 'cycle_policy' not in a[0]


@pytest.mark.parametrize('policy',[dict(POLICY,loss_pct='0'),dict(POLICY,drawback_pct='0.2'),
                                   dict(POLICY,activation_pct='NaN'),dict(POLICY,extra='1')])
def test_invalid_policy_before_calculation(policy):
    with pytest.raises(ValueError): replay(rows(['100','100']),CONFIG,'1000','2','1',[],cycle_policy=policy)


def test_matched_control_and_terminal_cycle_ledger():
    summary,candidate,hold=compare(rows(['100']*5),CONFIG,'1000','1000','0',[],
        terminal_closeout=True,cycle_policy=POLICY)
    assert candidate[1][:2]==hold[1]
    assert summary['cycles']['candidate']['completed_cycles']>=2
    assert summary['cycles']['hold']['completed_cycles']==1
    assert summary['cycles']['candidate']['real_quarantine_cycles']==0


def test_registered_policy_reaches_snapshot_and_verifier(study):
    import json
    from rebalance.research_runner import run_study
    from rebalance.research_verify import verify
    settings=json.loads(study['config'].read_text())
    settings['cycle_policy']=POLICY
    settings['spread_bps']='1000'
    study['config'].write_text(json.dumps(settings))
    study['candles'].write_text('open_time,open,high,low,close\n'+
        ''.join(f'{i*60000},100,100,100,100\n' for i in range(5)))
    assert run_study(**study,terminal_closeout=True)['status']=='COMPLETED'
    report=json.loads((study['output']/'comparison.json').read_text())
    assert report['candidate']['cycle_policy']==POLICY
    assert report['candidate']['cycle_entries']>=2
    assert verify(study['database'],study['output'])['status']=='PASS'
