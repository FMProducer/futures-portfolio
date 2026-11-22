"""Replay local historical candles through BACKTEST, PAPER and simulated REAL."""
import argparse
import asyncio
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_parity_case import run_case, write
from scripts.parity_funding import HistoricalFunding


def prepare(args):
    ticker = args.ticker.upper()
    if not re.fullmatch(r'[A-Z0-9]+USDT', ticker):
        raise ValueError('Ticker must be a USDT symbol, for example BTWUSDT')
    source = args.data.resolve()
    if not source.name.upper().startswith(ticker + '_'):
        raise ValueError('Data filename must start with TICKER_ to verify ticker provenance')
    frame = pd.read_feather(source)
    if not {'time', 'open', 'high', 'low', 'close', 'volume'} <= set(frame.columns):
        raise ValueError('Feather must contain time, open, high, low, close and volume')
    times = pd.to_datetime(frame['time'], utc=True, errors='raise')
    start = pd.to_datetime(args.start, utc=True) if args.start else times.iloc[0]
    end = pd.to_datetime(args.end, utc=True) if args.end else times.iloc[-1]
    if pd.isna(start) or pd.isna(end) or start > end:
        raise ValueError('Invalid UTC period: start must not exceed end')
    if start < times.min() or end > times.max():
        raise ValueError('Requested UTC period extends beyond available history')
    frame = frame.loc[(times >= start) & (times <= end)].copy().reset_index(drop=True)
    if len(frame) < 2:
        raise ValueError('Selected UTC period must contain at least two candles')
    times = pd.to_datetime(frame['time'], utc=True)
    stamps = times.dt.as_unit('ms').astype('int64').to_numpy()
    if times.isna().any() or np.any(np.diff(stamps) != 60000) or np.any(stamps % 60000):
        raise ValueError('Selected candles must be ordered, unique, contiguous UTC minutes')
    values = frame[['open', 'high', 'low', 'close', 'volume']].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values[:, :4] <= 0).any() or (values[:, 4] < 0).any():
        raise ValueError('OHLC must be positive and finite; volume must be nonnegative and finite')
    if ((frame.high < frame[['open', 'low', 'close']].max(axis=1)) |
            (frame.low > frame[['open', 'high', 'close']].min(axis=1))).any():
        raise ValueError('Invalid candle high/low range')
    config = json.loads(args.config.read_text(encoding='utf-8-sig'))
    if len(config.get('portfolios', [])) != 1:
        raise ValueError('Config must contain exactly one portfolio; supply its explicit policy')
    portfolio = config['portfolios'][0]
    if portfolio.get('paper_initial_capital', portfolio['initial_capital']) != portfolio['initial_capital']:
        raise ValueError('PAPER and BACKTEST initial capital must match')
    if config.get('funding_scenario'):
        raise ValueError('External funding_scenario is unsupported; use --funding-rates for signed_actual')
    rates = None
    if args.funding_rates:
        if config.get('funding_model') != 'signed_actual':
            raise ValueError('--funding-rates requires funding_model=signed_actual')
        rates = json.loads(args.funding_rates.read_text(encoding='utf-8-sig'))
        HistoricalFunding(ticker, rates)
        rates = [r for r in rates if stamps[0] <= r['fundingTime'] <= stamps[-1]]
    elif config.get('funding_model') == 'signed_actual':
        raise ValueError('signed_actual requires local --funding-rates; no network fallback')
    frame['time'] = times
    output = (args.output or ROOT/'reports'/f'parity_{ticker}_{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}').resolve()
    output.mkdir(parents=True, exist_ok=False)
    data = output/f'{ticker}_live.feather'
    frame.to_feather(data)
    write(output/'config.json', config)
    if rates is not None:
        write(output/'rates.json', rates)
    write(output/'case.json', dict(ticker=ticker, mode='selected_history', virtual_clock=True,
        bars=len(frame), initial_capital=portfolio['initial_capital'], reserve=portfolio['cash_reserve_usdt'],
        data_path=str(data), rates_path=str(output/'rates.json') if rates is not None else None))
    write(output/'inputs.json', dict(source=str(source), source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        config_source=str(args.config.resolve()), config_sha256=hashlib.sha256(args.config.read_bytes()).hexdigest(),
        selected_sha256=hashlib.sha256(data.read_bytes()).hexdigest(), ticker=ticker, bars=len(frame),
        start_utc=times.iloc[0].isoformat(), end_utc=times.iloc[-1].isoformat(),
        requested_start=str(start), requested_end=str(end), date_bounds='inclusive UTC candle-open times',
        funding_source=str(args.funding_rates.resolve()) if args.funding_rates else None,
        funding_sha256=hashlib.sha256(args.funding_rates.read_bytes()).hexdigest() if args.funding_rates else None))
    return output


def summarize(directory):
    result = json.loads((directory/'comparison.json').read_text())
    invariant_failures = [f for f in result['failures'] if f in
                          ('paper: negative CASH or VIRTUAL', 'real: negative CASH or VIRTUAL')]
    other_failures = [f for f in result['failures'] if f not in invariant_failures]
    if any(not math.isfinite(float(value)) for row in result['modes'].values()
           for key, value in row.items() if key in ('final_tpv', 'profit_pct', 'max_dd_pct', 'commission')):
        other_failures.append('Non-finite financial results')
    financial_pass = not other_failures and len(result['modes']) == 3
    lines = ['| Mode | Net USDT | Return % | Ending bot TPV | MaxDD % | Fees | Cycles | Fills |',
             '|---|---:|---:|---:|---:|---:|---:|---:|']
    capital = json.loads((directory/'case.json').read_text())['initial_capital']
    for mode, row in result['modes'].items():
        lines.append(f"| {mode.upper()} | {row['final_tpv']-capital:.8f} | {row['profit_pct']:.6f} | "
                     f"{row['final_tpv']:.8f} | {row['max_dd_pct']:.6f} | {row['commission']:.8f} | "
                     f"{row['cycles']} | {row['market_fills']} |")
    code = 1 if not financial_pass else (2 if invariant_failures else 0)
    summary = dict(financial_parity='PASS' if financial_pass else 'FAIL', overall='PASS' if code == 0 else 'FAIL',
                   invariant_failures=invariant_failures, other_failures=other_failures, exit_code=code)
    write(directory/'summary.json', summary)
    text = '\n'.join(lines) + '\n\n' + json.dumps(summary, indent=2)
    (directory/'SUMMARY.md').write_text(text, encoding='utf-8')
    print(text)
    print(f'Artifacts: {directory}')
    return code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, epilog=
        'Dates are inclusive UTC candle-open times. Exit: 0 all checks pass; 1 parity/execution failure; '
        '2 financial parity passes but strict CASH/VIRTUAL fails. No exchange orders or downloads.')
    parser.add_argument('ticker', help='For example BTWUSDT')
    parser.add_argument('--data', type=Path, required=True, help='Local TICKER_*.feather')
    parser.add_argument('--config', type=Path, default=ROOT/'config.json', help='Single-portfolio policy config')
    parser.add_argument('--start', help='First UTC candle timestamp; omitted = first available')
    parser.add_argument('--end', help='Last UTC candle timestamp; omitted = last available')
    parser.add_argument('--funding-rates', type=Path, help='Local historical funding/mark-price JSON for signed_actual')
    parser.add_argument('--output', type=Path, help='New artifact directory; existing directories are never overwritten')
    args = parser.parse_args(argv)
    directory = None
    try:
        directory = prepare(args)
        print(f'Running three modes; artifacts: {directory}', flush=True)
        asyncio.run(run_case(directory))
        return summarize(directory)
    except Exception as exc:
        if directory is not None:
            write(directory/'cli_error.json', dict(error=str(exc), exit_code=1))
        print(f'Parity check failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
