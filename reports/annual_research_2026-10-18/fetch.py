"""Public BTCUSDT research data, selected year; no credentials or trading."""
import csv
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import urllib.parse
import urllib.request
import zipfile


def get(url):
    with urllib.request.urlopen(url,timeout=30) as response:
        return response.read()


def funding_coverage(events,start,end):
    times=[row['fundingTime'] for row in events]
    if (any(type(t) is not int or not start<=t<end for t in times) or
            any(b<=a for a,b in zip(times,times[1:])) or
            [t//28800000 for t in times]!=list(range(start//28800000,end//28800000))):
        raise ValueError('missing/duplicate/out-of-order funding bucket')


def main():
    output=Path(__file__).parent/'runtime/data'
    output.mkdir(parents=True,exist_ok=False)
    start=int(datetime(2025,10,1,tzinfo=timezone.utc).timestamp()*1000)
    end=int(datetime(2026,10,1,tzinfo=timezone.utc).timestamp()*1000)
    records=[]
    previous=start-60000
    count=0
    try:
        with (output/'BTCUSDT.csv').open('x',newline='',encoding='utf-8') as stream:
            writer=csv.writer(stream)
            writer.writerow(['open_time','open','high','low','close'])
            for year,month in [(2025,m) for m in range(10,13)]+[(2026,m) for m in range(1,10)]:
                name=f'BTCUSDT-1m-{year}-{month:02d}.zip'
                url='https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1m/'+name
                body=get(url)
                checksum=get(url+'.CHECKSUM')
                digest=hashlib.sha256(body).hexdigest()
                if digest!=checksum.decode().split()[0]: raise ValueError('archive checksum mismatch')
                (output/name).write_bytes(body)
                (output/(name+'.CHECKSUM')).write_bytes(checksum)
                with zipfile.ZipFile(io.BytesIO(body)) as archive:
                    names=archive.namelist()
                    if len(names)!=1 or not names[0].endswith('.csv'): raise ValueError('unexpected archive members')
                    raw=archive.read(names[0])
                monthly=0
                for row in csv.reader(io.StringIO(raw.decode('utf-8-sig'))):
                    if row[0]=='open_time': continue
                    stamp=int(row[0])
                    prices=[Decimal(v) for v in row[1:5]]
                    if (stamp!=previous+60000 or not start<=stamp<end or
                            any(not v.is_finite() or v<=0 for v in prices) or
                            prices[2]>min(prices[0],prices[3]) or prices[1]<max(prices[0],prices[3])):
                        raise ValueError('invalid/gapped annual candle data')
                    writer.writerow(row[:5])
                    previous=stamp
                    monthly+=1
                count+=monthly
                records.append(dict(url=url,sha256=digest,bytes=len(body),rows=monthly,
                                    acquired_utc=datetime.now(timezone.utc).isoformat()))
                print(json.dumps(records[-1]),flush=True)
        assert count==(end-start)//60000 and previous==end-60000
        events=[]
        cursor=start
        while cursor<end:
            url='https://fapi.binance.com/fapi/v1/fundingRate?'+urllib.parse.urlencode(
                dict(symbol='BTCUSDT',startTime=cursor,endTime=end-1,limit=1000))
            raw=get(url)
            batch=json.loads(raw)
            if not isinstance(batch,list) or not batch: break
            (output/f'funding-page-{len(events)}.json').write_bytes(raw)
            records.append(dict(url=url,sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw),
                                acquired_utc=datetime.now(timezone.utc).isoformat()))
            for row in batch:
                stamp=row['fundingTime']
                if (type(stamp) is not int or not cursor<=stamp<end or row['symbol']!='BTCUSDT'
                        or events and stamp<=events[-1]['fundingTime']
                        or row.get('rateType','Regular')!='Regular'
                        or not Decimal(row['fundingRate']).is_finite()
                        or not Decimal(row['markPrice']).is_finite() or Decimal(row['markPrice'])<=0):
                    raise ValueError('invalid funding response')
                events.append(row)
            cursor=events[-1]['fundingTime']+1
        funding_coverage(events,start,end)
        with (output/'funding.csv').open('x',newline='',encoding='utf-8') as stream:
            writer=csv.writer(stream)
            writer.writerow(['timestamp','rate','mark_price'])
            writer.writerows((r['fundingTime'],r['fundingRate'],r['markPrice']) for r in events)
        manifest=dict(status='CAPTURED_VALIDATED',start=start,end_exclusive=end,candles=count,
            funding_events=len(events),records=records,data_usage='RESEARCH_SELECTION_NOT_OOS',
            files={name:hashlib.sha256((output/name).read_bytes()).hexdigest() for name in ('BTCUSDT.csv','funding.csv')},
            historical_filters_verified=False,admission_ready=False,trading_authorized=False)
        (output/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in manifest.items() if k!='records'}),flush=True)
    except Exception as exc:
        (output/'failure.json').write_text(json.dumps(dict(status='FAILED',reason=type(exc).__name__+': '+str(exc),
            records=records,admission_ready=False)),encoding='utf-8')
        raise


if __name__=='__main__': main()
