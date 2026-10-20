"""Read-only consistency audit of a completed registered study; no replay/admission."""
import argparse
from contextlib import closing
import csv
from datetime import datetime
from decimal import Decimal, getcontext
import hashlib
import json
from pathlib import Path
import sqlite3


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def number(value):
    require(not isinstance(value, (bool, float)), 'non-decimal financial value')
    result = Decimal(value)
    require(result.is_finite(), 'non-finite financial value')
    return result


def verify(database, output):
    """Raise on inconsistent evidence. Never import archived code or open DB writable."""
    database, output = Path(database).resolve(), Path(output).resolve()
    registration = load(output/'registration.json')
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro', uri=True)) as db:
        db.execute('BEGIN')
        require(db.execute('PRAGMA integrity_check').fetchall() == [('ok',)], 'registry integrity')
        require(db.execute('SELECT version FROM research_registry').fetchall() == [('v1',)], 'registry version')
        rows = db.execute('SELECT t.snapshot,t.started_utc,o.body,o.finished_utc FROM trials t '
                          'LEFT JOIN outcomes o ON o.trial_id=t.id WHERE t.id=?',
                          (registration['id'],)).fetchall()
    require(len(rows) == 1, 'registered trial missing')
    raw, started, body, finished = rows[0]
    require(body is not None, 'trial INCOMPLETE')
    snapshot, outcome = json.loads(raw), json.loads(body)
    require(outcome['status'] == 'COMPLETED', 'trial not COMPLETED')
    require(snapshot['mode'] == 'PROSPECTIVE', 'prospective runner registration required')
    begin, end = datetime.fromisoformat(started), datetime.fromisoformat(finished)
    require(begin.utcoffset() is not None and end.utcoffset() is not None and begin <= end,
            'invalid registration/outcome chronology')
    trial = dict(id=registration['id'], snapshot=snapshot, started_utc=started,
                 outcome=outcome, finished_utc=finished)
    exported = load(output/'registry_report.json')
    require([t for t in exported['trials'] if t['id'] == registration['id']] == [trial],
            'export differs from registry')
    require(exported['admission_ready'] is False and exported['trading_authorized'] is False,
            'unexpected admission claim')
    require(snapshot['artifacts'] == registration['artifacts'] and
            snapshot['metadata'] == registration['metadata'], 'registration mismatch')
    require(Path(snapshot['metadata']['output']).resolve() == output, 'output mismatch')
    require(registration['trading_authorized'] is False, 'unexpected trading claim')
    sources = {'code': 'research_runner.py', **{'code:'+n: n for n in
               ('__init__.py', 'trial_registry.py', 'comparison.py', 'historical.py', 'kernel.py')}}
    if 'code:closeout.py' in snapshot['artifacts']:
        sources['code:closeout.py'] = 'closeout.py'
    if snapshot['metadata'].get('terminal_closeout'):
        require('code:closeout.py' in sources, 'closeout code missing')
    roles = set(sources) | {'data', 'config'}
    mode = snapshot['metadata']['funding_mode']
    require(mode in ('CSV_RATES', 'ASSUMED_ZERO'), 'unknown funding mode')
    if mode == 'CSV_RATES':
        roles.add('funding')
    require(set(snapshot['artifacts']) == roles and set(registration['files']) == roles,
            'incomplete snapshot roles')

    def check(record, expected=None, parent=None):
        path = Path(record['path']).resolve()
        require(path.is_relative_to(output), 'artifact outside study')
        if expected is not None:
            require(path == expected.resolve(), 'unexpected artifact path')
        if parent is not None:
            require(path.parent == parent.resolve(), 'unexpected input directory')
        data = path.read_bytes()
        require(len(data) == record['bytes'] and hashlib.sha256(data).hexdigest() == record['sha256'],
                'artifact hash/size mismatch: '+path.name)

    for role, record in snapshot['artifacts'].items():
        require(registration['files'][role] == record['path'], 'registered path mismatch')
        if role in sources:
            check(record, output/'frozen/code/rebalance'/sources[role])
        else:
            check(record, parent=output/'frozen/inputs')
    names = {'comparison', 'execution_manifest'} | {arm+'_'+suffix for arm in ('candidate', 'hold')
                                                   for suffix in ('result', 'trades', 'equity')}
    require(set(outcome['evidence']) == names, 'incomplete result evidence')
    for name, record in outcome['evidence'].items():
        check(record, output/(name+'.json'))
    manifest = load(output/'execution_manifest.json')
    require(manifest['artifacts'] == snapshot['artifacts'] and
            manifest['metadata'] == snapshot['metadata'], 'execution manifest mismatch')
    require(manifest['settings'] == load(snapshot['artifacts']['config']['path']), 'settings mismatch')
    require(Path(manifest['executed_module']).resolve() == output/'frozen/code/rebalance/research_runner.py',
            'execution module mismatch')
    require(manifest['trading_authorized'] is False, 'unexpected execution trading claim')
    summary = load(output/'comparison.json')
    terminal = snapshot['metadata'].get('terminal_closeout', False)
    require(type(terminal) is bool and terminal == ('terminal_closeout' in summary), 'terminal mode mismatch')
    if terminal:
        with Path(snapshot['artifacts']['data']['path']).open(encoding='utf-8-sig', newline='') as stream:
            last = None
            for last in csv.DictReader(stream):
                pass
        require(last is not None, 'terminal candle missing')
        mark = number(last['close'])
        settings = manifest['settings']
        spread, slip = number(settings['spread_bps']), number(settings['slippage_bps'])
        step, rate = number(settings['strategy']['step']), number(settings['strategy']['fee_rate'])
        friction = (spread/2+slip)/10000
        require(mark > 0 and step > 0 and 0 <= rate < 1 and min(spread, slip) >= 0 and friction < 1,
                'invalid terminal costs')
    require(summary['mode'] == 'MATCHED_CANDLE_OPEN_MTM' and
            summary['admission_ready'] is False and summary['trading_authorized'] is False,
            'unexpected comparison/admission mode')
    arms = {}
    for arm in ('candidate', 'hold'):
        result, trades, curve = (load(output/(arm+'_'+suffix+'.json'))
                                 for suffix in ('result', 'trades', 'equity'))
        require(summary[arm] == result, 'summary result mismatch')
        require(result['terminal'] == 'OPEN_MTM', 'unsupported terminal convention')
        require(number(result['initial_equity']) == number(manifest['settings']['capital']), 'capital mismatch')
        require(number(result['net_mtm']) == number(result['equity'])-number(result['initial_equity']),
                'net/equity mismatch')
        require(len(curve) == result['candles'] and len(curve) >= 2, 'curve length mismatch')
        require(curve[0]['time'] == result['start']+59999 and curve[-1]['time'] == result['end'],
                'curve bounds mismatch')
        require(all(type(r['time']) is int and (i == 0 or r['time']-curve[i-1]['time'] == 60000)
                    and number(r['equity']).is_finite() for i, r in enumerate(curve)), 'curve timeline mismatch')
        require(number(curve[-1]['equity']) == number(result['equity']), 'terminal curve mismatch')
        require(sum((number(t['fee']) for t in trades), Decimal(0)) == number(result['fees']), 'fees mismatch')
        require(len(trades) == result['rebalances']+2, 'trade count mismatch')
        if terminal:
            exit = summary['terminal_closeout'][arm]
            require(exit['mode'] == 'SYNTHETIC_CANDLE_CLOSEOUT' and
                    exit['admission_ready'] is False and exit['trading_authorized'] is False, 'terminal claims')
            require(type(exit['time']) is int and exit['time'] == result['end'] == int(last['open_time'])+59999,
                    'terminal time mismatch')
            require(number(exit['mark_proxy']) == mark and number(exit['sell_price']) == mark*(1-friction) and
                    number(exit['buy_price']) == mark*(1+friction), 'terminal price mismatch')
            quantities = {s:number(result['quantities'][s]) for s in ('LONG','SHORT')}
            require(all(q >= 0 and q/step == (q/step).to_integral_value() for q in quantities.values()),
                    'terminal quantity/step mismatch')
            require([f['side'] for f in exit['fills']] == [s for s,q in quantities.items() if q],
                    'terminal fills missing/duplicate/order')
            fees = Decimal(0)
            for fill in exit['fills']:
                side, qty = fill['side'], quantities[fill['side']]
                price = mark*(1-friction if side == 'LONG' else 1+friction)
                require(type(fill['units']) is int and fill['units'] > 0 and fill['units']*step == qty and
                        number(fill['quantity']) == qty and number(fill['price']) == price and
                        number(fill['fee']) == qty*price*rate, 'terminal fill arithmetic mismatch')
                require(fill['kind'] == 'REDUCE' and fill['phase'] == 'TERMINAL' and
                        fill['time'] == exit['time'] and type(fill['time']) is int and
                        fill['id'] == 'synthetic-terminal-'+str(exit['time'])+'-'+side, 'terminal receipt mismatch')
                fees += number(fill['fee'])
            adjustment = quantities['LONG']*(mark*(1-friction)-mark)+quantities['SHORT']*(mark-mark*(1+friction))
            equity = number(exit['equity_flat'])
            expected = number(result['equity'])+adjustment-fees
            residual = equity-expected
            scale = max(abs(number(result['equity'])),abs(equity),abs(expected),Decimal(1)).adjusted()
            require(number(exit['equity_open']) == number(result['equity']) and
                    number(exit['price_adjustment']) == adjustment and number(exit['exit_fees']) == fees and
                    number(exit['formula_equity']) == expected and number(exit['decimal_bridge_residual']) == residual and
                    abs(residual) <= Decimal(8).scaleb(scale-getcontext().prec+1) and
                    number(exit['net_flat']) == equity-number(result['initial_equity']), 'terminal equity bridge mismatch')
            require(set(exit['quantities']) == {'LONG','SHORT'} and
                    all(number(q) == 0 for q in exit['quantities'].values()) and number(exit['margin']) == 0 and
                    number(exit['unrealized']) == 0, 'terminal state not flat')
            require(number(exit['total_fees']) == number(result['fees'])+fees and
                    number(exit['funding']) == number(result['funding']), 'terminal costs/funding mismatch')
            point = exit['terminal_curve_point']
            require(point['time'] == exit['time'] and type(point['time']) is int and
                    point['phase'] == 'TERMINAL' and number(point['equity']) == equity, 'terminal curve point mismatch')
            peak, drawdown = number(result['initial_equity']), Decimal(0)
            for value in [number(r['equity']) for r in curve]+[equity]:
                peak = max(peak,value)
                drawdown = max(drawdown,(peak-value)/peak*100)
            require(number(exit['max_close_plus_exit_drawdown_pct']) == drawdown, 'terminal drawdown mismatch')
        arms[arm] = result, trades, curve
    require(arms['candidate'][1][:2] == arms['hold'][1] and len(arms['hold'][1]) == 2, 'initial fills mismatch')
    require([r['time'] for r in arms['candidate'][2]] == [r['time'] for r in arms['hold'][2]], 'arm timelines mismatch')
    require(number(summary['delta_net_mtm']) == number(arms['candidate'][0]['net_mtm'])-
            number(arms['hold'][0]['net_mtm']), 'delta mismatch')
    if terminal:
        exits = summary['terminal_closeout']
        require(number(exits['delta_net_flat']) == number(exits['candidate']['net_flat'])-
                number(exits['hold']['net_flat']), 'terminal delta mismatch')
    if summary['cycle_definition'] == 'PORTFOLIO_FLAT_TO_FLAT':
        from .comparison import cycles
        from .historical import candles, funding_rows
        from .kernel import Config
        data = candles(snapshot['artifacts']['data']['path'])
        rates = funding_rows(snapshot['artifacts']['funding']['path']) if mode == 'CSV_RATES' else []
        expected_cycles = {arm:cycles(value[1],Config(**manifest['settings']['strategy']),value[0],rates,data,
            summary.get('terminal_closeout',{}).get(arm)) for arm,value in arms.items()}
        require(summary['cycles'] == expected_cycles and
                summary['profit_factor'] == expected_cycles['candidate']['profit_factor'], 'cycle/PF mismatch')
    else:
        require(summary['cycle_definition'] == 'NOT_APPROVED' and summary['profit_factor'] is None and
                'cycles' not in summary, 'unknown cycle definition')
    return dict(status='PASS', scope='EXPORTED_EVIDENCE_CONSISTENCY', trial_id=registration['id'],
                checked_snapshot_artifacts=len(roles), checked_evidence=len(names),
                replay_performed=False, oos_status='NOT_ESTABLISHED',
                terminal_closeout_validation='SEMANTIC_CONSISTENCY' if terminal else 'NOT_REQUESTED',
                admission_ready=False, trading_authorized=False,
                limitations=['local registry and files are not independently authenticated',
                             'no replay, economic model validation or completeness of all attempts',
                             'absolute original snapshot paths required; relocation unsupported'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--study', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        result = verify(args.database, args.study)
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error, ArithmeticError) as exc:
        result = dict(status='FAIL', reason=type(exc).__name__+': '+str(exc),
                      admission_ready=False, trading_authorized=False)
    if args.output:
        with args.output.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, indent=2)
    print(json.dumps(result, indent=2))
    if result['status'] != 'PASS':
        raise SystemExit(2)


if __name__ == '__main__':
    main()
