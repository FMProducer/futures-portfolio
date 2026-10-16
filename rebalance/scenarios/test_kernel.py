from copy import deepcopy
from decimal import Decimal as D

import pytest

from rebalance.kernel import Action, Config, Portfolio, nearest


def portfolio(capital='10000', fee='0'):
    return Portfolio(Config('.001', '5.3', '7', fee, '.7', '.7', '.09'), capital)


def enter(p, price='100'):
    actions = p.plan_entry(price, price, price)
    assert all(a.kind == 'ADD' for a in actions)
    for a in actions:
        p.fill('entry-'+a.side, a, price,
               a.units*p.config.step*D(price)*p.config.fee_rate, price)
        p.legs[a.side].initial_units = a.units


def test_25_25_50_is_not_margin_partition():
    p = portfolio()
    enter(p)
    s = p.snapshot('100')
    assert s['equity'] == 10000
    assert [s['legs'][side]['decision'] for side in ('LONG', 'SHORT')] == [2500, 2500]
    assert s['remainder'] == 5000
    assert abs(s['margin']-D(5000)/7) < D('1e-20')
    assert p.wallet == 10000


def test_small_capital_entry_is_atomic_preflight():
    p = portfolio('14')
    assert p.plan_entry('100', '100', '100')[0].reason == 'entry_min_notional_or_step'
    assert not p.receipts and not any(l.units for l in p.legs.values())


@pytest.mark.parametrize('side,price', [('LONG', '110'), ('SHORT', '90')])
def test_realization_changes_equity_only_by_fee(side, price):
    p = portfolio()
    enter(p)
    before = p.snapshot(price)['equity']
    a = Action('REDUCE', side, p.legs[side].units)
    p.fill('close', a, price, '.2', price)
    assert p.snapshot(price)['equity'] == before-D('.2')
    assert p.legs[side].margin == p.legs[side].open_fees == 0


def test_fill_replay_and_conflict_do_not_duplicate_cash():
    p = portfolio()
    a = Action('ADD', 'LONG', 100)
    p.fill('fill-1', a, '100', '.01', '100')
    before = deepcopy(p)
    p.fill('fill-1', a, '100', '.01', '100')
    assert p == before
    with pytest.raises(ValueError, match='conflicting'):
        p.fill('fill-1', a, '100', '.02', '100')
    assert p == before


def test_shared_funding_can_use_cash_unassigned_to_leg():
    p = portfolio('100')
    p.fill('one', Action('ADD', 'LONG', 6000), '100', '0', '100')
    assert p.legs['LONG'].margin > 50
    before = deepcopy(p)
    with pytest.raises(ValueError, match='insufficient'):
        p.fill('two', Action('ADD', 'SHORT', 6000), '100', '0', '100')
    assert p == before


def test_liability_and_order_reserve_protect_funding():
    p = portfolio('100')
    p.liability, p.order_reserve = D(30), D(20)
    assert p.snapshot('100')['available'] == 50
    assert p.snapshot('100')['target'] == D('17.5')


def test_funding_and_external_flow_replayed_once():
    p = portfolio()
    p.cash_event('fund', '-2', 'FUNDING')
    p.cash_event('fund', '-2', 'FUNDING')
    p.cash_event('deposit', '10', 'EXTERNAL')
    assert p.wallet == 10008 and p.funding == -2


def test_constant_price_no_phantom_growth():
    p = portfolio(fee='.0004')
    enter(p)
    before = p.snapshot('100')['equity']
    for _ in range(100):
        assert p.next_action('100', '100', '100').kind == 'WAIT'
    assert p.snapshot('100')['equity'] == before == D(10000)-p.fees


def test_trailing_uses_old_peak_and_reduce_precedes_add():
    p = portfolio()
    enter(p)
    p.next_action('110', '110', '110')
    action = p.next_action('109', '109', '109')
    assert action.kind == 'REDUCE' and action.side == 'LONG'
    p.fill('reduce', action, '109', '0', '109')
    assert p.extrema['LONG']['peak'] == D('25.7')


def test_add_scales_deficit_and_consumes_only_own_trough():
    p = portfolio()
    enter(p)
    p.next_action('90', '90', '90')
    action = p.next_action('91', '91', '91')
    assert action.kind == 'REDUCE' and action.side == 'SHORT'
    p.fill('reduce-short', action, '91', '0', '91')
    action = p.next_action('91', '91', '91')
    assert action.kind == 'ADD' and action.side == 'LONG'
    old_short = deepcopy(p.extrema['SHORT'])
    p.fill('add', action, '91', '0', '91')
    assert p.extrema['LONG']['trough'] == D('24.37')
    assert p.extrema['SHORT'] == old_short


def test_nearest_tie_smaller_and_no_forced_minimum():
    assert nearest(D('1.5'), D(1), 10) == 1
    assert nearest(D('.4'), D(1), 10) == 0


@pytest.mark.parametrize('value', ['NaN', 'Infinity', True, 0.1])
def test_invalid_money_rejected(value):
    with pytest.raises(ValueError):
        portfolio(value)


def test_overclose_does_not_mutate():
    p = portfolio()
    enter(p)
    before = deepcopy(p)
    with pytest.raises(ValueError):
        p.fill('bad', Action('REDUCE', 'LONG', 999999), '100', '0', '100')
    assert p == before
