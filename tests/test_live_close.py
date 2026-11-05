import json
from copy import deepcopy
from unittest.mock import AsyncMock, Mock, patch

import pytest

from core.live_close import close_live_wallet


def setup(tmp_path):
    state = {'base_ticker': 'TESTUSDT', 'initial_tpv': 1000, 'started_at': 123}
    wallet = {'balance': 1000, 'positions': {'TESTUSDT_LONG': 1, 'TESTUSDT_SHORT': 0},
              'long_entry_price': 100, 'short_entry_price': 0}
    connector = Mock()
    connector.get_positions = AsyncMock(side_effect=[{'TESTUSDT_LONG': {'qty': 1}}, {}])
    connector.get_mark_prices = AsyncMock(return_value={'TESTUSDT': 110})
    targets = {'BASE_LONG': {'leverage': 3}, 'BASE_SHORT': {'leverage': 3}}
    args = (connector, state, wallet, 'TESTUSDT', targets, {'TESTUSDT': .001},
            str(tmp_path / 'state.json'), str(tmp_path / 'wallet.json'), 'equity_trailing_stop')
    return args, state, wallet


def confirmed(**changes):
    result = {'status': 'SUCCESS', 'type': 'LONG', 'symbol': 'TESTUSDT_LONG', 'side': 'SELL',
              'qty': 1, 'price': 109, 'trade_pnl': 9, 'commission': .0436,
              'financials_confirmed': True, 'execution_pending': False, 'reduce_only': True,
              'execution_id': 'binance:TESTUSDT:1'}
    result.update(changes)
    return result


@pytest.mark.asyncio
async def test_intent_precedes_submission_and_actual_financials_are_used(tmp_path):
    args, state, wallet = setup(tmp_path)

    async def execute(*call_args):
        saved = json.loads((tmp_path / 'state.json').read_text())
        assert saved['startup_blocked'] and saved['live_close']['status'] == 'pending'
        assert saved['live_close']['orders'][0]['client_order_id'].startswith('close-')
        return confirmed()

    with patch('core.live_close.PortfolioExecutor._execute_single_action', side_effect=execute):
        await close_live_wallet(*args)
    assert wallet['balance'] == pytest.approx(1008.9564)
    assert wallet['positions']['TESTUSDT_LONG'] == 0
    assert state['live_close']['status'] == 'complete' and not state['startup_blocked']
    assert state['last_tpv'] == wallet['balance']
    assert json.loads((tmp_path / 'wallet.json').read_text()) == wallet


@pytest.mark.asyncio
@pytest.mark.parametrize('result', [confirmed(status='ERROR'), confirmed(financials_confirmed=False),
                                  confirmed(execution_pending=True), confirmed(commission=-1),
                                  confirmed(symbol='OTHERUSDT_LONG'), confirmed(qty=2)])
async def test_failed_unknown_or_invalid_close_never_fabricates_accounting(tmp_path, result):
    args, state, wallet = setup(tmp_path)
    before = deepcopy(wallet)
    with patch('core.live_close.PortfolioExecutor._execute_single_action', AsyncMock(return_value=result)):
        with pytest.raises(ValueError):
            await close_live_wallet(*args)
    assert wallet == before
    assert state['startup_blocked'] and state['live_close']['status'] == 'pending'
    assert json.loads((tmp_path / 'wallet.json').read_text()) == before


@pytest.mark.asyncio
async def test_confirmed_partial_updates_only_actual_fill_and_blocks_resubmission(tmp_path):
    args, state, wallet = setup(tmp_path)
    execute = AsyncMock(return_value=confirmed(qty=.4, trade_pnl=3.6, commission=.01744))
    with patch('core.live_close.PortfolioExecutor._execute_single_action', execute):
        with pytest.raises(ValueError, match='Partial'):
            await close_live_wallet(*args)
        assert wallet['positions']['TESTUSDT_LONG'] == pytest.approx(.6)
        assert wallet['balance'] == pytest.approx(1003.58256)
        assert wallet['long_entry_price'] == 100
        with pytest.raises(ValueError, match='Pending'):
            await close_live_wallet(*args)
    assert execute.await_count == 1


@pytest.mark.asyncio
async def test_submission_exception_leaves_durable_intent_and_original_wallet(tmp_path):
    args, state, wallet = setup(tmp_path)
    before = deepcopy(wallet)
    with patch('core.live_close.PortfolioExecutor._execute_single_action', AsyncMock(side_effect=TimeoutError)):
        with pytest.raises(TimeoutError):
            await close_live_wallet(*args)
    assert wallet == before
    assert json.loads((tmp_path / 'state.json').read_text())['startup_blocked']


@pytest.mark.asyncio
async def test_exchange_shadow_mismatch_does_not_submit(tmp_path):
    args, state, wallet = setup(tmp_path)
    args[0].get_positions = AsyncMock(return_value={'TESTUSDT_LONG': {'qty': 2}})
    with patch('core.live_close.PortfolioExecutor._execute_single_action', AsyncMock()) as execute:
        with pytest.raises(ValueError, match='reconciliation required'):
            await close_live_wallet(*args)
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_exchange_confirmed_flat_close_is_durable_without_an_order(tmp_path):
    args, state, wallet = setup(tmp_path)
    wallet['positions']['TESTUSDT_LONG'] = 0
    args[0].get_positions = AsyncMock(return_value={})
    with patch('core.live_close.PortfolioExecutor._execute_single_action', AsyncMock()) as execute:
        await close_live_wallet(*args)
    execute.assert_not_awaited()
    assert state['live_close']['status'] == 'complete'
    assert json.loads((tmp_path / 'state.json').read_text())['live_close']['status'] == 'complete'
    assert wallet['balance'] == 1000


@pytest.mark.asyncio
@pytest.mark.parametrize('close_only', [False, True])
async def test_emergency_stop_preserves_capital_and_books_only_confirmed_close(tmp_path, close_only):
    from core.main import emergency_stop
    args, state, wallet = setup(tmp_path)
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'portfolios': [{'initial_capital': 1000, 'targets': args[4]}]}))
    (tmp_path / 'state.json').write_text(json.dumps(state))
    (tmp_path / 'wallet.json').write_text(json.dumps(wallet))
    args[0].get_exchange_info = AsyncMock(return_value={'symbols': [{'symbol': 'TESTUSDT',
        'filters': [{'filterType': 'LOT_SIZE', 'stepSize': '.001'}]}]})
    with patch('core.live_close.PortfolioExecutor._execute_single_action', AsyncMock(return_value=confirmed())):
        await emergency_stop(args[0], str(config), args[6], args[7], Mock(), ticker_override='TESTUSDT',
                             paper_mode=False, close_only=close_only)
    final, closed = json.loads((tmp_path/'state.json').read_text()), json.loads((tmp_path/'wallet.json').read_text())
    assert final['initial_tpv'] == 1000
    assert closed['balance'] == pytest.approx(1008.9564)
    assert not any(closed['positions'].values())
    assert final['live_close']['status'] == 'complete' and final['trailing_stop_triggered']


@pytest.mark.asyncio
async def test_real_liquidation_guard_requires_durable_context(tmp_path):
    from core.main import _handle_liquidation_guard
    args, state, wallet = setup(tmp_path)
    connector = args[0]
    notifier = Mock(send_alert=AsyncMock())
    with patch('core.live_close.close_live_wallet', AsyncMock()) as close:
        with pytest.raises(ValueError, match='durable'):
            await _handle_liquidation_guard('TESTUSDT_LONG', 1, 110, 15, 8, False,
                {}, wallet, connector, 'TESTUSDT', args[5], notifier, Mock())
        close.assert_not_awaited()
        with patch('core.main.emit_signal'):
            await _handle_liquidation_guard('TESTUSDT_LONG', 1, 110, 15, 8, False,
                {}, wallet, connector, 'TESTUSDT', args[5], notifier, Mock(),
                state=state, strategy_path=args[6], wallet_path=args[7], targets=args[4])
        close.assert_awaited_once_with(connector, state, wallet, 'TESTUSDT', args[4], args[5], args[6], args[7], 'LIQUIDATION')


@pytest.mark.asyncio
async def test_exact_full_close_quantity_survives_notional_float_rounding():
    from decimal import Decimal
    from core.executor import PortfolioExecutor
    connector = Mock(client=True)
    executor = PortfolioExecutor(connector)
    quantity = Decimal('1234567.123')
    action = dict(type='ORDER', symbol='TESTUSDT_LONG', base_symbol='TESTUSDT', position_side='LONG',
                  diff_usdt=-33377.123, qty=str(quantity), is_reduction=True, client_order_id='close-exact')
    with patch.object(executor, 'execute_market_order', AsyncMock(return_value={'status': 'ERROR'})) as submit:
        await executor._execute_single_action(action, .0270353, False,
            {'limit_order_enabled': False, 'min_notional_usdt': 0}, {'TESTUSDT': .001})
    assert submit.call_args.kwargs['qty'] == quantity
    action['diff_usdt'] = abs(action['diff_usdt'])
    with pytest.raises(ValueError, match='positive reduction'):
        await executor._execute_single_action(action, .0270353, False, {}, {'TESTUSDT': .001})
