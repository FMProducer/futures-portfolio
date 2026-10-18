"""Verify one finished fixed study, never rerun its backtest."""
import hashlib
import json
from decimal import Decimal as D
from pathlib import Path

from rebalance.research_verify import verify


def main():
    root=Path(__file__).resolve().parents[2]
    base=Path(__file__).parent
    current=base/'fixed_BTCUSDT'
    old=root/'reports/research_runner_2026-10-18/fixed_BTCUSDT'
    load=lambda p:json.loads(p.read_text(encoding='utf-8-sig'))
    check=verify(base/'runtime/trials.sqlite',current)
    summary=load(current/'comparison.json')
    close=summary.pop('terminal_closeout')
    assert summary==load(old/'comparison.json')
    for arm in ('candidate','hold'):
        for suffix in ('result','trades','equity'):
            assert load(current/(arm+'_'+suffix+'.json'))==load(old/(arm+'_'+suffix+'.json'))
    illustration=load(root/'reports/terminal_proposal_2026-10-18/illustration.json')
    for arm in ('candidate','hold'):
        result=close[arm]
        assert all(D(q)==0 for q in result['quantities'].values()) and D(result['margin'])==0
        assert len(result['fills'])==2
        assert D(result['exit_fees'])==sum((D(f['fee']) for f in result['fills']),D(0))
        assert D(result['price_adjustment'])==D(illustration['arms'][arm]['adverse_price_adjustment'])
        assert D(result['exit_fees'])==D(illustration['arms'][arm]['exit_fees'])
        assert D(result['formula_equity'])==D(illustration['arms'][arm]['estimated_flat_equity'])
        assert D(result['equity_flat'])-D(result['formula_equity'])==D(result['decimal_bridge_residual'])
    paths=['rebalance/closeout.py','rebalance/historical.py','rebalance/comparison.py',
           'rebalance/research_runner.py','rebalance/research_verify.py','rebalance/scenarios/test_closeout.py']
    value=dict(status='PASS',read_only_evidence_check=check,open_results_trades_curves_exact=True,
               matched_summary_open_exact=True,illustration_formula_exact=True,
               closeout=close,source_sha256={p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in paths},
               replay_performed_by_verifier=False,admission_ready=False,trading_authorized=False)
    (base/'verification.json').write_text(json.dumps(value,indent=2),encoding='utf-8')
    print(json.dumps(close,indent=2))


if __name__=='__main__': main()
