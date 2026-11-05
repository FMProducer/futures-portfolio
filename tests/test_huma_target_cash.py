from copy import deepcopy
from decimal import Decimal as D
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.calculator import PortfolioCalculator, select_tick_actions
from core.executor import PortfolioExecutor
from core import profit_budget

TARGETS = {'BASE_LONG': {'share': .25, 'leverage': 3},
           'BASE_SHORT': {'share': .25, 'leverage': 3}, 'VIRTUAL': {'share': .5, 'leverage': 1}}
VIRTUAL = D('500.606392')


def calculator(book, price):
    return PortfolioCalculator(book['positions'], price, D(str(book['balance'])) - VIRTUAL, 0,
        long_entry_price=book['long_entry_price'], short_entry_price=book['short_entry_price'],
        virt_debt=VIRTUAL, base_ticker='HUMAUSDT', targets=TARGETS, commission_pct=.0004, slippage_pct=.0005,
        profit_budget=profit_budget.balance(book))


def post_fill(book, fill):
    leg, qty, price = fill['type'], D(str(fill['qty'])), D(str(fill['price']))
    symbol = 'HUMAUSDT_' + leg
    old = D(str(book['positions'][symbol]))
    key = leg.lower() + '_entry_price'
    if fill['reduce_only']:
        book['positions'][symbol] = str(old - qty)
    else:
        book[key] = str((old * D(str(book[key])) + qty * price) / (old + qty))
        book['positions'][symbol] = str(old + qty)
    book['balance'] = str((D(str(book['balance'])) + D(str(fill['trade_pnl']))
                          - D(str(fill['commission']))).quantize(D('.0001')))
    profit_budget.record_fill(book, fill, 3)


@pytest.mark.asyncio
async def test_huma_reduction_and_next_tick_addition_reach_cash_funded_targets():
    book = {'balance': '999.4016', 'positions': {'HUMAUSDT_LONG': '33262', 'HUMAUSDT_SHORT': '33182'},
            'long_entry_price': '.022521', 'short_entry_price': '.022521',
            'profit_budget': {'balance': '-.5985541296', 'receipts': {}, 'blocked': False}}
    connector = MagicMock()
    executor = PortfolioExecutor(connector, 'HUMAUSDT')
    cfg = {'min_notional_usdt': 7, 'targets': TARGETS}
    for price, expected in [('.02024', 'SHORT'), ('.0202562', 'LONG')]:
        calc = calculator(book, price)
        actions = calc.calculate_rebalance(TARGETS, .04, .04, calc.tpv, fuse_enabled=False)['actions']
        actions = select_tick_actions(actions, False)
        assert len(actions) == 1 and actions[0]['position_side'] == expected
        connector.get_order_book = AsyncMock(return_value={'bids': [[price, '1000000']], 'asks': [[price, '1000000']]})
        untouched = deepcopy(book)
        result = (await executor.execute_actions(actions, float(price), True, cfg, {'HUMAUSDT': 1},
                    book, cash_budget=calc.val_cash))[0]
        assert book == untouched
        assert result['status'] == 'SUCCESS'
        post_fill(book, result)
        after = calculator(book, price)
        value = after.val_short if expected == 'SHORT' else after.val_long
        if expected == 'SHORT':
            assert abs(value / after.tpv - D('.25')) < D('.00002')
        else:
            assert 0 <= D('.25') * after.tpv - value < D('1')
        assert after.val_cash >= 0
        assert not profit_budget.ledger(book)['blocked']
    assert after.val_cash < D('1')
    assert D(str(result['qty'])) > 10000


@pytest.mark.parametrize('leg', ['LONG', 'SHORT'])
def test_addition_targets_post_cost_nav_and_preserves_cash_limit(leg):
    targets = {key: dict(value) for key, value in TARGETS.items()}
    calc = PortfolioCalculator({'TEST_' + leg: 2}, 100, 1000, 0, base_ticker='TEST',
        long_entry_price=100, short_entry_price=100, targets=targets,
        commission_pct=.0004, slippage_pct=.0005, profit_budget=-100)
    action = next(a for a in calc.calculate_rebalance(targets, .04, .04, calc.tpv, fuse_enabled=False)['actions']
                  if a['position_side'] == leg)
    qty = D(str(action['diff_usdt'])) / calc.price
    fill = calc.price * (D('1.0005') if leg == 'LONG' else D('.9995'))
    sign = D(1) if leg == 'LONG' else D(-1)
    old = calc.val_long if leg == 'LONG' else calc.val_short
    cost = qty * (abs(fill - calc.price) + fill * D('.0004'))
    added = qty * (fill / 3 + sign * (calc.price - fill))
    assert abs((old + added) / (calc.tpv - cost) - D('.25')) < D('1e-12')
    assert qty * fill * (D(1) / 3 + D('.0004')) <= calc.val_cash
