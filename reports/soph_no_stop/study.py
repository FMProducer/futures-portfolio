"""Bounded stop-mechanism experiments, without parameter optimization."""
import asyncio
import copy
import hashlib
import json
import logging
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backtest.backtest_rebalance import run_backtest

P = Path(__file__).parent
BASE = json.loads((ROOT / 'reports/soph_audit/baseline_config.json').read_text())

async def main():
    summary = {}
    for name, reserve in [('no_tpv_stop_reserve5000', 5000), ('no_tpv_stop_reserve0', 0)]:
        config = copy.deepcopy(BASE)
        config['equity_trailing_stop_pct'] = 0
        config['max_replace_per_cycle'] = 0
        config['portfolios'][0]['cash_reserve_usdt'] = reserve
        config['portfolios'][0]['paper_account_free_margin'] = reserve
        config_path = P / f'{name}_config.json'
        config_path.write_text(json.dumps(config, indent=2))
        print(f'START {name}', flush=True)
        result = await run_backtest(str(config_path), str(ROOT / 'data'),
            ticker_override='SOPHUSDT', days=365, quiet=True)
        assert result is not None, name
        curve = result.pop('equity_curve')
        (P / f'{name}_equity.json').write_text(json.dumps(curve))
        (P / f'{name}_result.json').write_text(json.dumps(result, indent=2))
        summary[name] = {k: result.get(k) for k in ['status', 'profit_pct', 'max_dd_pct',
            'account_max_dd_pct', 'final_tpv', 'cycles', 'trailing_stops', 'liquidations',
            'commission_paid', 'accounting_ok', 'accounting_residual', 'data_window']}
        summary[name]['min_tpv'] = min(curve)
        summary[name]['min_account_equity'] = min(curve) + reserve
        summary[name]['events'] = dict(Counter(e['kind'] for e in result['events']))
        summary[name]['cooldown_reasons'] = dict(Counter(e.get('reason') for e in result['events']
                                                       if e['kind'] == 'cooldown_start'))
        (P / 'annual_summary.json').write_text(json.dumps(summary, indent=2))
        print(f'FINISHED {name}: {summary[name]}', flush=True)

asyncio.run(main())
