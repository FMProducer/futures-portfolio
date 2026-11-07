"""Offline runtime replay; exchange and persistence are isolated test adapters."""
import ast
import asyncio
from copy import deepcopy
from decimal import Decimal as D
import hashlib
import json
import logging
from pathlib import Path
import sys
import time
import tempfile
from contextlib import contextmanager, ExitStack, nullcontext, closing
import builtins
import io
import sqlite3
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import funding
from core.main import rebalance_loop


class EndReplay(BaseException):
    pass


class ReplayFailure(BaseException):
    pass


class Quiet:
    def __getattr__(self, name):
        if name == 'error':
            return lambda *args, **kwargs: (_ for _ in ()).throw(ReplayFailure(str(args)))
        return lambda *args, **kwargs: None


class Notifier:
    async def send_message(self, *args, **kwargs): pass
    async def send_alert(self, *args, **kwargs): pass
    async def send_status(self, *args, **kwargs): pass
    async def close(self): pass


@contextmanager
def isolated_reserve_store(config, memory=False, directory=None):
    """Exercise the real SQLite ledger without modifying the replay input configuration."""
    from core import funding_store
    original = funding_store.settle
    original_archive, original_lookup = funding_store.archive_events, funding_store.archived_event
    with (tempfile.TemporaryDirectory() if directory is None else nullcontext(str(directory))) as location:
        path = Path(location) / 'config.json'
        path.write_text(json.dumps(config), encoding='utf-8')

        snapshot = [deepcopy(config)]
        original_open = builtins.open
        original_save = __import__('core.storage', fromlist=['_atomic_save_json'])._atomic_save_json

        def read(file, *args, **kwargs):
            return io.StringIO(json.dumps(snapshot[0])) if str(file) == str(path) else original_open(file, *args, **kwargs)

        def save(file, value, **kwargs):
            if str(file) == str(path):
                snapshot[0] = deepcopy(value)
            else:
                original_save(file, value, **kwargs)

        def settle(_path, mode, symbol, event):
            result = original(str(path), mode, symbol, event)
            saved = snapshot[0] if memory else json.loads(path.read_text(encoding='utf-8'))
            for key in funding_store.KEYS:
                if key in saved['portfolios'][0]:
                    config['portfolios'][0][key] = saved['portfolios'][0][key]
            config.pop('funding_reserve_receipts', None)
            config['funding_reserve_totals'] = saved.get('funding_reserve_totals', {})
            config['funding_reserve_checkpoint'] = saved.get('funding_reserve_checkpoint')
            return result

        with ExitStack() as stack:
            stack.enter_context(patch('core.funding_store.archive_events',
                                      new=lambda _path, *args: original_archive(str(path), *args)))
            stack.enter_context(patch('core.funding_store.archived_event',
                                      new=lambda _path, *args, **kwargs: original_lookup(str(path), *args, **kwargs)))
            if memory:
                class Connection(sqlite3.Connection):
                    def close(self): pass
                db = sqlite3.connect(':memory:', factory=Connection, check_same_thread=False)
                stack.callback(sqlite3.Connection.close, db)
                with closing(funding_store.connect(path)) as source:
                    source.backup(db)
                stack.enter_context(patch('core.funding_store.connect', new=lambda *a, **kw: db))
                stack.enter_context(patch('core.funding_store.FileLock', new=lambda *a, **kw: nullcontext()))
                stack.enter_context(patch('core.storage._atomic_save_json', new=save))
                stack.enter_context(patch('builtins.open', new=read))
            yield settle


async def replay(config_path, feather, count, memory_reserve_store=False, native_persistence=False):
    if native_persistence and memory_reserve_store:
        raise ValueError('Native persistence cannot use a memory reserve store')
    config = json.loads(config_path.read_text())
    ticker = feather.stem.split('_')[0]
    if not ticker.isascii() or not ticker.isalnum() or ticker != ticker.upper() or not ticker.endswith('USDT'):
        raise ValueError('Replay history filename must identify a USDT ticker')
    config.update(base_ticker=ticker, paper_mode=True)
    frame = pd.read_feather(feather).iloc[:count]
    prices = frame.close.to_numpy()
    stamps = pd.to_datetime(frame.time, utc=True).dt.as_unit('ms').astype('int64').to_numpy()
    assert len(frame) == count and np.all(np.diff(stamps) == 60000)
    index = [0]
    capital = config['portfolios'][0]['paper_initial_capital']
    state_root = ROOT / 'reports' / f'runtime_parity_{ticker.lower()}'
    if native_persistence:
        state_root = Path(tempfile.mkdtemp(prefix='native-paper-', dir=config_path.parent))
    state_root.mkdir(parents=True, exist_ok=True)
    paths = [str(state_root / n) for n in [f'paper_state_{ticker}.json', f'paper_shadow_{ticker}.json']]
    strategy, wallet_path = paths
    docs = {strategy: {'base_ticker': ticker, 'initial_tpv': capital, 'reference_tpv': capital,
                      'tpv_ath': capital, 'started_at': float(stamps[0]/1000-1), 'rebalance_cycles': 0,
                      'virt_qty': 0, 'virt_debt': 0, 'siphoning_reserve': 0, 'funding_model': 'legacy_constant'},
            wallet_path: {'base_ticker': ticker, 'balance': capital,
                          'positions': {f'{ticker}_LONG': 0, f'{ticker}_SHORT': 0},
                          'long_entry_price': 0, 'short_entry_price': 0}}
    from core import storage
    native_boundary = funding.save_funding_boundary
    runtime_config_path = state_root/'config.json' if native_persistence else config_path
    if native_persistence:
        for path, value in docs.items():
            storage.safe_save_json_sync(path, value)
    curve = np.full(count, np.nan)
    totals = {'cycles': 0, 'funding': 0., 'fills': 0, 'stops': 0}
    seen, fills = set(), []
    archived_funding = {}
    minimum_cash, minimum_virtual = [D(str(capital))], [D(0)]
    trace = []

    def record_wallet(wallet):
        for identity, receipt in wallet.get('share_fill_receipts', {}).items():
            if identity not in seen:
                seen.add(identity)
                fills.append(dict(receipt, bar=index[0]))

    def current():
        return (int(stamps[min(index[0], count-1)]) + 60000) / 1000

    async def load(path, default=None):
        if native_persistence and str(path) in docs:
            return storage.safe_load_json_sync(str(path), default)
        return deepcopy(docs.get(str(path), default))

    async def save(path, value):
        if native_persistence and str(path) in docs:
            storage.safe_save_json_sync(str(path), value)
        if str(path) in docs:
            docs[str(path)] = deepcopy(value)
            if str(path) == wallet_path:
                record_wallet(value)

    def boundary(sp, wp, state, wallet, journal, halt):
        if native_persistence:
            native_boundary(sp, wp, state, wallet, journal, halt)
        else:
            funding.save_funding_state(journal, halt, state)
        applied = [row for row in state['funding_journal'] if row['status'] == 'applied']
        for row in applied:
            assert archived_funding.setdefault(row['event_id'], row) == row
        if not native_persistence:
            state['funding_journal'] = applied[-1:] + [row for row in state['funding_journal'] if row['status'] != 'applied']
        docs[sp], docs[wp] = deepcopy(state), deepcopy(wallet)
        record_wallet(wallet)

    real_sleep = asyncio.sleep

    async def advance(seconds):
        if seconds == 86400:
            raise ReplayFailure('Unexpected quarantine or terminal zombie')
        curve[index[0]] = docs[strategy]['last_tpv']
        state, wallet = docs[strategy], docs[wallet_path]
        if 246500 <= index[0] <= 246900:
            trace.append(dict(bar=index[0], state={key:deepcopy(state.get(key)) for key in
                ('virt_debt', 'share_trail', 'velocity_cooldown_bars', 'rebalance_preferred_side', 'last_rebalance_price', 'rebalance_cycles', 'cash_invariant_violation')},
                wallet={key:deepcopy(wallet.get(key)) for key in ('balance', 'positions', 'long_entry_price', 'short_entry_price')}))
        virtual = D(str(state.get('virt_debt', 0)))
        cash = D(str(wallet['balance']))-virtual-sum(D(str(wallet['positions'].get(f'{ticker}_{leg}', 0)))*D(str(wallet.get(f'{leg.lower()}_entry_price', 0)))/D(str(config['portfolios'][0]['targets'][f'BASE_{leg}']['leverage'])) for leg in ('LONG', 'SHORT'))
        minimum_cash[0], minimum_virtual[0] = min(minimum_cash[0], cash), min(minimum_virtual[0], virtual)
        index[0] += 1
        if index[0] % 50000 == 0:
            print(f'PAPER replay: {index[0]}/{count} bars', flush=True)
        if index[0] >= count:
            raise EndReplay()
        await real_sleep(0)

    class Connector:
        async def get_exchange_info(self):
            return {'symbols': [{'symbol': ticker, 'filters': [
                {'filterType': 'LOT_SIZE', 'stepSize': '.001'}, {'filterType': 'MIN_NOTIONAL', 'notional': '5'}]}]}

        async def get_mark_prices(self, *args): return {ticker: float(prices[index[0]])}
        async def get_futures_prices(self, *args): return {ticker: float(prices[index[0]])}

        async def get_futures_klines(self, symbol, interval, limit):
            return [[int(stamps[j]), float(prices[j]), float(prices[j]), float(prices[j]), float(prices[j]), 1]
                    for j in range(max(0, index[0]-limit+2), min(count, index[0]+2))]

    source = ROOT / 'supervisor/swarm_manager.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    function = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'reset_bot_state_files')
    import math, os, shutil
    namespace = dict(BASE_PATH=state_root, CORE_PATH=Path(strategy).parent,
                     math=math, os=os, shutil=shutil, time=time, logger=Quiet(), safe_load_json=load)
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), namespace)

    def finish_session():
        state, wallet = docs[strategy], docs[wallet_path]
        totals['cycles'] += state['rebalance_cycles']
        totals['funding'] += float(state.get('funding_paid', 0))
        totals['fills'] += sum(float(r.get('qty', 0)) > 0 for r in wallet.get('share_fill_receipts', {}).values())

    async def noop(*args, **kwargs): pass

    with isolated_reserve_store(config, memory_reserve_store, state_root if native_persistence else None) as settle, patch('core.funding_store.settle', new=settle), \
         patch('core.main.time.time', new=current), patch('core.main.asyncio.sleep', new=advance), \
         patch('core.main.TelegramNotifier', new=lambda *a, **kw: Notifier()), patch('core.main.load_json', new=load), \
         patch('core.main.save_json', new=save), patch('core.main.safe_load_json_sync', new=storage.safe_load_json_sync if native_persistence else lambda *a, **kw: config), \
         patch('core.funding.save_funding_boundary', new=boundary), \
         patch('core.storage.update_json_sync', new=lambda path, updater: updater(config)), \
         patch('core.main.self_kill_pm2', new=noop), patch('core.main.emit_signal', new=lambda *a, **kw: None):
        while index[0] < count:
            try:
                await rebalance_loop(Connector(), str(runtime_config_path), strategy, wallet_path, Quiet(),
                                     ticker_override=ticker, paper_mode_override=True)
            except EndReplay:
                finish_session()
                break
            state, wallet = docs[strategy], docs[wallet_path]
            assert state.get('paper_close_complete') and all(q == 0 for q in wallet['positions'].values())
            finish_session()
            totals['stops'] += 1
            start = index[0]
            target = max(start+1, int(np.searchsorted(stamps, state['restart_not_before']*1000)))
            curve[start:min(target, count)] = state['last_tpv']
            index[0] = target
            if target >= count or wallet['balance'] < 5:
                curve[start:] = state['last_tpv']
                break
            await namespace['reset_bot_state_files'](ticker, True, config, capital_override=wallet['balance'])
    assert np.all(np.isfinite(curve))
    peak = np.maximum(np.maximum.accumulate(curve), capital)
    return dict(totals, ticker=ticker, mode='PAPER', final_tpv=float(curve[-1]), profit_pct=float((curve[-1]/capital-1)*100),
                max_dd_pct=float(np.max((peak-curve)/peak)*100), positions=docs[wallet_path]['positions'],
                bars=count, persistence_adapter='native_json_wal' if native_persistence else 'isolated',
                persistence_root=str(state_root) if native_persistence else None,
                reserve_store_adapter='sqlite_memory' if memory_reserve_store else 'sqlite_disk',
                min_cash=str(minimum_cash[0]), min_virtual=str(minimum_virtual[0]),
                funding_reserve=config['portfolios'][0].get('paper_account_free_margin', 0),
                funding_events=len(archived_funding), _funding=list(archived_funding.values()), _trace=trace,
                config_sha256=hashlib.sha256(config_path.read_bytes()).hexdigest(),
                data_sha256=hashlib.sha256(feather.read_bytes()).hexdigest(), _curve=curve.tolist(), _fills=fills)


if __name__ == '__main__':
    result = asyncio.run(replay(Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]), '--memory-reserve-store' in sys.argv[5:], '--native-persistence' in sys.argv[5:]))
    for key in ('_curve', '_fills', '_funding', '_trace'):
        output = Path(sys.argv[4])
        output.with_name(output.stem + key + '.json').write_text(json.dumps(result.pop(key)), encoding='utf-8')
    Path(sys.argv[4]).write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))
