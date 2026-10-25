from copy import deepcopy
import io
import json

import pytest

from futures_portfolio.scanner.backtest_gate import closed_metrics, effective_config


def replay():
    return {"accounting_ok":True,"settings":{"slippage_pct":0.001,"commission_pct":0.0004},
            "open_positions":{"long":2,"short":1},"final_equity":1010,"initial_capital":1000,
            "equity_curve":[1000,1010],"max_dd_pct":0,"cycles":2,"funding_model":"legacy_constant"}


def test_gate_deducts_terminal_cost_and_requires_real_rebalance():
    result = replay();before = deepcopy(result)
    metrics = closed_metrics(result,100)
    assert metrics['terminal_slippage'] == pytest.approx(0.3)
    assert metrics['terminal_commission'] == pytest.approx(0.11996)
    assert metrics['net'] == pytest.approx(9.58004)
    assert metrics['pass'] and result == before
    result['cycles']=1
    assert not closed_metrics(result,100)['pass']
    result['cycles']=2;result['final_equity']=1000.1
    assert not closed_metrics(result,100)['pass']
    result['max_dd_pct']=50.01
    assert not closed_metrics(result,100)['pass']
    result['accounting_ok']=False
    with pytest.raises(ValueError):closed_metrics(result,100)
    assert 'api_key' not in effective_config({'api_key':'secret','portfolios':[]})


@pytest.mark.asyncio
async def test_gate_rejects_incomplete_window(tmp_path, monkeypatch):
    from futures_portfolio.scanner.backtest_gate import evaluate
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **k: io.BytesIO(b'[]'))
    result = await evaluate(tmp_path,['TESTUSDT'],{'portfolios':[{'paper_initial_capital':1000}]},86400000)
    assert not result['TESTUSDT']['pass']
    assert 'Missing, duplicate or unclosed' in result['TESTUSDT']['error']
    assert not list(tmp_path.rglob('*.feather'))
