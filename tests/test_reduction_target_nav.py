import json
from decimal import Decimal as D, ROUND_DOWN
from pathlib import Path

import pytest

from core.calculator import PortfolioCalculator


@pytest.mark.parametrize('case', json.loads((Path(__file__).parent / 'fixtures/reduction_target_nav.json').read_text()))
def test_reduction_leaves_target_nav_after_costs_and_rounding(case):
    targets = {key: {'share': share, 'leverage': lev} for key, share, lev in
               [('BASE_LONG', .25, 3), ('BASE_SHORT', .25, 3), ('VIRTUAL', .5, 1)]}
    calc = PortfolioCalculator(**case['params'], targets=targets, min_notional=0,
                               commission_pct=.0004, slippage_pct=.0005)
    actions = calc.calculate_rebalance(targets, 0, 0, calc.tpv, fuse_enabled=False)['actions']
    action = next(a for a in actions if a['key'] == case['leg'] and a['is_reduction'])
    qty = D(str(case['params']['positions'][case['symbol']]))
    value = calc.val_long if case['leg'] == 'BASE_LONG' else calc.val_short
    order_qty = abs(D(str(action['diff_usdt']))) / calc.price
    assert 0 < order_qty < qty
    rounded_qty = (order_qty / D(case['step'])).to_integral_value(rounding=ROUND_DOWN) * D(case['step'])
    fill = calc.price * (D('0.9995') if case['leg'] == 'BASE_LONG' else D('1.0005'))
    cost_per_qty = abs(fill - calc.price) + fill * D('.0004')
    remaining = value * (1 - rounded_qty / qty)
    target = D('.25') * (calc.tpv - rounded_qty * cost_per_qty)
    tolerance = D(case['step']) * (value / qty - D('.25') * cost_per_qty)
    assert abs(remaining - target) <= tolerance + D('1e-8')
    assert remaining > 0


@pytest.mark.parametrize('leg', ['BASE_LONG', 'BASE_SHORT'])
def test_zero_target_still_allows_full_close(leg):
    symbol = 'TEST_' + leg.replace('BASE_', '')
    targets = {key: {'share': 0, 'leverage': 3} for key in ['BASE_LONG', 'BASE_SHORT', 'VIRTUAL']}
    calc = PortfolioCalculator({symbol: 10}, 100, 1000, 0, long_entry_price=100,
                               short_entry_price=100, base_ticker='TEST', targets=targets)
    result = calc.calculate_rebalance(targets, 0, 0, calc.tpv, fuse_enabled=False)
    action = next(a for a in result['actions'] if a['key'] == leg)
    assert action['diff_usdt'] == -1000
