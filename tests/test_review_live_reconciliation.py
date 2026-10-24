"""Live funding reconciliation contract tests; never connect to an exchange."""
import copy
from decimal import Decimal as D
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from core.main import _reconcile_funding_leg
from core.funding import FundingHalt, CloseLegState


def setup_case(eid='binance:BTCUSDT:123', closed='0', remaining='1'):
    close = CloseLegState('BASE_LONG', 'close_L', qty_closed=D(closed),
        qty_remaining=D(remaining), execution_id=eid)
    halt = FundingHalt(active=True, close_state={'BASE_LONG': close})
    wallet = {'balance': 100., 'positions': {'BTCUSDT_LONG': 1.}, 'long_entry_price': 100.}
    connector = MagicMock()
    connector.get_order_status = AsyncMock(return_value={'orderId': 123,
        'symbol': 'BTCUSDT', 'side': 'SELL', 'positionSide': 'LONG',
        'status': 'FILLED', 'origQty': '1', 'executedQty': '1', 'avgPrice': '105'})
    connector.get_order_trades = AsyncMock(return_value=[{'id': 1,
        'orderId': 123, 'symbol': 'BTCUSDT', 'side': 'SELL', 'positionSide': 'LONG',
        'qty': '1', 'price': '105', 'commission': '.1', 'commissionAsset': 'USDT',
        'realizedPnl': '5'}])
    connector.get_positions = AsyncMock(return_value={'BTCUSDT_LONG': {'qty': 1.}})
    return connector, halt, wallet


async def reconcile(connector, halt, wallet):
    return await _reconcile_funding_leg(connector, halt, 'BASE_LONG', wallet,
        'BTCUSDT', False, MagicMock())


@pytest.mark.asyncio
async def test_valid_fill_is_applied_only_once():
    connector, halt, wallet = setup_case()
    assert await reconcile(connector, halt, wallet) == 'handled'
    assert wallet['balance'] == 104.9
    assert wallet['positions']['BTCUSDT_LONG'] == 0
    before = copy.deepcopy(wallet)
    await reconcile(connector, halt, wallet)
    assert wallet == before


@pytest.mark.asyncio
async def test_api_failure_quarantines_without_mutating_wallet():
    connector, halt, wallet = setup_case()
    connector.get_order_status.side_effect = TimeoutError('Injected timeout')
    before = copy.deepcopy(wallet)
    await reconcile(connector, halt, wallet)
    assert halt.close_state['BASE_LONG'].status == 'quarantined'
    assert wallet == before


@pytest.mark.asyncio
async def test_unknown_order_identity_does_not_authorize_retry_from_position_only():
    connector, halt, wallet = setup_case(eid=None)
    assert await reconcile(connector, halt, wallet) == 'handled'
    assert halt.close_state['BASE_LONG'].status == 'quarantined'


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['empty_trades', 'wrong_symbol', 'wrong_side',
    'wrong_asset', 'duplicate_trades', 'overclose', 'trade_quantity', 'order_id', 'nan_average', 'orig_quantity', 'huge_pnl'])
async def test_unverified_fill_cannot_mutate_wallet(fault):
    connector, halt, wallet = setup_case()
    if fault == 'empty_trades':
        connector.get_order_trades.return_value = []
    elif fault == 'wrong_symbol':
        connector.get_order_status.return_value['symbol'] = 'ETHUSDT'
    elif fault == 'wrong_side':
        connector.get_order_status.return_value['side'] = 'BUY'
    elif fault == 'wrong_asset':
        connector.get_order_trades.return_value[0]['commissionAsset'] = 'BNB'
    elif fault == 'duplicate_trades':
        connector.get_order_trades.return_value *= 2
    elif fault == 'overclose':
        wallet['positions']['BTCUSDT_LONG'] = .5
    elif fault == 'trade_quantity':
        connector.get_order_trades.return_value[0]['qty'] = '.5'
    elif fault == 'order_id':
        connector.get_order_status.return_value['orderId'] = 999
    elif fault == 'nan_average':
        connector.get_order_status.return_value['avgPrice'] = 'NaN'
    elif fault == 'orig_quantity':
        connector.get_order_status.return_value['origQty'] = '.6'
    elif fault == 'huge_pnl':
        connector.get_order_trades.return_value[0]['realizedPnl'] = '1e1000'
    before = copy.deepcopy(wallet)
    await reconcile(connector, halt, wallet)
    assert wallet == before
    assert halt.close_state['BASE_LONG'].status == 'quarantined'


@pytest.mark.asyncio
async def test_late_fee_is_accounted_when_quantity_unchanged():
    connector, halt, wallet = setup_case(closed='1', remaining='0')
    wallet['positions']['BTCUSDT_LONG'] = 0
    halt.close_state['BASE_LONG'].realized_pnl = D('5')
    await reconcile(connector, halt, wallet)
    assert wallet['balance'] == 99.9
    assert halt.close_state['BASE_LONG'].commission == D('.1')


@pytest.mark.asyncio
async def test_real_startup_never_executes_funding_close_as_paper():
    import test_main_coverage as author
    state = author._make_state()
    state['funding_halt'] = {'active': True, 'reason': 'funding_shortfall', 'close_state': {
        leg: {'leg': leg, 'close_event_id': leg, 'status': 'pending'}
        for leg in ('BASE_LONG', 'BASE_SHORT')}}
    wallet = author._make_paper_state(positions={'BTCUSDT_LONG': .001, 'BTCUSDT_SHORT': .001},
        long_entry_price=60000, short_entry_price=60000)
    executor = MagicMock()
    executor.execute_actions = AsyncMock(return_value=[{'status': 'SKIPPED', 'qty': 0}])
    connector = MagicMock()
    connector.get_positions = AsyncMock(return_value={
        'BTCUSDT_LONG': {'qty': .001}, 'BTCUSDT_SHORT': {'qty': .001}})
    connector.get_exchange_info = AsyncMock(return_value={'symbols': [{
        'symbol': 'BTCUSDT', 'filters': [{'filterType': 'LOT_SIZE', 'stepSize': '.001'}]}]})
    with patch(f'{author.M}.self_kill_pm2', AsyncMock()), \
         patch(f'{author.M}.PortfolioExecutor', return_value=executor), \
         patch(f'{author.M}._reconcile_funding_leg', AsyncMock(return_value='reissue')), \
         patch('core.funding.save_funding_boundary'), \
         patch('core.funding.recover_funding_boundary', return_value=('ABSENT', None, None)):
        await author._run_rebalance(author._make_config(paper_mode=False), state, wallet,
            connector=connector, loop_limit=1)
    executor.execute_actions.assert_not_awaited()
    executor.execute_market_order.assert_not_called()


@pytest.mark.asyncio
async def test_late_fee_is_persisted_across_reload():
    connector, halt, wallet = setup_case(closed='1', remaining='0')
    wallet['positions']['BTCUSDT_LONG'] = 0
    halt.close_state['BASE_LONG'].realized_pnl = D('5')
    await reconcile(connector, halt, wallet)
    halt = FundingHalt.from_dict(halt.to_dict())
    await reconcile(connector, halt, wallet)
    assert wallet['balance'] == 99.9
    assert halt.close_state['BASE_LONG'].commission == D('.1')


@pytest.mark.asyncio
async def test_non_usdt_fee_facts_survive_quarantine():
    connector, halt, wallet = setup_case()
    connector.get_order_trades.return_value[0]['commissionAsset'] = 'BNB'
    before = copy.deepcopy(wallet)
    await reconcile(connector, halt, wallet)
    restored = FundingHalt.from_dict(halt.to_dict())
    assert restored.close_state['BASE_LONG'].fee_obligations == {'BNB': '0.1'}
    assert restored.close_state['BASE_LONG'].reconciliation_payload['trades']
    assert wallet == before


@pytest.mark.asyncio
async def test_real_startup_submits_prepared_intents_as_real_market_orders():
    import test_main_coverage as author
    state = author._make_state(virt_qty=0)
    state['funding_halt'] = {'active': True, 'reason': 'funding_shortfall', 'close_state': {
        leg: {'leg': leg, 'close_event_id': leg, 'status': 'pending',
              'qty_remaining': .001, 'client_order_id': leg, 'submission_state': 'ready'}
        for leg in ('BASE_LONG', 'BASE_SHORT')}}
    wallet = author._make_paper_state(positions={'BTCUSDT_LONG': .001, 'BTCUSDT_SHORT': .001},
        long_entry_price=60000, short_entry_price=60000)
    connector = MagicMock()
    connector.get_exchange_info = AsyncMock(return_value={'symbols': [{
        'symbol': 'BTCUSDT', 'filters': [{'filterType': 'LOT_SIZE', 'stepSize': '.001'}]}]})
    orders = {}
    async def submit(**kwargs):
        leg = 'BASE_' + kwargs['position_side']
        orders[kwargs['client_order_id']] = {'orderId': len(orders) + 1,
            'clientOrderId': kwargs['client_order_id'], 'symbol': 'BTCUSDT',
            'side': kwargs['side'], 'positionSide': kwargs['position_side'],
            'status': 'FILLED', 'origQty': '.001', 'executedQty': '.001', 'avgPrice': '60000'}
    async def query(symbol, client):
        return orders[client]
    async def trades(symbol, oid):
        order = next(value for value in orders.values() if value['orderId'] == oid)
        return [{'id': oid, 'orderId': oid, 'symbol': symbol, 'side': order['side'],
                 'positionSide': order['positionSide'], 'qty': '.001', 'price': '60000',
                 'commission': '.01', 'commissionAsset': 'USDT', 'realizedPnl': '0'}]
    connector.get_order_by_client_id = AsyncMock(side_effect=query)
    connector.get_order_trades = AsyncMock(side_effect=trades)
    connector.get_positions = AsyncMock(return_value={
        'BTCUSDT_LONG': {'qty': .001}, 'BTCUSDT_SHORT': {'qty': .001}})
    executor = MagicMock()
    executor.execute_market_order = AsyncMock(side_effect=submit)
    snapshots = []
    def save(sp, wp, current, shadow, journal, halt):
        current['funding_halt'] = halt.to_dict()
        snapshots.append((copy.deepcopy(current), copy.deepcopy(shadow)))
    with patch(f'{author.M}.self_kill_pm2', AsyncMock()), \
         patch(f'{author.M}.PortfolioExecutor', return_value=executor), \
         patch('core.funding.save_funding_boundary', side_effect=save), \
         patch('core.funding.recover_funding_boundary', return_value=('ABSENT', None, None)):
        await author._run_rebalance(author._make_config(paper_mode=False), state, wallet,
            connector=connector, loop_limit=1)
    assert executor.execute_market_order.await_count == 2
    executor.execute_actions.assert_not_called()
    assert snapshots[-1][1]['balance'] == 99.98
    assert all(q == 0 for q in snapshots[-1][1]['positions'].values())
    assert snapshots[0][0]['funding_halt']['close_state']['BASE_LONG']['submission_state'] == 'submitting'


@pytest.mark.asyncio
async def test_partial_cumulative_fill_updates_only_increment():
    connector, halt, wallet = setup_case(closed='.4', remaining='.6')
    close = halt.close_state['BASE_LONG']
    close.commission, close.realized_pnl = D('.04'), D('2')
    wallet['positions']['BTCUSDT_LONG'] = .6
    connector.get_order_status.return_value.update(status='PARTIALLY_FILLED', executedQty='.8')
    connector.get_order_trades.return_value[0].update(qty='.8', commission='.08', realizedPnl='4')
    await reconcile(connector, halt, wallet)
    assert wallet['positions']['BTCUSDT_LONG'] == .2
    assert wallet['balance'] == 101.96
    before = copy.deepcopy(wallet)
    await reconcile(connector, halt, wallet)
    assert wallet == before
    assert close.qty_remaining == D('.2')


@pytest.mark.asyncio
async def test_late_realized_pnl_is_applied_without_quantity_change():
    connector, halt, wallet = setup_case(closed='1', remaining='0')
    wallet['positions']['BTCUSDT_LONG'] = 0
    close = halt.close_state['BASE_LONG']
    close.commission, close.realized_pnl = D('.1'), D('4')
    await reconcile(connector, halt, wallet)
    assert wallet['balance'] == 101
