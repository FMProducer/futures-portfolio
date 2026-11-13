"""Offline shared-clock swarm experiment using the production state machines."""
import ast
import asyncio
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from decimal import Decimal as D
import hashlib
import heapq
import json
import logging
import math
import os
from pathlib import Path
import random
import re
import shlex
import shutil
import sqlite3
import sys
import subprocess
import time
import traceback
import uuid
from urllib.parse import unquote, urlparse
from types import SimpleNamespace
from typing import Any, Dict, List, Set
from unittest.mock import patch

ROOT = Path('C:\\Python\\Prosperous_Bot\\futures_portfolio')
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, default=str), encoding='utf-8')
    temporary.replace(path)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def install_guard(folder):
    """Fail closed on external effects, credentials, or operational snapshots."""
    folder = folder.resolve()
    def inside(path):
        return Path(path).resolve().is_relative_to(folder)
    def audit(event, args):
        if event == 'subprocess.Popen':
            executable, command, cwd, env = args
            expected = [sys.executable,'-B',str(HERE/'gate_worker.py')]
            allowed = (executable is None or Path(executable).resolve()==Path(sys.executable).resolve()) and (
                command==expected or command==subprocess.list2cmdline(expected))
            if not allowed:
                raise PermissionError('Offline replay forbids subprocess.Popen')
        if event in ('socket.connect', 'socket.getaddrinfo', 'os.system'):
            raise PermissionError(f'Offline replay forbids {event}')
        if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
            path, mode, flags = args
            path = os.fsdecode(path)
            writing = (isinstance(mode, str) and any(c in mode for c in 'wax+')) or bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
            if writing and not inside(path):
                raise PermissionError(f'Write outside experiment: {path}')
            p = Path(path).resolve()
            if p.name == '.env' or 'credentials' in p.name.lower():
                raise PermissionError(f'Credential read forbidden: {p.name}')
            if not inside(p) and (p == ROOT/'config.json' or p.parent == ROOT/'core' and
                    (p.suffix == '.sqlite' or any(s in p.name for s in ('state_', 'shadow_')))):
                raise PermissionError(f'Operational snapshot read forbidden: {p.name}')
        if event == 'sqlite3.connect' and args[0] != ':memory:':
            raw = str(args[0])
            path = unquote(urlparse(raw).path).lstrip('/') if raw.startswith('file:') else raw
            path = path.split('?', 1)[0]
            if not inside(path):
                raise PermissionError(f'External SQLite forbidden: {path}')
        if event in ('os.remove', 'os.mkdir', 'os.rmdir', 'os.rename', 'os.link'):
            targets = args[:2] if event in ('os.rename',) else args[:1]
            if event == 'os.link':
                targets = args[1:2]
            if any(isinstance(p, (str, bytes, os.PathLike)) and not inside(os.fsdecode(p)) for p in targets):
                raise PermissionError(f'Filesystem mutation outside experiment: {event}')
    sys.addaudithook(audit)


class Clock:
    def __init__(self, now):
        self.now, self.waiters, self.sequence = now, [], 0

    async def sleep(self, seconds):
        if seconds <= 0:
            await asyncio.sleep(0)
            return
        future = asyncio.get_running_loop().create_future()
        self.sequence += 1
        heapq.heappush(self.waiters, (self.now + seconds, self.sequence, future))
        await future

    async def settle(self):
        # Every adapter completes inline; ready tasks can only enqueue more ready tasks.
        loop = asyncio.get_running_loop()
        for _ in range(10000):
            await asyncio.sleep(0)
            if not loop._ready:
                return
        raise RuntimeError('Virtual scheduler did not quiesce')

    async def advance(self, until):
        while self.waiters and self.waiters[0][2].done():
            heapq.heappop(self.waiters)
        self.now = min(until, self.waiters[0][0] if self.waiters else until)
        while self.waiters and self.waiters[0][0] <= self.now + 1e-7:
            _, _, future = heapq.heappop(self.waiters)
            if not future.done():
                future.set_result(None)
        await self.settle()


async def inline(function, *args, **kwargs):
    return function(*args, **kwargs)


def validate():
    import numpy as np
    import pandas as pd
    manifest = json.loads((HERE/'manifest.json').read_text())
    assert digest(HERE/'config.source.json') == manifest['config_sha256']
    assert all(digest(ROOT/p) == h for p, h in manifest['source_hashes'].items()), 'Frozen source drift'
    config = json.loads((HERE/'config.source.json').read_text())
    assert config['funding_model'] == 'disabled'
    assert config['supervisor_interval_days'] * 86400 == 950.4
    tickers = (ROOT/'tickers.txt').read_text(encoding='utf-8-sig').split()
    assert len(tickers) == len(set(tickers)) == len(manifest['data']) == 50
    assert set(tickers) == {r['ticker'] for r in manifest['data']}
    frames, hashes, axis = {}, {}, None
    for row in manifest['data']:
        assert digest(row['path']) == row['sha256'], row['ticker']
        frame = pd.read_feather(row['path'])
        stamps = pd.to_datetime(frame.time, utc=True).dt.as_unit('ms').astype('int64').to_numpy()
        assert len(frame) == row['bars'] == 46080 and np.all(np.diff(stamps) == 60000)
        assert str(pd.Timestamp(stamps[0], unit='ms', tz='UTC')) == row['start']
        assert str(pd.Timestamp(stamps[-1], unit='ms', tz='UTC')) == row['end']
        assert axis is None or np.array_equal(axis, stamps)
        values = frame[['open', 'high', 'low', 'close', 'volume']].to_numpy()
        assert np.isfinite(values).all() and (values[:, :4] > 0).all() and (values[:, 4] >= 0).all()
        assert (frame.high >= frame[['open', 'low', 'close']].max(axis=1)).all()
        assert (frame.low <= frame[['open', 'high', 'close']].min(axis=1)).all()
        frames[row['ticker']], hashes[row['ticker']], axis = frame, row['sha256'], stamps
    return config, manifest, frames, axis, hashes


class Replay:
    def __init__(self, folder, minutes, recover_from=None):
        import numpy as np
        self.np = np
        self.folder, self.minutes = folder, minutes
        self.driver_sha256 = digest(__file__)
        self.config, self.manifest, self.frames, self.stamps, self.hashes = validate()
        self.config.update(max_bots=10, paper_mode_bots=8, max_drawdown_limit=99.99, use_real_whitelist=True, real_whitelist=['ONUSDT','HOTUSDT'])
        self.clock = Clock(float(self.stamps[2880]/1000))
        self.start = self.clock.now
        self.end = self.start + minutes * 60
        self.docs, self.processes, self.tasks, self.sessions = {}, {}, {}, []
        self.active_sessions, self.fills_seen, self.failures = {}, set(), []
        self.exchange, self.exchange_wallet = None, D(6000)
        self.curve, self.selection, self.checkpoints = [], 0, []
        self.return_changed = True
        self.needs_sample = False
        self.recovery = json.loads((recover_from/'clock.json').read_text()) if recover_from else None
        self.recovered = False
        self.native_save = None
        self.folder.mkdir(parents=True, exist_ok=False)
        (self.folder/'core').mkdir()
        (self.folder/'signals').mkdir()
        (self.folder/'history').mkdir()
        (self.folder/'data').mkdir()
        (self.folder/'source').mkdir()
        for name in ('replay.py','gate_worker.py'):
            shutil.copy2(HERE/name,self.folder/'source'/name)
        for row in self.manifest['data']:
            os.link(row['path'], self.folder/'data'/Path(row['path']).name)
        write(self.folder/'config.json', self.config)
        write(self.folder/'config.effective.initial.json', self.config)
        self.events = (self.folder/'events.jsonl').open('w', encoding='utf-8')
        self.fill_file = (self.folder/'fills.jsonl').open('w', encoding='utf-8')
        self.gate_workers = []
        self.worker_logs = []
        for number in range(min(2, os.cpu_count() or 1)):
            log = (folder/f'gate_worker_{number}.log').open('w', encoding='utf-8')
            process = subprocess.Popen([sys.executable,'-B',str(HERE/'gate_worker.py')],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,text=True,encoding='utf-8',
                env=dict(os.environ),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.gate_workers.append(process)
            self.worker_logs.append(log)

    def event(self, kind, **fields):
        self.events.write(json.dumps(dict(time=self.clock.now, kind=kind, **fields), default=str) + '\n')
        self.events.flush()

    def index(self):
        return int(self.np.searchsorted(self.stamps, int(self.clock.now*1000)-60000, side='right')-1)

    def paths(self, ticker, paper):
        return (self.folder/'core'/f"{'paper' if paper else 'real'}_state_{ticker}.json",
                self.folder/'core'/f"{'paper_shadow' if paper else 'shadow_state'}_{ticker}.json")

    def load(self, path, default=None):
        key = str(path)
        if key in self.docs and Path(path).name != 'config.json':
            return deepcopy(self.docs[key])
        if Path(path).exists():
            return json.loads(Path(path).read_text(encoding='utf-8'))
        return deepcopy(default)

    async def aload(self, path, default=None):
        return self.load(path, default)

    async def save(self, path, value):
        if Path(path).name == 'config.json':
            self.native_save(str(path), value)
            return
        self.docs[str(path)] = deepcopy(value)
        if Path(path).name == 'config.json' or not Path(path).exists():
            self.native_save(str(path), value)
        if 'positions' in value:
            self.record_fills(path, value)

    def record_fills(self, path, wallet):
        if 'paper_shadow' not in str(path):
            return
        for identity, receipt in wallet.get('share_fill_receipts', {}).items():
            session = self.active_sessions.get(('p_' if 'paper_shadow' in str(path) else 'r_') + wallet['base_ticker'])
            key = (str(path), session, identity)
            if key not in self.fills_seen:
                self.fills_seen.add(key)
                self.needs_sample = True
                self.fill_file.write(json.dumps(dict(time=self.clock.now, session=session, mode='PAPER', receipt=receipt), default=str)+'\n')

    def flush(self):
        for path, value in self.docs.items():
            self.native_save(path, value)

    def boundary(self, sp, wp, state, wallet, journal, halt):
        self.original_boundary(sp, wp, state, wallet, journal, halt)
        self.docs[str(sp)], self.docs[str(wp)] = deepcopy(state), deepcopy(wallet)
        if 'paper_shadow' in str(wp):
            self.record_fills(wp, wallet)

    def signal(self, kind, ticker, paper):
        from core.paper_budget import confirm_close
        self.flush()
        if paper:
            try:
                confirm_close(self.folder, ticker)
                sp, _ = self.paths(ticker, True)
                self.docs.pop(str(sp), None)
            except (ValueError, OSError, KeyError) as error:
                self.event('blocked_return', ticker=ticker, error=str(error))
        (self.folder/'signals'/f"{kind}_{'paper' if paper else 'real'}_{ticker}.flag").touch()
        self.event('signal', ticker=ticker, paper=paper, signal=kind)
        self.return_changed = True
        sp, wp = self.paths(ticker, paper)
        state, wallet = self.load(sp, {}), self.load(wp, {})
        session = self.active_sessions.get(('p_' if paper else 'r_')+ticker)
        for row in reversed(self.sessions):
            if row['session'] == session:
                row.update(end=self.clock.now, equity=state.get('last_tpv'), reason=state.get('stop_reason', kind),
                           cycles=state.get('rebalance_cycles'), close_confirmed=bool(state.get('paper_close_complete') if paper
                           else state.get('live_close', {}).get('status') == 'complete'))
                break
        write(self.folder/'terminal'/f'{session}.json'.replace(':','_'), dict(state=state, wallet=wallet))

    async def sleep(self, seconds):
        await self.clock.sleep(seconds)

    def connector(self, ticker):
        from scripts.verify_real_runtime_parity import Exchange
        replay = self
        class Local(Exchange):
            def price(self):
                return D(str(replay.frames[self.ticker].close.iloc[replay.index()]))

            async def verify_connection(self):
                return True

            async def get_positions(self):
                return {key:value for key,value in (await super().get_positions()).items() if value['qty'] != 0}

            async def get_futures_klines(self, symbol, interval, limit):
                assert interval == '1m' and symbol == self.ticker
                end = replay.index()+1
                frame = replay.frames[symbol].iloc[max(0, end-limit):end]
                assert int(replay.stamps[end-1])+60000 <= int(replay.clock.now*1000)
                return [[int(t.value//1000000), o, h, l, c, v]
                        for t, o, h, l, c, v in frame[['time','open','high','low','close','volume']].itertuples(index=False, name=None)]

            async def get_order_book(self, *args, **kwargs):
                return dict(bids=[[str(self.price()*D('.9995')), '1000000000000']],
                            asks=[[str(self.price()*D('1.0005')), '1000000000000']], _fetched_at=replay.clock.now)

            def create_order(self, **params):
                self.index[0] = replay.index()
                result = super().create_order(**params)
                replay.needs_sample = True
                replay.fill_file.write(json.dumps(dict(time=replay.clock.now, mode='SIMULATED_REAL',
                    session=replay.active_sessions.get('r_'+self.ticker), receipt=self.fills[-1]), default=str)+'\n')
                replay.event('mock_order', ticker=self.ticker, request=params, result=result)
                return result
        local = Local(ticker, self.frames[ticker].close.to_numpy(), self.stamps, [self.index()], 1000, 5000,
                      self.config['portfolios'][0]['targets'], funding_model='disabled')
        local.wallet = self.exchange_wallet
        return local

    async def worker(self, ticker, paper):
        from core.main import rebalance_loop
        key = ('p_' if paper else 'r_')+ticker
        sp, wp = self.paths(ticker, paper)
        connector = self.connector(ticker) if paper else self.exchange
        try:
            await rebalance_loop(connector, str(self.folder/'config.json'), str(sp), str(wp),
                                 self.logger, ticker_override=ticker, paper_mode_override=paper)
            self.event('worker_exit', ticker=ticker, paper=paper)
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            self.failures.append(dict(time=self.clock.now, worker=key, error=repr(error)))
            self.event('worker_error', worker=key, error=traceback.format_exc())
        finally:
            self.processes.pop(key, None)

    async def shell(self, command, **kwargs):
        async def wait():
            return 0
        async def communicate():
            return b'', b''
        result = SimpleNamespace(returncode=0, wait=wait, communicate=communicate)
        if command == 'pm2 save':
            return result
        if command.startswith('pm2 start '):
            ticker = command.split('--ticker ')[1].split()[0]
            paper = '--paper' in command
            key = ('p_' if paper else 'r_')+ticker
            assert key not in self.processes
            assert sum(k.startswith('p_') for k in self.processes) < 8 if paper else not any(k.startswith('r_') for k in self.processes)
            sp, _ = self.paths(ticker, paper)
            state = self.load(sp, {})
            session = f"{key}:{state['started_at']}"
            self.active_sessions[key] = session
            self.sessions.append(dict(session=session, ticker=ticker, paper=paper, start=self.clock.now,
                                      capital=state['initial_tpv'], state_path=str(sp)))
            self.processes[key] = dict(name=f"{'paper' if paper else 'real'}-{ticker[:-4].lower()}", paper=paper)
            self.tasks[key] = asyncio.create_task(self.worker(ticker, paper), name=key)
            self.event('ADD', worker=key, session=session, selection=self.selection)
            return result
        if command.startswith('pm2 delete '):
            name = command.split()[-1]
            keys = [k for k, value in self.processes.items() if value['name'] == name]
            for key in keys:
                self.tasks[key].cancel()
                await asyncio.gather(self.tasks[key], return_exceptions=True)
                self.processes.pop(key, None)
            return result
        raise RuntimeError(f'Unexpected virtual PM2 command: {command}')

    async def child(self, *args, **kwargs):
        from core.main import emergency_stop
        assert '--stop' in args and Path(kwargs['cwd']).resolve() == self.folder
        ticker = args[args.index('--ticker')+1]
        paper = '--paper' in args
        sp, wp = self.paths(ticker, paper)
        await emergency_stop(self.connector(ticker) if paper else self.exchange, str(self.folder/'config.json'),
                             str(sp), str(wp), self.logger, ticker, paper_mode=paper)
        self.docs.pop(str(sp), None)
        self.docs.pop(str(wp), None)
        state = self.load(sp, {})
        session = self.active_sessions.get(('p_' if paper else 'r_')+ticker)
        for row in reversed(self.sessions):
            if row['session'] == session:
                row.update(end=self.clock.now, equity=state['last_tpv'], reason=state.get('stop_reason', 'rotation'),
                           cycles=state['rebalance_cycles'])
                break
        if not paper:
            self.exchange_wallet = self.exchange.wallet
        self.event('CLOSE', ticker=ticker, paper=paper, session=session, state=state)
        self.return_changed = True
        async def wait():
            return 0
        return SimpleNamespace(wait=wait, returncode=0)

    async def running(self):
        return deepcopy(self.processes)

    async def pm2(self):
        return [dict(name=p['name'], pm2_env=dict(status='online')) for p in self.processes.values()]

    async def scan(self, **kwargs):
        import pandas as pd
        from scanner.rank_tickers import TickerRanker
        cfg = self.load(self.folder/'config.json', {})
        end = self.index()+1
        count = int(cfg['scanner_period_days']*1440)
        frames, funnel = [], []
        for ticker, frame in self.frames.items():
            day = frame.iloc[max(0, end-1440):end]
            volume = float((day.volume*day.close).sum())
            reason = 'black_list' if ticker in cfg.get('black_list', []) else ('volume_below_15m' if volume < 15000000 else None)
            funnel.append(dict(symbol=ticker, quote_volume_proxy=volume, rejection=reason))
            if reason is None:
                view = frame.iloc[end-count:end].copy()
                view['ticker'] = ticker
                frames.append(view.set_index(['ticker','time']))
        results = []
        if frames:
            metrics = TickerRanker(pd.concat(frames)).calculate_metrics(.005)
            metrics['symbol'], metrics['funding'] = metrics.index, 0.
            for item in metrics.to_dict('records'):
                row = next(r for r in funnel if r['symbol'] == item['symbol'])
                row.update(item)
                if item['cycles'] >= 10:
                    item['cycles'] = int(item['cycles'])
                    results.append(item)
                else:
                    row['rejection'] = 'cycles_below_10'
            results.sort(key=lambda r: r['cycles'], reverse=True)
        results = [r for r in results if r['symbol'] in self.config['real_whitelist']]
        write(self.folder/'selection'/f'{self.selection:05d}.scanner.json', dict(time=self.clock.now,
              last_closed_ms=int(self.stamps[end-1]), funnel=funnel, ranked=results))
        return results

    async def gate(self, root, candidates, config, existing, reassess=()):
        from scanner.backtest_gate import effective_config, rank_candidates
        end = int(self.clock.now//3600)*3600000
        symbols = list(dict.fromkeys([r['symbol'] for r in candidates if r['symbol'] not in existing]+sorted(reassess)))
        effective = effective_config(config)
        cfg = self.folder/'gate.config.json'
        write(cfg, effective)
        config_sha = digest(cfg)
        results, pending = {}, []
        for ticker in symbols:
            target = self.folder/'gate'/str(end)/f'{ticker}_{config_sha[:20]}.json'
            if target.exists():
                results[ticker] = json.loads(target.read_text())
                continue
            pending.append((ticker,target))
        def batch(number):
            process = self.gate_workers[number]
            output = []
            for ticker,target in pending[number::len(self.gate_workers)]:
                job = [end,ticker,str(cfg),str(self.folder/'data'),self.clock.now]
                process.stdin.write(json.dumps(job)+'\n')
                process.stdin.flush()
                line = process.stdout.readline()
                if not line:
                    raise RuntimeError(f'Gate worker {number} exited: {process.poll()}')
                row = json.loads(line)
                row.update(data_sha256=self.hashes[ticker],config_sha256=config_sha)
                write(target,row)
                output.append((ticker,row))
            return output
        if pending:
            with ThreadPoolExecutor(max_workers=len(self.gate_workers)) as pool:
                for batch_results in pool.map(batch,range(len(self.gate_workers))):
                    results.update(batch_results)
        ranked = rank_candidates(candidates, results, existing) if symbols else candidates
        for row in ranked:
            if row['symbol'] in results:
                row['backtest'] = results[row['symbol']]
        for ticker in reassess:
            if not any(r['symbol'] == ticker for r in ranked):
                ranked.append(dict(symbol=ticker, backtest=results[ticker]))
        write(self.folder/'selection'/f'{self.selection:05d}.gate.json', dict(time=self.clock.now, existing=sorted(existing),
            results={t:str(self.folder/'gate'/str(end)/f'{t}_{config_sha[:20]}.json') for t in symbols}, ranked=ranked))
        return ranked

    def checkpoint(self):
        self.flush()
        number = len(self.checkpoints)
        destination = self.folder/'checkpoints'/f'{number:04d}'
        destination.mkdir(parents=True)
        for area in ('core','signals','history'):
            shutil.copytree(self.folder/area, destination/area)
        shutil.copy2(self.folder/'config.json', destination/'config.json')
        financial = {p.name:json.loads(p.read_text(encoding='utf-8')) for p in sorted((self.folder/'core').glob('*.json'))}
        normalized = json.dumps(financial, sort_keys=True, default=str).replace(str(self.folder), '<run>').replace(str(self.folder).replace('\\','\\\\'), '<run>')
        fingerprint = hashlib.sha256(normalized.encode()).hexdigest()
        write(destination/'clock.json', dict(time=self.clock.now, processes=self.processes,
              sessions=self.sessions, exchange_wallet=str(self.exchange.wallet if self.exchange else self.exchange_wallet),
              exchange_positions=self.exchange.positions if self.exchange else {},
              waiters=[(t, n) for t,n,f in self.clock.waiters if not f.done()],
              financial_sha256=fingerprint, driver_sha256=self.driver_sha256,
              recovery='Deterministic replay from start; verify native financial snapshot at checkpoint, then continue'))
        if self.recovery and abs(self.clock.now-self.recovery['time']) < .001:
            assert fingerprint == self.recovery['financial_sha256'], 'Recovery financial snapshot mismatch'
            assert self.recovery['driver_sha256'] == self.driver_sha256, 'Recovery code mismatch'
            self.recovered = True
            self.event('recovery_verified', financial_sha256=fingerprint)
        self.checkpoints.append(str(destination))

    def sample(self):
        from core.paper_budget import read_budget
        budget = read_budget(self.folder)
        paper_equity = budget['cash']
        locked = 0.
        for ticker, capital in budget['allocations'].items():
            sp, wp = self.paths(ticker, True)
            state, wallet = self.load(sp, {}), self.load(wp, {})
            price = float(self.frames[ticker].close.iloc[self.index()])
            equity = float(wallet['balance'])+float(state.get('siphoning_reserve', 0))
            for leg, sign in (('LONG',1), ('SHORT',-1)):
                equity += float(wallet['positions'][f'{ticker}_{leg}'])*(price-float(wallet[f'{leg.lower()}_entry_price']))*sign
            paper_equity += equity
            if state.get('startup_blocked') or state.get('rebalance_inflight'):
                locked += capital
        cfg = self.load(self.folder/'config.json', {})
        # SAFE remains part of system equity even after its worker exits.
        # Returned wallets already include their SAFE; ledger cash is authoritative.
        real_equity, real_cash = float(self.exchange_wallet), float(self.exchange_wallet)
        if self.exchange:
            real_equity = float(self.exchange.wallet)+sum(float(q*(self.exchange.price()-entry)*(1 if leg=='LONG' else -1))
                                                        for leg,(q,entry) in self.exchange.positions.items())
            real_cash = float(self.exchange.wallet)-sum(float(q*entry/D(str(cfg['portfolios'][0]['targets'][f'BASE_{leg}']['leverage'])))
                                                       for leg,(q,entry) in self.exchange.positions.items())
        assert math.isfinite(paper_equity) and budget['cash'] >= 0
        point = dict(time=self.clock.now, paper_equity=paper_equity, paper_cash=budget['cash'],
              paper_reserved=sum(budget['allocations'].values()), paper_unavailable=locked,
              paper_slots=sum(k.startswith('p_') for k in self.processes), real_account_equity=real_equity,
              real_strategy_equity=real_equity-5000, real_available=real_cash,
              real_slots=sum(k.startswith('r_') for k in self.processes))
        if self.curve and abs(self.curve[-1]['time']-self.clock.now) < 1e-7:
            self.curve[-1] = point
        else:
            self.curve.append(point)
        self.needs_sample = False

    async def monitor(self):
        from core.paper_budget import reconcile_terminal_allocations
        last_return, deadline, sample_at, checkpoint_at = 0., self.start, self.start, self.start+86400
        while self.clock.now <= self.end:
            latest = last_return
            if self.return_changed:
                self.return_changed = False
                self.flush()
                returned, blocked = reconcile_terminal_allocations(self.folder, recover=True)
                for ticker in returned:
                    self.docs.pop(str(self.paths(ticker, True)[0]), None)
                if blocked:
                    self.event('blocked_returns', reasons=blocked)
                with sqlite3.connect(self.folder/'core/paper_budget.sqlite') as db:
                    latest = db.execute("SELECT coalesce(max(ts),0) FROM events WHERE kind='return'").fetchone()[0]
            if latest > last_return:
                last_return, deadline = latest, latest+950.4
                self.event('selection_timer_reset', deadline=deadline)
            if self.clock.now >= deadline-1e-7:
                self.selection += 1
                before = deepcopy(self.processes)
                self.flush()
                self.event('selection_start', selection=self.selection)
                write(self.folder/'selection'/f'{self.selection:05d}.config.before.json', self.load(self.folder/'config.json', {}))
                try:
                    await self.manager['manage_swarm']()
                except Exception as error:
                    self.failures.append(dict(time=self.clock.now, selection=self.selection, error=repr(error)))
                    self.event('selection_error', selection=self.selection, error=traceback.format_exc())
                self.flush()
                self.event('selection_end', selection=self.selection, before=before, after=self.processes)
                write(self.folder/'selection'/f'{self.selection:05d}.config.after.json', self.load(self.folder/'config.json', {}))
                removed, added = sorted(set(before)-set(self.processes)), sorted(set(self.processes)-set(before))
                if removed and added:
                    self.event('REPLACE', removed=removed, added=added, selection=self.selection,
                               reason='production_supervisor_selection; exact inputs in selection directory')
                for key in set(before).intersection(self.processes):
                    self.event('KEEP', worker=key, session=self.active_sessions.get(key), selection=self.selection)
                deadline = self.clock.now+950.4
            if self.clock.now >= sample_at-1e-7:
                self.sample()
                sample_at = self.clock.now+60
            if self.clock.now >= checkpoint_at:
                self.checkpoint()
                checkpoint_at += 86400
                print(f'PROGRESS {(self.clock.now-self.start)/86400:.3f}/{self.minutes/1440:.3f} days; selections={self.selection}; failures={len(self.failures)}', flush=True)
                write(self.folder/'status.json', dict(status='RUNNING', virtual_time=self.clock.now,
                      days=(self.clock.now-self.start)/86400, selections=self.selection, failures=self.failures))
            next_time = min(deadline, sample_at, checkpoint_at, self.end)
            if next_time <= self.clock.now+1e-7:
                break
            await self.clock.sleep(min(1., next_time-self.clock.now))

    async def run(self):
        from core import main, storage, funding, paper_budget
        from scripts.verify_runtime_parity import Notifier
        proxy = SimpleNamespace(**{name:getattr(asyncio, name) for name in dir(asyncio)})
        proxy.sleep, proxy.to_thread = self.sleep, inline
        proxy.create_subprocess_shell, proxy.create_subprocess_exec = self.shell, self.child
        self.native_save, self.original_boundary = storage.safe_save_json_sync, funding.save_funding_boundary
        self.logger = logging.getLogger('swarm-replay')
        self.logger.setLevel(logging.INFO)
        handler = logging.FileHandler(self.folder/'runtime.log', encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(levelname)s %(message)s'))
        self.logger.addHandler(handler)
        source = ROOT/'supervisor/swarm_manager.py'
        from policy import instrument, cumulative_cycles, observe
        functions = [n for n in ast.parse(instrument(source.read_text(encoding='utf-8'))).body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        self.manager = dict(__name__='offline_supervisor', BASE_PATH=self.folder, CORE_PATH=self.folder/'core',
            CONFIG_PATH=str(self.folder/'config.json'), logger=self.logger, asyncio=proxy, json=json, os=os,
            time=time, math=math, random=random, re=re, sys=sys, shutil=shutil, Dict=Dict, List=List, Set=Set,
            Any=Any, Decimal=D, Path=Path, _last_start_time={}, _RESTART_COOLDOWN_SEC=30,
            safe_load_json=self.aload, safe_save_json=self.save, safe_load_json_sync=self.load,
            safe_save_json_sync=self.native_save, siphoning=__import__('core.siphoning', fromlist=['']),
            BinanceConnector=lambda **kw:self.exchange or self.connector(next(iter(self.frames))), run_scanner=self.scan)
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), 'exec'), self.manager)
        self.manager.update(get_running_bots_info=self.running, get_pm2_processes=self.pm2, research_observe=lambda *args:observe(self,*args))
        original_performance = self.manager['get_bot_efficiency']
        async def performance(ticker, config, is_real=False):
            result = await original_performance(ticker, config, is_real)
            state = self.load(self.paths(ticker, not is_real)[0], {})
            if not is_real:
                result['session_cycles'] = result['cycles']
                result['cycles'] = cumulative_cycles(self.folder, ticker, state)
            encoded = json.dumps(state, sort_keys=True).encode()
            identity = hashlib.sha256(encoded).hexdigest()
            target = self.folder/'inputs'/f'{identity}.json'
            if not target.exists():
                write(target, state)
            self.event('performance_input', ticker=ticker, is_real=is_real, metrics=result,
                       state_sha256=identity, selection=self.selection)
            return result
        self.manager['get_bot_efficiency'] = performance
        original_start = self.manager['start_bot']
        async def start(ticker, is_paper=True, config=None):
            self.flush()
            if not is_paper and (self.exchange is None or self.exchange.ticker != ticker):
                if self.exchange:
                    assert all(q == 0 for q,e in self.exchange.positions.values())
                    self.exchange_wallet = self.exchange.wallet
                self.exchange = self.connector(ticker)
            await original_start(ticker, is_paper, config)
            for path in self.paths(ticker, is_paper):
                if path.exists():
                    self.docs[str(path)] = json.loads(path.read_text(encoding='utf-8'))
        self.manager['start_bot'] = start
        async def noop(*args, **kwargs):
            return None
        async def kill(ticker, paper):
            self.event('self_stop', ticker=ticker, paper=paper)
        with patch('time.time',new=lambda:self.clock.now):
            paper_budget.initialize(self.folder, 8, 1000, {})
        from scanner import backtest_gate
        with ExitStack() as stack:
            for target, replacement in [('core.main.asyncio',proxy), ('core.executor.asyncio',proxy),
                    ('core.main.TelegramNotifier',lambda *a,**kw:Notifier()), ('core.main.load_json',self.aload),
                    ('core.main.save_json',self.save), ('core.main.safe_load_json_sync',self.load),
                    ('core.main.self_kill_pm2',kill), ('core.main.emit_signal',self.signal),
                    ('core.funding.save_funding_boundary',self.boundary),
                    ('scanner.backtest_gate.filter_candidates',self.gate), ('time.time',lambda:self.clock.now)]:
                stack.enter_context(patch(target,new=replacement))
            monitor = asyncio.create_task(self.monitor(), name='supervisor')
            await self.clock.settle()
            while self.clock.now < self.end and not monitor.done():
                minute = (math.floor(self.clock.now/60)+1)*60
                recovery_time = self.recovery['time'] if self.recovery and not self.recovered else self.end
                await self.clock.advance(min(self.end, minute, recovery_time))
                if self.needs_sample or abs(self.clock.now-minute) < 1e-7:
                    self.sample()
                if self.recovery and not self.recovered and abs(self.clock.now-recovery_time) < 1e-7:
                    self.checkpoint()
            if monitor.done():
                monitor.result()
            self.sample()
            self.checkpoint()
            monitor.cancel()
            for task in self.tasks.values():
                task.cancel()
            await asyncio.gather(monitor, *self.tasks.values(), return_exceptions=True)
            self.flush()
        self.fill_file.flush()
        assert not self.recovery or self.recovered, 'Recovery checkpoint not reached'
        self.finish()

    def finish(self):
        import pandas as pd
        for row in self.sessions:
            if 'end' not in row:
                sp, wp = self.paths(row['ticker'], row['paper'])
                state, wallet = self.load(sp, {}), self.load(wp, {})
                row.update(marked_at=self.clock.now, equity=state.get('last_tpv'),
                           cycles=state.get('rebalance_cycles'), status='OPEN_AT_END')
        frame = pd.DataFrame(self.curve)
        frame.to_csv(self.folder/'equity.csv', index=False)
        write(self.folder/'sessions.json', self.sessions)
        write(self.folder/'failures.json', self.failures)
        with sqlite3.connect(self.folder/'core/paper_budget.sqlite') as db:
            events = [dict(time=t,ticker=s,kind=k,amount=a) for t,s,k,a in db.execute('SELECT * FROM events ORDER BY rowid')]
            cash = float(db.execute('SELECT cash FROM account').fetchone()[0])
        write(self.folder/'paper_budget_events.json', events)
        expected = 8000+sum(float(e['amount'])*(1 if e['kind']=='return' else -1 if e['kind']=='reserve' else 0) for e in events)
        assert abs(expected-cash) < .0001, (expected, cash)
        active = set()
        for e in events:
            if e['kind']=='reserve':
                assert e['ticker'] not in active
                active.add(e['ticker'])
            elif e['kind']=='return':
                assert e['ticker'] in active, 'Duplicate return'
                active.remove(e['ticker'])
        write(self.folder/'accounting_checks.json', dict(paper_ledger_cash=cash, reconstructed_cash=expected,
            unique_returns=True, active_allocations=sorted(active),
            note='System equity is synchronized cash plus active marked wallets, including active SAFE; fees already deducted in wallets'))
        metrics = {}
        for mode, column, initial in [('PAPER','paper_equity',8000), ('SIMULATED_REAL_STRATEGY','real_strategy_equity',1000),
                                      ('SIMULATED_REAL_ACCOUNT','real_account_equity',6000)]:
            values = self.np.r_[initial, frame[column].to_numpy()]
            peak = self.np.maximum.accumulate(values)
            metrics[mode] = dict(initial=initial, equity=float(values[-1]), net=float(values[-1]-initial),
                                 max_dd_pct=float(self.np.max((peak-values)/peak)*100))
        write(self.folder/'summary.json', dict(status='COMPLETED_WITH_ERRORS' if self.failures else 'COMPLETED',
            start=self.start, end=self.clock.now, minutes=self.minutes, selections=self.selection, metrics=metrics,
            failures=self.failures, checkpoints=self.checkpoints))
        write(self.folder/'status.json', dict(status='COMPLETED_WITH_ERRORS' if self.failures else 'COMPLETED',
              virtual_time=self.clock.now, exit_code=0, selections=self.selection))
        self.events.close()
        self.fill_file.close()

    def close_workers(self):
        for process in self.gate_workers:
            if process.poll() is None:
                process.stdin.write('null\n')
                process.stdin.flush()
        for process in self.gate_workers:
            process.wait(timeout=60)
        for log in self.worker_logs:
            log.close()


async def entry():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--name', required=True)
    parser.add_argument('--minutes', type=int, default=43200)
    parser.add_argument('--recover-from', type=Path)
    args = parser.parse_args()
    assert re.fullmatch(r'[a-zA-Z0-9_-]+', args.name) and 0 < args.minutes <= 43200
    folder = HERE/args.name
    assert not folder.exists(), 'Preserve previous evidence; choose a new name'
    assert os.environ.get('PYTHONHASHSEED') == '0', 'Launch with PYTHONHASHSEED=0 for deterministic set ordering'
    install_guard(HERE)
    os.environ = {'PYTHONDONTWRITEBYTECODE':'1', 'SystemRoot':r'C:\Windows', 'PYTHONHASHSEED':'0'}
    random.seed(0)
    sequence = iter(range(1, 10**12))
    import dotenv
    with patch.object(dotenv, 'load_dotenv', return_value=False), patch.object(uuid, 'uuid4', new=lambda:uuid.UUID(int=next(sequence))):
        replay = Replay(folder, args.minutes, args.recover_from)
        write(folder/'provenance.json', dict(command=sys.argv, source_sha256=digest(__file__),
             prepared_manifest_sha256=digest(HERE/'manifest.json'), data=replay.hashes,
             effective_changes={'max_bots':10,'paper_mode_bots':8,'max_drawdown_limit':99.99,'gate_hours':[48,36],'modeled_volume_min':15000000,'target_universe':['ONUSDT','HOTUSDT'],'cycles':'unique closed PAPER sessions plus current','REAL_execution':'disabled; record fresh-slot decisions and sticky identity reservations only'},
             execution='Immediate full fills at last closed close +/- 5bp; taker fee 4bp; synthetic step .001 and min notional 5',
             scanner='Production TickerRanker; 24h quote volume estimated by sum(volume*close); fixed 50 pool'))
        write(folder/'status.json', dict(status='RUNNING', command=sys.argv))
        try:
            await replay.run()
        except BaseException:
            write(folder/'status.json', dict(status='FAILED_INCOMPLETE', exit_code=1,
                  virtual_time=replay.clock.now, traceback=traceback.format_exc()))
            raise
        finally:
            replay.close_workers()


if __name__ == '__main__':
    asyncio.run(entry())
