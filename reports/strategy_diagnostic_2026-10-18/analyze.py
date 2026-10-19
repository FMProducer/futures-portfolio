"""Read-only residual-delta and minute-PnL audit of completed B00 evidence."""
import csv
from datetime import datetime, timezone
from decimal import Decimal as D
import hashlib
from itertools import groupby
import json
from pathlib import Path

import numpy as np


def main():
    base=Path(__file__).resolve().parent
    root=base.parents[1]
    annual=root/'reports/annual_research_2026-10-18'
    study=annual/'runtime/studies/B00'
    load=lambda p:json.loads(p.read_text(encoding='utf-8-sig'))
    curve=load(study/'candidate_equity.json')
    trades=load(study/'candidate_trades.json')
    cfg=load(study/'frozen/inputs/config.json')
    step=D(cfg['strategy']['step'])
    equity=np.array([float(r['equity']) for r in curve])
    changes=np.diff(equity)
    worst_indices=[int(np.argmin(changes))+1,int(np.argmax(changes))+1]
    wanted={curve[i]['time']-59999 for i in worst_indices}
    before={curve[i-1]['time']-59999 for i in worst_indices}
    candles={}
    with (annual/'runtime/data/BTCUSDT.csv').open(newline='') as stream:
        for r in csv.DictReader(stream):
            if int(r['open_time']) in wanted|before: candles[int(r['open_time'])]=r
    funding=load(annual/'runtime/data/funding-page-0.json')
    for path in sorted((annual/'runtime/data').glob('funding-page-*.json')):
        if path.name!='funding-page-0.json': funding.extend(load(path))
    qty={'LONG':D(0),'SHORT':D(0)}
    max_abs=D(0)
    max_row=None
    for stamp,group in groupby(trades,key=lambda t:t['time']):
        for trade in group:
            qty[trade['side']]+=trade['units']*step*(1 if trade['kind']=='ADD' else -1)
        delta=qty['LONG']-qty['SHORT']
        if abs(delta)>max_abs:
            max_abs=abs(delta)
            max_row=dict(time=stamp,long=str(qty['LONG']),short=str(qty['SHORT']),delta_btc=str(delta))
    findings=[]
    for i in worst_indices:
        stamp=curve[i]['time']-59999
        old={'LONG':D(0),'SHORT':D(0)}
        for trade in trades:
            if trade['time']>=stamp: break
            old[trade['side']]+=trade['units']*step*(1 if trade['kind']=='ADD' else -1)
        fills=[t for t in trades if t['time']==stamp]
        close=D(candles[stamp]['close'])
        prior=D(candles[stamp-60000]['close'])
        interval_funding=[r for r in funding if stamp<=r['fundingTime']<=stamp+59999]
        assert not interval_funding
        price_component=(old['LONG']-old['SHORT'])*(close-prior)
        fill_component=sum((t['units']*step*(close-D(t['price']))*
            (1 if (t['kind']=='ADD')==(t['side']=='LONG') else -1)-D(t['fee']) for t in fills),D(0))
        observed=D(curve[i]['equity'])-D(curve[i-1]['equity'])
        assert abs(observed-price_component-fill_component)<D('1e-19')
        findings.append(dict(time_utc=datetime.fromtimestamp(stamp/1000,timezone.utc).isoformat(),
            prior_close=str(prior),close=str(close),price_change=str(close-prior),
            long_before=str(old['LONG']),short_before=str(old['SHORT']),delta_btc=str(old['LONG']-old['SHORT']),
            observed_equity_change=str(observed),residual_delta_pnl=str(price_component),
            fills_pnl_less_fees=str(fill_component),fills=fills,funding_events=0,
            arithmetic_residual=str(observed-price_component-fill_component)))
    frozen=root/'reports/day_retry_2026-10-18'
    paper=load(frozen/'frozen.json')
    paper_hash=hashlib.sha256((frozen/'runtime/code/rebalance/kernel.py').read_bytes()).hexdigest()
    baseline_hash=hashlib.sha256((study/'frozen/code/rebalance/kernel.py').read_bytes()).hexdigest()
    assert paper_hash==baseline_hash==paper['sources']['kernel.py']
    report=dict(status='DIAGNOSTIC_NOT_ADMISSION',same_kernel_sha256=paper_hash,
        same_strategy_settings=paper['settings']==cfg,max_post_event_group_residual_delta=max_row,
        extreme_minute_changes=findings,paper_cancelled_by_user=True,
        limitations=['two extreme minute bridges checked, not every event',
                     'maximum is after all fills at each timestamp, before synthetic terminal',
                     'price provenance requires separate exchange-data audit'],
        admission_ready=False,trading_authorized=False)
    qty={'LONG':D(0),'SHORT':D(0)}
    ti=fi=nonzero=0
    previous_equity=D('10000')
    previous_close=None
    maximum_residual=D(0)
    totals={k:D(0) for k in ('price','fills_less_fees','funding','observed','residual','fees')}
    funding=sorted(funding,key=lambda e:e['fundingTime'])
    with (annual/'runtime/data/BTCUSDT.csv').open(newline='') as stream:
        for i,bar in enumerate(csv.DictReader(stream)):
            stamp=int(bar['open_time'])
            assert curve[i]['time']==stamp+59999
            close=D(bar['close'])
            if previous_close is None: previous_close=D(bar['open'])
            price=(qty['LONG']-qty['SHORT'])*(close-previous_close)
            cash=D(0)
            while fi<len(funding) and funding[fi]['fundingTime']<=stamp:
                event=funding[fi]
                cash+=(qty['SHORT']-qty['LONG'])*D(event['markPrice'])*D(event['fundingRate'])
                fi+=1
            fills_component=D(0)
            while ti<len(trades) and trades[ti]['time']==stamp:
                trade=trades[ti]
                change=trade['units']*step*(1 if trade['kind']=='ADD' else -1)
                signed=change*(1 if trade['side']=='LONG' else -1)
                fee=D(trade['fee'])
                fills_component+=signed*(close-D(trade['price']))-fee
                totals['fees']+=fee
                qty[trade['side']]+=change
                ti+=1
            while fi<len(funding) and funding[fi]['fundingTime']<=stamp+59999:
                event=funding[fi]
                cash+=(qty['SHORT']-qty['LONG'])*D(event['markPrice'])*D(event['fundingRate'])
                fi+=1
            observed=D(curve[i]['equity'])-previous_equity
            residual=observed-price-fills_component-cash
            maximum_residual=max(maximum_residual,abs(residual))
            assert abs(residual)<D('1e-19'), (stamp,str(residual))
            for key,value in dict(price=price,fills_less_fees=fills_component,funding=cash,
                                  observed=observed,residual=residual).items(): totals[key]+=value
            nonzero+=qty['LONG']!=qty['SHORT']
            previous_close=close
            previous_equity=D(curve[i]['equity'])
    result=load(study/'candidate_result.json')
    assert i+1==len(curve)==result['candles'] and ti==len(trades) and fi==result['funding_events']
    assert {k:str(v) for k,v in qty.items()}==result['quantities']
    assert totals['funding']==D(result['funding']) and totals['fees']==D(result['fees'])
    report['all_minute_reconciliation']=dict(minutes=i+1,fills=ti,funding_events=fi,
        nonzero_delta_minutes=nonzero,max_absolute_arithmetic_residual=str(maximum_residual),
        numerical_tolerance='1e-19',totals={k:str(v) for k,v in totals.items()},
        final_quantities={k:str(v) for k,v in qty.items()})
    report['limitations'][0]='all minute bridges checked against saved prices, fills and funding; not execution realism'
    with (base/'minute_reconciliation.json').open('x',encoding='utf-8') as stream: json.dump(report,stream,indent=2)
    print(json.dumps(report,indent=2))


if __name__=='__main__': main()
