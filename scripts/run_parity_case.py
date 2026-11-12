"""Run and verify one frozen annual case using the actual runtime state machines."""
import asyncio
from decimal import Decimal as D
import hashlib
import json
from pathlib import Path
import sys
import traceback

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backtest.backtest_rebalance import run_backtest
from scripts.parity_funding import HistoricalFunding, backtest_funding_bridge
from scripts.verify_runtime_parity import replay as paper_replay
from scripts.verify_real_runtime_parity import replay as real_replay


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2), encoding='utf-8')


def save_result(directory, mode, result):
    for key in list(result):
        if key.startswith('_'):
            write(directory/f'{mode}{key}.json', result.pop(key))
    write(directory/f'{mode}.json', result)


def compare(directory, info):
    failures, curves, fills, modes = [], {}, {}, {}
    capital, reserve = D(str(info['initial_capital'])), D(str(info['reserve']))
    for mode in ('backtest','paper','real'):
        path = directory/f'{mode}.json'
        if not path.exists():
            failures.append(f'{mode}: replay did not complete')
            continue
        result = json.loads(path.read_text())
        if mode == 'backtest':
            curve = result['equity_curve']
            trades = [row for row in result['fills'] if row['leg'] in ('BASE_LONG','BASE_SHORT')]
            fees, paid, final_reserve = result['commission_paid'], result['funding_paid'], result['cash_reserve_usdt']
            cycles, stops = result['cycles'], result['trailing_stops']
            if result['status'] != 'completed': failures.append(f'backtest status: {result["status"]}')
            positions = result['open_positions']
        else:
            curve = json.loads((directory/f'{mode}_curve.json').read_text())
            trades = json.loads((directory/f'{mode}_fills.json').read_text())
            fees = sum(float(row['commission']) for row in trades)
            paid, final_reserve = result['funding'], float(result['funding_reserve'])
            cycles, stops = result['cycles'], result['stops']
            positions = {leg.lower():result['positions'].get(f'{info["ticker"]}_{leg}',0) for leg in ('LONG','SHORT')}
            if D(result['min_cash']) < 0 or D(result['min_virtual']) < 0:
                failures.append(f'{mode}: negative CASH or VIRTUAL')
        if len(curve) != info['bars']: failures.append(f'{mode}: incomplete equity curve')
        curves[mode], fills[mode] = curve, trades
        modes[mode] = dict(profit_pct=(curve[-1]/float(capital)-1)*100,
            profit_after_funding_pct=(curve[-1]-float(capital)-paid)/float(capital)*100,
            max_dd_pct=float(np.max((np.maximum(np.maximum.accumulate(curve),float(capital))-curve)/
                                   np.maximum(np.maximum.accumulate(curve),float(capital)))*100),
            final_tpv=curve[-1], minimum_tpv=min(curve), commission=fees, funding_paid=paid,
            reserve_end=final_reserve, cycles=cycles,stops=stops,market_fills=len(trades),positions=positions)
    errors = {}
    if len(modes)==3:
        reference = modes['backtest']
        for mode in ('paper','real'):
            if len(curves[mode]) == len(curves['backtest']):
                error = float(np.max(np.abs(np.array(curves[mode])-curves['backtest'])))
                errors[mode] = error
                if error >= 1e-8:
                    difference = np.abs(np.array(curves[mode])-curves['backtest'])
                    first = int(np.flatnonzero(difference>=1e-8)[0])
                    failures.append(f'{mode}: first equity mismatch at bar {first}, maximum error {error}')
            for key in ('cycles','stops','market_fills','positions'):
                if modes[mode][key] != reference[key]: failures.append(f'{mode}: {key} mismatch')
            for key in ('commission','funding_paid','reserve_end'):
                if abs(modes[mode][key]-reference[key])>=1e-8: failures.append(f'{mode}: {key} mismatch')
            if len(fills[mode]) == len(fills['backtest']):
                for index,(a,b) in enumerate(zip(fills['backtest'],fills[mode])):
                    leg = b['type'] if mode=='paper' else b['symbol'].split('_')[-1]
                    pairs = [('price','price'),('commission','commission'),('realized_pnl','trade_pnl' if mode=='paper' else 'pnl')]
                    if (a['bar'] != b['bar'] or a['side'] != b['side'] or a['leg'].replace('BASE_','') != leg
                        or D(str(a['qty'])) != D(str(b['qty']))
                        or any(abs(float(a[x])-float(b[y]))>=1e-8 for x,y in pairs)):
                        failures.append(f'{mode}: first fill mismatch at index {index}')
                        break
        expected = json.loads((directory/'backtest_rates.json').read_text())
        expected = [(row['fundingTime'],D(row['signed_amount'])) for row in expected if D(row['signed_amount'])]
        for mode in ('paper','real'):
            name = 'paper_funding.json' if mode=='paper' else 'real_funding_receipts.json'
            rows = json.loads((directory/name).read_text())
            stream = [(int(round(row['event_time']*1000))-(60000 if mode=='real' else 0),D(str(row['signed_amount']))) for row in rows]
            if len(stream)!=len(expected) or any(t!=u or abs(v-w)>=D('1e-18') for (t,v),(u,w) in zip(stream,expected)):
                failures.append(f'{mode}: historical funding cashflow mismatch')
        bt_stops = [row['bar'] for row in json.loads((directory/'backtest.json').read_text())['events'] if row['kind']=='cooldown_start']
        bt_restarts = [row['bar'] for row in json.loads((directory/'backtest.json').read_text())['events'] if row['kind']=='cooldown_end']
        for mode in ('paper','real'):
            sessions = json.loads((directory/f'{mode}_sessions.json').read_text())
            stops = [row['bar'] for row in sessions if row.get('reason')]
            if stops != bt_stops: failures.append(f'{mode}: stop/restart boundary mismatch')
            if [row['start_bar'] for row in sessions[1:]] != bt_restarts:
                failures.append(f'{mode}: restart entry mismatch')
    result = dict(status='PASS' if not failures else 'FAIL',ticker=info['ticker'],scenario=info['mode'],
        bars=info['bars'],modes=modes,equity_errors=errors,failures=failures,
        funding_events=sum(D(row['signed_amount']) != 0 for row in json.loads((directory/'backtest_rates.json').read_text())),
        funding_calendar_observations=len(json.loads((directory/'backtest_rates.json').read_text())),
        scope='Full actual runtime with isolated JSON I/O and in-memory SQLite; native prefix checked separately')
    write(directory/'comparison.json',result)
    return result


async def run_case(directory):
    if (directory/'status.json').exists():
        raise ValueError('Preserve the existing run; use a new case directory')
    info = json.loads((directory/'case.json').read_text())
    config_path, data = directory/'config.json', Path(info['data_path'])
    config = json.loads(config_path.read_text())
    rows = json.loads(Path(info['rates_path']).read_text()) if info['rates_path'] else None
    def schedule(): return HistoricalFunding(info['ticker'],rows) if rows is not None else None
    write(directory/'status.json',dict(status='RUNNING',stage='backtest'))
    book = schedule()
    try:
        if book:
            with backtest_funding_bridge(book):
                b = await run_backtest(str(config_path),str(data.parent),ticker_override=info['ticker'],days=365,quiet=True)
        else:
            b = await run_backtest(str(config_path),str(data.parent),ticker_override=info['ticker'],days=365,quiet=True)
        assert b is not None
        write(directory/'backtest.json',b)
        observations = book.observations if book else []
        write(directory/'backtest_rates.json',observations)
        if book:
            events = [dict(event_id=f"rates:{info['ticker']}:{row['fundingTime']}",
                signed_amount=row['signed_amount'],event_time=row['fundingTime']/1000,
                symbol=info['ticker'],status='received') for row in observations if D(row['signed_amount'])]
            write(directory/'backtest_control_events.json',events)
            write(directory/'backtest_control_config.json',dict(config,funding_scenario=str(directory/'backtest_control_events.json')))
            control = await run_backtest(str(directory/'backtest_control_config.json'),str(data.parent),
                                          ticker_override=info['ticker'],days=365,quiet=True)
            assert control and control['equity_curve']==b['equity_curve'] and control['fills']==b['fills']
            write(directory/'backtest_control.json',dict(status='EXACT_MATCH',bars=len(control['equity_curve'])))
    except BaseException:
        write(directory/'backtest_error.json',dict(error=traceback.format_exc()))
        if not (directory/'backtest_rates.json').exists(): write(directory/'backtest_rates.json',[])
    for mode in ('paper','real'):
        write(directory/'status.json',dict(status='RUNNING',stage=mode))
        print(f'{info["ticker"]} {info["mode"]}: {mode} starting',flush=True)
        try:
            if mode=='paper':
                value = await paper_replay(config_path,data,info['bars'],memory_reserve_store=True,funding_rates=schedule())
            else:
                value = await real_replay(config_path,data,info['bars'],archive_applied=True,memory_reserve_store=True,funding_rates=schedule())
            save_result(directory,mode,value)
        except BaseException:
            write(directory/f'{mode}_error.json',dict(error=traceback.format_exc()))
    comparison = compare(directory,info)
    prefix = directory/'native_prefix'
    prefix.mkdir()
    write(prefix/'config.json',config)
    prefix_count = min(180,info['bars'])
    pd.read_feather(data).iloc[:prefix_count].to_feather(prefix/data.name)
    try:
        for mode in ('paper','real'):
            if mode=='paper':
                value = await paper_replay(prefix/'config.json',prefix/data.name,prefix_count,
                                          native_persistence=True,funding_rates=schedule())
            else:
                value = await real_replay(prefix/'config.json',prefix/data.name,prefix_count,archive_applied=True,
                                         native_persistence=True,funding_rates=schedule())
            reference_path = directory/f'{mode}_curve.json'
            if reference_path.exists():
                assert max(abs(a-b) for a,b in zip(value['_curve'],json.loads(reference_path.read_text())[:prefix_count])) < 1e-8
            save_result(prefix,mode,value)
        comparison['native_prefix'] = dict(status='PASS',bars=prefix_count)
    except BaseException:
        write(prefix/'error.json',dict(error=traceback.format_exc()))
        comparison['native_prefix'] = dict(status='FAIL',bars=prefix_count)
        comparison['failures'].append('Native prefix replay failed')
        comparison['status']='FAIL'
    write(directory/'comparison.json',comparison)
    write(directory/'status.json',dict(status=comparison['status'],stage='completed'))
    print(json.dumps(comparison),flush=True)


if __name__=='__main__':
    asyncio.run(run_case(Path(sys.argv[1]).resolve()))
