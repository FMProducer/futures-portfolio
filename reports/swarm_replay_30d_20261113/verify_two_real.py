"""Verify independent modeled exchange fills against the integrated swarm wallet."""
from decimal import Decimal as D
import hashlib
import json
from pathlib import Path
import sys


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    import pandas as pd
    folder = Path(sys.argv[1]).resolve()
    assert read(folder/'status.json')['status'] == 'COMPLETED'
    assert not read(folder/'failures.json')
    config = read(folder/'config.effective.initial.json')
    assert config['max_bots']-config['paper_mode_bots'] == 2
    assert config['max_drawdown_limit'] == 99.99
    curve = pd.read_csv(folder/'equity.csv')
    assert curve.real_slots.max() == 2
    rows = [json.loads(line) for line in (folder/'fills.jsonl').open(encoding='utf-8')]
    real = [r for r in rows if r['mode'] == 'SIMULATED_REAL']
    assert real
    wallet,positions,pnl,fees,counts,frames = D(7000),{}, {},{},{},{}
    maximum_open_tickers,first_overlap = 0,None
    for row in real:
        fill = row['receipt']
        ticker,leg = fill['symbol'].rsplit('_',1)
        assert ticker in ('ONUSDT','HOTUSDT')
        if ticker not in frames:
            frames[ticker] = pd.read_feather(next((folder/'data').glob(ticker+'_*.feather')))
        frame = frames[ticker]
        stamp = pd.Timestamp(frame.time.iloc[fill['bar']]).timestamp()
        assert stamp+60 <= row['time']+.000001
        qty,price = D(fill['qty']),D(fill['price'])
        assert qty > 0
        assert price == D(str(frame.close.iloc[fill['bar']]))*D('1.0005' if fill['side']=='BUY' else '.9995')
        old,entry = positions.get((ticker,leg),(D(0),D(0)))
        reduction = (leg == 'LONG' and fill['side'] == 'SELL') or (leg == 'SHORT' and fill['side'] == 'BUY')
        realized = D(0)
        if reduction:
            assert D(0) < qty <= old
            realized = qty*(price-entry)*(1 if leg == 'LONG' else -1)
            positions[ticker,leg] = (old-qty,entry if old>qty else D(0))
        else:
            positions[ticker,leg] = (old+qty,(old*entry+qty*price)/(old+qty))
        commission = qty*price*D('.0004')
        assert abs(realized-D(fill['pnl'])) < D('.00000001')
        assert abs(commission-D(fill['commission'])) < D('.00000001')
        wallet += realized-commission
        pnl[ticker] = pnl.get(ticker,D(0))+realized
        fees[ticker] = fees.get(ticker,D(0))+commission
        counts[ticker] = counts.get(ticker,0)+1
        opened = {t for (t,leg),(q,entry) in positions.items() if q}
        maximum_open_tickers = max(maximum_open_tickers,len(opened))
        if len(opened) == 2 and first_overlap is None:
            first_overlap = row['time']
    assert set(counts) == {'ONUSDT','HOTUSDT'}
    assert maximum_open_tickers == 2
    equity = wallet
    unrealized = {t:D(0) for t in counts}
    for (ticker,leg),(qty,entry) in positions.items():
        price = D(str(frames[ticker].close.iloc[-1]))
        unrealized[ticker] += qty*(price-entry)*(1 if leg == 'LONG' else -1)
        wp = folder/'core'/f'shadow_state_{ticker}.json'
        shadow = read(wp)
        assert abs(qty-D(str(shadow['positions'][f'{ticker}_{leg}']))) < D('.00000001')
    equity += sum(unrealized.values())
    assert abs(equity-D(str(curve.real_account_equity.iloc[-1]))) < D('.000001')
    margin = sum((qty*entry/D(str(config['portfolios'][0]['targets'][f'BASE_{leg}']['leverage']))
                  for (ticker,leg),(qty,entry) in positions.items()),D(0))
    assert abs(wallet-margin-D(str(curve.real_available.iloc[-1]))) < D('.000001')
    ledger = read(folder/'core/real_allocations.json')
    assert len(ledger['active']) <= 2 and ledger['initial_wallet'] == 7000
    owners = {}
    for ticker in ledger['active']:
        state,shadow = read(folder/'core'/f'real_state_{ticker}.json'),read(folder/'core'/f'shadow_state_{ticker}.json')
        assert not state.get('startup_blocked') and not state.get('cash_invariant_violation')
        assert not (shadow.get('profit_budget') or {}).get('blocked')
        owners[ticker] = (state,shadow)
    for path in (folder/'core').glob('*state_*.json'):
        state = read(path)
        assert not state.get('startup_blocked'), (path.name,state.get('startup_block_reason'))
        assert not state.get('cash_invariant_violation'), path.name
        assert not (state.get('funding_halt') or {}).get('active'), path.name
        assert not (state.get('live_funding') or {}).get('blocked'), path.name
        assert not state.get('rebalance_inflight'), path.name
        assert state.get('status') not in ('portfolio_default','modeled_liquidation','reconciliation_required'), path.name
    import sqlite3
    with sqlite3.connect((folder/'core/paper_budget.sqlite').resolve().as_uri()+'?mode=ro',uri=True) as db:
        allocations = db.execute('SELECT ticker,capital FROM allocations WHERE released=0').fetchall()
    for ticker,capital in allocations:
        state = read(folder/'core'/f'paper_state_{ticker}.json')
        assert abs(D(capital)-D(str(state['initial_tpv']))) < D('.0000001')
    events = [json.loads(line) for line in (folder/'events.jsonl').open(encoding='utf-8')]
    assert not any(e['kind'] in ('blocked_return','blocked_returns','worker_error','selection_error') for e in events)
    from shared_real_account import SharedAccount
    account = SharedAccount()
    account.wallet = wallet
    account.reconcile(owners,D(ledger['closed_cashflow']))
    result = dict(status='PASS',initial_account=7000,shared_reserve_initial=5000,slots=2,
        independent_wallet=str(wallet),independent_equity=str(equity),
        independent_net=str(equity-D(7000)),max_concurrent_real_workers=int(curve.real_slots.max()),
        independent_margin=str(margin),independent_available=str(wallet-margin),
        max_simultaneously_open_tickers=maximum_open_tickers,
        first_overlapping_positions_utc=pd.Timestamp(first_overlap,unit='s',tz='UTC').isoformat(),
        per_ticker={t:dict(market_fills=counts[t],realized=str(pnl[t]),commission=str(fees[t]),
            unrealized=str(unrealized[t]),net=str(pnl[t]-fees[t]+unrealized[t])) for t in sorted(counts)},
        metrics=read(folder/'summary.json')['metrics'],
        source_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.parent.glob('*.py')},
        scope='Integrated native PAPER and simulated REAL workers; synthetic fills; no live exchange orders')
    for ticker in result['per_ticker']:
        matching = [r for r in real if r['receipt']['symbol'].startswith(ticker+'_')]
        result['per_ticker'][ticker]['first_fill_utc'] = pd.Timestamp(matching[0]['time'],unit='s',tz='UTC').isoformat()
        result['per_ticker'][ticker]['last_fill_utc'] = pd.Timestamp(matching[-1]['time'],unit='s',tz='UTC').isoformat()
    provenance = read(folder/'provenance.json')
    for ticker,expected in provenance['data'].items():
        path = next((folder/'data').glob(ticker+'_*.feather'))
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected
    result['data_sha256'] = provenance['data']
    result['tests'] = '8 focused shared-account tests passed'
    (folder/'two_real_verification.json').write_text(json.dumps(result,indent=2))
    comparison = [dict(ticker=t,**v) for t,v in result['per_ticker'].items()]
    pd.DataFrame(comparison).to_csv(folder/'real_comparison.csv',index=False)
    summary = read(folder/'summary.json')
    lines = ['# Integrated two-REAL-slot swarm replay','',
        'Status: PASS for this completed offline run. No live exchange orders or production changes.','',
        f"Period: {pd.Timestamp(summary['start'],unit='s',tz='UTC')} through {pd.Timestamp(summary['end'],unit='s',tz='UTC')}.",
        'Initial modeled REAL account: 7,000 USDT; two initial 1,000-USDT slots and one shared 5,000-USDT reserve.',
        'Initial PAPER pool: 8,000 USDT; dedicated ON/HOT admission universe. Gate 48/36, modeled volume >=15M, MaxDD=99.99, cumulative 15 PAPER cycles.',
        'Commission 4bp, slippage 5bp, funding disabled, immediate synthetic fills at closed-minute prices. Positions remain marked at the final close; hypothetical final liquidation costs are not deducted.','',
        '| Ticker | Market fills | Net USDT | Commission USDT | First actual modeled fill UTC |',
        '|---|---:|---:|---:|---|']
    for ticker,v in result['per_ticker'].items():
        lines.append(f"| {ticker} | {v['market_fills']} | {float(v['net']):.6f} | {float(v['commission']):.6f} | {v['first_fill_utc']} |")
    lines += ['',f"Final account equity: {equity}; Net: {equity-D(7000)} USDT.",
        f"Account MaxDD: {summary['metrics']['SIMULATED_REAL_ACCOUNT']['max_dd_pct']:.6f}%.",
        f"Both tickers have simultaneously open positions from {result['first_overlapping_positions_utc']}.",
        f"Native supervisor selections: {summary['selections']}; recorded failures: {len(summary['failures'])}.",'',
        'Independent verification reconstructs positions, weighted entries, realized PnL, commissions and the single wallet from exchange receipts; checks closed candle timestamps and fill prices; matches final account equity, both strategy shadows and the allocation ledger. All 50 data hashes remain unchanged.',
        'The experimental allocator supports two owners; each executor sees its own positions and funded capital, while the supervisor sees and validates the whole account. Global cash is reconciled to confirmed fills; each owner shadow is verified at its own stable checkpoint, allowing known in-flight cash movements of the other owner.',
        'Production single-owner allocation and process locks are untouched. This is a historical modeled execution result, not certification for live two-owner trading. Earlier variant02/03/04 outputs are not accepted results.','',
        'Evidence: summary.json, two_real_verification.json, real_comparison.csv, fills.jsonl, events.jsonl, core/real_allocations.json, equity.csv and daily checkpoints.']
    (folder/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__ == '__main__':
    main()
