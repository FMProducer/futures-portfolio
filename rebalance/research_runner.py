"""Registered offline comparison from an isolated source/input snapshot."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from .trial_registry import Registry, artifacts

SOURCES=('__init__.py','research_runner.py','trial_registry.py','comparison.py','historical.py','kernel.py','closeout.py')


def write(path,value):
    with Path(path).open('x',encoding='utf-8') as stream: json.dump(value,stream,indent=2)


def run_study(database,identity,candles,config,output,*,funding=None,funding_zero=False,terminal_closeout=False):
    if type(terminal_closeout) is not bool: raise ValueError('terminal_closeout must be boolean')
    if bool(funding) == bool(funding_zero): raise ValueError('choose funding CSV or explicit zero')
    database,output=Path(database).resolve(),Path(output).resolve()
    output.mkdir(parents=True,exist_ok=False) # Exclusive study/output claim, never resume.
    code=output/'frozen/code/rebalance'
    inputs=output/'frozen/inputs'
    code.mkdir(parents=True)
    inputs.mkdir()
    files={}
    originals={}
    for name in SOURCES:
        path=Path(__file__).with_name(name)
        target=code/name
        target.write_bytes(path.read_bytes())
        files['code' if name=='research_runner.py' else 'code:'+name]=target
    for role,path in (('data',candles),('config',config),('funding',funding)):
        if path is None: continue
        path=Path(path).resolve()
        target=inputs/(role+path.suffix)
        target.write_bytes(path.read_bytes())
        files[role]=target
        originals[role]=str(path)
    try:
        git=subprocess.run(['git','rev-parse','HEAD'],cwd=Path(__file__).parent,capture_output=True,text=True)
        source_head=git.stdout.strip() if git.returncode==0 else None
    except OSError:
        source_head=None
    metadata=dict(output=str(output),original_paths=originals,source_head=source_head,engine='comparison',
        funding_mode='ASSUMED_ZERO' if funding_zero else 'CSV_RATES',
        terminal_closeout=terminal_closeout,
        data_usage='NOT_ESTABLISHED; caller must disclose prior use',admission_ready=False)
    registry=Registry(database)
    registry.start(identity,files,metadata,mode='PROSPECTIVE')
    write(output/'registration.json',dict(id=identity,database=str(database),files={k:str(v) for k,v in files.items()},
        metadata=metadata,artifacts=artifacts(files),trading_authorized=False))
    env=os.environ.copy()
    env['PYTHONPATH']=str(code.parent)
    command=[sys.executable,'-m','rebalance.research_runner','worker','--database',str(database),
             '--id',identity,'--output',str(output)]
    try:
        with (output/'worker.stdout.log').open('w') as stdout,(output/'worker.stderr.log').open('w') as stderr:
            process=subprocess.Popen(command,cwd=code.parent,env=env,stdout=stdout,stderr=stderr)
            try:
                exit_code=process.wait()
            except KeyboardInterrupt:
                process.terminate()
                process.wait()
                registry.finish(identity,'ABORTED',reason='operator interrupted offline child')
                exit_code=130
        trial=next(t for t in registry.report()['trials'] if t['id']==identity)
        if trial['outcome']['status']=='INCOMPLETE':
            registry.finish(identity,'FAILED',reason='worker exited without outcome: '+str(exit_code))
    except Exception:
        # If supervisor cannot finish recording, INCOMPLETE remains visible.
        raise
    report=registry.report()
    write(output/'registry_report.json',report)
    return next(t['outcome'] for t in report['trials'] if t['id']==identity)


def worker(database,identity,output):
    output=Path(output).resolve()
    if Path(__file__).resolve()!=output/'frozen/code/rebalance/research_runner.py':
        raise ValueError('worker requires isolated snapshot import')
    (output/'worker.claim').mkdir(exist_ok=False)
    registry=Registry(database)
    trial=next((t for t in registry.report()['trials'] if t['id']==identity),None)
    if not trial or trial['outcome']['status']!='INCOMPLETE':
        raise ValueError('registered incomplete trial required before calculation')
    snapshot=trial['snapshot']
    if snapshot['metadata']['output']!=str(output): raise ValueError('trial output mismatch')
    paths={k:Path(v['path']) for k,v in snapshot['artifacts'].items()}
    try:
        if artifacts(paths)!=snapshot['artifacts']: raise ValueError('snapshot changed before execution')
        mode=snapshot['metadata']['funding_mode']
        if mode not in ('CSV_RATES','ASSUMED_ZERO') or ('funding' in paths)!=(mode=='CSV_RATES'):
            raise ValueError('funding mode does not match registered artifacts')
        from .comparison import compare
        from .historical import candles, funding_rows
        from .kernel import Config
        settings=json.loads(paths['config'].read_text(encoding='utf-8-sig'))
        terminal=snapshot['metadata'].get('terminal_closeout',False)
        if type(terminal) is not bool: raise ValueError('invalid terminal flag')
        summary,candidate,control=compare(candles(paths['data']),Config(**settings['strategy']),
            settings['capital'],settings['spread_bps'],settings['slippage_bps'],funding_rows(paths.get('funding')),
            terminal_closeout=terminal,cycle_policy=settings.get('cycle_policy'))
        evidence={}
        for name,value in (('comparison',summary),('execution_manifest',dict(
                artifacts=snapshot['artifacts'],metadata=snapshot['metadata'],settings=settings,
                executed_module=str(Path(__file__).resolve()),trading_authorized=False))):
            path=output/(name+'.json')
            write(path,value)
            evidence[name]=path
        for arm,data in (('candidate',candidate),('hold',control)):
            for suffix,value in zip(('result','trades','equity'),data):
                path=output/(arm+'_'+suffix+'.json')
                write(path,value)
                evidence[arm+'_'+suffix]=path
        registry.finish(identity,'COMPLETED',evidence=evidence)
        return 0
    except Exception as exc:
        path=output/'failure.json'
        reason=type(exc).__name__+': '+str(exc)
        write(path,dict(status='FAILED',reason=reason,trading_authorized=False))
        registry.finish(identity,'FAILED',reason=reason,evidence=dict(failure=path))
        return 2


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    run=sub.add_parser('run')
    internal=sub.add_parser('worker')
    for item in (run,internal):
        item.add_argument('--database',type=Path,required=True)
        item.add_argument('--id',required=True)
        item.add_argument('--output',type=Path,required=True)
    run.add_argument('--candles',type=Path,required=True)
    run.add_argument('--config',type=Path,required=True)
    group=run.add_mutually_exclusive_group(required=True)
    group.add_argument('--funding',type=Path)
    group.add_argument('--funding-zero',action='store_true')
    run.add_argument('--terminal-closeout',action='store_true',help='add separate synthetic closing evaluation')
    args=parser.parse_args()
    if args.action=='worker': raise SystemExit(worker(args.database,args.id,args.output))
    outcome=run_study(args.database,args.id,args.candles,args.config,args.output,
                      funding=args.funding,funding_zero=args.funding_zero,terminal_closeout=args.terminal_closeout)
    print(json.dumps(outcome,indent=2))
    if outcome['status']!='COMPLETED': raise SystemExit(2)


if __name__=='__main__': main()
