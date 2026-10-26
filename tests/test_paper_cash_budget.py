from decimal import Decimal as D
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from futures_portfolio.core.executor import PortfolioExecutor


def action(leg='LONG', amount=332.29024, reduction=False):
    return {'type': 'ORDER', 'key': f'BASE_{leg}', 'symbol': f'ZECUSDT_{leg}',
            'base_symbol': 'ZECUSDT', 'position_side': leg, 'leverage': 7,
            'diff_usdt': -amount if reduction else amount, 'is_reduction': reduction}


def setup_executor(price=1339.88):
    connector = MagicMock()
    connector.get_order_book = AsyncMock(return_value={'bids': [[str(price), '1']], 'asks': [[str(price), '1']]})
    return PortfolioExecutor(connector)


CFG = {'min_notional_usdt': 6, 'targets': {'BASE_LONG': {'leverage': 7}, 'BASE_SHORT': {'leverage': 7}}}
PAPER = {'balance': 1000, 'positions': {'ZECUSDT_LONG': 1, 'ZECUSDT_SHORT': 1},
         'long_entry_price': 1339.88, 'short_entry_price': 1339.88}


@pytest.mark.asyncio
async def test_incident_deficit_reserves_commission_before_rounding():
    executor = setup_executor()
    budget = D('47.4743358')
    results = await executor.execute_actions([action()], 1339.88, True, CFG,
        {'ZECUSDT': .001}, PAPER, cash_budget=budget)
    fill = results[0]
    assert fill['status'] == 'SUCCESS'
    assert fill['qty'] == .247
    expense = D(str(fill['qty'])) * D(str(fill['price'])) / 7 + D(str(fill['commission']))
    assert expense <= budget
    assert PAPER['balance'] == 1000


@pytest.mark.asyncio
async def test_two_expansions_share_one_budget_at_actual_price():
    executor = setup_executor(1400)
    budget = D('50')
    results = await executor.execute_actions([action(), action('SHORT')], 1300, True, CFG,
        {'ZECUSDT': .001}, PAPER, cash_budget=budget)
    expenses = sum((D(str(r['qty'])) * D(str(r['price'])) / 7 + D(str(r['commission']))
                   for r in results if r['status'] == 'SUCCESS'), D('0'))
    assert expenses <= budget
    assert results[1]['status'] == 'SUCCESS'
    assert results[1]['qty'] < results[0]['qty']


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['ERROR', 'SKIPPED'])
async def test_failed_reduction_creates_no_buying_budget(status):
    executor = setup_executor()
    execute = AsyncMock(return_value={'status': status})
    with patch.object(executor, '_execute_single_action', execute):
        results = await executor.execute_actions([action('SHORT', 300, True), action()],
            1339.88, True, CFG, {'ZECUSDT': .001}, PAPER, cash_budget=D('0'))
    assert execute.await_count == 1
    assert results[1]['status'] == 'SKIPPED'


@pytest.mark.asyncio
async def test_partial_reduction_credits_only_actual_margin_and_pnl_net_fee():
    executor = setup_executor(1400)
    original = executor._execute_single_action
    async def partial(a, *args):
        if a['is_reduction']:
            return {'status': 'SUCCESS', 'qty': .01, 'price': 1400, 'reduce_only': True,
                    'commission': .0056, 'trade_pnl': -.6012, 'type': 'SHORT'}
        return await original(a, *args)
    with patch.object(executor, '_execute_single_action', side_effect=partial):
        results = await executor.execute_actions([action('SHORT', 1000, True), action()],
            1400, True, CFG, {'ZECUSDT': .001}, PAPER, cash_budget=D('0'))
    actual_proceeds = D('.01') * D('1339.88') / 7 - D('.6012') - D('.0056')
    fill = results[1]
    assert fill['status'] == 'SUCCESS'
    cost = D(str(fill['qty'])) * D(str(fill['price'])) / 7 + D(str(fill['commission']))
    assert cost <= actual_proceeds


@pytest.mark.asyncio
async def test_normal_rebalance_unexpected_fee_records_cash_violation():
    import test_main_coverage as author
    config = author._make_config(equity_trailing_stop_activation_pct=9000, max_drawdown_limit=200)
    state = author._make_state()
    paper = author._make_paper_state(balance=100,
        positions={'BTCUSDT_LONG': .001, 'BTCUSDT_SHORT': .001},
        long_entry_price=60000, short_entry_price=60000)
    calc_result = {'total_tpv': 100, 'tpv': 100, 'total_pnl_pct': 0, 'total_pnl': 0,
        'share_long_pct': 0, 'share_short_pct': 0, 'share_virt_pct': 100, 'share_cash_pct': 0,
        'val_long': 0, 'val_short': 0, 'val_virt': 0, 'val_cash': 10,
        'actions': [dict(action(amount=10), symbol='BTCUSDT_LONG', base_symbol='BTCUSDT', side='BUY')]}
    executor = MagicMock()
    executor.execute_actions = AsyncMock(return_value=[{'status': 'SUCCESS', 'type': 'LONG',
        'symbol': 'BTCUSDT_LONG', 'side': 'BUY', 'qty': .001, 'price': 60000,
        'commission': 101, 'trade_pnl': 0, 'reduce_only': False, 'execution_id': 'paper:fee-fixture'}])
    with patch(f'{author.M}.PortfolioExecutor', return_value=executor):
        saves, _, _ = await author._run_rebalance(config, state, paper, loop_limit=1,
            calc_return=calc_result, calc_val_cash=-1)
    saved = [v for p, v in saves if p == 'state.json'][-1]
    assert saved['cash_invariant_violation']['cash'] == -1
    assert saved['cash_invariant_violation']['executions'][0]['commission'] == 101
