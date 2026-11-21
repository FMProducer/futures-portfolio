"""Configuration and historical-cycle admission regression checks."""
import json
from unittest.mock import AsyncMock
import pytest
from core.paper_budget import cumulative_cycles
from scanner.backtest_gate import admission_windows, scanner_min_volume, effective_config
from scanner.daily_universe import admission_history
from supervisor import swarm_manager as manager


@pytest.mark.parametrize('value', [[48,48],[24,48],[0,24],[48,True],[48,24.5],'48/36',[48],None])
def test_invalid_windows_fail_closed(value):
    with pytest.raises(ValueError):
        admission_windows({'admission_window_hours':value})


@pytest.mark.parametrize('value', [0,-1,float('nan'),float('inf'),True,'15000000'])
def test_invalid_volume_fail_closed(value):
    with pytest.raises(ValueError):
        scanner_min_volume({'scanner_min_volume_usdt':value})


def test_window_configuration_changes_cache_input():
    assert admission_windows({}) == (48,24)
    assert scanner_min_volume({}) == 20000000
    assert effective_config({'admission_window_hours':[48,24]}) != effective_config({'admission_window_hours':[48,36]})


def close(root, count=9):
    folder=root/'history';folder.mkdir(exist_ok=True)
    data={'state':{'base_ticker':'TUSDT','started_at':1,'rebalance_cycles':count,'paper_close_complete':True},
          'paper_state':{'base_ticker':'TUSDT','positions':{'TUSDT_LONG':0,'TUSDT_SHORT':0},
                         'share_fill_receipts':{'fill':{'event_id':f'trailing:1:{count}'}}},'close_event':f'trailing:1:{count}'}
    (folder/'paper_close_TUSDT_1.json').write_text(json.dumps(data))
    return data


def test_cycles_deduplicate_confirmed_sessions(tmp_path):
    data=close(tmp_path)
    (tmp_path/'history/paper_close_TUSDT_duplicate.json').write_text(json.dumps(data))
    current={'base_ticker':'TUSDT','started_at':2,'rebalance_cycles':6}
    assert cumulative_cycles(tmp_path,'TUSDT',current) == 15
    assert cumulative_cycles(tmp_path,'TUSDT',data['state']) == 9
    data['paper_state']['share_fill_receipts']={}
    (tmp_path/'history/paper_close_TUSDT_duplicate.json').write_text(json.dumps(data))
    with pytest.raises(ValueError,match='Unconfirmed'):
        cumulative_cycles(tmp_path,'TUSDT',current)


@pytest.mark.asyncio
async def test_cumulative_cycles_do_not_change_paper_rotation_age(tmp_path,monkeypatch):
    close(tmp_path)
    state={'base_ticker':'TUSDT','started_at':2,'rebalance_cycles':6,'last_profit':10}
    monkeypatch.setattr(manager,'BASE_PATH',tmp_path)
    monkeypatch.setattr(manager,'CORE_PATH',tmp_path/'core')
    monkeypatch.setattr(manager,'safe_load_json',AsyncMock(return_value=state))
    config={'real_use_cumulative_paper_cycles':True,'paper_idle_hours':0}
    paper=await manager.get_bot_efficiency('TUSDT',config)
    real=await manager.get_bot_efficiency('TUSDT',config,is_real=True)
    assert paper['cycles']==6 and paper['real_cycles']==15
    assert real['cycles']==real['real_cycles']==6


def test_old_gate_history_cannot_pass_new_window(tmp_path):
    folder=tmp_path/'reports/daily_universe/old';folder.mkdir(parents=True)
    (folder/'status.json').write_text(json.dumps({'status':'published'}))
    result={'TUSDT':{'pass':True,'windows':{str(h):{'pass':True,'funding_model':'disabled'} for h in (48,24)}}}
    (folder/'results.json').write_text(json.dumps(result))
    assert 'TUSDT' in admission_history(tmp_path,{'TUSDT'},'disabled',(48,24))
    assert not admission_history(tmp_path,{'TUSDT'},'disabled',(48,36))


@pytest.mark.asyncio
async def test_scanner_reads_volume_config_without_network(tmp_path,monkeypatch):
    from scanner import rank_tickers
    from unittest.mock import Mock
    config=tmp_path/'config.json'
    config.write_text(json.dumps({'scanner_min_volume_usdt':15000000}))
    scanner=Mock(get_top_tickers=AsyncMock(return_value=[]))
    monkeypatch.setattr(rank_tickers,'CONFIG_FILE',str(config))
    monkeypatch.setattr(rank_tickers,'TickerScanner',Mock(return_value=scanner))
    await rank_tickers.main(quiet=True)
    scanner.get_top_tickers.assert_awaited_once_with(min_volume=15000000)
    config.write_text('{invalid')
    with pytest.raises(ValueError,match='Cannot read scanner'):
        await rank_tickers.main(quiet=True)
