"""Offline PAPER eligibility study; never places orders or changes runtime state."""
import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import sqlite3


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False), encoding='utf-8')


def validate_close(archive, ticker, capital, returned):
    state, wallet = archive['state'], archive['paper_state']
    count = state['rebalance_cycles']
    identity = state['started_at']
    assert state['base_ticker'] == wallet['base_ticker'] == ticker
    assert state['paper_close_complete'] and type(count) is int and count >= 0
    assert all(f'{ticker}_{leg}' in wallet['positions'] for leg in ('LONG', 'SHORT'))
    assert all(D(str(q)) == 0 for q in wallet['positions'].values())
    assert archive['close_event'] in (f'stop:{identity}:{count}', f'trailing:{identity}:{count}')
    receipts = wallet['share_fill_receipts']
    assert any(r.get('event_id') == archive['close_event'] for r in receipts.values())
    assert not any(state.get(k) for k in ('rebalance_inflight', 'initial_build_pending', 'startup_blocked', 'cash_invariant_violation'))
    assert not state.get('funding_halt', {}).get('active')
    assert abs(D(str(state['initial_tpv'])) - capital) < D('.00000002')
    assert abs(D(str(wallet['balance'])) + D(str(state.get('siphoning_reserve', 0))) - returned) < D('.00000002')
    return count


def qualification(rows, cumulative=True, minimum=15):
    by_ticker = defaultdict(list)
    for row in rows:
        by_ticker[row['ticker']].append(row)
    result = {}
    for ticker, sessions in sorted(by_ticker.items()):
        sessions = sorted(sessions, key=lambda x: x['started_at'])
        assert len({r['started_at'] for r in sessions}) == len(sessions)
        cycles = sum(r['cycles'] for r in sessions) if cumulative else sessions[-1]['cycles']
        net = sum(r['net'] for r in sessions)
        normalized = sum(r['net']/r['capital'] for r in sessions)
        result[ticker] = {'cycles': cycles, 'net': net, 'normalized_return': normalized,
            'eligible_upper_bound': cycles >= minimum and net > 0,
            'score': normalized/max(cycles, minimum)*math.log1p(cycles),
            'specifications': len({json.dumps(r['spec'], sort_keys=True) for r in sessions})}
    return result


def risk_samples(log, session):
    start, end = session['started_at'], session['observed_until']
    values = []
    for line in log.splitlines():
        match = re.search(r'Heartbeat: TPV=([\d.-]+).*?Bar=(\d+)', line)
        if not match:
            continue
        try:
            stamp = datetime.strptime(line[:19], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone(timedelta(hours=2))).timestamp()
        except ValueError:
            continue
        if start <= stamp <= end:
            values.append((stamp, float(match[1])))
    values = sorted(set(values))
    peak, dd = session['capital'], 0.
    for _, equity in values:
        peak = max(peak, equity)
        dd = max(dd, (peak-equity)/peak*100) if peak > 0 else dd
    gaps = [b[0]-a[0] for a,b in zip(values, values[1:])]
    return {'sampled_dd_lower_bound_pct': dd if values else None, 'heartbeats': len(values),
        'largest_gap_seconds': max(gaps, default=None),
        'first_sample_delay_seconds': values[0][0]-start if values else None,
        'last_sample_delay_seconds': end-values[-1][0] if values else None}



def observed_replay(snapshot, rows):
    events = []
    for row in rows:
        ticker, start, end = row['ticker'], row['started_at'], row['observed_until']
        path = snapshot/'core/logs'/f'paper_{ticker}.log'
        for line in path.read_text(encoding='utf-8', errors='replace').splitlines() if path.exists() else []:
            heartbeat = re.search(r'Heartbeat: TPV=([\d.-]+)', line)
            rebalance = re.search(r'Rebalance #(\d+) complete\. TPV: ([\d.-]+)', line)
            if not heartbeat and not rebalance:
                continue
            try:
                stamp = datetime.strptime(line[:19], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone(timedelta(hours=2))).timestamp()
            except ValueError:
                continue
            if start <= stamp <= end:
                equity = float(heartbeat[1]) if heartbeat else float(rebalance[2])
                events.append((stamp, ticker, start, equity-row['capital'], int(rebalance[1]) if rebalance else None, row))
        if row['closed']:
            events.append((end, ticker, start, row['net'], row['cycles'], row))
    observed, admissions, counts = {}, {'per_session':{}, 'cumulative':{}}, 0
    for stamp, ticker, start, net, cycles, row in sorted(events, key=lambda e:e[0]):
        key = ticker, start
        prior = observed.get(key)
        current = dict(row, net=net, cycles=max(cycles or 0, prior['cycles'] if prior else 0))
        observed[key] = current
        for mode in admissions:
            metrics = qualification(list(observed.values()), mode == 'cumulative')
            for candidate, metric in metrics.items():
                if metric['eligible_upper_bound']:
                    admissions[mode].setdefault(candidate, stamp)
        counts += 1
    return {'observations':counts, 'first_observed_eligibility':admissions,
        'scope':'Recorded heartbeat/rebalance and confirmed return events; missing intervals and execution costs are not reconstructed.'}


def study(snapshot):
    with sqlite3.connect((snapshot/'core/paper_budget.sqlite').resolve().as_uri()+'?mode=ro', uri=True) as db:
        events = db.execute('SELECT rowid,ts,ticker,kind,amount FROM events ORDER BY rowid').fetchall()
        allocations = db.execute('SELECT ticker,capital,released,session FROM allocations').fetchall()
        initial, cash, created = db.execute('SELECT initial,cash,created FROM account').fetchone()
    archives = [json.loads(p.read_text(encoding='utf-8-sig')) for p in (snapshot/'history').glob('paper_close_*.json')]
    pending, rows, timeline, used = {}, [], [], set()
    for event_id, stamp, ticker, kind, value in events:
        if kind in ('reserve', 'adopt'):
            assert ticker not in pending
            pending[ticker] = (D(value), stamp)
        elif kind == 'return':
            capital, opening = pending.pop(ticker)
            matches = [a for a in archives if a['state'].get('base_ticker') == ticker
                       and opening-5 <= a['state']['started_at'] <= a['timestamp'] <= stamp
                       and (ticker, a['state']['started_at']) not in used]
            assert len(matches) == 1, (ticker, event_id, len(matches))
            archive = matches[0]
            state = archive['state']
            cycles = validate_close(archive, ticker, capital, D(value))
            used.add((ticker, state['started_at']))
            rows.append({'ticker':ticker, 'started_at':state['started_at'], 'observed_until':stamp,
                'capital':float(capital), 'net':float(D(value)-capital), 'cycles':cycles,
                'spec':state.get('share_trail_spec'), 'closed':True})
            # Only completed sessions available by this event enter this historical lower-bound view.
            timeline.append({'at':stamp, 'per_session':qualification(rows, False), 'cumulative':qualification(rows)})
    closed_count = len(rows)
    for ticker, value, released, session in allocations:
        if released:
            continue
        state = json.loads((snapshot/'core'/f'paper_state_{ticker}.json').read_text(encoding='utf-8-sig'))
        assert str(state['started_at']) == session
        assert ticker in pending and D(value) == pending[ticker][0]
        rows.append({'ticker':ticker, 'started_at':state['started_at'], 'observed_until':state['last_update'],
            'capital':float(value), 'net':float(state['last_profit']), 'cycles':state['rebalance_cycles'],
            'spec':state.get('share_trail_spec'), 'closed':False})
    for row in rows:
        log = snapshot/'core/logs'/f"paper_{row['ticker']}.log"
        row.update(risk_samples(log.read_text(encoding='utf-8', errors='replace') if log.exists() else '', row))
    ranked = qualification(rows)
    latest = qualification(rows, False)
    cutoffs = sorted({r['sampled_dd_lower_bound_pct'] for r in rows if r['sampled_dd_lower_bound_pct'] is not None})
    sensitivity = [{'cutoff_pct':c, 'observed_sessions_above':sum(r['sampled_dd_lower_bound_pct'] is not None and r['sampled_dd_lower_bound_pct'] > c for r in rows)} for c in cutoffs]
    return {'budget_created':created, 'initial':float(initial), 'cash':float(cash),
        'closed_sessions':closed_count, 'closed_net':sum(r['net'] for r in rows if r['closed']),
        'open_reported_net':sum(r['net'] for r in rows if not r['closed']),
        'sessions':rows, 'per_session':latest, 'cumulative':ranked, 'closed_only_timeline':timeline,
        'risk_sensitivity':sensitivity, 'observed_replay':observed_replay(snapshot, rows),
        'limitations':['Open Net is state-marked, not executable BID/ASK after exit fees.',
            'Closed-only event timeline excludes then-open sessions; not a causal full swarm replay.',
            'Observed drawdown is sampled and cannot certify a risk limit.',
            'Historical order books, fee-accurate joint margin and scan/gate snapshots are missing.',
            'Share-trail specification alone does not prove complete strategy configuration identity.',
            'No counterfactual REAL PnL, execution, rotation superiority or second-slot safety is claimed.']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out, root = args.output, args.runtime
    out.mkdir(parents=True, exist_ok=False)
    snap = out/'snapshot'
    (snap/'core').mkdir(parents=True)
    with sqlite3.connect((root/'core/paper_budget.sqlite').resolve().as_uri()+'?mode=ro', uri=True) as src:
        with sqlite3.connect(snap/'core/paper_budget.sqlite') as dst:
            src.backup(dst)
    copied = []
    for pattern in ('history/paper_close_*.json', 'core/paper_state_*.json', 'core/logs/paper_*.log'):
        for source in root.glob(pattern):
            target = snap/source.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            copied.append(str(target.relative_to(out)))
    write(out/'manifest.json', {'captured_at_utc':datetime.now(timezone.utc).isoformat(),
        'runtime':str(root), 'snapshot_consistency':'SQLite online backup; state/log files captured sequentially; not a global atomic snapshot',
        'sha256':{str(p.relative_to(out)):hashlib.sha256(p.read_bytes()).hexdigest() for p in snap.rglob('*') if p.is_file()}})
    result = study(snap)
    write(out/'results.json', result)
    print(json.dumps({k:result[k] for k in ('closed_sessions','closed_net','open_reported_net')}))
    print(json.dumps({'per_session_candidates':[t for t,v in result['per_session'].items() if v['eligible_upper_bound']],
        'cumulative_candidates':[t for t,v in result['cumulative'].items() if v['eligible_upper_bound']]}))


if __name__ == '__main__':
    main()

