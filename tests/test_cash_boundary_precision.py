"""Regression checks for exact designation admission and runtime persistence."""
import ast
from decimal import Decimal as D
import json
import math
from pathlib import Path
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.executor import PortfolioExecutor
from core.calculator import PortfolioCalculator
from test_main_coverage import M, _make_config, _make_state, _make_paper_state, _run_rebalance

ROOT = Path(sys.modules[M].__file__).resolve().parents[1]
SAMPLES = Path(__file__).parent / 'fixtures/cash_boundary_precision'
assert len(list(SAMPLES.glob('*.json'))) == 4, 'Expected all four captured monetary cases'


@pytest.mark.asyncio
@pytest.mark.parametrize('paper', [True, False])
async def test_virtual_admission_never_rounds_above_cash(paper):
    budget = D('39.519361346493886676647333')
    action = dict(type='VIRTUAL_ORDER', diff_usdt=40., is_reduction=False)
    executor = PortfolioExecutor(MagicMock())
    results = await executor.execute_actions([action], 1., paper_mode=paper,
        paper_state=dict(balance=100., positions={}), cash_budget=budget)
    assert results[0]['status'] == 'SUCCESS'
    assert D(str(results[0]['diff_usdt'])) <= budget
    assert D(str(results[0]['diff_usdt'])) > 0


@pytest.mark.parametrize('sample_path', sorted(SAMPLES.glob('*.json')))
def test_saved_designation_and_calculator_keep_cash_nonnegative(sample_path):
    sample = json.loads(sample_path.read_text(encoding='utf-8-sig'))
    wallet, state = sample['wallet'], sample['state']
    targets = sample['targets']
    margin = sum(D(str(wallet['positions'][f"{sample['ticker']}_{leg}"]))
                 * D(str(wallet[f'{leg.lower()}_entry_price'])) / D(str(targets[f'BASE_{leg}']['leverage']))
                 for leg in ('LONG', 'SHORT'))
    capacity = D(str(wallet['balance'])) - margin
    assert D(str(wallet['balance'])) - D(str(state['virt_debt'])) - margin < 0
    tree = ast.parse((ROOT/'core/main.py').read_text(encoding='utf-8'))
    branch = next(node for node in ast.walk(tree) if isinstance(node, ast.If)
                  and ast.unparse(node.test) == "res.get('type') == 'VIRTUAL_ORDER'")
    start = next(i for i, node in enumerate(branch.body)
                 if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == 'new_virt_debt')
    fragment = ast.Module(body=branch.body[start:start+4], type_ignores=[])
    context = dict(Decimal=D, math=math, state={'virt_debt': 0}, diff_usdt=capacity)
    exec(compile(ast.fix_missing_locations(fragment), '<candidate designation>', 'exec'), context)
    stored = context['state']['virt_debt']
    assert D(str(stored)) <= capacity
    calc = PortfolioCalculator(wallet['positions'], sample['calculator']['price'],
        D(str(wallet['balance'])) - D(str(stored)), state['virt_qty'],
        wallet['long_entry_price'], wallet['short_entry_price'], virt_debt=stored,
        targets=targets, base_ticker=sample['ticker'])
    assert calc.val_cash >= 0


@pytest.mark.asyncio
@pytest.mark.parametrize('paper', [True, False])
async def test_regular_virtual_transfer_records_actual_cash_violation(paper):
    config = _make_config(paper_mode=paper)
    state = _make_state(virt_debt=10., rebalance_cycles=10)
    wallet = _make_paper_state()
    action = dict(type='VIRTUAL_ORDER', diff_usdt=1., key='VIRTUAL', position_side='BOTH')
    calculation = dict(total_tpv=100., tpv=100., actions=[action], total_pnl_pct=0.)
    executor = MagicMock()
    executor.execute_actions = AsyncMock(return_value=[dict(
        type='VIRTUAL_ORDER', status='SUCCESS', execution_id='virtual:cash-regression',
        diff_usdt=1., side='BUY')])
    with patch(f'{M}.PortfolioExecutor', return_value=executor):
        saves, _, _ = await _run_rebalance(config, state, wallet, real_mode=not paper,
            calc_return=calculation, calc_val_cash=D('-.02'), loop_limit=3)
    assert any(value.get('cash_invariant_violation') for path, value in saves if path == 'state.json')
