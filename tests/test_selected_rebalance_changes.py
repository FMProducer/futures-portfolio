import json
import ast
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from core.calculator import PortfolioCalculator

TARGETS = {'BASE_LONG': {'share': .333, 'leverage': 3}, 'BASE_SHORT': {'share': .333, 'leverage': 3}, 'VIRTUAL': {'share': .334, 'leverage': 1}}


def test_negative_tpv_preserves_actual_cash():
    calc = PortfolioCalculator({'TESTUSDT_LONG': 1}, 1, 0, 0,
                               long_entry_price=100, base_ticker='TESTUSDT', targets=TARGETS)
    assert calc.tpv == -99
    assert float(calc.val_cash) == pytest.approx(-100 / 3)


def test_capped_original_request_is_partial():
    source = (Path(__file__).resolve().parents[1] / 'backtest/backtest_rebalance.py').read_text(encoding='utf-8')
    record = next(node for node in ast.walk(ast.parse(source))
                  if isinstance(node, ast.FunctionDef) and node.name == 'record')
    namespace = {'Decimal': Decimal, 'Dict': dict, 'Any': object, 'List': list,
                 'execution_records': [], 'action_side': lambda *args: 'SELL'}
    exec(compile(ast.Module(body=[record], type_ignores=[]), 'record', 'exec'), namespace)
    namespace['record']({'key': 'BASE_SHORT', 'diff_usdt': 48.54,
                         'requested_usdt': 212.68, 'approved_usdt': 48.54}, 'filled', Decimal('48.38'))
    assert namespace['execution_records'][0]['status'] == 'partial'
    assert namespace['execution_records'][0]['requested_usdt'] == 212.68

@pytest.mark.asyncio
async def test_initial_pair_waits_for_directional_guard(tmp_path):
    import pandas as pd
    from backtest.backtest_rebalance import run_backtest

    config = {'base_ticker': 'TESTUSDT', 'max_drawdown_limit': 200,
              'portfolios': [{'initial_capital': 1000, 'targets': TARGETS,
                              'safety_guards': {'max_price_velocity_pct': 100},
                              'trend_add_block': {'pct': .05, 'window_bars': 1}}]}
    config_path = tmp_path / 'config.json'
    config_path.write_text(json.dumps(config))
    prices = [100., 110., 110., 110.]
    pd.DataFrame({key: prices for key in ('open', 'high', 'low', 'close', 'volume')}).to_feather(tmp_path / 'TESTUSDT_live.feather')
    original = PortfolioCalculator.calculate_rebalance
    calls = 0

    def delay_first_tick(self, *args, **kwargs):
        nonlocal calls
        result = original(self, *args, **kwargs)
        if calls == 0:
            result['actions'] = []
        calls += 1
        return result

    with patch.object(PortfolioCalculator, 'calculate_rebalance', delay_first_tick):
        result = await run_backtest(str(config_path), str(tmp_path), days=1, quiet=True)
    assert result and result['accounting_ok']
    assert any(event['kind'] == 'trend_add_block' and event['bar'] == 1 for event in result['events'])
    initial = [fill for fill in result['fills'] if fill.get('initial_build') and fill['leg'] in ('BASE_LONG', 'BASE_SHORT')]
    assert {(fill['bar'], fill['leg']) for fill in initial} == {(2, 'BASE_LONG'), (2, 'BASE_SHORT')}
