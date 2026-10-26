"""Admission replay on closed UTC hours; never manages processes or orders."""

import asyncio
import hashlib
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path


def closed_metrics(result, price):
    if not result or not result.get("accounting_ok") or result.get("cash_invariant_violation") or result.get("initial_build_pending"):
        raise ValueError("Incomplete replay or accounting failure")
    if result.get("funding_halt_active") or result.get("liquidations", 0):
        raise ValueError("Funding halt or model liquidation")
    settings = result["settings"]
    slip, fee = settings["slippage_pct"], settings["commission_pct"]
    positions = result["open_positions"]
    long, short = positions["long"], positions["short"]
    slippage = (long+short)*price*slip
    commission = (long*price*(1-slip)+short*price*(1+slip))*fee
    equity = result["final_equity"]-slippage-commission
    initial = result["initial_capital"]
    peak = max(initial, *result["equity_curve"])
    dd = max(result["max_dd_pct"], (peak-equity)/peak*100)
    net = equity-initial
    if not all(math.isfinite(v) for v in (net, dd, equity)):
        raise ValueError("Nonfinite replay metrics")
    return {"net":net, "net_pct":net/initial*100, "max_dd_pct":dd,
            "cycles":result["cycles"], "terminal_equity":equity,
            "terminal_commission":commission, "terminal_slippage":slippage,
            "funding_model":result["funding_model"],
            "pass":net>0 and dd<=50 and result["cycles"]>=2}


def effective_config(config):
    keys = ("portfolios", "rebalance_share_trail_distance", "equity_trailing_stop_pct",
            "equity_trailing_stop_activation_pct", "equity_trailing_stop_timeout_sec",
            "min_notional_usdt", "toxic_cooldown_days", "max_drawdown_cooldown_days",
            "max_drawdown_limit", "funding_model", "funding_scenario", "trend_guard")
    return {k:config[k] for k in keys if k in config}


async def evaluate(root, symbols, config, end):
    import pandas as pd
    from backtest.backtest_rebalance import run_backtest
    effective = effective_config(config)
    capital = effective["portfolios"][0]["paper_initial_capital"]
    encoded = json.dumps(effective, sort_keys=True).encode()
    source = hashlib.sha256()
    for area in ("backtest", "core", "scanner"):
        for path in sorted((root/area).glob("*.py")):
            source.update(path.name.encode()+path.read_bytes())
    if effective.get('funding_scenario'):
        source.update(Path(effective['funding_scenario']).read_bytes())
    key = hashlib.sha256(encoded+str(end).encode()+source.digest()).hexdigest()[:20]
    folder = root/"reports/backtest_gate"/key
    folder.mkdir(parents=True, exist_ok=True)
    cfg = folder/"effective_config.json"
    cfg.write_bytes(encoded)
    results = {}
    for symbol in symbols:
        target = folder/f"{symbol}.json"
        if target.exists():
            results[symbol] = json.loads(target.read_text())
            continue
        result = {"symbol":symbol, "pass":False, "start_ms":end-86400000, "end_exclusive_ms":end,
                  "rules":{"window_hours":24,"min_net_exclusive":0,"max_dd_pct":50,"min_cycles":2},
                  "capital":capital, "config_sha256":hashlib.sha256(encoded).hexdigest(),
                  "source_sha256":source.hexdigest(), "created":time.time()}
        try:
            params = urllib.parse.urlencode({"symbol":symbol,"interval":"1m","limit":1440,"endTime":end-1})
            url = "https://fapi.binance.com/fapi/v1/klines?"+params
            with urllib.request.urlopen(url,timeout=30) as response:raw=response.read()
            bars = json.loads(raw)
            if len(bars)!=1440 or [b[0] for b in bars]!=list(range(end-86400000,end,60000)) or any(b[6]>=end for b in bars):
                raise ValueError("Missing, duplicate or unclosed minute bars")
            data = folder/symbol
            data.mkdir(exist_ok=True)
            (data/"klines.json").write_bytes(raw)
            frame = pd.DataFrame({"time":pd.to_datetime([b[0] for b in bars],unit="ms",utc=True),
                                  **{k:[float(b[i]) for b in bars] for i,k in enumerate(("open","high","low","close","volume"),1)}})
            if not all(math.isfinite(v) and v>0 for k in ("open","high","low","close") for v in frame[k]):
                raise ValueError("Invalid prices")
            if (not all(math.isfinite(v) and v>=0 for v in frame['volume'])
                    or any(b[2] < max(b[1], b[3], b[4]) or b[3] > min(b[1], b[2], b[4]) for b in
                           frame[['time','open','high','low','close']].itertuples(index=False,name=None))):
                raise ValueError("Invalid OHLC or volume")
            frame.to_feather(data/f"{symbol}_live.feather")
            replay = await run_backtest(str(cfg),str(data),live_mode=False,ticker_override=symbol,
                                        days=1,capital_override=capital,quiet=True)
            result.update(closed_metrics(replay,float(frame['close'].iloc[-1])))
            result["data_sha256"] = hashlib.sha256(raw).hexdigest()
            result["source_url"] = url
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(result,indent=2))
        temporary.replace(target)
        results[symbol] = result
    return results


def rank_candidates(candidates, results, existing):
    admitted = []
    for candidate in candidates:
        symbol = candidate['symbol']
        if symbol in existing:
            admitted.append(candidate)
        elif results[symbol].get('pass'):
            metrics = results[symbol]
            if not all(math.isfinite(metrics[k]) for k in ('net_pct', 'max_dd_pct')):
                raise ValueError("Nonfinite candidate ranking metrics")
            admitted.append({**candidate, 'backtest_net_pct': metrics['net_pct'],
                             'backtest_max_dd_pct': metrics['max_dd_pct']})
    pending = [r for r in admitted if r['symbol'] not in existing]
    ranked = []
    while pending:
        front = [r for r in pending if not any(
            other['backtest_net_pct'] >= r['backtest_net_pct']
            and other['backtest_max_dd_pct'] <= r['backtest_max_dd_pct']
            and (other['backtest_net_pct'] > r['backtest_net_pct']
                 or other['backtest_max_dd_pct'] < r['backtest_max_dd_pct'])
            for other in pending)]
        for candidate in front:
            candidate['backtest_rank'] = 1 + max((r['backtest_rank'] for r in ranked), default=0)
        ranked.extend(front)
        pending = [r for r in pending if r not in front]
    ordered = iter(ranked)
    return [r if r['symbol'] in existing else next(ordered) for r in admitted]


async def filter_candidates(root, candidates, config, existing):
    symbols = [r['symbol'] for r in candidates if r['symbol'] not in existing]
    if not symbols:
        return candidates
    end = int(time.time()//3600)*3600000
    request = root/"reports/backtest_gate/request.json"
    request.parent.mkdir(parents=True,exist_ok=True)
    request.write_text(json.dumps({"symbols":symbols,"config":effective_config(config),"end":end}))
    output = request.with_name('response.json')
    with request.with_name('worker.log').open('w') as log:
        proc = await asyncio.create_subprocess_exec(sys.executable,str(Path(__file__).resolve()),str(root),str(request),str(output),stdout=log,stderr=log)
        try:
            code = await asyncio.wait_for(proc.wait(),600)
            if code:
                raise RuntimeError("Backtest gate worker failed")
            results = json.loads(output.read_text())
        except BaseException:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            raise
    ranked = rank_candidates(candidates, results, existing)
    request.with_name('ranking.json').write_text(json.dumps(ranked, indent=2))
    return ranked


if __name__ == '__main__':
    root, request, output = map(Path,sys.argv[1:])
    sys.path.insert(0,str(root))
    data = json.loads(request.read_text())
    output.write_text(json.dumps(asyncio.run(evaluate(root,data['symbols'],data['config'],data['end']))))
