"""Audit one predeclared double-slippage study without rerunning it."""
from decimal import Decimal as D
import json
from pathlib import Path

from rebalance.research_verify import verify


def main():
    base=Path(__file__).resolve().parent
    root=base.parents[1]
    reference=root/'reports/cycles_2026-10-18'
    studies={'baseline':reference/'fixed_BTCUSDT','stress':base/'fixed_BTCUSDT'}
    load=lambda p:json.loads(p.read_text(encoding='utf-8-sig'))
    evidence={name:verify((reference if name=='baseline' else base)/'runtime/trials.sqlite',study)
              for name,study in studies.items()}
    manifests={name:load(study/'execution_manifest.json') for name,study in studies.items()}
    old,new=manifests['baseline'],manifests['stress']
    changed=dict(new['settings'])
    assert D(changed.pop('slippage_bps'))==2*D(old['settings']['slippage_bps'])
    expected=dict(old['settings'])
    expected.pop('slippage_bps')
    assert changed==expected
    for role,record in old['artifacts'].items():
        if role!='config':
            assert (record['sha256'],record['bytes'])==(new['artifacts'][role]['sha256'],new['artifacts'][role]['bytes'])
    assert old['metadata']['funding_mode']==new['metadata']['funding_mode']=='ASSUMED_ZERO'
    assert old['metadata']['terminal_closeout'] is new['metadata']['terminal_closeout'] is True
    metrics={}
    for name,study in studies.items():
        summary=load(study/'comparison.json')
        metrics[name]={arm:dict(net_open=summary[arm]['net_mtm'],
            net_flat=summary['terminal_closeout'][arm]['net_flat'],
            total_fees=summary['terminal_closeout'][arm]['total_fees'],
            rebalances=summary[arm]['rebalances'],profit_factor=summary['cycles'][arm]['profit_factor'],
            pf_status=summary['cycles'][arm]['status']) for arm in ('candidate','hold')}
    value=dict(status='PASS',scope='MATCHED_USED_DATA_STRESS',evidence=evidence,metrics=metrics,
        only_setting_change=dict(slippage_bps=[old['settings']['slippage_bps'],new['settings']['slippage_bps']]),
        stress_candidate_nonnegative=D(metrics['stress']['candidate']['net_flat'])>=0,
        oos_requirement_verified=False,admission_ready=False,trading_authorized=False,
        backtest_rerun_by_verifier=False)
    with (base/'verification.json').open('x',encoding='utf-8') as stream:
        json.dump(value,stream,indent=2)
    print(json.dumps(metrics,indent=2))


if __name__=='__main__': main()
