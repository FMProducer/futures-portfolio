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
    with (base/'diagnostic.json').open('x',encoding='utf-8') as stream: json.dump(report,stream,indent=2)
    print(json.dumps(report,indent=2))


if __name__=='__main__': main()
