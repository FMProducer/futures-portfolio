import json
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from core.main import rebalance_loop
from core.real_allocation import prepare
from core.executor import PortfolioExecutor


class EndReplay(BaseException):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize('unexplained_credit', [False, True])
async def test_shared_account_runtime_spends_only_worker_cash_and_books_actual_events(tmp_path, unexplained_credit):
    (tmp_path/'core').mkdir()
    paper = tmp_path/'core/paper_budget.sqlite'
    paper.write_bytes(b'unchanged-eight-paper-slots')
    config = json.loads((Path(__file__).resolve().parents[1]/'config.json').read_text())
    config.update(base_ticker='ZECUSDT', paper_mode=False, telegram_enabled=False,
                  max_bots=9, paper_mode_bots=8, funding_model='signed_actual')
    cp = tmp_path/'config.json'; cp.write_text(json.dumps(config))
    sp, wp = tmp_path/'core/real_state_ZECUSDT.json', tmp_path/'core/shadow_state_ZECUSDT.json'
    now = [1767225720.]
    start = now[0]
    actual, positions, orders = [D(6000)], {}, []
    paid = [False]

    async def usdt():
        margin = sum(abs(D(str(p['qty'])))*D(100)/3 for p in positions.values())
        return {'wallet': str(actual[0]), 'available': str(actual[0]-margin)}

    async def income(symbol, since, until):
        if not paid[0]:
            return []
        return [{'symbol': 'ZECUSDT', 'incomeType': 'FUNDING_FEE', 'asset': 'USDT',
                 'tranId': 1, 'time': int((start+60)*1000), 'income': '-.01'}]

    async def execute(**kwargs):
        assert kwargs['symbol'] == 'ZECUSDT' and not kwargs['reduce_only']
        qty = rounder.round_quantity(kwargs['qty'], kwargs['step_size'])
        side = kwargs['position_side']
        fee = qty*100*D('.0004')
        actual[0] -= fee
        positions[f'ZECUSDT_{side}'] = {'qty': float(qty if side == 'LONG' else -qty), 'entry_price': 100}
        orders.append(dict(kwargs, qty=qty))
        return {'status': 'SUCCESS', 'executed_qty': qty, 'avg_price': D(100), 'realized_pnl': D(0),
                'commission': fee, 'financials_confirmed': True,
                'result': {'orderId': len(orders), 'status': 'FILLED'}}

    async def candles(symbol, interval, limit):
        boundary = int(now[0])//60*60000
        return [[t, 100, 100, 100, 100, 1] for t in range(boundary-limit*60000, boundary+1, 60000)]

    connector = SimpleNamespace(api_key='shared-real-test-key', testnet=False, client=True,
        get_usdt_account=usdt, get_positions=AsyncMock(side_effect=lambda: dict(positions)),
        get_open_orders=AsyncMock(return_value=[]), get_funding_income=income,
        get_exchange_info=AsyncMock(return_value={'symbols': [{'symbol': 'ZECUSDT', 'filters': [
            {'filterType': 'LOT_SIZE', 'stepSize': '.001'}, {'filterType': 'MIN_NOTIONAL', 'notional': '5'}]}]}),
        get_hedge_mode=AsyncMock(return_value=True), set_leverage=AsyncMock(), set_margin_type=AsyncMock(),
        get_mark_prices=AsyncMock(return_value={'ZECUSDT': 100}),
        get_margin_ratio=AsyncMock(return_value={'margin_ratio': 1000, 'total_wallet_balance': 6000}),
        get_position_risk=AsyncMock(side_effect=lambda: dict(positions)), get_futures_klines=candles,
        get_order_book=AsyncMock(side_effect=lambda *a, **kw: {'bids': [['100', '100']], 'asks': [['100', '100']], '_fetched_at': now[0]}),
        get_commission_rates=AsyncMock(return_value={'takerCommissionRate': '.0004'}))
    rounder = PortfolioExecutor(connector)
    notifier = Mock(send_message=AsyncMock(), send_alert=AsyncMock(), close=AsyncMock())
    sleeps = [0]

    async def advance(seconds):
        sleeps[0] += 1
        if seconds == 86400 or sleeps[0] > 8:
            raise EndReplay()
        now[0] += seconds
        if now[0] >= start+60 and not paid[0]:
            actual[0] -= D('.01')
            if unexplained_credit:
                actual[0] += 1
            paid[0] = True

    with patch('core.real_allocation.time.time', side_effect=lambda: now[0]):
        await prepare(connector, tmp_path, 'ZECUSDT', config)
    logger = Mock()
    with patch('core.main.time.time', side_effect=lambda: now[0]), patch('core.main.asyncio.sleep', side_effect=advance), \
         patch('core.main.TelegramNotifier', return_value=notifier), patch('core.main.emit_signal'), \
         patch('core.main.self_kill_pm2', AsyncMock()), patch.object(PortfolioExecutor, 'execute_market_order', side_effect=execute):
        with pytest.raises(EndReplay):
            await rebalance_loop(connector, str(cp), str(sp), str(wp), logger, paper_mode_override=False)
    state, wallet = json.loads(sp.read_text()), json.loads(wp.read_text())
    assert len(orders) == 2, (logger.error.call_args_list, logger.warning.call_args_list, logger.critical.call_args_list)
    assert wallet['positions'] == {'ZECUSDT_LONG': 7.5, 'ZECUSDT_SHORT': 7.482}
    assert wallet['balance'] == pytest.approx(999.40072)
    assert state['funding_paid'] == .01 and len(state['funding_journal']) == 1
    assert state['initial_tpv'] == 1000 and wallet['balance'] < 1000
    assert paper.read_bytes() == b'unchanged-eight-paper-slots'
    if unexplained_credit:
        assert state['startup_blocked'] and 'unexplained cashflows' in state['startup_block_reason']
        assert D(str(wallet['balance']))+D('4999.99') == actual[0]-1
    else:
        assert not state.get('startup_blocked') and not logger.error.called, logger.error.call_args_list
        assert D(str(wallet['balance']))+D('4999.99') == actual[0]
        assert state['last_tpv'] == pytest.approx(wallet['balance'])
