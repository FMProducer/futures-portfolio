import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
BASE = OUT.parent / 'targets_25_25_50'


async def run():
    variant = OUT / 'zec_yearly'
    variant.mkdir(exist_ok=True)
    manifest = json.loads((BASE / 'manifest.json').read_text())
    for name, digest in manifest['variants']['original']['sources'].items():
        source = BASE / 'original' / name
        assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
        target = variant / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    calc = (ROOT / 'core/calculator.py').read_text()
    calc = calc.replace('if self.tpv == 0:', 'if self.tpv <= 0:').replace(
        'self.val_cash = self.real_equity - (self.val_long - mtm_pnl_l) - (self.val_short - mtm_pnl_s)',
        'self.val_cash = self.tpv - (self.val_long + self.val_short + self.val_virt)')
    (variant / 'core/calculator.py').write_text(calc, encoding='utf-8')
    path = variant / 'core/profit_budget.py'
    old = "    if expense > available(book):\n        row[\"blocked\"] = True\n        row[\"reason\"] = \"confirmed_cost_exceeded_profit_budget\"\n"
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, ''), encoding='utf-8')
    path = variant / 'backtest.py'
    old = '                if not building:\n                    remaining_funds = min(remaining_funds, profit_budget.available(state.profit_book))'
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, '                if profit_budget.ledger(state.profit_book)["blocked"]:\n                    remaining_funds = Decimal(\'0\')'), encoding='utf-8')
    for name, digest in manifest['inputs'].items():
        assert hashlib.sha256((BASE / 'inputs' / name).read_bytes()).hexdigest() == digest
    config = json.loads((BASE / 'inputs/config.json').read_text())
    assert config['rebalance_share_trail_distance'] == .05800887342949769
    shutil.copy2(BASE / 'inputs/config.json', variant / 'config.json')
    (variant / 'data').mkdir(exist_ok=True)
    shutil.copy2(BASE / 'inputs/funding_ZECUSDT.json', variant / 'data/funding_ZECUSDT.json')
    hashes = {name: hashlib.sha256((variant / name).read_bytes()).hexdigest() for name in manifest['variants']['original']['sources']}
    (variant / 'provenance.json').write_text(json.dumps({'sources': hashes, 'inputs': manifest['inputs'],
        'baseline_commit': manifest['variants']['original']['commit'], 'unrelated_branch_changes_excluded': ['negative TPV preservation', 'CASH identity', 'paired initial guard', 'cooldown funding fix']}, indent=2), encoding='utf-8')
    os.chdir(variant)
    sys.path.insert(0, str(variant))
    import backtest
    result = await backtest.run_backtest(str(variant / 'config.json'), str(BASE / 'inputs'), days=365,
        ticker_override='ZECUSDT', since_utc='2025-10-20 00:00', until_utc='2026-10-19 23:59', quiet=True)
    assert result and result['accounting_ok'] and result['data_window']['bars'] == 525600
    (variant / 'result.json').write_text(json.dumps(result), encoding='utf-8')
    summary = {key: result.get(key) for key in ['profit_pct', 'max_dd_pct', 'cycles', 'trailing_stops',
        'liquidations', 'funding_halt_active', 'accounting_ok', 'funding_paid', 'commission_paid']}
    (variant / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    asyncio.run(run())
