import hashlib
import json

import pytest

from rebalance.dataset_catalog import catalog, inspect_candles


HEADER='open_time,open,high,low,close\n'
GOOD='0,100,101,99,100\n60000,100,101,99,100\n'


def test_identity_and_no_implicit_admission(tmp_path):
    path=tmp_path/'data.csv'
    path.write_text(HEADER+GOOD,encoding='utf-8')
    report=catalog(path,symbol='BTCUSDT',usage='previously_used')
    assert report['artifact']['sha256']==hashlib.sha256(path.read_bytes()).hexdigest()
    assert report['status']=='INVENTORIED'
    assert report['oos_status']=='ALREADY_USED'
    assert report['provenance']['status']=='INCOMPLETE'
    assert not report['admission_ready'] and not report['trading_authorized']
    assert not report['funding']['zero_funding_assumed']
    assert report['funding']['coverage']=='NOT_ESTABLISHED'


@pytest.mark.parametrize('body,key',[
    (GOOD+'60000,100,101,99,100\n','duplicate_timestamps'),
    ('60000,100,101,99,100\n0,100,101,99,100\n','out_of_order'),
    (GOOD.replace('60000','120000'),'missing_minutes'),
    (GOOD.replace('60000','60001'),'misaligned_timestamps'),
    (GOOD.replace('100,101,99,100','100,90,99,100',1),'invalid_rows'),
    (GOOD.replace('100,101,99,100','NaN,101,99,100',1),'invalid_rows'),
    (GOOD.replace('100,101,99,100','100,101,99',1),'invalid_rows'),
])
def test_data_faults(body,key):
    report=inspect_candles((HEADER+body).encode())
    assert report['status']=='FAIL' and report[key]>0


def test_empty_and_duplicate_columns():
    assert inspect_candles(HEADER.encode())['status']=='FAIL'
    with pytest.raises(ValueError):
        inspect_candles(b'open_time,open,high,low,close,close\n')


def test_declared_source_is_not_authentication(tmp_path):
    path=tmp_path/'data.csv'
    path.write_text(HEADER+GOOD)
    report=catalog(path,symbol='BTCUSDT',source='declared fixture',acquired_utc='2026-10-18T00:00:00Z')
    assert report['provenance']['status']=='DECLARED_NOT_AUTHENTICATED'
    assert report['oos_status']=='NOT_ESTABLISHED'
    with pytest.raises(ValueError):
        catalog(path,symbol='BTCUSDT',acquired_utc='2026-10-18T00:00:00')


def test_funding_does_not_prove_coverage_or_zero(tmp_path):
    path=tmp_path/'data.csv'
    path.write_text(HEADER+GOOD)
    rates=tmp_path/'funding.csv'
    rates.write_text('timestamp,rate,mark_price\n60000,0.001,100\n')
    report=catalog(path,symbol='BTCUSDT',funding=rates)
    assert report['funding']['rows']==1
    assert report['funding']['coverage']=='NOT_ESTABLISHED'
    rates.write_text('timestamp,rate,mark_price\n120000,0.001,100\n')
    assert catalog(path,symbol='BTCUSDT',funding=rates)['status']=='FAIL'


def test_current_filters_not_historical_evidence(tmp_path):
    path=tmp_path/'data.csv'
    path.write_text(HEADER+GOOD)
    filters=tmp_path/'filters.json'
    filters.write_text(json.dumps({'current':'snapshot'}))
    report=catalog(path,symbol='BTCUSDT',filters=filters)
    assert report['filters']['status']=='ARCHIVED_JSON_ONLY'
    assert report['filters']['historical_applicability']=='NOT_ESTABLISHED'
