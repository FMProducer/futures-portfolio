import json
from decimal import Decimal

import pytest

from scripts.measure_native_funding_disk import measure


def test_native_prefix_writes_real_files_and_never_claims_annual_pass(tmp_path):
    config = tmp_path/'input.json'
    config.write_text(json.dumps({'portfolios':[{'cash_reserve_usdt':5000}]}))
    events = tmp_path/'events.json'
    events.write_text(json.dumps([dict(event_id=str(i),signed_amount='-1',event_time=i,
                                      symbol='AGTUSDT',source='binance_income') for i in range(3)]))
    output=tmp_path/'run'
    result=measure(config,events,output,count=3)
    assert result['status']=='PERSISTENCE_COMPLETED_NOT_STRATEGY_PASS'
    assert result['events']==result['archived_events']==3
    assert result['journal_events']==2
    assert Decimal(str(result['reserve']))==4997
    assert result['funding_paid']==3
    assert result['native_json_bytes_written']>result['final_state_bytes']>0
    assert (output/'config.json.funding.sqlite').is_file()
    assert not (output/'strategy.json.funding_intent').exists()
    assert json.loads(config.read_text())['portfolios'][0]['cash_reserve_usdt']==5000


def test_native_prefix_rejects_invalid_count(tmp_path):
    config,events=tmp_path/'config.json',tmp_path/'events.json'
    config.write_text(json.dumps({'portfolios':[{'cash_reserve_usdt':5000}]}));events.write_text('[]')
    with pytest.raises(ValueError,match='Prefix count'):
        measure(config,events,tmp_path/'run',count=1)
