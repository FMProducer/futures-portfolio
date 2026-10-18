"""Read-only verification of the single completed cycle/PF study."""
import hashlib
import json
from decimal import Decimal as D
from pathlib import Path

from rebalance.research_verify import verify


def main():
    base=Path(__file__).resolve().parent
    root=base.parents[1]
    current=base/'fixed_BTCUSDT'
    old=root/'reports/closeout_2026-10-18/fixed_BTCUSDT'
    load=lambda path:json.loads(path.read_text(encoding='utf-8-sig'))
    check=verify(base/'runtime/trials.sqlite',current)
    summary=load(current/'comparison.json')
    baseline=load(old/'comparison.json')
    for arm in ('candidate','hold'):
        for suffix in ('result','trades','equity'):
            assert load(current/(arm+'_'+suffix+'.json'))==load(old/(arm+'_'+suffix+'.json'))
        cycle=summary['cycles'][arm]
        assert cycle['completed_cycles']==cycle['synthetic_cycles']==1
        assert cycle['natural_simulated_cycles']==cycle['real_quarantine_cycles']==cycle['open_cycles']==0
        assert D(cycle['episodes'][0]['net'])==D(summary['terminal_closeout'][arm]['net_flat'])
    assert summary['terminal_closeout']==baseline['terminal_closeout']
    assert summary['cycles']['candidate']['profit_factor'] is None
    assert summary['cycles']['candidate']['status']=='NO_LOSS'
    assert summary['cycles']['hold']['profit_factor']=='0'
    assert summary['cycles']['hold']['status']=='NO_PROFIT'
    paths=['rebalance/comparison.py','rebalance/research_verify.py','rebalance/scenarios/test_cycles.py',
           'rebalance/scenarios/test_closeout.py','rebalance/scenarios/test_research_verify.py']
    value=dict(status='PASS',evidence=check,open_results_trades_curves_exact=True,
               terminal_closeout_exact=True,cycles=summary['cycles'],
               source_sha256={p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in paths},
               backtest_rerun_by_verifier=False,admission_ready=False,trading_authorized=False)
    with (base/'verification.json').open('x',encoding='utf-8') as stream:
        json.dump(value,stream,indent=2)
    print(json.dumps(value['cycles'],indent=2))


if __name__=='__main__': main()
