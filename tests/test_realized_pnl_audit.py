import json
from decimal import Decimal as D
from pathlib import Path
import re
import shutil
import subprocess

import pandas as pd
import pytest

from backtest.backtest_rebalance import BacktestState, MarketOrderSlippageSimulator, run_backtest


@pytest.mark.parametrize('amount,event_bar', [(10, 1), (-5, 1), (10, 3), (-5, 3)])
@pytest.mark.asyncio
async def test_funding_survives_cooldown_and_restart(tmp_path, amount, event_bar):
    times = pd.date_range('2026-01-01', periods=6, freq='min', tz='UTC')
    pd.DataFrame({'time': times, **{key: [100.] * 6 for key in ('open', 'high', 'low', 'close', 'volume')}}).to_feather(tmp_path / 'TESTUSDT_live.feather')
    journal = tmp_path / 'funding.json'
    journal.write_text(json.dumps([{'event_id': 'test:cooldown', 'signed_amount': amount,
                                   'event_time': times[event_bar].timestamp(), 'status': 'received'}]))
    config = {'base_ticker': 'TESTUSDT', 'funding_model': 'signed_actual', 'funding_scenario': str(journal),
              'max_drawdown_limit': .1, 'max_drawdown_cooldown_days': 3 / 1440,
              'portfolios': [{'initial_capital': 1000, 'targets': {
                  'BASE_LONG': {'share': .333, 'leverage': 3},
                  'BASE_SHORT': {'share': .333, 'leverage': 3},
                  'VIRTUAL': {'share': .334, 'leverage': 1}}}]}
    config_path = tmp_path / 'config.json'
    config_path.write_text(json.dumps(config))
    result = await run_backtest(str(config_path), str(tmp_path), days=1, commission=.002, slippage=0, quiet=True)
    assert result and result['accounting_ok']
    stop = next(event for event in result['events'] if event['kind'] == 'cooldown_start')
    resume = next(event for event in result['events'] if event['kind'] == 'cooldown_end')
    assert stop['bar'] == 0 and resume['bar'] == 3
    assert resume['rebased'] == pytest.approx(stop['tpv'] + amount)
    if event_bar == 1:
        assert result['equity_curve'][event_bar] == pytest.approx(stop['tpv'] + amount)


@pytest.mark.parametrize('funding,expected', [(-10, 10), (5, -5)])
def test_dashboard_funding_credit_has_positive_contribution(funding, expected):
    template = (Path(__file__).resolve().parents[1] / 'scripts/dashboard_template.html').read_text(encoding='utf-8')
    row = re.search(r"\['Funding',[^\n]+\]", template).group()
    code = (f'const T={{funding:{funding}}}; function finite(x){{return x;}} '
            'function clsNum(x){return x < 0 ? "neg" : "pos";} '
            f'console.log(JSON.stringify({row}));')
    result = subprocess.run([shutil.which('node'), '-e', code], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)[1] == expected


def test_negative_short_realized_pnl_is_actual_execution_loss():
    qty, entry, mid = D('4.554'), D('237.2810563801054'), D('247.97')
    state = BacktestState(D(1000), 'TESTUSDT', D(1000) - qty * entry / 3,
                          pos_short=qty, short_entry_price=entry, s_lev=D(3))
    simulator = MarketOrderSlippageSimulator(.0004, .0005)
    fill = state.close_position_for_funding_shortfall('BASE_SHORT', qty, mid, simulator, 0,
                                                     trigger='equity_trailing_stop')
    assert fill['realized_pnl'] == pytest.approx(-49.242076935)
    assert float(state.val_cash) == pytest.approx(1000 + fill['realized_pnl'] - state.fills[-1]['commission'])
