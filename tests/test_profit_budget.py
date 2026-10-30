import json
from decimal import Decimal as D
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import profit_budget as pb
from core.calculator import PortfolioCalculator
from core.executor import PortfolioExecutor, financials_confirmed


def fill(identity, reduction, pnl=0, fee=0, qty=1, price=100, **extra):
    return dict(status='SUCCESS', execution_id=identity, type='LONG', symbol='TESTUSDT_LONG',
                side='SELL' if reduction else 'BUY', reduce_only=reduction, qty=qty,
                price=price, trade_pnl=pnl, commission=fee, **extra)


def test_only_confirmed_net_profit_survives_restart_and_is_not_credited_twice():
    book = {'balance': 1000000}
    assert pb.available(book) == 0
    pb.record_fill(book, fill('initial', False, fee=1), 3, initial_build=True)
    assert pb.balance(book) == -1
    sale = fill('sale', True, pnl=20, fee=1)
    pb.record_fill(book, sale, 3)
    assert pb.available(book) == 18
    book = json.loads(json.dumps(book))
    assert not pb.record_fill(book, sale, 3)
    assert pb.available(book) == 18
    with pytest.raises(ValueError, match='reconciliation'):
        pb.record_fill(book, dict(sale, trade_pnl=21), 3)
    pb.record_fill(book, fill('addition', False, fee=.5, qty=.45), 3)
    assert pb.available(book) == D('2.5')
    assert book['balance'] == 1000000
    assert not pb.ledger(book)['blocked']


def test_failure_and_returned_virtual_margin_do_not_create_profit():
    book = {}
    assert not pb.record_fill(book, dict(fill('failed', True, pnl=999), status='ERROR'), 3)
    pb.record_fill(book, dict(type='VIRTUAL_ORDER', status='SUCCESS', execution_id='release', diff_usdt=-500))
    assert pb.available(book) == 0
    pb.charge(book, D('2'))
    pb.record_fill(book, fill('sale', True, pnl=10, fee=1), 3)
    assert pb.available(book) == 7


def test_unconfirmed_or_overspent_fill_blocks_future_additions():
    book = {}
    pb.record_fill(book, fill('unknown', True, pnl=999, financials_confirmed=False), 3, require_confirmed=True)
    assert pb.available(book) == 0 and pb.ledger(book)['blocked']
    book = {}
    pb.record_fill(book, fill('sale', True, pnl=10), 3)
    pb.record_fill(book, fill('bad-fee', False, qty=.3, fee=2), 3)
    assert pb.balance(book) == -2 and pb.ledger(book)['blocked']


def test_calculator_does_not_buy_deficit_from_large_collateral_reserve():
    targets = {'BASE_LONG': {'share': .5, 'leverage': 3},
               'BASE_SHORT': {'share': .5, 'leverage': 3}, 'VIRTUAL': {'share': 0, 'leverage': 1}}
    calc = PortfolioCalculator({'TESTUSDT_SHORT': 300}, 2, 1000000, 0,
        short_entry_price=1, base_ticker='TESTUSDT', targets=targets)
    actions = calc.calculate_rebalance(targets, .04, .04, calc.tpv, fuse_enabled=False)['actions']
    assert not any(not a['is_reduction'] for a in actions)


def action(leg, reduction, amount):
    return dict(type='ORDER', key=f'BASE_{leg}', symbol=f'TESTUSDT_{leg}', base_symbol='TESTUSDT',
                position_side=leg, leverage=3, is_reduction=reduction, diff_usdt=-amount if reduction else amount)


@pytest.mark.asyncio
async def test_executor_spends_sale_profit_without_reusing_returned_margin_or_reserve():
    connector = MagicMock()
    connector.get_order_book = AsyncMock(return_value={'bids': [['120', '1000']], 'asks': [['120', '1000']]})
    executor = PortfolioExecutor(connector, 'TESTUSDT')
    cfg = {'min_notional_usdt': 1, 'targets': {'BASE_LONG': {'leverage': 3}, 'BASE_SHORT': {'leverage': 3}}}
    wallet = {'balance': 1000000, 'positions': {'TESTUSDT_LONG': 1, 'TESTUSDT_SHORT': 1},
              'long_entry_price': 100, 'short_entry_price': 100}
    results = await executor.execute_actions([action('LONG', True, 120), action('SHORT', False, 10000)],
        120, True, cfg, {'TESTUSDT': .001}, wallet, cash_budget=D('999000'))
    sale, addition = results
    assert sale['status'] == addition['status'] == 'SUCCESS'
    cost = D(str(addition['qty'])) * D(str(addition['price'])) / 3 + D(str(addition['commission']))
    assert 0 < cost <= D(str(sale['trade_pnl'])) - D(str(sale['commission']))
    assert wallet['balance'] == 1000000 and 'profit_budget' not in wallet


@pytest.mark.asyncio
@pytest.mark.parametrize('status,pending', [('ERROR', False), ('SUCCESS', True)])
async def test_failed_or_pending_sale_cannot_fund_addition(status, pending):
    connector = MagicMock()
    connector.get_order_book = AsyncMock(return_value={'bids': [['120', '1']], 'asks': [['120', '1']]})
    executor = PortfolioExecutor(connector, 'TESTUSDT')
    sale = dict(fill('sale', True, pnl=20, fee=.05), status=status, execution_pending=pending)
    execute = AsyncMock(return_value=sale)
    with patch.object(executor, '_execute_single_action', execute):
        results = await executor.execute_actions([action('LONG', True, 120), action('SHORT', False, 10000)],
            120, True, {'min_notional_usdt': 1}, {},
            {'balance': 1000000, 'positions': {'TESTUSDT_LONG': 1}, 'long_entry_price': 100},
            cash_budget=D('999000'))
    assert execute.await_count == 1 and results[1]['status'] == 'SKIPPED'


def test_live_profit_requires_complete_usdt_trade_facts():
    trade = dict(qty='1', realizedPnl='20', commission='.05', commissionAsset='USDT')
    assert financials_confirmed([trade], D('1'))
    assert not financials_confirmed([], D('1'))
    assert not financials_confirmed([trade], D('2'))
    assert not financials_confirmed([dict(trade, commissionAsset='BNB')], D('1'))


@pytest.mark.asyncio
async def test_persisted_budget_uncertainty_blocks_runtime_restart():
    import tests.test_main_coverage as author
    book = author._make_paper_state()
    book['profit_budget'] = {'balance': '1000', 'receipts': {}, 'blocked': True}
    with pytest.raises(ValueError, match='Profit budget requires reconciliation'):
        await author._run_rebalance(author._make_config(), author._make_state(), book, loop_limit=1)


@pytest.mark.asyncio
async def test_unfilled_additions_do_not_advance_cycles_or_fuse_reference(tmp_path):
    import pandas as pd
    from backtest.backtest_rebalance import run_backtest
    cfg = {'base_ticker': 'TESTUSDT', 'max_drawdown_limit': 200, 'min_notional_usdt': 7,
        'portfolios': [{'initial_capital': 1000, 'cash_reserve_usdt': 8900, 'targets': {
            'BASE_LONG': {'share': .5, 'leverage': 3}, 'BASE_SHORT': {'share': .5, 'leverage': 3},
            'VIRTUAL': {'share': 0, 'leverage': 1}}}]}
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(cfg))
    prices = [100+i*.01 for i in range(10)]
    pd.DataFrame({k: prices for k in ('open', 'high', 'low', 'close', 'volume')}).to_feather(tmp_path/'TESTUSDT_live.feather')
    original = PortfolioCalculator.calculate_rebalance
    references = []
    def forced(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        if not self.initial_build:
            references.append(self.last_rebalance_price)
            result['actions'] = [action('LONG', False, 100)]
        return result
    with patch.object(PortfolioCalculator, 'calculate_rebalance', forced):
        result = await run_backtest(str(path), str(tmp_path), days=1, quiet=True)
    assert result and result['accounting_ok']
    assert result['cycles']==len(result['rebalances'])==1
    assert len(references)==9 and set(references)=={D('100.0')}
