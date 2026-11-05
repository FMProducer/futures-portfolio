"""Offline runtime replay; exchange and persistence are isolated test adapters."""
import ast
import asyncio
from copy import deepcopy
import hashlib
import json
import logging
from pathlib import Path
import sys
import time
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


async def replay(config_path, feather, count):
    config = json.loads(config_path.read_text())
    ticker = 'ZECUSDT'
    config.update(base_ticker=ticker, paper_mode=True)
    frame = pd.read_feather(feather).iloc[:count]
    prices = frame.close.to_numpy()
    stamps = pd.to_datetime(frame.time, utc=True).dt.as_unit('ms').astype('int64').to_numpy()
    assert len(frame) == count and np.all(np.diff(stamps) == 60000)
    index = [0]
    capital = config['portfolios'][0]['paper_initial_capital']
    paths = [str(ROOT / 'reports/parity_reference_01e4df4' / n) for n in ['paper_state_ZECUSDT.json', 'paper_shadow_ZECUSDT.json']]
    strategy, wallet_path = paths
    docs = {strategy: {'base_ticker': ticker, 'initial_tpv': capital, 'reference_tpv': capital,
                      'tpv_ath': capital, 'started_at': float(stamps[0]/1000-1), 'rebalance_cycles': 0,
                      'virt_qty': 0, 'virt_debt': 0, 'siphoning_reserve': 0, 'funding_model': 'legacy_constant'},
            wallet_path: {'base_ticker': ticker, 'balance': capital,
                          'positions': {f'{ticker}_LONG': 0, f'{ticker}_SHORT': 0},
                          'long_entry_price': 0, 'short_entry_price': 0}}
    curve = np.full(count, np.nan)
    totals = {'cycles': 0, 'funding': 0., 'fills': 0, 'stops': 0}
    seen, fills = set(), []

    def record_wallet(wallet):
        for identity, receipt in wallet.get('share_fill_receipts', {}).items():
            if identity not in seen:
                seen.add(identity)
                fills.append(dict(receipt, bar=index[0]))

    def current():
        return (int(stamps[min(index[0], count-1)]) + 60000) / 1000

    async def load(path, default=None):
        return deepcopy(docs.get(str(path), default))

    async def save(path, value):
        if str(path) in docs:
            docs[str(path)] = deepcopy(value)
            if str(path) == wallet_path:
                record_wallet(value)

    def boundary(sp, wp, state, wallet, journal, halt):
        funding.save_funding_state(journal, halt, state)
        docs[sp], docs[wp] = deepcopy(state), deepcopy(wallet)
        record_wallet(wallet)

    real_sleep = asyncio.sleep

    async def advance(seconds):
        if seconds == 86400:
            raise ReplayFailure('Unexpected quarantine or terminal zombie')
        curve[index[0]] = docs[strategy]['last_tpv']
        index[0] += 1
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
    namespace = dict(BASE_PATH=ROOT / 'reports/parity_reference_01e4df4', CORE_PATH=Path(strategy).parent,
                     math=math, os=os, shutil=shutil, time=time, logger=Quiet(), safe_load_json=load)
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), namespace)

    def finish_session():
        state, wallet = docs[strategy], docs[wallet_path]
        totals['cycles'] += state['rebalance_cycles']
        totals['funding'] += float(state.get('funding_paid', 0))
        totals['fills'] += sum(float(r.get('qty', 0)) > 0 for r in wallet.get('share_fill_receipts', {}).values())

    async def noop(*args, **kwargs): pass

    with patch('core.main.time.time', new=current), patch('core.main.asyncio.sleep', new=advance), \
         patch('core.main.TelegramNotifier', new=lambda *a, **kw: Notifier()), patch('core.main.load_json', new=load), \
         patch('core.main.save_json', new=save), patch('core.main.safe_load_json_sync', new=lambda *a, **kw: config), \
         patch('core.funding.save_funding_boundary', new=boundary), \
         patch('core.main.self_kill_pm2', new=noop), patch('core.main.emit_signal', new=lambda *a, **kw: None):
        while index[0] < count:
            try:
                await rebalance_loop(Connector(), str(config_path), strategy, wallet_path, Quiet(),
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
    return dict(totals, final_tpv=float(curve[-1]), profit_pct=float((curve[-1]/capital-1)*100),
                max_dd_pct=float(np.max((peak-curve)/peak)*100), positions=docs[wallet_path]['positions'],
                bars=count, config_sha256=hashlib.sha256(config_path.read_bytes()).hexdigest(),
                data_sha256=hashlib.sha256(feather.read_bytes()).hexdigest(), _curve=curve.tolist(), _fills=fills)


if __name__ == '__main__':
    result = asyncio.run(replay(Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])))
    for key in ('_curve', '_fills'):
        output = Path(sys.argv[4])
        output.with_name(output.stem + key + '.json').write_text(json.dumps(result.pop(key)), encoding='utf-8')
    Path(sys.argv[4]).write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))
