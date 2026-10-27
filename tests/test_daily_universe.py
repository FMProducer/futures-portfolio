import json
from unittest.mock import AsyncMock

import pytest

from futures_portfolio.scanner.daily_universe import top_symbols, publish


def test_top100_uses_eligible_quote_turnover_not_existing_whitelist():
    symbols = [{'symbol':f'S{i}USDT', 'contractType':'PERPETUAL', 'status':'TRADING',
                'quoteAsset':'USDT', 'marginAsset':'USDT'} for i in range(105)]
    symbols += [dict(symbols[0], symbol='DELIVERY', contractType='CURRENT_QUARTER')]
    symbols += [dict(symbols[0], symbol='币USDT')]
    rows = [{'symbol':s['symbol'], 'quoteVolume':str(i+1)} for i,s in enumerate(symbols)]
    result = top_symbols({'symbols':symbols}, rows)
    assert len(result) == 100
    assert result[0]['symbol'] == 'S104USDT' and result[-1]['symbol'] == 'S5USDT'


def fixture_run(tmp_path):
    (tmp_path/'config.json').write_text('{}')
    (tmp_path/'tickers.txt').write_text('OLDUSDT\n')
    run = tmp_path/'run'
    run.mkdir()
    candidates = [{'symbol':s} for s in ['WEAK', 'STRONG', 'FAIL']]
    results = {'WEAK':{'pass':True, 'net_pct':1, 'max_dd_pct':4},
               'STRONG':{'pass':True, 'net_pct':2, 'max_dd_pct':2}, 'FAIL':{'pass':False}}
    return run, candidates, results


def test_publish_uses_pareto_and_keeps_fewer_than50_with_backup(tmp_path):
    run, candidates, results = fixture_run(tmp_path)
    status = publish(tmp_path, run, candidates, results, {})
    assert status['selected'] == ['STRONG', 'WEAK']
    assert (tmp_path/'tickers.txt').read_text() == 'STRONG\nWEAK\n'
    assert (run/'tickers.before.txt').read_text() == 'OLDUSDT\n'


@pytest.mark.parametrize('failure', ['missing', 'error', 'config'])
def test_incomplete_or_failed_run_never_changes_whitelist(tmp_path, failure):
    run, candidates, results = fixture_run(tmp_path)
    if failure == 'missing':
        results.pop('FAIL')
    elif failure == 'error':
        results['FAIL']['error'] = 'Missing bars'
    else:
        (tmp_path/'config.json').write_text(json.dumps({'max_drawdown_limit':10}))
    with pytest.raises(ValueError):
        publish(tmp_path, run, candidates, results, {})
    assert (tmp_path/'tickers.txt').read_text() == 'OLDUSDT\n'


@pytest.mark.asyncio
async def test_empty_published_whitelist_does_not_expand_scanner_to_market(tmp_path, monkeypatch):
    from futures_portfolio.scanner.rank_tickers import TickerScanner
    run, candidates, results = fixture_run(tmp_path)
    for row in results.values():
        row['pass'] = False
    publish(tmp_path, run, candidates, results, {})
    monkeypatch.chdir(tmp_path)
    scanner = TickerScanner()
    scanner.fetch = AsyncMock()
    try:
        assert await scanner.get_top_tickers() == []
        scanner.fetch.assert_not_called()
    finally:
        scanner.process_executor.shutdown()
