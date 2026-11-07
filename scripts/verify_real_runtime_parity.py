"""Offline REAL strategy replay with independent exchange facts and synthetic funding."""
import ast
from contextlib import nullcontext
import asyncio
from copy import deepcopy
from decimal import Decimal as D
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import funding
from core.main import rebalance_loop
from core.real_allocation import prepare
from scripts.verify_runtime_parity import EndReplay, ReplayFailure, Quiet, Notifier, isolated_reserve_store


class Exchange:
    """Immediate market fills; private trade facts are independent of the shadow wallet."""
    api_key, testnet, client = 'offline-replay-no-exchange-credentials', False, True

    def __init__(self, ticker, prices, stamps, index, capital, reserve, targets, archive_applied=False):
        self.ticker, self.prices, self.stamps, self.index = ticker, prices, stamps, index
        self.wallet = D(str(capital)) + D(str(reserve))
        self.targets = targets
        self.archive_applied = archive_applied
        self.positions = {leg: [D(0), D(0)] for leg in ('LONG', 'SHORT')}
        self.orders, self.trades, self.fills, self.income = {}, {}, [], []
        self.funding_bar = -1
        self.futures_client = SimpleNamespace(futures_create_order=self.create_order)

    def price(self):
        return D(str(self.prices[self.index[0]]))

    def create_order(self, **params):
        assert params['symbol'] == self.ticker and params['type'] == 'MARKET'
        leg, side = params['positionSide'], params['side']
        qty, price = D(params['quantity']), self.price() * (D('1.0005') if side == 'BUY' else D('.9995'))
        old, entry = self.positions[leg]
        reduction = (leg == 'LONG' and side == 'SELL') or (leg == 'SHORT' and side == 'BUY')
        pnl = D(0)
        if reduction:
            assert 0 < qty <= old, (qty, old)
            pnl = qty * (price-entry) * (1 if leg == 'LONG' else -1)
            self.positions[leg] = [old-qty, entry if old > qty else D(0)]
        else:
            self.positions[leg] = [old+qty, (old*entry+qty*price)/(old+qty)]
        fee = qty*price*D('.0004')
        self.wallet += pnl-fee
        identity = len(self.orders)+1
        result = dict(orderId=identity, status='FILLED', executedQty=str(qty), avgPrice=str(price),
                      clientOrderId=params.get('newClientOrderId', ''))
        self.orders[identity] = result
        self.trades[identity] = [dict(qty=str(qty), price=str(price), realizedPnl=str(pnl),
                                      commission=str(fee), commissionAsset='USDT')]
        self.fills.append(dict(bar=self.index[0], symbol=f'{self.ticker}_{leg}', side=side,
                               qty=str(qty), price=str(price), pnl=str(pnl), commission=str(fee), order_id=identity))
        return dict(result)

    async def get_order_status(self, symbol, identity): return dict(self.orders[int(identity)])
    async def get_order_trades(self, symbol, identity): return deepcopy(self.trades[int(identity)])
    async def get_open_orders(self, *args, **kwargs): return []
    async def get_hedge_mode(self): return True
    async def set_leverage(self, *args, **kwargs): pass
    async def set_margin_type(self, *args, **kwargs): pass
    async def get_commission_rates(self, *args): return {'takerCommissionRate': '.0004'}
    async def get_mark_prices(self, *args): return {self.ticker: float(self.price())}
    async def get_futures_prices(self, *args): return await self.get_mark_prices()

    async def get_positions(self):
        return {f'{self.ticker}_{leg}': dict(qty=float(qty if leg == 'LONG' else -qty), entry_price=float(entry))
                for leg, (qty, entry) in self.positions.items()}

    async def get_position_risk(self): return await self.get_positions()

    async def get_usdt_account(self):
        margin = sum(qty*entry/D(str(self.targets[f'BASE_{leg}']['leverage']))
                     for leg, (qty, entry) in self.positions.items())
        return dict(wallet=str(self.wallet), available=str(self.wallet-margin))

    async def get_margin_ratio(self):
        unrealized = sum(qty*(self.price()-entry)*(1 if leg == 'LONG' else -1)
                         for leg, (qty, entry) in self.positions.items())
        maintenance = sum(qty*self.price()*D('.004') for qty, _ in self.positions.values())
        equity = self.wallet+unrealized
        return dict(margin_ratio=float(equity/maintenance) if maintenance else float('inf'),
                    total_wallet_balance=float(self.wallet), total_margin_balance=float(equity),
                    total_maint_margin=float(maintenance))

    async def get_exchange_info(self):
        return {'symbols': [{'symbol': self.ticker, 'filters': [
            {'filterType': 'LOT_SIZE', 'stepSize': '.001'}, {'filterType': 'MIN_NOTIONAL', 'notional': '5'}]}]}

    async def get_order_book(self, *args, **kwargs):
        return dict(bids=[[str(self.price()*D('.9995')), '1000000000000']],
                    asks=[[str(self.price()*D('1.0005')), '1000000000000']],
                    _fetched_at=(int(self.stamps[self.index[0]])+60000)/1000)

    async def get_futures_klines(self, symbol, interval, limit):
        return [[int(self.stamps[j]), float(self.prices[j]), float(self.prices[j]), float(self.prices[j]),
                 float(self.prices[j]), 1]
                for j in range(max(0, self.index[0]-limit+2), min(len(self.prices), self.index[0]+2))]

    async def get_funding_income(self, symbol, since, until):
        if self.funding_bar != self.index[0]:
            amount = -sum(qty*self.price() for qty, _ in self.positions.values())*D('.0002')/1440
            self.wallet += amount
            self.income.append(dict(symbol=symbol, incomeType='FUNDING_FEE', asset='USDT',
                tranId=self.index[0]+1, time=int(self.stamps[self.index[0]])+60000, income=str(amount)))
            self.funding_bar = self.index[0]
        rows = self.income[-2:] if self.archive_applied else self.income
        return [r for r in rows if since < r['time'] <= until and D(r['income']) != 0]


class ReplayLogger(Quiet):
    def __init__(self):
        self.cash_blocks = 0

    def error(self, message, *args, **kwargs):
        if message.startswith('Cash invariant violation active: blocked '):
            self.cash_blocks += 1
            return
        raise ReplayFailure(str((message, *args)))


async def replay(config_path, feather, count, archive_applied=False, memory_reserve_store=False, native_persistence=False):
    if native_persistence and memory_reserve_store:
        raise ValueError('Native persistence cannot use a memory reserve store')
    config = json.loads(config_path.read_text())
    ticker = feather.stem.split('_')[0]
    assert ticker.isascii() and ticker.isalnum() and ticker == ticker.upper() and ticker.endswith('USDT')
    assert config.get('funding_model') == 'legacy_constant'
    config.update(base_ticker=ticker, paper_mode=False, max_bots=9, paper_mode_bots=8, telegram_enabled=False)
    frame = pd.read_feather(feather).iloc[:count]
    prices = frame.close.to_numpy()
    stamps = pd.to_datetime(frame.time, utc=True).dt.as_unit('ms').astype('int64').to_numpy()
    assert len(frame) == count and np.all(np.diff(stamps) == 60000)
    index, curve = [0], np.full(count, np.nan)
    portfolio = config['portfolios'][0]
    capital = portfolio['initial_capital']
    exchange = Exchange(ticker, prices, stamps, index, capital, portfolio['cash_reserve_usdt'],
                        portfolio['targets'], archive_applied)
    totals = dict(cycles=0, stops=0)
    runtime_logger = ReplayLogger()
    minimum_cash, minimum_virtual = [D(str(capital))], [D(0)]
    docs, sessions, archived = {}, [], {}
    trace = []
    real_sleep = asyncio.sleep
    from core import storage
    native_boundary = funding.save_funding_boundary

    def current(): return (int(stamps[min(index[0], count-1)])+60000)/1000

    async def load(path, default=None):
        if native_persistence and str(path) in docs:
            return storage.safe_load_json_sync(str(path),default)
        return deepcopy(docs.get(str(path), default))

    async def save(path, value):
        if native_persistence and str(path) in docs:
            storage.safe_save_json_sync(str(path),value)
        if str(path) in docs: docs[str(path)] = deepcopy(value)

    def boundary(sp, wp, state, wallet, journal, halt):
        if native_persistence:
            native_boundary(sp,wp,state,wallet,journal,halt)
        else:
            funding.save_funding_state(journal, halt, state)
        if archive_applied:
            applied = [row for row in state['funding_journal'] if row['status'] == 'applied']
            for row in applied:
                assert archived.setdefault(row['event_id'], row) == row
            keep = applied[-1:]
            if not native_persistence:
                state['funding_journal'] = keep + [row for row in state['funding_journal'] if row['status'] != 'applied']
        docs[str(sp)], docs[str(wp)] = deepcopy(state), deepcopy(wallet)

    def update_config(path, updater): return updater(config)

    async def noop(*args, **kwargs): pass

    with (nullcontext(tempfile.mkdtemp(prefix='native-real-',dir=config_path.parent)) if native_persistence
          else tempfile.TemporaryDirectory(prefix='agt-real-replay-', ignore_cleanup_errors=True)) as temporary:
        root = Path(temporary)
        (root/'core').mkdir()
        sp, wp = root/f'core/real_state_{ticker}.json', root/f'core/shadow_state_{ticker}.json'
        strategy, wallet_path = str(sp), str(wp)
        with patch('core.real_allocation.time.time', new=lambda: current()-1):
            await prepare(exchange, root, ticker, config)
        docs.update({strategy: json.loads(sp.read_text()), wallet_path: json.loads(wp.read_text())})
        source = ROOT/'supervisor/swarm_manager.py'
        function = next(n for n in ast.parse(source.read_text(encoding='utf-8')).body
                        if isinstance(n, ast.AsyncFunctionDef) and n.name == 'reset_bot_state_files')
        namespace = dict(BASE_PATH=root, CORE_PATH=root/'core', math=math, os=os, shutil=shutil,
                         time=time, logger=Quiet(), safe_load_json=load, Decimal=D)
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), namespace)

        async def advance(seconds):
            if seconds == 86400: raise ReplayFailure(f'REAL quarantine at bar {index[0]}: {docs[strategy]}')
            if Path(sys._getframe(1).f_code.co_filename).resolve() == ROOT/'core/executor.py':
                await real_sleep(0)
                return
            if docs[strategy].get('strategy_bar_ms') != int(stamps[index[0]]):
                if seconds in (.3, .5, 1):
                    await real_sleep(0)
                    return
                raise ReplayFailure(f'Unexpected REAL delay {seconds} at bar {index[0]}')
            curve[index[0]] = docs[strategy]['last_tpv']
            state, wallet = docs[strategy], docs[wallet_path]
            if 246500 <= index[0] <= 246900:
                trace.append(dict(bar=index[0], state={key:deepcopy(state.get(key)) for key in
                    ('virt_debt', 'share_trail', 'velocity_cooldown_bars', 'rebalance_preferred_side', 'last_rebalance_price', 'rebalance_cycles', 'cash_invariant_violation')},
                    wallet={key:deepcopy(wallet.get(key)) for key in ('balance', 'positions', 'long_entry_price', 'short_entry_price')}))
            virtual = D(str(state.get('virt_debt', 0)))
            cash = D(str(wallet['balance']))-virtual-sum(D(str(wallet['positions'].get(f'{ticker}_{leg}', 0)))*D(str(wallet.get(f'{leg.lower()}_entry_price', 0)))/D(str(portfolio['targets'][f'BASE_{leg}']['leverage'])) for leg in ('LONG', 'SHORT'))
            minimum_cash[0], minimum_virtual[0] = min(minimum_cash[0], cash), min(minimum_virtual[0], virtual)
            index[0] += 1
            if index[0] % 50000 == 0: print(f'REAL replay: {index[0]}/{count} bars', flush=True)
            if index[0] >= count: raise EndReplay()
            await real_sleep(0)

        def finish():
            state = docs[strategy]
            totals['cycles'] += state['rebalance_cycles']
            sessions.append(dict(bar=index[0], tpv=state['last_tpv'], funding=state.get('funding_paid', 0)))

        runtime_config_path=root/'config.json' if native_persistence else config_path
        with isolated_reserve_store(config, memory_reserve_store, root if native_persistence else None) as settle, patch('core.funding_store.settle', new=settle), \
             patch('core.main.time.time', new=current), patch('core.main.asyncio.sleep', new=advance), \
             patch('core.main.TelegramNotifier', new=lambda *a, **kw: Notifier()), \
             patch('core.main.load_json', new=load), patch('core.main.save_json', new=save), \
             patch('core.main.safe_load_json_sync', new=storage.safe_load_json_sync if native_persistence else lambda *a, **kw: config), \
             patch('core.storage.update_json_sync', new=update_config), \
             patch('core.funding.save_funding_boundary', new=boundary), \
             patch('core.main.self_kill_pm2', new=noop), patch('core.main.emit_signal', new=lambda *a, **kw: None):
            while index[0] < count:
                for path in (strategy, wallet_path):
                    Path(path).write_text(json.dumps(docs[path]), encoding='utf-8')
                try:
                    await rebalance_loop(exchange, str(runtime_config_path), strategy, wallet_path, runtime_logger,
                                         ticker_override=ticker, paper_mode_override=False)
                except EndReplay:
                    finish()
                    break
                except ReplayFailure as error:
                    failure = dict(status='BLOCKED', mode='SIMULATED_REAL', ticker=ticker, bar=index[0],
                        bar_utc=str(pd.Timestamp(int(stamps[index[0]]), unit='ms', tz='UTC')),
                        error=str(error), fills=exchange.fills, funding_income=exchange.income,
                        state=docs[strategy], wallet=docs[wallet_path],
                        config_sha256=hashlib.sha256(config_path.read_bytes()).hexdigest(),
                        data_sha256=hashlib.sha256(feather.read_bytes()).hexdigest())
                    (config_path.parent/'real_failure.json').write_text(json.dumps(failure, indent=2), encoding='utf-8')
                    raise
                state, wallet = docs[strategy], docs[wallet_path]
                assert state.get('live_close', {}).get('status') == 'complete'
                assert all(q == 0 for q in wallet['positions'].values())
                assert all(q == 0 for q, _ in exchange.positions.values())
                finish()
                totals['stops'] += 1
                start = index[0]
                target = max(start+1, int(np.searchsorted(stamps, state['restart_not_before']*1000)))
                curve[start:min(target, count)] = state['last_tpv']
                index[0] = target
                if target >= count or wallet['balance'] < 5:
                    curve[start:] = state['last_tpv']
                    break
                await namespace['reset_bot_state_files'](ticker, False, config, capital_override=wallet['balance'])
        assert np.all(np.isfinite(curve))
        peak = np.maximum(np.maximum.accumulate(curve), capital)
        return dict(totals, ticker=ticker, mode='SIMULATED_REAL', bars=count, fills=len(exchange.fills),
            funding=float(-sum(D(row['income']) for row in exchange.income)),
            funding_events=len(archived) if archive_applied else len(docs[strategy].get('funding_journal', [])),
            journal_adapter='native_sqlite_archive' if native_persistence else 'archived_applied_with_two_event_overlap' if archive_applied else 'native',
            persistence_adapter='native_json_wal' if native_persistence else 'isolated',
            persistence_root=str(root) if native_persistence else None,
            reserve_store_adapter='sqlite_memory' if memory_reserve_store else 'sqlite_disk',
            cash_guard_block_messages=runtime_logger.cash_blocks,
            min_cash=str(minimum_cash[0]), min_virtual=str(minimum_virtual[0]),
            funding_reserve=config['portfolios'][0]['cash_reserve_usdt'],
            final_tpv=float(curve[-1]), profit_pct=float((curve[-1]/capital-1)*100),
            max_dd_pct=float(np.max((peak-curve)/peak)*100), positions=docs[wallet_path]['positions'],
            config_sha256=hashlib.sha256(config_path.read_bytes()).hexdigest(),
            data_sha256=hashlib.sha256(feather.read_bytes()).hexdigest(),
            _curve=curve.tolist(), _fills=exchange.fills, _sessions=sessions,
            _funding=exchange.income, _funding_receipts=list(archived.values()), _trace=trace,
            final_state=docs[strategy], final_wallet=docs[wallet_path])


if __name__ == '__main__':
    result = asyncio.run(replay(Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]), '--archive-applied' in sys.argv[5:], '--memory-reserve-store' in sys.argv[5:], '--native-persistence' in sys.argv[5:]))
    output = Path(sys.argv[4])
    for key in ('_curve', '_fills', '_sessions', '_funding', '_funding_receipts', '_trace'):
        output.with_name(output.stem+key+'.json').write_text(json.dumps(result.pop(key)), encoding='utf-8')
    output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))
