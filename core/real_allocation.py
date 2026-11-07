"""Exclusive REAL allocation backed by an observed shared-account wallet."""
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from core import funding


def account_key(connector):
    key = connector.api_key
    if not isinstance(key, str) or not key or key == 'YOUR_API_KEY':
        raise ValueError('REAL allocation requires identified exchange credentials')
    return hashlib.sha256((str(bool(connector.testnet)) + ':' + key).encode()).hexdigest()


def amount(value):
    try:
        value = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError('Invalid REAL allocation amount') from error
    if not value.is_finite():
        raise ValueError('Non-finite REAL allocation amount')
    return value


def claim(root, key, ticker, capital, reserve, wallet, available, snapshot_ms):
    capital, reserve, wallet, available = map(amount, (capital, reserve, wallet, available))
    if (capital <= 0 or reserve < 0 or wallet < capital+reserve or available < capital+reserve
            or type(snapshot_ms) is not int or snapshot_ms <= 0):
        raise ValueError('Insufficient confirmed account funds for REAL capital and shared reserve')
    proof = dict(id=uuid4().hex, account_key=key, ticker=ticker, capital=str(capital), reserve=str(reserve),
                 account_wallet=str(wallet), available=str(available), snapshot_ms=snapshot_ms)
    with sqlite3.connect(Path(root)/'core/real_budget.sqlite') as db:
        db.execute('PRAGMA synchronous=FULL')
        db.execute('CREATE TABLE IF NOT EXISTS allocations(id TEXT PRIMARY KEY, active INTEGER, proof TEXT, closed TEXT)')
        db.execute('CREATE UNIQUE INDEX IF NOT EXISTS one_real_owner ON allocations(active) WHERE active=1')
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM allocations WHERE active=1').fetchone():
            raise ValueError('REAL slot already allocated; reconcile the existing owner before handover')
        db.execute('INSERT INTO allocations VALUES(?,1,?,NULL)', (proof['id'], json.dumps(proof, sort_keys=True)))
    return proof


def validate(root, proof, ticker, key):
    if (not isinstance(proof, dict) or proof.get('ticker') != ticker or proof.get('account_key') != key
            or not (Path(root)/'core/real_budget.sqlite').is_file()):
        raise ValueError('REAL allocation identity or ledger missing')
    with sqlite3.connect(f"file:{(Path(root)/'core/real_budget.sqlite').as_posix()}?mode=ro", uri=True) as db:
        row = db.execute('SELECT proof FROM allocations WHERE id=? AND active=1', (proof.get('id'),)).fetchone()
    if not row or json.loads(row[0]) != proof:
        raise ValueError('REAL allocation is not the active confirmed owner')


def reconcile(root, source, state, wallet, actual_wallet, key):
    proof = source['allocation']
    validate(root, proof, state['base_ticker'], key)
    expected = (amount(proof['account_wallet']) + amount(wallet['balance']) - amount(proof['capital'])
                + amount(state.get('siphoning_reserve', 0)) + amount(source.get('reserve_offset', 0))
                - amount(state.get('funding_paid', 0)))
    if abs(amount(actual_wallet)-expected) > Decimal('.00000001'):
        raise ValueError('Shared REAL wallet has unexplained cashflows; reconciliation required')


async def retire(connector, root, actual_wallet):
    ledger = Path(root)/'core/real_budget.sqlite'
    if not ledger.exists():
        return
    with sqlite3.connect(f'file:{ledger.as_posix()}?mode=ro', uri=True) as db:
        row = db.execute('SELECT proof FROM allocations WHERE active=1').fetchone()
    if not row:
        return
    proof = json.loads(row[0])
    ticker = proof['ticker']
    sp, wp = Path(root)/f'core/real_state_{ticker}.json', Path(root)/f'core/shadow_state_{ticker}.json'
    if not sp.is_file() or not wp.is_file():
        raise ValueError('Existing REAL owner snapshot missing; reconcile before handover')
    state, wallet = json.loads(sp.read_text(encoding='utf-8')), json.loads(wp.read_text(encoding='utf-8'))
    source = state.get('live_funding') or {}
    if (source.get('allocation') != proof or source.get('blocked') or state.get('startup_blocked')
            or state.get('rebalance_inflight') or (wallet.get('profit_budget') or {}).get('blocked')
            or (state.get('funding_halt') or {}).get('active')
            or not state.get('trailing_stop_triggered') or state.get('live_close', {}).get('status') != 'complete'
            or not wallet.get('positions') or any(amount(q) != 0 for q in wallet['positions'].values())):
        raise ValueError('Existing REAL owner has no confirmed terminal close')
    validate(root, proof, ticker, account_key(connector))
    _, halt = funding.load_funding_state(state, config_path=str(Path(root)/'config.json'))
    journal = None
    reserve_after = None
    try:
        journal = await funding.poll_live_funding(connector, state, ticker, int(time.time()*1000), config_path=str(Path(root)/'config.json'))
        for event in journal.get_received():
            import asyncio
            receipt = await asyncio.to_thread(funding.settle_funding_reserve,
                str(Path(root)/'config.json'), 'real', ticker, event)
            result = funding.apply_funding_paper(event.signed_amount, wallet, state, receipt)
            reserve_after = receipt['current_reserve']
            journal.mark_applied(event.event_id)
            if result['shortfall']:
                raise ValueError('Closed REAL owner has an external funding reserve shortfall')
        if amount(state.get('virt_debt', 0)) < 0:
            raise ValueError('Closed REAL owner has a funding shortfall')
        state['last_tpv'] = float(amount(wallet['balance'])+amount(state.get('siphoning_reserve', 0)))
        state['last_profit'] = state['last_tpv']-float(state['initial_tpv'])
        state['total_pnl_pct'] = state['last_profit']/float(state['initial_tpv'])*100
        funding.save_funding_boundary(str(sp), str(wp), state, wallet, journal, halt)
        actual = await connector.get_usdt_account()
        if amount(actual['wallet']) != amount(actual_wallet):
            raise ValueError('Account changed during REAL handover')
        reconcile(root, source, state, wallet, actual['wallet'], account_key(connector))
    except Exception as error:
        if journal is None:
            journal, halt = funding.load_funding_state(state, config_path=str(Path(root)/'config.json'))
        source['blocked'] = str(error)
        state['startup_blocked'] = True
        funding.save_funding_boundary(str(sp), str(wp), state, wallet, journal, halt)
        raise
    with sqlite3.connect(ledger) as db:
        db.execute('BEGIN IMMEDIATE')
        closed = json.dumps(dict(state=state, wallet=wallet, account_wallet=actual['wallet'], closed_at=time.time()))
        result = db.execute('UPDATE allocations SET active=0,closed=? WHERE id=? AND active=1', (closed, proof['id']))
        if result.rowcount != 1:
            raise ValueError('REAL owner changed during handover')
    return reserve_after


async def prepare(connector, root, ticker, config):
    """Prepare a fresh allocation using reads only; no order or exchange transfer."""
    root = Path(root)
    if not isinstance(ticker, str) or not ticker.isascii() or not ticker.isalnum() or ticker != ticker.upper() or not ticker.endswith('USDT'):
        raise ValueError('REAL allocation requires a USDT futures symbol')
    strategy, shadow = root/f'core/real_state_{ticker}.json', root/f'core/shadow_state_{ticker}.json'
    if strategy.exists() or shadow.exists():
        raise ValueError('REAL opening preparation refuses to overwrite an existing snapshot')
    slots = config.get('max_bots', 10)-config.get('paper_mode_bots', 9)
    if slots != 1:
        raise ValueError('The accepted REAL allocation requires exactly one REAL slot')
    key = account_key(connector)
    before = await connector.get_usdt_account()
    positions = await connector.get_positions()
    if any(amount(p['qty']) != 0 for p in positions.values()):
        raise ValueError('Fresh REAL allocation requires a confirmed flat account')
    if await connector.get_open_orders():
        raise ValueError('Fresh REAL allocation refuses existing exchange orders')
    after = await connector.get_usdt_account()
    if amount(before['wallet']) != amount(after['wallet']):
        raise ValueError('Account changed during REAL opening snapshot')
    portfolio = config['portfolios'][0]
    capital, reserve = amount(portfolio['initial_capital']), amount(portfolio.get('cash_reserve_usdt', 0))
    virtual = capital*amount(portfolio['targets']['VIRTUAL']['share'])
    if not 0 <= virtual <= capital or capital <= 0 or reserve < 0 or amount(after['wallet']) < capital+reserve or amount(after['available']) < capital+reserve:
        raise ValueError('Insufficient confirmed account funds for REAL capital and shared reserve')
    reserve_after = await retire(connector, root, after['wallet'])
    if reserve_after is not None:
        reserve = amount(reserve_after)
    stamp = int(time.time()*1000)
    proof = claim(root, key, ticker, capital, reserve, after['wallet'], after['available'], stamp)
    state = dict(base_ticker=ticker, started_at=stamp/1000, initial_tpv=float(capital), reference_tpv=float(capital),
                 tpv_ath=float(capital), last_tpv=float(capital), last_profit=0., total_pnl_pct=0.,
                 rebalance_cycles=0, virt_qty=0., virt_debt=float(virtual),
                 siphoning_reserve=0.)
    wallet = dict(base_ticker=ticker, balance=float(capital), positions={f'{ticker}_LONG': 0., f'{ticker}_SHORT': 0.},
                  long_entry_price=0., short_entry_price=0., last_price=0.)
    state = funding.prepare_live_funding_snapshot(state, wallet, ticker, stamp, after['wallet'], positions,
                                          allocation=proof)
    journal, halt = funding.load_funding_state(state, config_path=str(Path(root)/'config.json'))
    funding.save_funding_boundary(str(strategy), str(shadow), state, wallet, journal, halt)
    return state, wallet
