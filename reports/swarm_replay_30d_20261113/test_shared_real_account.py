"""Shared-wallet accounting must aggregate both routed execution owners."""
from decimal import Decimal as D
from types import SimpleNamespace
import pytest
from shared_real_account import SharedAccount, shared_connector_type


class LocalExchange:
    def __init__(self,ticker):
        self.ticker = ticker
        self.wallet = D(6000)
        self.targets = {'BASE_LONG':{'leverage':3},'BASE_SHORT':{'leverage':3}}
        self.positions = {'LONG':[D(0),D(0)],'SHORT':[D(0),D(0)]}
    def price(self):
        return D(10)


@pytest.mark.asyncio
async def test_two_owners_share_one_wallet_and_aggregate_margin():
    account = SharedAccount()
    owner = shared_connector_type(LocalExchange)
    on, hot = owner('ONUSDT'), owner('HOTUSDT')
    for connector in (on,hot):
        connector.account = account
        account.attach(connector)
    on.positions['LONG'] = [D(30),D(9)]
    hot.positions['SHORT'] = [D(60),D(11)]
    on.wallet -= D(2)
    hot.wallet += D(5)
    assert on.wallet == hot.wallet == D(7003)
    totals = account.totals()
    assert totals['available'] == D(6693)
    assert totals['equity'] == D(7093)
    assert await on.get_usdt_account() == await hot.get_usdt_account()
    with pytest.raises(AssertionError):
        account.attach(SimpleNamespace(ticker='BTCUSDT'))


def test_reconciliation_counts_both_owners_and_retired_cashflows_once():
    account = SharedAccount()
    def snapshots(ticker,balance,safe=0):
        return (dict(base_ticker=ticker,started_at=1,initial_tpv=1000,siphoning_reserve=safe),
                dict(base_ticker=ticker,balance=balance))
    owners = {'ONUSDT':snapshots('ONUSDT',990,20),'HOTUSDT':snapshots('HOTUSDT',1005)}
    account.wallet = D(7015)
    assert account.reconcile(owners) == D(7015)
    account.wallet += 1
    with pytest.raises(ValueError):
        account.reconcile(owners)
    account.wallet = D(7015)
    assert account.reconcile({'HOTUSDT':owners['HOTUSDT']},closed_cashflow=D(10)) == D(7015)
    restarted = (dict(base_ticker='ONUSDT',started_at=2,initial_tpv=990),dict(base_ticker='ONUSDT',balance=992))
    account.wallet = D(7017)
    assert account.reconcile({'ONUSDT':restarted,'HOTUSDT':owners['HOTUSDT']},closed_cashflow=D(10)) == D(7017)


def test_two_real_allocations_have_distinct_proofs_and_one_shared_reserve(tmp_path):
    from two_real_support import Runtime
    replay = SimpleNamespace(folder=tmp_path)
    runtime = Runtime(replay)
    first = runtime.claim(tmp_path,'offline','ONUSDT',1000,5000,7000,7000,1)
    second = runtime.claim(tmp_path,'offline','HOTUSDT',1000,5000,7000,6400,2)
    assert first['id'] != second['id'] and len(runtime.active) == 2
    assert runtime.account.wallet == D(7000)
    runtime.validate(tmp_path,first,'ONUSDT','offline')
    runtime.validate(tmp_path,second,'HOTUSDT','offline')
    with pytest.raises(AssertionError):
        runtime.validate(tmp_path,first,'HOTUSDT','offline')
    with pytest.raises(AssertionError):
        runtime.claim(tmp_path,'offline','BTCUSDT',1000,5000,7000,7000,3)


@pytest.mark.asyncio
async def test_native_opening_snapshot_accepts_second_scoped_owner(tmp_path):
    import json
    from unittest.mock import patch
    from core import real_allocation
    from core.funding import live_snapshot_pair_error
    from two_real_support import Runtime
    (tmp_path/'core').mkdir()
    config = dict(max_bots=10,paper_mode_bots=8,funding_model='disabled',
        portfolios=[dict(initial_capital=1000,cash_reserve_usdt=5000,targets={'VIRTUAL':{'share':.334}})])
    (tmp_path/'config.json').write_text(json.dumps(config))
    runtime = Runtime(SimpleNamespace(folder=tmp_path,exchange=None))
    runtime.original_prepare = real_allocation.prepare
    kind = shared_connector_type(LocalExchange)
    connectors = [kind(t) for t in ('ONUSDT','HOTUSDT')]
    runtime.connectors = {c.ticker:c for c in connectors}
    for connector in connectors:
        connector.account = runtime.account
        connector.api_key,connector.testnet = 'offline-test',False
        runtime.account.attach(connector)
        async def positions():
            return {f'{c.ticker}_{leg}':dict(qty=float(q),entry_price=float(entry))
                for c in connectors for leg,(q,entry) in c.positions.items() if q}
        async def orders():
            return []
        connector.get_positions,connector.get_open_orders = positions,orders
    with patch.object(real_allocation,'claim',runtime.claim),patch.object(real_allocation,'retire',runtime.no_handover):
        runtime.replay.exchange = connectors[0]
        await runtime.prepare(runtime.view,tmp_path,'ONUSDT',config)
        connectors[0].positions['LONG'] = [D(30),D(9)]
        runtime.replay.exchange = connectors[1]
        await runtime.prepare(runtime.view,tmp_path,'HOTUSDT',config)
    assert len(runtime.active) == 2
    for ticker in ('ONUSDT','HOTUSDT'):
        assert not live_snapshot_pair_error(str(tmp_path/'core'/f'real_state_{ticker}.json'),
            str(tmp_path/'core'/f'shadow_state_{ticker}.json'),ticker)


def test_retirement_preserves_actual_restart_capital_without_top_up(tmp_path):
    import json
    from two_real_support import Runtime
    (tmp_path/'core').mkdir()
    (tmp_path/'history').mkdir()
    sp,wp = tmp_path/'core/real_state_ONUSDT.json',tmp_path/'core/shadow_state_ONUSDT.json'
    state = dict(base_ticker='ONUSDT',started_at=1,initial_tpv=1000,siphoning_reserve=25,
                 trailing_stop_triggered=True,live_close={'status':'complete'})
    wallet = dict(base_ticker='ONUSDT',balance=990)
    docs = {str(sp):state,str(wp):wallet}
    def flush():
        for name,data in docs.items():
            from pathlib import Path
            Path(name).write_text(json.dumps(data))
    replay = SimpleNamespace(folder=tmp_path,docs=docs,clock=SimpleNamespace(now=2),
        paths=lambda *args:(sp,wp),load=lambda path,default:docs.get(str(path),default),flush=flush)
    runtime = Runtime(replay)
    runtime.connectors['ONUSDT'] = SimpleNamespace(positions={'LONG':(D(0),D(0)),'SHORT':(D(0),D(0))})
    runtime.claim(tmp_path,'offline','ONUSDT',1000,5000,7000,7000,1)
    runtime.account.wallet = D(7015)
    runtime.retire_closed('ONUSDT')
    assert runtime.closed_cashflow == D(15) and runtime.restart_capital['ONUSDT'] == D(990)
    assert not sp.exists() and not wp.exists() and not docs
    proof = runtime.claim(tmp_path,'offline','ONUSDT',990,5025,7015,7015,2)
    assert D(proof['capital']) == D(990) and runtime.account.wallet == D(7015)


@pytest.mark.asyncio
async def test_worker_positions_are_scoped_but_account_view_audits_both(tmp_path):
    import pandas as pd
    from two_real_support import Runtime
    class Base(LocalExchange):
        def __init__(self,ticker,*args,**kwargs):
            super().__init__(ticker)
    replay = SimpleNamespace(folder=tmp_path,exchange=None,index=lambda:0,stamps=[0],
        connector=lambda ticker:Base(ticker),
        frames={t:SimpleNamespace(close=pd.Series([10])) for t in ('ONUSDT','HOTUSDT')},
        config={'portfolios':[{'targets':{}}]})
    runtime = Runtime(replay)
    on,hot = runtime.connector('ONUSDT'),runtime.connector('HOTUSDT')
    on.positions['LONG'] = [D(30),D(9)]
    hot.positions['SHORT'] = [D(60),D(11)]
    assert set(await on.get_positions()) == {'ONUSDT_LONG'}
    assert set(await hot.get_positions()) == {'HOTUSDT_SHORT'}
    with pytest.raises(ValueError):
        await runtime.view.get_positions()
    runtime.active = {'ONUSDT':{},'HOTUSDT':{}}
    assert set(await runtime.view.get_positions()) == {'ONUSDT_LONG','HOTUSDT_SHORT'}


def test_worker_config_uses_funded_capital_without_changing_shared_config(tmp_path):
    from copy import deepcopy
    from two_real_support import Runtime
    from core.paper_budget import initialize,reserve
    (tmp_path/'core').mkdir()
    initialize(tmp_path,8,1000,{})
    reserve(tmp_path,'ONUSDT',990)
    runtime = Runtime(SimpleNamespace(folder=tmp_path))
    baseline = {'portfolios':[dict(initial_capital=1000,paper_initial_capital=1000)]}
    token = runtime.worker_scope.set(('ONUSDT',True))
    try:
        assert runtime.config_view(deepcopy(baseline))['portfolios'][0]['paper_initial_capital'] == 990
    finally:
        runtime.worker_scope.reset(token)
    runtime.active['HOTUSDT'] = {'capital':'980'}
    token = runtime.worker_scope.set(('HOTUSDT',False))
    try:
        assert runtime.config_view(deepcopy(baseline))['portfolios'][0]['initial_capital'] == 980
    finally:
        runtime.worker_scope.reset(token)
    assert runtime.config_view(deepcopy(baseline)) == baseline


def test_shared_reconciliation_accepts_known_other_owner_inflight_but_rejects_unknown_cash(tmp_path):
    from two_real_support import Runtime
    runtime = Runtime(SimpleNamespace(folder=tmp_path,config={'funding_model':'disabled'}))
    runtime.connectors = {t:SimpleNamespace(fills=[]) for t in ('ONUSDT','HOTUSDT')}
    on = runtime.claim(tmp_path,'offline','ONUSDT',1000,5000,7000,7000,1)
    hot = runtime.claim(tmp_path,'offline','HOTUSDT',1000,5000,7000,6400,2)
    runtime.connectors['ONUSDT'].fills.append(dict(pnl='10',commission='1'))
    runtime.account.wallet = D(7009)
    runtime.reconcile(tmp_path,{'allocation':hot},{'base_ticker':'HOTUSDT'},{'balance':1000},7009,'offline')
    with pytest.raises(ValueError):
        runtime.reconcile(tmp_path,{'allocation':on},{'base_ticker':'ONUSDT'},{'balance':1000},7009,'offline')
    runtime.reconcile(tmp_path,{'allocation':on},{'base_ticker':'ONUSDT'},{'balance':1009},7009,'offline')
    runtime.account.wallet += 1
    with pytest.raises(ValueError):
        runtime.reconcile(tmp_path,{'allocation':hot},{'base_ticker':'HOTUSDT'},{'balance':1000},7010,'offline')
