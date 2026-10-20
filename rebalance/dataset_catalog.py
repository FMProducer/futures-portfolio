"""Offline inventory of disclosed research data. Never grants OOS/live admission."""
import argparse
import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
from pathlib import Path
import re


def artifact(path):
    path=Path(path).resolve()
    data=path.read_bytes() # Hash and parse the SAME captured bytes.
    return dict(path=str(path),sha256=hashlib.sha256(data).hexdigest(),bytes=len(data)),data


def number(text):
    value=Decimal(text)
    if not value.is_finite(): raise ValueError('nonfinite value')
    return value


def table(data,required):
    reader=csv.DictReader(io.StringIO(data.decode('utf-8-sig'),newline=''))
    names=reader.fieldnames or []
    if len(names)!=len(set(names)) or not set(required)<=set(names):
        raise ValueError('missing or duplicate CSV columns')
    return reader


def inspect_candles(data):
    reader=table(data,('open_time','open','high','low','close'))
    seen=set()
    first=last=previous=None
    count=duplicates=out_of_order=missing=misaligned=invalid=0
    errors=[]
    for line,row in enumerate(reader,2):
        count+=1
        try:
            if None in row or any(v is None for v in row.values()): raise ValueError('CSV row width')
            stamp=int(row['open_time'])
            if stamp<0: raise ValueError('negative timestamp')
            if stamp%60000: misaligned+=1
            if stamp in seen: duplicates+=1
            seen.add(stamp)
            first=stamp if first is None else min(first,stamp)
            last=stamp if last is None else max(last,stamp)
            if previous is not None:
                delta=stamp-previous
                if delta<0: out_of_order+=1
                if delta>60000: missing+=(delta-1)//60000
            previous=stamp
            o,h,l,c=(number(row[k]) for k in ('open','high','low','close'))
            if min(o,h,l,c)<=0 or l>min(o,c) or h<max(o,c) or l>h:
                raise ValueError('invalid OHLC')
            if 'close_time' in row and int(row['close_time'])!=stamp+59999:
                raise ValueError('invalid 1m close_time')
            if 'volume' in row and number(row['volume'])<0: raise ValueError('negative volume')
        except (ValueError,TypeError,InvalidOperation) as exc:
            invalid+=1
            if len(errors)<10: errors.append(dict(line=line,reason=str(exc)))
    passed=count>=2 and not any((duplicates,out_of_order,missing,misaligned,invalid))
    return dict(status='PASS' if passed else 'FAIL',rows=count,first_open_time=first,
        last_open_time=last,last_close_time=None if last is None else last+59999,
        duplicate_timestamps=duplicates,out_of_order=out_of_order,missing_minutes=missing,
        misaligned_timestamps=misaligned,invalid_rows=invalid,errors=errors)


def inspect_funding(data,start,end):
    reader=table(data,('timestamp','rate','mark_price'))
    count=invalid=0
    previous=None
    for row in reader:
        count+=1
        try:
            stamp=int(row['timestamp'])
            rate,mark=number(row['rate']),number(row['mark_price'])
            if (None in row or any(v is None for v in row.values()) or mark<=0
                    or previous is not None and stamp<=previous
                    or start is None or not start<=stamp<=end):
                raise ValueError('invalid funding identity/range')
            previous=stamp
        except (ValueError,TypeError,InvalidOperation): invalid+=1
    # Even plausible rows cannot prove completeness or the historical schedule.
    return dict(status='PASS' if not invalid else 'FAIL',rows=count,invalid_rows=invalid,
                coverage='NOT_ESTABLISHED',zero_funding_assumed=False)


def catalog(path,*,symbol,source=None,acquired_utc=None,usage='unknown',funding=None,filters=None):
    if not re.fullmatch('[A-Z0-9]{5,20}',symbol) or usage not in ('unknown','previously_used'):
        raise ValueError('invalid symbol or usage')
    if acquired_utc is not None:
        stamp=datetime.fromisoformat(acquired_utc.replace('Z','+00:00'))
        if stamp.utcoffset() is None or stamp.utcoffset().total_seconds()!=0:
            raise ValueError('acquired_utc requires explicit UTC')
    info,data=artifact(path)
    candles=inspect_candles(data)
    result=dict(version='dataset-catalog-v1',symbol_declared=symbol,interval='1m',artifact=info,
        candles=candles,provenance=dict(source_declared=source,acquired_utc_declared=acquired_utc,
        status='DECLARED_NOT_AUTHENTICATED' if source and acquired_utc else 'INCOMPLETE'),
        usage=usage,oos_status='ALREADY_USED' if usage=='previously_used' else 'NOT_ESTABLISHED',
        funding=dict(status='NOT_PROVIDED',coverage='NOT_ESTABLISHED',zero_funding_assumed=False),
        filters=dict(status='NOT_PROVIDED',historical_applicability='NOT_ESTABLISHED'),
        inspected_utc=datetime.now(timezone.utc).isoformat(),admission_ready=False,trading_authorized=False,
        limitations=['CSV symbol is caller-declared, not authenticated',
                    'SHA proves byte identity, not source authenticity',
                    'No historical exchange schedule/filters or OOS freshness inferred'])
    if funding:
        item,raw=artifact(funding)
        result['funding']=dict(artifact=item,**inspect_funding(raw,candles['first_open_time'],candles['last_close_time']))
    if filters:
        item,raw=artifact(filters)
        # Archive evidence bytes without confusing a current snapshot with history.
        json.loads(raw.decode('utf-8-sig'))
        result['filters']=dict(status='ARCHIVED_JSON_ONLY',artifact=item,historical_applicability='NOT_ESTABLISHED')
    result['status']='FAIL' if candles['status']=='FAIL' or result['funding']['status']=='FAIL' else 'INVENTORIED'
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candles',type=Path,required=True)
    parser.add_argument('--symbol',required=True)
    parser.add_argument('--source')
    parser.add_argument('--acquired-utc')
    parser.add_argument('--usage',choices=('unknown','previously_used'),default='unknown')
    parser.add_argument('--funding',type=Path)
    parser.add_argument('--filters',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=catalog(args.candles,symbol=args.symbol,source=args.source,acquired_utc=args.acquired_utc,
        usage=args.usage,funding=args.funding,filters=args.filters)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as stream:
        json.dump(result,stream,indent=2)
    print(json.dumps(dict(status=result['status'],rows=result['candles']['rows'],admission_ready=False)))
    if result['status']=='FAIL': raise SystemExit(2)


if __name__=='__main__': main()
