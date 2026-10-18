"""Bounded, frozen annual research; never changes a trading configuration."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal as D
import hashlib
import itertools
import json
from pathlib import Path
import sys

from rebalance.research_runner import SOURCES, run_study
from rebalance.research_verify import verify


def main():
    base=Path(sys.argv[2]).resolve() if len(sys.argv)>2 else Path(__file__).resolve().parent
    runtime=base/'runtime'
    load=lambda p:json.loads(p.read_text(encoding='utf-8-sig'))
    if sys.argv[1]=='prepare':
        root=base.parents[1]
        settings=load(root/'rebalance/historical_example.json')
        grid=[dict(activation_pct=a,drawback_pct=d,loss_pct=l)
              for a,d,l in itertools.product(('0.05','0.1','0.25','0.5'),('0.025','0.05','0.1'),('0.5','1','2')) if D(d)<D(a)]
        assert len(grid)==27 and len({json.dumps(p,sort_keys=True) for p in grid})==27
        code=runtime/'code/rebalance'
        code.mkdir(parents=True,exist_ok=False)
        names=(*SOURCES,'research_verify.py')
        for name in names: (code/name).write_bytes((root/'rebalance'/name).read_bytes())
        (code.parent/'sweep.py').write_bytes(Path(__file__).read_bytes())
        manifest=load(runtime/'data/manifest.json')
        plan=dict(frozen_utc=datetime.now(timezone.utc).isoformat(),window=['2025-10-01','2026-10-01'],
            symbol='BTCUSDT',grid=grid,settings=settings,initial_trials=28,max_trials=31,
            stress='top 3 annual Net_flat candidates, slippage multiplied by 2, no retune',
            selection='annual Net_flat descending; PF/DD reported, no admission inferred',
            reentry_after_loss=True,reentry_after_trailing=True,
            execution='completed-close Net_close signal; next open exit; reentry on following open',
            data_sha256=manifest['files'],data_usage='FULL_YEAR_SELECTION_NOT_OOS',
            code_sha256={name:hashlib.sha256((code/name).read_bytes()).hexdigest() for name in names},
            worker_sha256=hashlib.sha256((code.parent/'sweep.py').read_bytes()).hexdigest(),
            admission_ready=False,trading_authorized=False)
        with (base/'plan.json').open('x',encoding='utf-8') as stream: json.dump(plan,stream,indent=2)
        print('Prepared 27 candidates + legacy control + at most 3 stress trials')
        return
    assert sys.argv[1]=='run'
    (runtime/'worker.claim').mkdir(exist_ok=False)
    plan=load(base/'plan.json')
    code=runtime/'code/rebalance'
    assert all(hashlib.sha256((code/n).read_bytes()).hexdigest()==h for n,h in plan['code_sha256'].items())
    assert hashlib.sha256(Path(__file__).read_bytes()).hexdigest()==plan['worker_sha256']
    assert all(hashlib.sha256((runtime/'data'/n).read_bytes()).hexdigest()==h for n,h in plan['data_sha256'].items())
    configs=runtime/'configs'
    configs.mkdir(exist_ok=False)
    jobs=[('B00',None,False)]+[(f'T{i:02d}',policy,False) for i,policy in enumerate(plan['grid'],1)]
    report=dict(status='RUNNING',started_utc=datetime.now(timezone.utc).isoformat(),trials=[],
        plan_sha256=hashlib.sha256((base/'plan.json').read_bytes()).hexdigest(),
        data_usage=plan['data_usage'],admission_ready=False,trading_authorized=False)
    for identity,policy,stress in jobs:
        settings=deepcopy(plan['settings'])
        if policy is not None: settings['cycle_policy']=policy
        if stress: settings['slippage_bps']=str(D(settings['slippage_bps'])*2)
        config=configs/(identity+'.json')
        with config.open('x',encoding='utf-8') as stream: json.dump(settings,stream,indent=2)
        record=dict(id=identity,cycle_policy=policy,stress=stress,started_utc=datetime.now(timezone.utc).isoformat())
        try:
            study=runtime/'studies'/identity
            outcome=run_study(runtime/'trials.sqlite',identity,runtime/'data/BTCUSDT.csv',config,study,
                funding=runtime/'data/funding.csv',terminal_closeout=True)
            record['outcome']=outcome['status']
            if outcome['status']=='COMPLETED':
                verify(runtime/'trials.sqlite',study)
                summary=load(study/'comparison.json')
                record['net_flat']=summary['terminal_closeout']['candidate']['net_flat']
                record['hold_net_flat']=summary['terminal_closeout']['hold']['net_flat']
                record['max_dd_pct']=summary['terminal_closeout']['candidate']['max_close_plus_exit_drawdown_pct']
                record['cycles']=summary['cycles']['candidate']
            else: record['reason']=outcome.get('reason')
        except Exception as exc:
            record.update(outcome='FAILED',reason=type(exc).__name__+': '+str(exc))
        record['finished_utc']=datetime.now(timezone.utc).isoformat()
        report['trials'].append(record)
        if identity=='T27':
            ranked=sorted((r for r in report['trials'] if r['id'].startswith('T') and r['outcome']=='COMPLETED'),
                          key=lambda r:(-D(r['net_flat']),r['id']))[:3]
            report['top3_training']=[r['id'] for r in ranked]
            jobs.extend(('S'+r['id'][1:],r['cycle_policy'],True) for r in ranked)
        report['status']='COMPLETED' if len(report['trials'])==len(jobs) else 'RUNNING'
        report['updated_utc']=record['finished_utc']
        temp=base/'results.tmp'
        temp.write_text(json.dumps(report,indent=2),encoding='utf-8')
        temp.replace(base/'results.json')
        print(json.dumps({k:v for k,v in record.items() if k!='cycles'}),flush=True)
    assert len(report['trials'])<=plan['max_trials']


if __name__=='__main__': main()
