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
               'STRONG':{'pass':True, 'net_pct':2, 'max_dd_pct':2},
               'FAIL':{'pass':False, 'net_pct':-1, 'max_dd_pct':5}}
    return run, candidates, results


def test_publish_uses_pareto_and_keeps_fewer_than50_with_backup(tmp_path):
    run, candidates, results = fixture_run(tmp_path)
    status = publish(tmp_path, run, candidates, results, {})
    assert status['selected'] == ['STRONG', 'WEAK']
    assert status['passed'] == 2 and status['pool_size'] == 2
    assert results['FAIL']['pass'] is False
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
    publish(tmp_path, run, [], {}, {})
    monkeypatch.chdir(tmp_path)
    scanner = TickerScanner()
    scanner.fetch = AsyncMock()
    try:
        assert await scanner.get_top_tickers() == []
        scanner.fetch.assert_not_called()
    finally:
        scanner.process_executor.shutdown()


@pytest.mark.parametrize('passed_count,expected', [(8,8),(60,50)])
def test_cumulative_pool_does_not_pad_with_never_passed(tmp_path, passed_count, expected):
    from futures_portfolio.scanner.backtest_gate import rank_candidates
    run, _, _ = fixture_run(tmp_path)
    candidates = [{'symbol':f'S{i}USDT'} for i in range(100)]
    results = {r['symbol']:{'pass':i<passed_count, 'net_pct':100-i, 'max_dd_pct':i}
               for i,r in enumerate(candidates)}
    status = publish(tmp_path, run, candidates, results, {})
    assert len(status['selected']) == status['pool_size'] == expected
    assert status['passed'] == passed_count and status['list_role'] == 'cumulative_passed_pool'
    assert len(rank_candidates(candidates, results, set())) == passed_count
    assert sum(r['pass'] for r in results.values()) == passed_count


def test_union_tests_old_symbols_outside_top100_once():
    symbols = [{'symbol':f'S{i}USDT', 'contractType':'PERPETUAL', 'status':'TRADING',
                'quoteAsset':'USDT', 'marginAsset':'USDT'} for i in range(150)]
    volumes = [{'symbol':r['symbol'], 'quoteVolume':str(150-i)} for i,r in enumerate(symbols)]
    previous = [f'S{i}USDT' for i in range(75,125)]
    rows = top_symbols({'symbols':symbols}, volumes, previous)
    names = [r['symbol'] for r in rows]
    assert len(names) == len(set(names)) == 125
    assert set(previous).issubset(names)


def test_only_proven_old_pass_can_fill_after_fresh_candidates(tmp_path):
    run, candidates, results = fixture_run(tmp_path)
    (tmp_path/'tickers.txt').write_text('FAIL\nNEVER\n')
    archive = tmp_path/'reports/daily_universe/old'
    archive.mkdir(parents=True)
    (archive/'status.json').write_text(json.dumps({'status':'published','end_exclusive_ms':1}))
    (archive/'results.json').write_text(json.dumps({'FAIL':{
        'pass':True,'windows':{'48':{'pass':True},'24':{'pass':True}}}}))
    candidates.append({'symbol':'NEVER'})
    results['NEVER'] = {'pass':False,'net_pct':100,'max_dd_pct':0}
    status = publish(tmp_path, run, candidates, results, {})
    assert status['selected'] == ['STRONG','WEAK','FAIL']
    assert status['retained_historical'] == 1
    provenance = json.loads((run/'admissions.json').read_text())
    assert provenance['FAIL']['basis'] == 'historical_pass'
    assert provenance['STRONG']['basis'] == 'fresh_pass'
    assert results['FAIL']['pass'] is False


def test_failed_run_cannot_supply_historical_admission(tmp_path):
    run, candidates, results = fixture_run(tmp_path)
    (tmp_path/'tickers.txt').write_text('FAIL\n')
    archive=tmp_path/'reports/daily_universe/failed';archive.mkdir(parents=True)
    (archive/'status.json').write_text(json.dumps({'status':'failed'}))
    (archive/'results.json').write_text(json.dumps({'FAIL':{'pass':True,
        'windows':{'48':{'pass':True},'24':{'pass':True}}}}))
    assert 'FAIL' not in publish(tmp_path,run,candidates,results,{})['selected']
