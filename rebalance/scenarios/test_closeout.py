from copy import deepcopy
from decimal import Decimal as D
import json

import pytest

from rebalance.closeout import closeout
from rebalance.comparison import compare
from rebalance.kernel import Action, Config, Portfolio
from rebalance.research_runner import run_study
from rebalance.research_verify import verify
from rebalance.scenarios.test_research_runner import study


@pytest.fixture
def portfolio():
    p = Portfolio(Config('0.1','0','7','0.001','0.7','0.7','0.09'), '1000')
    p.fill('long', Action('ADD','LONG',10), '100', '0.1', '100')
    p.fill('short', Action('ADD','SHORT',20), '100', '0.2', '100')
    return p


def evaluate(p, mark='100', spread='2', slip='1', capital='1000'):
    return closeout(p,mark,spread,slip,capital,
                    [dict(time=59999,equity=str(p.snapshot(mark)['equity']))])


def test_two_full_exits_formula_and_no_source_mutation(portfolio):
    before = deepcopy(portfolio)
    result = evaluate(portfolio)
    assert portfolio == before
    assert [f['side'] for f in result['fills']] == ['LONG','SHORT']
    assert [f['units'] for f in result['fills']] == [10,20]
    assert [D(f['price']) for f in result['fills']] == [D('99.98'),D('100.02')]
    assert D(result['price_adjustment']) == D('-0.06')
    assert D(result['exit_fees']) == D('0.30002')
    assert D(result['net_flat']) == D('-0.66002')
    assert D(result['total_fees']) == D('0.60002')
    assert D(result['margin']) == 0 and all(D(q)==0 for q in result['quantities'].values())
    assert D(result['decimal_bridge_residual']) == 0


def test_forced_exit_closes_losing_leg_and_funding_once(portfolio):
    portfolio.cash_event('funding', '-3', 'FUNDING')
    result = evaluate(portfolio,mark='90',spread='0',slip='0')
    assert len(result['fills']) == 2
    assert D(result['funding']) == -3
    assert D(result['net_flat']) == D('6.43')  # gross +10, old fees .3, funding -3, exit .27


def test_flat_no_fills_and_no_extra_fees():
    p = Portfolio(Config('0.1','0','7','0.001','0.7','0.7','0.09'), '1000')
    result = evaluate(p)
    assert result['fills'] == [] and D(result['exit_fees']) == 0
    assert D(result['net_flat']) == 0


def test_drawdown_includes_original_peak_and_terminal(portfolio):
    before = portfolio.snapshot('100')['equity']
    result = closeout(portfolio,'100','100','0','1000',[
        dict(time=1,equity='1100'),dict(time=2,equity=str(before))])
    assert D(result['max_close_plus_exit_drawdown_pct']) == (D(1100)-D(result['equity_flat']))/1100*100


@pytest.mark.parametrize('spread,slip', [('-1','0'),('0','-1'),('20000','0'),('0','NaN')])
def test_invalid_costs(portfolio,spread,slip):
    with pytest.raises(ValueError): evaluate(portfolio,spread=spread,slip=slip)


@pytest.mark.parametrize('field,value', [('units',D('1.5')),('units',-1),('margin',D('-1')),
                                       ('avg',D('0'))])
def test_invalid_leg(portfolio,field,value):
    setattr(portfolio.legs['LONG'],field,value)
    with pytest.raises(ValueError): evaluate(portfolio)


@pytest.mark.parametrize('kind', ['reserve','curve','receipt'])
def test_inconsistent_inputs_block_without_mutation(portfolio,kind):
    if kind=='reserve': portfolio.order_reserve=D(1)
    if kind=='receipt': portfolio.receipts['synthetic-terminal-59999-LONG']=('old',)
    original=deepcopy(portfolio)
    curve=[dict(time=59999,equity='1000' if kind=='curve' else str(portfolio.snapshot('100')['equity']))]
    with pytest.raises(ValueError): closeout(portfolio,'100','2','1','1000',curve)
    assert portfolio==original


def test_matched_open_baseline_unchanged_and_funding_before_exit(portfolio):
    rows=[dict(time=i*60000,open=D(100),high=D(100),low=D(100),close=D(100)) for i in range(2)]
    funding=[dict(time=119999,rate=D('0.01'),mark=D(100))]
    baseline=compare(rows,portfolio.config,'1000','2','1',funding)
    candidate=compare(rows,portfolio.config,'1000','2','1',funding,terminal_closeout=True)
    extended=deepcopy(candidate[0])
    terminal=extended.pop('terminal_closeout')
    assert extended==baseline[0] and candidate[1:]==baseline[1:]
    assert candidate[0]['candidate']['funding_events']==1
    for arm in ('candidate','hold'):
        assert terminal[arm]['funding']==baseline[0][arm]['funding']
        assert terminal[arm]['time']==119999


def test_registered_closeout_snapshot_and_evidence(study):
    assert run_study(**study,terminal_closeout=True)['status']=='COMPLETED'
    summary=json.loads((study['output']/'comparison.json').read_text())
    assert summary['terminal_closeout']['candidate']['mode']=='SYNTHETIC_CANDLE_CLOSEOUT'
    report=json.loads((study['output']/'registry_report.json').read_text())
    snapshot=report['trials'][0]['snapshot']
    assert snapshot['metadata']['terminal_closeout'] is True
    assert 'code:closeout.py' in snapshot['artifacts']
    assert verify(study['database'],study['output'])['status']=='PASS'
