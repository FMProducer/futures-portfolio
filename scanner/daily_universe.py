"""Publish a daily PAPER research universe without managing trading processes."""

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scanner.backtest_gate import effective_config, evaluate, rank_candidates, read_public_url


def top_symbols(exchange, tickers, previous=()):
    eligible = {s['symbol'] for s in exchange['symbols']
                if s.get('contractType') == 'PERPETUAL' and s.get('status') == 'TRADING'
                and s.get('quoteAsset') == 'USDT' and s.get('marginAsset') == 'USDT'}
    volumes = {}
    for row in tickers:
        symbol = row['symbol']
        if symbol not in eligible:
            continue
        if not symbol.isascii() or not symbol.isalnum():
            continue
        volume = float(row['quoteVolume'])
        if not math.isfinite(volume) or volume < 0:
            raise ValueError('Invalid market volume or symbol')
        if volume == 0:
            continue
        if symbol in volumes:
            raise ValueError('Duplicate market ticker')
        volumes[symbol] = volume
    ordered = sorted(volumes, key=lambda s: (-volumes[s], s))[:100]
    if len(ordered) != 100:
        raise ValueError('Fewer than 100 eligible liquid contracts')
    ordered += sorted(set(previous).intersection(volumes).difference(ordered), key=lambda s: (-volumes[s], s))
    return [{'symbol':s, 'quote_volume_24h':volumes[s]} for s in ordered]


@contextmanager
def exclusive_run(path):
    with path.open('a+b') as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def write_json(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2), encoding='utf-8')
    temporary.replace(path)


def admission_history(root, symbols):
    history = {}
    for status_path in sorted((root/'reports/daily_universe').glob('*/status.json')):
        status = json.loads(status_path.read_text(encoding='utf-8'))
        if status.get('status') != 'published':
            continue
        result_path = status_path.with_name('results.json')
        if not result_path.exists():
            continue
        results = json.loads(result_path.read_text(encoding='utf-8'))
        for symbol in symbols:
            result = results.get(symbol, {})
            windows = result.get('windows', {})
            if (result.get('pass') is True and not result.get('error')
                    and all(windows.get(w, {}).get('pass') is True for w in ('48','24'))):
                end = result.get('end_exclusive_ms', status.get('end_exclusive_ms', 0))
                if end >= history.get(symbol, {}).get('end_exclusive_ms', -1):
                    history[symbol] = {'report':str(result_path), 'end_exclusive_ms':end,
                                       'result_sha256':hashlib.sha256(result_path.read_bytes()).hexdigest()}
    return history


def publish(root, run, candidates, results, config):
    if set(results) != {r['symbol'] for r in candidates}:
        raise ValueError('Incomplete backtest universe')
    errors = {s:r['error'] for s,r in results.items() if r.get('error')}
    if errors:
        raise ValueError(f'Backtest errors; previous universe retained: {errors}')
    current = json.loads((root/'config.json').read_text(encoding='utf-8'))
    if effective_config(current) != effective_config(config):
        raise ValueError('Trading configuration changed during evaluation')
    target = root/'tickers.txt'
    previous = target.read_bytes() if target.exists() else b''
    input_path = run/'tickers.input.txt'
    if input_path.exists() and input_path.read_bytes() != previous:
        raise ValueError('Candidate list changed during evaluation')
    old_symbols = set(previous.decode('utf-8-sig').split())
    history = admission_history(root, old_symbols)
    fresh = rank_candidates(candidates, results, set())
    fresh_symbols = {r['symbol'] for r in fresh}
    retained = rank_candidates([r for r in candidates if r['symbol'] in old_symbols
                               and r['symbol'] in history and r['symbol'] not in fresh_symbols],
                              results, set(), require_pass=False)
    ranked = fresh + retained
    selected = [r['symbol'] for r in ranked[:50]]
    write_json(run/'results.json', results)
    admissions = {symbol: {'basis':'fresh_pass' if symbol in fresh_symbols else 'historical_pass',
                          'last_pass': {'report':str(run/'results.json'),
                                        'end_exclusive_ms':results[symbol].get('end_exclusive_ms'),
                                        'result_sha256':hashlib.sha256((run/'results.json').read_bytes()).hexdigest()}
                          if symbol in fresh_symbols else history[symbol]}
                  for symbol in selected}
    write_json(run/'admissions.json', admissions)
    (run/'tickers.before.txt').write_bytes(previous)
    content = ''.join(s+'\n' for s in selected).encode('utf-8')
    (run/'tickers.selected.txt').write_bytes(content)
    write_json(run/'ranking.json', ranked)
    temporary = target.with_suffix('.tmp')
    temporary.write_bytes(content)
    temporary.replace(target)
    return {'selected':selected, 'passed':sum(bool(r.get('pass')) for r in results.values()),
            'pool_size':len(selected), 'retained_historical':sum(t not in fresh_symbols for t in selected), 'list_role':'cumulative_passed_pool', 'tested':len(results),
            'tickers_sha256':hashlib.sha256(content).hexdigest()}


async def main(root=ROOT):
    reports = root/'reports/daily_universe'
    reports.mkdir(parents=True, exist_ok=True)
    with exclusive_run(reports/'run.lock'):
        end = int(time.time()//3600)*3600000
        run = reports/datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')
        run.mkdir()
        status = {'created_utc':datetime.now(timezone.utc).isoformat(),
                  'start_ms':end-172800000, 'window_hours':[48,24], 'end_exclusive_ms':end, 'status':'running'}
        write_json(run/'status.json', status)
        try:
            config = json.loads((root/'config.json').read_text(encoding='utf-8'))
            write_json(run/'effective_config.json', effective_config(config))
            previous_bytes = (root/'tickers.txt').read_bytes() if (root/'tickers.txt').exists() else b''
            previous = list(dict.fromkeys(previous_bytes.decode('utf-8-sig').split()))
            if len(previous)>50:
                raise ValueError('Previous pool exceeds 50 symbols')
            (run/'tickers.input.txt').write_bytes(previous_bytes)
            snapshots = {}
            for name, endpoint in [('exchange','exchangeInfo'), ('volumes','ticker/24hr')]:
                url = 'https://fapi.binance.com/fapi/v1/'+endpoint
                raw = read_public_url(url)
                (run/(name+'.json')).write_bytes(raw)
                snapshots[name] = json.loads(raw)
            candidates = top_symbols(snapshots['exchange'], snapshots['volumes'], previous)
            write_json(run/'candidates.json', candidates)
            write_json(run/'union.json', {'previous':previous, 'unique_tested':len(candidates),
                       'unavailable_previous':sorted(set(previous)-{r['symbol'] for r in candidates})})
            results = await evaluate(root, [r['symbol'] for r in candidates], config, end,
                                     report_dir=run/'backtests')
            write_json(run/'results.json', results)
            status.update(publish(root, run, candidates, results, config), status='published')
        except Exception as error:
            status.update(status='failed', error=f'{type(error).__name__}: {error}')
            raise
        finally:
            write_json(run/'status.json', status)
            write_json(reports/'latest.json', {**status, 'report':str(run)})
        print(json.dumps(status))


if __name__ == '__main__':
    asyncio.run(main())
