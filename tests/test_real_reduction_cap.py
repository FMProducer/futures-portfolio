"""REAL reductions must obey the same available-position bound as PAPER."""
from copy import deepcopy
from decimal import Decimal as D

import pytest

from core.executor import PortfolioExecutor
from scripts.verify_real_runtime_parity import Exchange


def setup(leg='LONG'):
    targets = {'BASE_LONG': {'leverage': 3}, 'BASE_SHORT': {'leverage': 3}}
    exchange = Exchange('JCTUSDT', [.013878], [0], [0], 1000, 5000, targets)
    exchange.positions[leg] = [D('3776.513'), D('.00361')]
    wallet = dict(balance=1000, positions={f'JCTUSDT_{side}': float(exchange.positions[side][0])
                                         for side in ('LONG', 'SHORT')},
                  long_entry_price=.00361, short_entry_price=.00361)
    config = dict(min_notional_usdt=7, limit_order_enabled=False, targets=targets)
    action = dict(type='ORDER', key=f'BASE_{leg}', symbol=f'JCTUSDT_{leg}', base_symbol='JCTUSDT',
                  position_side=leg, diff_usdt=-228.25142417375542, leverage=3, is_reduction=True)
    return exchange, wallet, config, action


@pytest.mark.asyncio
@pytest.mark.parametrize('leg', ['LONG', 'SHORT'])
@pytest.mark.parametrize('requested', [D('1000'), D('16446.996')])
async def test_real_reduction_caps_quantity_without_mutating_snapshot(leg, requested):
    exchange, wallet, config, action = setup(leg)
    action['diff_usdt'] = -float(requested*D('.013878'))
    before = deepcopy(wallet)
    result = await PortfolioExecutor(exchange)._execute_single_action(
        action, .013878, False, config, {'JCTUSDT': .001}, wallet)
    expected = min(requested, D('3776.513'))
    assert result['status'] == 'SUCCESS' and result['financials_confirmed']
    assert D(str(result['qty'])) == expected
    assert exchange.positions[leg][0] == D('3776.513')-expected
    assert D(exchange.fills[0]['qty']) == expected
    assert wallet == before


@pytest.mark.asyncio
async def test_confirmed_real_reduction_updates_available_position_inside_batch():
    exchange, wallet, config, action = setup()
    before = deepcopy(wallet)
    results = await PortfolioExecutor(exchange).execute_actions(
        [action, dict(action)], .013878, False, config, {'JCTUSDT': .001}, wallet)
    assert results[0]['status'] == 'SUCCESS'
    assert results[1]['status'] == 'NO_ORDER' and results[1]['qty'] == 0
    assert len(exchange.fills) == 1 and exchange.positions['LONG'][0] == 0
    assert wallet == before


@pytest.mark.asyncio
async def test_real_reduction_without_snapshot_never_submits():
    exchange, _, config, action = setup()
    with pytest.raises(ValueError, match='REAL reduction without wallet'):
        await PortfolioExecutor(exchange)._execute_single_action(action, .013878, False, config, {'JCTUSDT': .001})
    assert not exchange.fills


@pytest.mark.asyncio
async def test_simulated_real_margin_includes_external_reserve():
    exchange = Exchange('JCTUSDT', [300.], [0], [0], 1000, 5000, {})
    exchange.positions['SHORT'] = [D('10'), D('100')]
    metrics = await exchange.get_margin_ratio()
    assert exchange.wallet == 6000
    assert metrics['total_margin_balance'] == 4000
    assert metrics['margin_ratio'] > 1.5
