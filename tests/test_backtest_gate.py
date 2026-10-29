from copy import deepcopy
import io
import json

import pytest

from futures_portfolio.scanner.backtest_gate import closed_metrics, effective_config


def test_candidate_ranking_uses_terminal_net_and_drawdown_without_moving_existing():
    from futures_portfolio.scanner.backtest_gate import rank_candidates
    candidates = [{'symbol': s} for s in ['WEAK', 'EXISTING', 'STRONG', 'SAFE', 'TIE', 'FAIL']]
    before = deepcopy(candidates)
    results = {s: {'pass': True, 'net_pct': net, 'max_dd_pct': dd}
               for s, net, dd in [('WEAK', 1, 4), ('STRONG', 3, 2),
                                  ('SAFE', 2, 0), ('TIE', 3, 2)]}
    results['FAIL'] = {'pass': False}
    ranked = rank_candidates(candidates, results, {'EXISTING'})
    assert [r['symbol'] for r in ranked] == ['STRONG', 'EXISTING', 'SAFE', 'TIE', 'WEAK']
    assert [r.get('backtest_rank') for r in ranked] == [1, None, 1, 1, 2]
    assert candidates == before
    results['STRONG']['net_pct'] = float('nan')
    with pytest.raises(ValueError, match='Nonfinite'):
        rank_candidates(candidates, results, {'EXISTING'})


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


@pytest.mark.asyncio
async def test_gate_reassesses_idle_and_new_candidates_in_one_closed_window(tmp_path, monkeypatch):
    from futures_portfolio.scanner.backtest_gate import filter_candidates
    from unittest.mock import AsyncMock
    response = {'NEW':{'pass':True, 'net_pct':2, 'max_dd_pct':1},
                'OLD':{'pass':False, 'net_pct':-1, 'max_dd_pct':2}}
    def spawn(*args, **kwargs):
        from pathlib import Path
        Path(args[-1]).write_text(json.dumps(response))
        process = AsyncMock()
        process.wait.return_value = 0
        return process
    monkeypatch.setattr('asyncio.create_subprocess_exec', AsyncMock(side_effect=spawn))
    monkeypatch.setattr('time.time', lambda: 10000)
    ranked = await filter_candidates(tmp_path, [{'symbol':'NEW'}], {}, {'OLD'}, reassess={'OLD'})
    request = json.loads((tmp_path/'reports/backtest_gate/request.json').read_text())
    assert request['symbols'] == ['NEW', 'OLD'] and request['end'] == 7200000
    assert ranked[0]['backtest']['pass']
    assert ranked[1]['symbol'] == 'OLD' and not ranked[1]['backtest']['pass']


@pytest.mark.asyncio
@pytest.mark.parametrize("long_ok,short_ok", [(True,True),(False,True),(True,False),(False,False)])
async def test_gate_downloads_once_and_requires_both_windows(tmp_path, monkeypatch, long_ok, short_ok):
    import urllib.parse
    import pandas as pd
    from scanner.backtest_gate import evaluate
    from backtest import backtest_rebalance
    end = 172800000
    requests, runs = [], []
    def download(url, **kwargs):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        start = int(query['startTime'][0])
        requests.append(start)
        bars = [[t, '100','101','99','100','1',t+59999] for t in range(start,start+86400000,60000)]
        return io.BytesIO(json.dumps(bars).encode())
    async def run(config, data, **kwargs):
        frame = pd.read_feather(next(__import__('pathlib').Path(data).glob('*.feather')))
        days = kwargs['days']
        runs.append((days, len(frame), kwargs['capital_override']))
        result = replay()
        result['final_equity'] = 1010 if (long_ok if days==2 else short_ok) else 999
        return result
    monkeypatch.setattr('urllib.request.urlopen', download)
    monkeypatch.setattr(backtest_rebalance, 'run_backtest', run)
    result = (await evaluate(tmp_path,['TESTUSDT'],{'portfolios':[{'paper_initial_capital':1000}]},end))['TESTUSDT']
    assert requests == [0,86400000]
    assert runs == [(2,2880,1000),(1,2880,1000)]
    assert result['pass'] is (long_ok and short_ok)
    assert result['windows']['48']['pass'] is long_ok
    assert result['windows']['24']['pass'] is short_ok
    assert result['net'] == result['windows']['24']['net']
    assert len(json.loads(next(tmp_path.rglob('klines.json')).read_text())) == 2880
    assert len(list(tmp_path.rglob('klines_*.json'))) == 2
    assert (await evaluate(tmp_path,['TESTUSDT'],{'portfolios':[{'paper_initial_capital':1000}]},end))['TESTUSDT'] == result
    assert len(requests) == 2 and len(runs) == 2


@pytest.mark.parametrize('wrapped', [False, True])
def test_network_retry_allows_three_retries_then_success(monkeypatch, wrapped):
    from urllib.error import URLError
    from unittest.mock import Mock
    from futures_portfolio.scanner.backtest_gate import read_public_url
    error = URLError(TimeoutError('read timeout')) if wrapped else TimeoutError('read timeout')
    request = Mock(side_effect=[error,error,error,io.BytesIO(b'ok')])
    sleeps = Mock()
    monkeypatch.setattr('urllib.request.urlopen', request)
    monkeypatch.setattr('time.sleep', sleeps)
    assert read_public_url('https://example.test') == b'ok'
    assert request.call_count == 4
    assert [c.args[0] for c in sleeps.call_args_list] == [1,2,3]


def test_network_retry_exhaustion_propagates(monkeypatch):
    from unittest.mock import Mock
    from futures_portfolio.scanner.backtest_gate import read_public_url
    request = Mock(side_effect=ConnectionResetError('reset'))
    monkeypatch.setattr('urllib.request.urlopen', request)
    monkeypatch.setattr('time.sleep', lambda delay: None)
    with pytest.raises(ConnectionResetError):read_public_url('https://example.test')
    assert request.call_count == 4


@pytest.mark.parametrize('kind', ['http','data','certificate','url'])
def test_non_network_errors_are_not_retried(monkeypatch, kind):
    from urllib.error import HTTPError, URLError
    from ssl import SSLCertVerificationError
    from unittest.mock import Mock
    from futures_portfolio.scanner.backtest_gate import read_public_url
    error = {'http':HTTPError('https://example.test',429,'limited',{},None),
             'data':ValueError('invalid data'),
             'certificate':URLError(SSLCertVerificationError('bad certificate')),
             'url':URLError('unsupported URL')}[kind]
    request = Mock(side_effect=error)
    monkeypatch.setattr('urllib.request.urlopen', request)
    with pytest.raises(type(error)):read_public_url('https://example.test')
    assert request.call_count == 1
