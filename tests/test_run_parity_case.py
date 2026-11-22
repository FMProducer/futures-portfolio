"""The case reporter must validate all modes, not only headline return."""
import json

import pytest

from scripts.run_parity_case import run_case
from test_parity_funding_modes import inputs


@pytest.mark.asyncio
async def test_complete_case_produces_verified_report_and_native_prefix(tmp_path):
    _,feather,_=inputs(tmp_path,'disabled')
    (tmp_path/'case.json').write_text(json.dumps(dict(ticker='TESTUSDT',mode='disabled',bars=40,
        initial_capital=1000,reserve=5000,data_path=str(feather),rates_path=None)))
    await run_case(tmp_path)
    result=json.loads((tmp_path/'comparison.json').read_text())
    assert result['status']=='PASS' and not result['failures']
    assert set(result['modes'])=={'backtest','paper','real'}
    assert result['native_prefix']==dict(status='PASS',bars=40)


@pytest.mark.asyncio
async def test_virtual_clock_case_exercises_real_margin_without_dropping_bars(tmp_path):
    _,feather,_=inputs(tmp_path,'disabled')
    (tmp_path/'case.json').write_text(json.dumps(dict(ticker='TESTUSDT',mode='virtual_clock',virtual_clock=True,
        bars=40,initial_capital=1000,reserve=5000,data_path=str(feather),rates_path=None)))
    await run_case(tmp_path)
    result=json.loads((tmp_path/'comparison.json').read_text())
    assert result['status']=='PASS' and not result['failures']
    for mode in ('paper','real'):
        clock=json.loads((tmp_path/f'{mode}_clock.json').read_text())
        assert clock['margin_ms']==5000
        assert clock['premature']==clock['valid']==clock['processed_bars']==clock['file_rows']==40
        assert clock['skipped_bars']==0


@pytest.mark.asyncio
async def test_real_exchange_book_stamp_follows_virtual_clock(tmp_path):
    import pandas as pd
    from scripts.verify_real_runtime_parity import Exchange

    _,feather,_=inputs(tmp_path,'disabled')
    frame=pd.read_feather(feather)
    stamps=pd.to_datetime(frame.time,utc=True).dt.as_unit('ms').astype('int64').to_numpy()
    exchange=Exchange('TESTUSDT',frame.close.to_numpy(),stamps,[0],1000,5000,{},clock=lambda: 1780000006.0)
    book=await exchange.get_order_book('TESTUSDT')
    assert book['_fetched_at']==1780000006.0
