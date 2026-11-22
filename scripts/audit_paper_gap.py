"""Snapshot and reconcile blocked PAPER sessions without changing their financial state."""
import asyncio
import hashlib
import importlib.util
import json
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.direct_network import direct_opener


def audit(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    config_bytes = (root / 'config.json').read_bytes()
    config = json.loads(config_bytes)
    if config.get('paper_mode') is not True or config.get('funding_model') != 'disabled':
        raise ValueError('This recovery requires PAPER mode with disabled funding')
    source = Path(__file__).resolve().parents[1] / 'core/backtest_rules.py'
    spec = importlib.util.spec_from_file_location('gap_rules', source)
    rules = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rules)
    output.mkdir(parents=True, exist_ok=False)
    with sqlite3.connect((root / 'core/paper_budget.sqlite').as_uri() + '?mode=ro', uri=True) as ledger:
        with sqlite3.connect(output / 'paper_budget.sqlite') as backup:
            ledger.backup(backup)
        allocations = {ticker: (Decimal(capital), released, session) for ticker, capital, released, session
                       in ledger.execute('SELECT ticker,capital,released,session FROM allocations')}
    opener = direct_opener()
    rows = []
    snapshots = {}
    for path in sorted((root / 'core').glob('paper_state_*.json')):
        ticker = path.stem.removeprefix('paper_state_')
        wallet_path = path.with_name('paper_shadow_' + ticker + '.json')
        if Path(str(path) + '.funding_intent').exists():
            raise ValueError(f'{ticker}: unresolved funding WAL intent')
        state_raw, wallet_raw = path.read_bytes(), wallet_path.read_bytes()
        state, wallet = json.loads(state_raw), json.loads(wallet_raw)
        snapshots[path] = state_raw
        snapshots[wallet_path] = wallet_raw
        (output / path.name).write_bytes(state_raw)
        (output / wallet_path.name).write_bytes(wallet_raw)
        metrics = rules.reconcile_paper_gap(ticker, state, wallet)
        capital, released, session = allocations[ticker]
        if released or session != str(state['started_at']) or abs(capital - Decimal(str(state['initial_tpv']))) > Decimal('.0001'):
            raise ValueError(f'{ticker}: allocation/session mismatch')
        quantities = {key: Decimal(0) for key in wallet['positions']}
        reconstructed_balance = Decimal(str(state['initial_tpv']))
        for receipt in wallet['share_fill_receipts'].values():
            quantity = Decimal(receipt['qty'])
            fee, pnl = Decimal(receipt['commission']), Decimal(receipt['trade_pnl'])
            if not all(value.is_finite() for value in (quantity, fee, pnl)) or quantity <= 0 or fee < 0:
                raise ValueError(f'{ticker}: invalid confirmed fill')
            reduced = receipt['reduce_only'] == 'True'
            if receipt['reduce_only'] not in ('True', 'False'):
                raise ValueError(f'{ticker}: invalid reduction flag')
            quantities[receipt['symbol']] += -quantity if reduced else quantity
            reconstructed_balance += pnl - fee
        for key, quantity in quantities.items():
            if abs(quantity - Decimal(str(wallet['positions'][key]))) > Decimal('1e-8'):
                raise ValueError(f'{ticker}: receipt/position mismatch for {key}')
        residual = reconstructed_balance - Decimal(str(wallet['balance']))
        if abs(residual) > Decimal('.0001'):
            raise ValueError(f'{ticker}: receipt/wallet balance mismatch: {residual}')
        params = urllib.parse.urlencode({'symbol': ticker, 'interval': '1m', 'limit': 150})
        with opener.open('https://fapi.binance.com/fapi/v1/klines?' + params, timeout=20) as response:
            candle_raw = response.read()
        (output / (ticker + '_klines.json')).write_bytes(candle_raw)
        candles = json.loads(candle_raw)

        class History:
            async def get_futures_klines(self, *args):
                return candles

        before_wallet = json.dumps(wallet, sort_keys=True)
        now_ms = int(time.time() * 1000)
        bars = asyncio.run(rules.strategy_minutes(History(), ticker, state, config, config['portfolios'][0],
                                                  now_ms, wallet, True))
        if not bars and now_ms % 60000 < rules.MINUTE_FINALIZE_MARGIN_MS:
            # Inside the ingestion-finalization margin: wait it out, then retry once.
            time.sleep((rules.MINUTE_FINALIZE_MARGIN_MS - now_ms % 60000) / 1000.0 + 0.25)
            bars = asyncio.run(rules.strategy_minutes(History(), ticker, state, config, config['portfolios'][0],
                                                      int(time.time() * 1000), wallet, True))
        if json.dumps(wallet, sort_keys=True) != before_wallet:
            raise ValueError(f'{ticker}: audit mutated wallet')
        rows.append(dict(ticker=ticker, status='reconciled', **metrics,
                         receipt_balance_residual=str(residual), positions=wallet['positions'],
                         started_at=state['started_at'], initial_tpv=state['initial_tpv'],
                         previous_bar_ms=state['strategy_bar_ms'], resume_bar_ms=bars[-1][0],
                         gap=state.get('strategy_gap_reconciliation'),
                         state_sha256=hashlib.sha256(state_raw).hexdigest(),
                         wallet_sha256=hashlib.sha256(wallet_raw).hexdigest()))
    expected = {ticker for ticker, (_, released, _) in allocations.items() if not released}
    if {row['ticker'] for row in rows} != expected:
        raise ValueError('Active allocations do not match the audited sessions')
    if any(path.read_bytes() != original for path, original in snapshots.items()):
        raise ValueError('Runtime snapshot changed during audit; do not deploy')
    if (root / 'config.json').read_bytes() != config_bytes:
        raise ValueError('Runtime configuration changed during audit; do not deploy')
    report = dict(status='reconciled_without_state_mutation', root=str(root),
                  policy='hold_positions_no_retroactive_trades', created_utc=datetime.now(timezone.utc).isoformat(),
                  config_sha256=hashlib.sha256(config_bytes).hexdigest(),
                  source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), bots=rows)
    (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    return report


if __name__ == '__main__':
    audit(sys.argv[1], sys.argv[2])
