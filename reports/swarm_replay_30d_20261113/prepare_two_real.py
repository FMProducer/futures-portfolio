"""Generate a frozen integrated two-REAL replay without touching production."""
import json
from pathlib import Path
from replay import HERE
from prepare_target_policy import replace_once


def main():
    folder = HERE/'two_real_variant05'
    folder.mkdir(exist_ok=False)
    source = HERE/'target_policy_variant'
    text = (source/'replay.py').read_text(encoding='utf-8')
    text = replace_once(text,"self.exchange, self.exchange_wallet = None, D(6000)",
        "self.exchange, self.exchange_wallet = None, D(7000)\n        from two_real_support import Runtime\n        self.real_runtime = Runtime(self)")
    text = replace_once(text,"connector = self.connector(ticker) if paper else self.exchange",
        "connector = self.connector(ticker) if paper else self.real_runtime.connector(ticker)")
    text = replace_once(text,"else not any(k.startswith('r_') for k in self.processes)",
        "else sum(k.startswith('r_') for k in self.processes) < 2")
    text = replace_once(text,"await emergency_stop(self.connector(ticker) if paper else self.exchange,",
        "await emergency_stop(self.connector(ticker) if paper else self.real_runtime.connector(ticker),")
    text = replace_once(text,"BinanceConnector=lambda **kw:self.exchange or self.connector(next(iter(self.frames)))",
        "BinanceConnector=lambda **kw:self.real_runtime.view")
    old = """            if not is_paper and (self.exchange is None or self.exchange.ticker != ticker):
                if self.exchange:
                    assert all(q == 0 for q,e in self.exchange.positions.values())
                    self.exchange_wallet = self.exchange.wallet
                self.exchange = self.connector(ticker)"""
    text = replace_once(text,old,"""            if not is_paper:
                self.exchange = self.real_runtime.connector(ticker)
                self.real_runtime.retire_closed(ticker)""")
    start = text.index('        real_equity, real_cash = float(self.exchange_wallet)')
    end = text.index('        assert math.isfinite(paper_equity)',start)
    text = text[:start]+"""        totals = self.real_runtime.account.totals()
        real_equity, real_cash = float(totals['equity']), float(totals['available'])
"""+text[end:]
    text = text.replace("('SIMULATED_REAL_STRATEGY','real_strategy_equity',1000)","('SIMULATED_REAL_STRATEGY','real_strategy_equity',2000)")
    text = text.replace("('SIMULATED_REAL_ACCOUNT','real_account_equity',6000)","('SIMULATED_REAL_ACCOUNT','real_account_equity',7000)")
    text = replace_once(text,'        from scanner import backtest_gate',
        '        from scanner import backtest_gate\n        from core import real_allocation\n        self.real_runtime.original_prepare = real_allocation.prepare')
    text = replace_once(text,'        with ExitStack() as stack:',
        """        with ExitStack() as stack:
            for name in ('prepare','claim','validate','reconcile'):
                stack.enter_context(patch.object(real_allocation,name,getattr(self.real_runtime,name)))
            stack.enter_context(patch.object(real_allocation,'retire',self.real_runtime.no_handover))""")
    text = text.replace("'REAL_execution':'disabled; record fresh-slot decisions and sticky identity reservations only'",
        "'REAL_execution':'two native workers; one 7000 wallet; two 1000 slots; shared 5000 reserve; experimental allocation adapter'")
    text = replace_once(text,"        self.finish()","        self.real_runtime.persist()\n        self.finish()")
    text = replace_once(text,'await self.clock.sleep(min(1., next_time-self.clock.now))',
        'await self.clock.sleep(min(1., next_time-self.clock.now) if self.processes else next_time-self.clock.now)')
    text = replace_once(text,'    def load(self, path, default=None):','    def base_load(self, path, default=None):')
    text = replace_once(text,'    async def worker(self, ticker, paper):','    async def worker_body(self, ticker, paper):')
    text = replace_once(text,'    async def child(self, *args, **kwargs):','    async def child_body(self, *args, **kwargs):')
    text = replace_once(text,'\n\nasync def entry():',"""

    def load(self,path,default=None):
        document = self.base_load(path,default)
        return self.real_runtime.config_view(document) if Path(path).name == 'config.json' else document

    async def worker(self,ticker,paper):
        token = self.real_runtime.worker_scope.set((ticker,paper))
        try:
            await self.worker_body(ticker,paper)
        finally:
            self.real_runtime.worker_scope.reset(token)

    async def child(self,*args,**kwargs):
        ticker = args[args.index('--ticker')+1]
        token = self.real_runtime.worker_scope.set((ticker,'--paper' in args))
        try:
            return await self.child_body(*args,**kwargs)
        finally:
            self.real_runtime.worker_scope.reset(token)


async def entry():""")
    (folder/'replay.py').write_text(text,encoding='utf-8')
    policy = (source/'policy.py').read_text(encoding='utf-8')
    policy = policy.replace("        '    target_real_bots = []\\n' + marker)","        + marker)")
    policy = policy.replace("    assert not any(k.startswith('r_') for k in replay.processes)",
        "    assert sum(k.startswith('r_') for k in replay.processes) <= 2")
    policy = policy.replace("        assert performance[ticker]['cycles'] >= 15 and performance[ticker]['profit'] > 0",
        "        if 'r_'+ticker not in replay.processes:\n            assert performance[ticker]['cycles'] >= 15 and performance[ticker]['profit'] > 0")
    old = "    return source.replace(marker,"
    new = """    source = source.replace("if any(name.startswith('r_') for name in await get_running_bots_info()):", "if sum(name.startswith('r_') for name in await get_running_bots_info()) >= 2:")
    source = source.replace("sum(name.startswith('r_') for name in running) >= 1", "sum(name.startswith('r_') for name in running) >= 2")
    return source.replace(marker,"""
    policy = replace_once(policy,old,new)
    (folder/'policy.py').write_text(policy,encoding='utf-8')
    for name in ('gate_worker.py','manifest.json','config.source.json'):
        (folder/name).write_bytes((source/name).read_bytes())
    for name in ('shared_real_account.py','two_real_support.py'):
        (folder/name).write_bytes((HERE/name).read_bytes())
    compile(text,str(folder/'replay.py'),'exec')
    compile(policy,str(folder/'policy.py'),'exec')
    print(folder)


if __name__ == '__main__':
    main()
