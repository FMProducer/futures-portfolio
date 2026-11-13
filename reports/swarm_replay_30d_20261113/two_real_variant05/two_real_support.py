"""Experiment-only two-owner allocation over independent shared exchange facts."""
from copy import deepcopy
from contextvars import ContextVar
from decimal import Decimal as D
import json
import time
import uuid
from shared_real_account import SharedAccount, shared_connector_type


class Runtime:
    def __init__(self,replay):
        self.replay = replay
        self.account = SharedAccount()
        self.connectors = {}
        self.active = {}
        self.closed_cashflow = D(0)
        self.closed = []
        self.restart_capital = {}
        self.original_prepare = None
        self.worker_scope = ContextVar('modeled_worker_scope',default=None)
        self.view = AccountView(self)

    def config_view(self,config):
        scope = self.worker_scope.get()
        if scope is None or not config:
            return config
        ticker,paper = scope
        if paper:
            from core.paper_budget import read_budget
            budget = read_budget(self.replay.folder)
            capital = None if not budget else budget['allocations'].get(ticker)
            key = 'paper_initial_capital'
        else:
            proof = self.active.get(ticker)
            capital = None if not proof else float(proof['capital'])
            key = 'initial_capital'
        if capital is not None:
            config['portfolios'][0][key] = capital
        return config

    def connector(self,ticker):
        assert ticker in ('ONUSDT','HOTUSDT')
        if ticker not in self.connectors:
            sample = self.replay.connector(ticker)
            base = shared_connector_type(type(sample))
            runtime = self
            class Routed(base):
                async def get_positions(self):
                    return {f'{self.ticker}_{leg}':dict(qty=float(q if leg=='LONG' else -q),entry_price=float(entry))
                        for leg,(q,entry) in self.positions.items() if q}
                async def get_mark_prices(self,*args):
                    return {t:float(c.price()) for t,c in runtime.connectors.items()}
            c = Routed(ticker,self.replay.frames[ticker].close.to_numpy(),self.replay.stamps,
                [self.replay.index()],1000,5000,self.replay.config['portfolios'][0]['targets'],funding_model='disabled')
            c.account = self.account
            self.account.attach(c)
            self.connectors[ticker] = c
        return self.connectors[ticker]

    def persist(self):
        from replay import write
        write(self.replay.folder/'core/real_allocations.json',dict(active=self.active,
            closed=self.closed,closed_cashflow=str(self.closed_cashflow),initial_wallet=7000))

    def validate(self,root,proof,ticker,key):
        assert root.resolve() == self.replay.folder.resolve()
        assert ticker in self.active and self.active[ticker] == proof
        assert proof['ticker'] == ticker and proof['account_key'] == key

    def claim(self,root,key,ticker,capital,reserve,wallet,available,snapshot_ms):
        assert ticker not in self.active and len(self.active) < 2
        assert D(str(capital)) == self.restart_capital.get(ticker,D(1000)) and D(str(reserve)) >= D(5000)
        required = D(str(capital))+D(str(reserve))+D(1000)*(1-len(self.active))
        if D(str(available)) < required:
            raise ValueError('Insufficient shared modeled margin for two slots and reserve')
        proof = dict(id=uuid.uuid4().hex,account_key=key,ticker=ticker,capital=str(capital),
            reserve=str(reserve),account_wallet=str(wallet),available=str(available),snapshot_ms=snapshot_ms,
            fill_offset=len(getattr(self.connectors.get(ticker),'fills',[])))
        self.active[ticker] = proof
        self.persist()
        return proof

    def reconcile(self,root,source,state,wallet,actual_wallet,key):
        self.validate(root,source['allocation'],state['base_ticker'],key)
        assert self.replay.config['funding_model'] == 'disabled'
        assert D(str(actual_wallet)) == self.account.wallet
        def cashflow(fills):
            return sum((D(f['pnl'])-D(f['commission']) for f in fills),D(0))
        expected = D(7000)+sum((cashflow(getattr(c,'fills',[])) for c in self.connectors.values()),D(0))
        if abs(expected-self.account.wallet) > D('.00000001'):
            raise ValueError('Shared modeled exchange wallet has unexplained cashflows')
        ticker = state['base_ticker']
        proof = self.active[ticker]
        own_facts = cashflow(self.connectors[ticker].fills[proof['fill_offset']:])
        own_shadow = D(str(wallet['balance']))-D(proof['capital'])+D(str(state.get('siphoning_reserve',0)))
        if abs(own_facts-own_shadow) > D('.00000001'):
            raise ValueError('Modeled REAL owner shadow disagrees with its confirmed fills')

    async def no_handover(self,*args):
        return None

    async def prepare(self,connector,root,ticker,config):
        assert config['max_bots']-config['paper_mode_bots'] == 2
        original = connector.get_positions
        async def own_positions():
            return {k:v for k,v in (await original()).items() if k.startswith(ticker+'_')}
        connector.get_positions = own_positions
        try:
            isolated = deepcopy(config)
            isolated['max_bots'] = isolated['paper_mode_bots']+1
            isolated['portfolios'][0]['initial_capital'] = float(self.restart_capital.get(ticker,D(1000)))
            return await self.original_prepare(connector,root,ticker,isolated)
        finally:
            connector.get_positions = original

    def retire_closed(self,ticker):
        sp,wp = self.replay.paths(ticker,False)
        state,wallet = self.replay.load(sp,{}),self.replay.load(wp,{})
        if not state or not state.get('trailing_stop_triggered'):
            return
        assert state['live_close']['status'] == 'complete'
        assert not state.get('startup_blocked') and not state.get('rebalance_inflight')
        assert not any(q for q,e in self.connectors[ticker].positions.values())
        proof = self.active.pop(ticker)
        self.restart_capital[ticker] = D(str(wallet['balance']))
        delta = D(str(wallet['balance']))-D(str(state['initial_tpv']))+D(str(state.get('siphoning_reserve',0)))
        self.closed_cashflow += delta
        self.closed.append(dict(proof=proof,state=state,wallet=wallet,cashflow=str(delta),time=self.replay.clock.now))
        self.replay.flush()
        for path in (sp,wp):
            self.replay.docs.pop(str(path),None)
            path.rename(self.replay.folder/'history'/f"retired_{proof['id']}_{path.name}")
        self.persist()


class AccountView:
    def __init__(self,runtime):
        self.runtime = runtime

    def __getattr__(self,name):
        connector = self.runtime.replay.exchange or self.runtime.connector('ONUSDT')
        return getattr(connector,name)

    async def get_positions(self):
        result = {}
        for ticker,connector in self.runtime.connectors.items():
            for leg,(qty,entry) in connector.positions.items():
                if not qty:
                    continue
                if ticker not in self.runtime.active:
                    raise ValueError('Unallocated modeled account position')
                result[f'{ticker}_{leg}'] = dict(qty=float(qty if leg=='LONG' else -qty),entry_price=float(entry))
        return result
