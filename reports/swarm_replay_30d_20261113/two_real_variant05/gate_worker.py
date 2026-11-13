"""Persistent isolated clean-capital gate worker; JSON lines only on stdout."""
import asyncio
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

from replay import HERE, ROOT, install_guard

sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))
os.environ = {'PYTHONDONTWRITEBYTECODE':'1','SystemRoot':r'C:\Windows','PYTHONHASHSEED':'0'}
loop = asyncio.new_event_loop()
install_guard(HERE)
import dotenv
with patch.object(dotenv,'load_dotenv',return_value=False):
    import pandas as pd
    from backtest.backtest_rebalance import run_backtest
    from scanner.backtest_gate import closed_metrics

    async def evaluate(job):
        end, ticker, config_path, data_path, now = job
        config = json.loads(Path(config_path).read_text())
        row = {'symbol':ticker, 'end_exclusive_ms':end, 'time':now, 'pass':False, 'windows':{}}
        try:
            frame = pd.read_feather(next(Path(data_path).glob(ticker+'_*.feather')))
            stamps = pd.to_datetime(frame.time,utc=True).dt.as_unit('ms').astype('int64')
            if end-172800000 < stamps.iloc[0]:
                raise ValueError('Insufficient closed-hour 48h history')
            assert end <= now*1000
            price = float(frame.loc[stamps < end,'close'].iloc[-1])
            for hours in (48,36):
                since = pd.Timestamp(end-hours*3600000,unit='ms',tz='UTC').strftime('%Y-%m-%d %H:%M')
                until = pd.Timestamp(end-60000,unit='ms',tz='UTC').strftime('%Y-%m-%d %H:%M')
                result = await run_backtest(config_path,data_path,ticker_override=ticker,days=hours/24,
                    since_utc=since,until_utc=until,capital_override=config['portfolios'][0]['paper_initial_capital'],quiet=True)
                assert result is not None and result['data_window']['bars']==hours*60
                row['windows'][str(hours)] = dict(closed_metrics(result,price),start_ms=end-hours*3600000,end_exclusive_ms=end)
            row.update(row['windows']['36'])
            row['pass'] = all(w['pass'] for w in row['windows'].values())
        except Exception as error:
            row['error'] = f'{type(error).__name__}: {error}'
        return row

    for line in sys.stdin:
        job = json.loads(line)
        if job is None:
            break
        with patch('time.time',return_value=job[-1]):
            print(json.dumps(loop.run_until_complete(evaluate(job))),flush=True)
loop.close()
