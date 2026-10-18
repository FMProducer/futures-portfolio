"""Offline matched comparison and portfolio-cycle PF; no admission authority."""
import argparse
from copy import deepcopy
from decimal import Decimal as D
import json
from pathlib import Path

from .historical import candles, funding_rows, replay, sha
from .kernel import Action, Config, Portfolio, decimal


def cycles(trades,config,result,funding,rows,terminal=None):
    marks={row['time']:row['open'] for row in rows}
    mark=rows[-1]['close']
    p=Portfolio(config,result['initial_equity'])
    events=[]
    active=None
    index=0
    previous=-1
    for i,event in enumerate(funding):
        if (type(event['time']) is not int or not result['start']<=event['time']<=result['end']
                or i and event['time']<=funding[i-1]['time']):
            raise ValueError('invalid cycle funding timeline')
        if decimal(event['mark'])<=0: raise ValueError('invalid cycle funding mark')
        decimal(event['rate'])

    def accrue(until):
        nonlocal index
        while index<len(funding) and funding[index]['time']<=until:
            event=funding[index]
            amount=(p.legs['SHORT'].units-p.legs['LONG'].units)*config.step*decimal(event['mark'])*decimal(event['rate'])
            p.cash_event('funding-'+str(event['time']),amount,'FUNDING')
            index+=1

    def consume(fill,synthetic):
        nonlocal active,previous
        stamp=fill['time']
        if type(stamp) is not int or not max(previous,result['start'])<=stamp<=result['end']:
            raise ValueError('invalid cycle fill timeline')
        previous=stamp
        if active is None:
            active=dict(start=stamp,wallet=p.wallet,fees=p.fees,funding=p.funding)
        p.fill('cycle-fill-'+str(len(p.receipts)),Action(fill['kind'],fill['side'],fill['units']),
               fill['price'],fill['fee'],mark if synthetic else marks[stamp])
        if not any(leg.units for leg in p.legs.values()):
            events.append(dict(start=active['start'],end=stamp,
                completion='SYNTHETIC' if synthetic else 'NATURAL_SIMULATED',
                net=str(p.wallet-active['wallet']),fees=str(p.fees-active['fees']),
                funding=str(p.funding-active['funding'])))
            active=None

    for fill in trades:
        accrue(fill['time'])
        consume(fill,False)
    accrue(result['end'])
    if (p.snapshot(mark)['equity']!=decimal(result['equity']) or p.fees!=decimal(result['fees'])
            or p.funding!=decimal(result['funding']) or index!=result['funding_events']
            or any(p.legs[s].units*config.step!=decimal(result['quantities'][s]) for s in ('LONG','SHORT'))):
        raise ValueError('cycle ledger does not match open result')
    if terminal is not None:
        if terminal['mode']!='SYNTHETIC_CANDLE_CLOSEOUT' or terminal['time']!=result['end']:
            raise ValueError('invalid cycle terminal mode/time')
        for fill in terminal['fills']:
            if fill['time']!=result['end'] or fill['kind']!='REDUCE':
                raise ValueError('invalid cycle terminal fill')
            consume(fill,True)
        if any(leg.units for leg in p.legs.values()) or p.wallet!=decimal(terminal['equity_flat']):
            raise ValueError('cycle terminal ledger mismatch')
    profit=sum((max(D(e['net']),D(0)) for e in events),D(0))
    loss=-sum((min(D(e['net']),D(0)) for e in events),D(0))
    pf=None
    if active is not None: status='OPEN_PORTFOLIO'
    elif not events: status='NO_CYCLES'
    elif not profit and not loss: status='ZERO_ONLY'
    elif not loss: status='NO_LOSS'
    elif not profit: status='NO_PROFIT'; pf='0'
    else: status='DEFINED'; pf=str(profit/loss)
    return dict(definition='PORTFOLIO_FLAT_TO_FLAT',profit_factor=pf,status=status,
        gross_profit=str(profit),gross_loss=str(loss),completed_cycles=len(events),
        natural_simulated_cycles=sum(e['completion']=='NATURAL_SIMULATED' for e in events),
        synthetic_cycles=sum(e['completion']=='SYNTHETIC' for e in events),
        zero_cycles=sum(D(e['net'])==0 for e in events),open_cycles=int(active is not None),
        real_quarantine_cycles=0,episodes=events,external_flows='NONE; unsupported',
        terminal_equity=str(p.snapshot(mark)['equity']),terminal_net=str(p.snapshot(mark)['equity']-decimal(result['initial_equity'])),
        admission_ready=False,trading_authorized=False)


def hold(rows,config,capital,spread_bps,slippage_bps,funding,*,state_sink=None):
    rows,funding=deepcopy(rows),deepcopy(funding)
    if len(rows)<2: raise ValueError('requires at least two candles')
    for i,row in enumerate(rows):
        stamp=row['time']
        if type(stamp) is not int or stamp<0 or stamp%60000 or i and stamp-rows[i-1]['time']!=60000:
            raise ValueError('requires aligned contiguous minute candles')
        for key in ('open','high','low','close'): row[key]=decimal(row[key])
        if (min(row[k] for k in ('open','high','low','close'))<=0
                or row['low']>min(row['open'],row['close']) or row['high']<max(row['open'],row['close'])):
            raise ValueError('invalid OHLC')
    for i,event in enumerate(funding):
        if (type(event['time']) is not int or not rows[0]['time']<=event['time']<=rows[-1]['time']+59999
                or i and event['time']<=funding[i-1]['time']):
            raise ValueError('invalid funding time/range/order')
        event['rate'],event['mark']=decimal(event['rate']),decimal(event['mark'])
        if event['mark']<=0: raise ValueError('invalid funding mark')
    spread,slip=decimal(spread_bps),decimal(slippage_bps)
    friction=(spread/2+slip)/10000
    if min(spread,slip)<0 or friction>=1: raise ValueError('invalid execution costs')
    p=Portfolio(config,capital)
    index=0
    fills,curve=[],[]
    peak=decimal(capital)
    max_dd=D(0)
    def accrue(until):
        nonlocal index
        while index<len(funding) and funding[index]['time']<=until:
            event=funding[index]
            amount=(p.legs['SHORT'].units-p.legs['LONG'].units)*config.step*event['mark']*event['rate']
            p.cash_event('funding-'+str(event['time']),amount,'FUNDING')
            index+=1
    for i,row in enumerate(rows):
        accrue(row['time'])
        if i==0:
            bid,ask=row['open']*(1-friction),row['open']*(1+friction)
            actions=p.plan_entry(row['open'],bid,ask)
            if actions[0].kind=='WAIT': raise ValueError('entry blocked: '+actions[0].reason)
            for action in actions:
                price=ask if action.side=='LONG' else bid
                fee=action.units*config.step*price*config.fee_rate
                p.fill('bar-0-'+action.side,action,price,fee,row['open'])
                p.legs[action.side].initial_units=action.units
                fills.append(dict(time=row['time'],kind=action.kind,side=action.side,
                                  units=action.units,price=str(price),fee=str(fee)))
        accrue(row['time']+59999)
        snapshot=p.snapshot(row['close'])
        equity=snapshot['equity']
        peak=max(peak,equity)
        max_dd=max(max_dd,(peak-equity)/peak*100)
        curve.append(dict(time=row['time']+59999,equity=str(equity)))
        if equity<=snapshot['margin']:
            raise ValueError('hold collateral exhausted; liquidation model required')
    result=dict(candles=len(rows),start=rows[0]['time'],end=rows[-1]['time']+59999,
        initial_equity=str(capital),equity=str(equity),net_mtm=str(equity-decimal(capital)),
        realized=str(p.realized),unrealized=str(sum((r['upl'] for r in snapshot['legs'].values()),D(0))),
        fees=str(p.fees),funding=str(p.funding),funding_events=index,rebalances=0,
        rejected_funding_checks=0,max_close_drawdown_pct=str(max_dd),terminal='OPEN_MTM',
        quantities={s:str(l.units*config.step) for s,l in p.legs.items()})
    if state_sink is not None: state_sink(deepcopy(p))
    return result,fills,curve


def compare(rows,config,capital,spread_bps,slippage_bps,funding,*,terminal_closeout=False):
    # Hold validation precedes candidate execution; both receive detached inputs.
    states={}
    control=hold(rows,config,capital,spread_bps,slippage_bps,funding,
                 state_sink=(lambda p: states.update(hold=p)) if terminal_closeout else None)
    candidate=replay(deepcopy(rows),config,capital,spread_bps,slippage_bps,deepcopy(funding),
                     state_sink=(lambda p: states.update(candidate=p)) if terminal_closeout else None)
    if candidate[1][:2]!=control[1]: raise ValueError('initial fills differ')
    if [r['time'] for r in candidate[2]]!=[r['time'] for r in control[2]]:
        raise ValueError('valuation timeline differs')
    delta=D(candidate[0]['net_mtm'])-D(control[0]['net_mtm'])
    summary=dict(mode='MATCHED_CANDLE_OPEN_MTM',candidate=candidate[0],hold=control[0],
        delta_net_mtm=str(delta),profit_factor=None,cycle_definition='PORTFOLIO_FLAT_TO_FLAT',
        admission_ready=False,trading_authorized=False,
        limitations=['matched research assumptions, not executable market evidence',
                     'full fills, trade candle mark proxy, no depth/latency/liquidation',
                     'terminal open positions; closing costs excluded for BOTH arms',
                     'net already includes fees/funding; delta is not proof of alpha'])
    if terminal_closeout:
        from .closeout import closeout
        exits={arm:closeout(states[arm],rows[-1]['close'],spread_bps,slippage_bps,
                           capital,data[2]) for arm,data in (('candidate',candidate),('hold',control))}
        exits['delta_net_flat']=str(D(exits['candidate']['net_flat'])-D(exits['hold']['net_flat']))
        summary['terminal_closeout']=exits
    summary['cycles']={arm:cycles(data[1],config,data[0],funding,rows,
        summary.get('terminal_closeout',{}).get(arm)) for arm,data in (('candidate',candidate),('hold',control))}
    summary['profit_factor']=summary['cycles']['candidate']['profit_factor']
    return summary,candidate,control


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candles',type=Path,required=True)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--funding',type=Path)
    group.add_argument('--funding-zero',action='store_true')
    args=parser.parse_args()
    if args.output.exists(): parser.error('output exists; choose a new folder')
    settings=json.loads(args.config.read_text(encoding='utf-8-sig'))
    summary,candidate,control=compare(candles(args.candles),Config(**settings['strategy']),
        settings['capital'],settings['spread_bps'],settings['slippage_bps'],funding_rows(args.funding))
    inputs=[args.candles,args.config]+([args.funding] if args.funding else [])
    code=[Path(__file__),Path(__file__).with_name('historical.py'),Path(__file__).with_name('kernel.py')]
    manifest=dict(inputs={str(p.resolve()):sha(p) for p in inputs},code={p.name:sha(p) for p in code},
        settings=settings,funding_mode='CSV_RATES' if args.funding else 'ASSUMED_ZERO',
        net_flows='NONE; equal virtual initial capital',limitations=summary['limitations'])
    args.output.mkdir(parents=True,exist_ok=False)
    for name,value in (('comparison',summary),('manifest',manifest)):
        (args.output/(name+'.json')).write_text(json.dumps(value,indent=2),encoding='utf-8')
    for name,data in (('candidate',candidate),('hold',control)):
        for suffix,value in zip(('result','trades','equity'),data):
            (args.output/(name+'_'+suffix+'.json')).write_text(json.dumps(value,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2))


if __name__=='__main__': main()
