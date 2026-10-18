"""Validate preserved public capture after the exact-timestamp check failed."""
import csv
from datetime import datetime, timezone
from decimal import Decimal as D
import hashlib
import importlib
import json
from pathlib import Path


def main():
    base=Path(__file__).parent
    output=base/'runtime/data'
    failure=json.loads((output/'failure.json').read_text())
    assert failure['reason']=='AssertionError: '
    records=failure['records']
    start=int(datetime(2025,10,1,tzinfo=timezone.utc).timestamp()*1000)
    end=int(datetime(2026,10,1,tzinfo=timezone.utc).timestamp()*1000)
    archives=[r for r in records if r['url'].endswith('.zip')]
    assert len(archives)==12 and sum(r['rows'] for r in archives)==525600
    for record in archives:
        path=output/record['url'].rsplit('/',1)[1]
        assert hashlib.sha256(path.read_bytes()).hexdigest()==record['sha256']
        assert path.with_name(path.name+'.CHECKSUM').read_text().split()[0]==record['sha256']
    pages=sorted(output.glob('funding-page-*.json'),key=lambda p:int(p.stem.split('-')[-1]))
    assert len(pages)==len(records)-12
    events=[]
    for path,record in zip(pages,records[12:]):
        raw=path.read_bytes()
        assert hashlib.sha256(raw).hexdigest()==record['sha256']
        events.extend(json.loads(raw))
    coverage=importlib.import_module('reports.annual_research_2026-10-18.fetch').funding_coverage
    coverage(events,start,end)
    coverage([dict(fundingTime=3),dict(fundingTime=28800007)],0,57600000)
    for bad in ([dict(fundingTime=3)], [dict(fundingTime=3),dict(fundingTime=7)]):
        try: coverage(bad,0,57600000)
        except ValueError: pass
        else: raise AssertionError('missing/duplicate bucket accepted')
    for row in events:
        assert row['symbol']=='BTCUSDT' and row.get('rateType','Regular')=='Regular'
        assert D(row['fundingRate']).is_finite() and D(row['markPrice']).is_finite() and D(row['markPrice'])>0
    with (output/'funding.csv').open('x',newline='',encoding='utf-8') as stream:
        writer=csv.writer(stream)
        writer.writerow(['timestamp','rate','mark_price'])
        writer.writerows((r['fundingTime'],r['fundingRate'],r['markPrice']) for r in events)
    from rebalance.historical import candles
    data=candles(output/'BTCUSDT.csv')
    assert len(data)==525600 and data[0]['time']==start and data[-1]['time']==end-60000
    manifest=dict(status='CAPTURED_VALIDATED',start=start,end_exclusive=end,candles=len(data),
        funding_events=len(events),records=records,data_usage='RESEARCH_SELECTION_NOT_OOS',
        funding_coverage='one actual event in each 8h bucket; raw milliseconds preserved',
        files={name:hashlib.sha256((output/name).read_bytes()).hexdigest() for name in ('BTCUSDT.csv','funding.csv')},
        historical_filters_verified=False,admission_ready=False,trading_authorized=False)
    for target in (output/'manifest.json',base/'data_manifest.json'):
        with target.open('x',encoding='utf-8') as stream: json.dump(manifest,stream,indent=2)
    print(json.dumps({k:v for k,v in manifest.items() if k!='records'},indent=2))


if __name__=='__main__': main()
