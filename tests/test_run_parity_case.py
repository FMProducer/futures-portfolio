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
