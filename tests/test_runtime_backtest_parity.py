import json
from contextlib import nullcontext
from decimal import Decimal as D
from unittest.mock import AsyncMock, Mock, patch

import pytest

from core.main import rebalance_loop


class EndReplay(BaseException):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize('opening,capital,terminal,stress,real', [(False, 1000, False, False, False), (True, 1000, False, False, False),
    (False, 900, False, False, False), (False, 1100, True, False, False), (True, 1000, False, True, False),
    (False, 1000, False, False, True), (False, 850, 'maxdd', False, False),
    (False, 1000, False, False, 'missing_long'), (False, 1000, False, False, 'missing_short'),
    (False, 1000, False, False, 'warning'), (False, 1100, True, False, True),
    (False, 850, 'maxdd', False, True), (False, 1000, False, False, 'missing_both'),
    (False, 1000, False, False, 'critical')])
async def test_paper_minute_clock_and_legacy_funding_replay(tmp_path, opening, capital, terminal, stress, real):
    cfg = json.loads((__import__('pathlib').Path(__file__).resolve().parents[1] / 'config.json').read_text())
    cfg.update(base_ticker='BTCUSDT', tickers=['BTCUSDT'], paper_mode=not real,
               telegram_enabled=False, funding_model='legacy_constant')
    if real:
        cfg['portfolios'][0]['cash_reserve_usdt'] = 5000
        cfg.update(max_bots=9, paper_mode_bots=8, funding_model='signed_actual')
    if terminal == 'maxdd':
        cfg['max_drawdown_limit'] = 10
    cfg_path, state_path, wallet_path = [tmp_path / name for name in ['config.json', 'state.json', 'wallet.json']]
    if real:
        (tmp_path/'core').mkdir()
        state_path, wallet_path = tmp_path/'core/state.json', tmp_path/'core/wallet.json'

    cfg_path.write_text(json.dumps(cfg))
    now = [1767225720.]
    import math
    prices = [100*(1 + .35*math.sin(i/17)) for i in range(300)] if stress else [100.]*3
    origin = int(now[0])*1000 - 60000
    state = {'base_ticker': 'BTCUSDT', 'started_at': now[0]-1, 'initial_tpv': 1000,
             'reference_tpv': 1000, 'tpv_ath': 1000, 'virt_debt': 500, 'virt_qty': 1,
             'rebalance_cycles': 1, 'siphoning_reserve': 0, 'funding_model': 'legacy_constant'}
    state.update(last_tpv=1000, last_profit=0, total_pnl_pct=0)
    wallet = {'base_ticker': 'BTCUSDT', 'balance': 1000, 'positions': {'BTCUSDT_LONG': 7.5, 'BTCUSDT_SHORT': 7.5},
              'long_entry_price': 100, 'short_entry_price': 100, 'last_price': 100}
    if opening:
        state['rebalance_cycles'] = 0
        wallet.update(positions={'BTCUSDT_LONG': 0, 'BTCUSDT_SHORT': 0}, long_entry_price=0, short_entry_price=0)
    elif capital != 1000:
        state.update(initial_tpv=capital, reference_tpv=capital, tpv_ath=capital, virt_debt=capital/2)
        wallet.update(balance=capital, positions={'BTCUSDT_LONG': capital*.0075, 'BTCUSDT_SHORT': capital*.0075})
    if terminal:
        state.update(initial_tpv=1000, reference_tpv=1000, tpv_ath=1200,
                     trailing_stop_violation_start=now[0]-180)
    if real:
        snapshot = int(now[0]*1000)-120000
        state.update(funding_model='signed_actual',
                     live_funding={'symbol': 'BTCUSDT', 'snapshot_ms': snapshot, 'boundary_ms': snapshot,
                                   'opening_wallet': '6000', 'blocked': ''})
        wallet['live_funding_snapshot'] = {'symbol': 'BTCUSDT', 'snapshot_ms': snapshot}
    state_path.write_text(json.dumps(state)); wallet_path.write_text(json.dumps(wallet))
    connector = Mock()
    connector.get_exchange_info = AsyncMock(return_value={'symbols': [{'symbol': 'BTCUSDT', 'filters': [
        {'filterType': 'LOT_SIZE', 'stepSize': '.001'}, {'filterType': 'MIN_NOTIONAL', 'notional': '5'}]}]})
    connector.get_hedge_mode = AsyncMock(return_value=True)
    connector.get_mark_prices = AsyncMock(return_value={'BTCUSDT': 100})
    connector.get_futures_prices = AsyncMock(return_value={'BTCUSDT': 100})
    connector.get_positions = AsyncMock(return_value={})
    connector.get_margin_ratio = AsyncMock(return_value={'margin_ratio': 100})
    connector.set_leverage = AsyncMock(); connector.set_margin_type = AsyncMock()
    connector.get_order_book = AsyncMock(return_value={'bids': [['100', '1000']], 'asks': [['100', '1000']]})
    if real:
        exchange_positions = {f'BTCUSDT_{leg}': {'qty': wallet['positions'][f'BTCUSDT_{leg}'], 'entry_price': 100}
                              for leg in ('LONG', 'SHORT')}
        connector.get_positions.return_value = exchange_positions
        connector.get_position_risk = AsyncMock(return_value=exchange_positions)
        connector.get_commission_rates = AsyncMock(return_value={'takerCommissionRate': '.0004'})
        connector.get_order_book.side_effect = lambda *a, **kw: {
            'bids': [['100', '1000']], 'asks': [['100', '1000']], '_fetched_at': now[0]}
        connector.get_funding_income = AsyncMock(return_value=[{
            'symbol': 'BTCUSDT', 'incomeType': 'FUNDING_FEE', 'asset': 'USDT',
            'tranId': 1, 'time': int(now[0]*1000)-1000, 'income': '-.01'}])
        if real == 'missing_both':
            exchange_positions.clear()
        elif real in ('missing_long', 'missing_short'):
            del exchange_positions[f'BTCUSDT_{real.split("_")[1].upper()}']
        if real == 'warning':
            p = cfg['portfolios'][0]
            connector.get_margin_ratio.return_value = {'margin_ratio': (p['margin_ratio_warning']+p['margin_ratio_critical'])/2}
        if real == 'critical':
            connector.get_margin_ratio.return_value = {'margin_ratio': 1.}

    if real:
        from core.real_allocation import claim, account_key
        connector.api_key, connector.testnet = 'runtime-fixture-key', True
        actual_balance = [D(5000) + D(str(capital)) - D('.01')]
        connector.get_usdt_account = AsyncMock(side_effect=lambda: {
            'wallet': str(actual_balance[0]), 'available': '5000'})
        state['live_funding']['allocation'] = claim(tmp_path, account_key(connector), 'BTCUSDT',
            1000, 5000, 6000, 6000, state['live_funding']['snapshot_ms'])
        state_path.write_text(json.dumps(state))

    async def candles(*args):
        boundary = int(now[0])//60*60000
        rows = []
        for t in range(boundary-args[-1]*60000, boundary+1, 60000):
            value = prices[min(len(prices)-1, max(0, (t-origin)//60000))]
            rows.append([t, value, value, value, value, 1, t+59999])
        return rows

    connector.get_futures_klines = AsyncMock(side_effect=candles)
    notifier = Mock(send_message=AsyncMock(), send_alert=AsyncMock(), close=AsyncMock())
    sleeps = [0]

    async def advance(seconds):
        sleeps[0] += 1
        if sleeps[0] > (len(prices)-1)*4 or seconds == 86400:
            raise EndReplay()
        now[0] += seconds

    logger = Mock()
    async def close_exchange(action, price, paper_mode, portfolio, steps, shadow, mid):
        symbol, leg = action['symbol'], action['position_side']
        qty = exchange_positions.pop(symbol)['qty']
        fill = 99.9 if leg == 'LONG' else 100.1
        actual_balance[0] += D(str(qty*(fill-100)*(1 if leg == 'LONG' else -1) - qty*fill*.0006))
        return dict(status='SUCCESS', type=leg, symbol=symbol, side='SELL' if leg == 'LONG' else 'BUY',
                    qty=qty, price=fill, commission=qty*fill*.0006,
                    trade_pnl=qty*(fill-100)*(1 if leg == 'LONG' else -1), reduce_only=True,
                    financials_confirmed=True, execution_pending=False, execution_id=f'exchange-close:{leg}')
    close_patch = patch('core.live_close.PortfolioExecutor._execute_single_action', side_effect=close_exchange) if real and (terminal or real == 'critical') else nullcontext()
    def confirmed_signal(*args, **kwargs):
        closed_wallet = json.loads(wallet_path.read_text())
        assert not any(closed_wallet['positions'].values())
    with patch('core.main.TelegramNotifier', return_value=notifier), patch('core.main.time.time', side_effect=lambda: now[0]), \
         patch('core.main.asyncio.sleep', side_effect=advance), patch('core.main.emit_signal', side_effect=confirmed_signal), \
         patch('core.main.self_kill_pm2', AsyncMock()), close_patch:
        if terminal or real == 'critical':
            await rebalance_loop(connector, str(cfg_path), str(state_path), str(wallet_path), logger)
        elif stress:
            try:
                await rebalance_loop(connector, str(cfg_path), str(state_path), str(wallet_path), logger)
            except EndReplay:
                pass
        else:
            with pytest.raises(EndReplay):
                await rebalance_loop(connector, str(cfg_path), str(state_path), str(wallet_path), logger)
    if real not in ('missing_long', 'missing_short', 'missing_both', 'critical'):
        assert not logger.error.called, logger.error.call_args_list
    final = json.loads(state_path.read_text())
    final_wallet = json.loads(wallet_path.read_text())
    if not terminal and not stress and real in (False, True, 'warning'):
        heartbeats = [call.args[0] for call in logger.info.call_args_list
                      if call.args and str(call.args[0]).startswith('Heartbeat:')]
        expected_bars = range(origin, final['strategy_bar_ms'] + 1, 60000)
        assert [int(message.rsplit('Bar=', 1)[1]) for message in heartbeats] == list(expected_bars)
        assert all('L:' in message and 'S:' in message and 'V:' in message and 'C:' in message
                   for message in heartbeats)
    if real:
        if terminal or real == 'critical':
            assert final_wallet['balance'] == pytest.approx(capital*.9976, abs=1e-9)
            assert not any(final_wallet['positions'].values())
            assert final['live_close']['status'] == 'complete' and final['trailing_stop_triggered']
            assert final['funding_paid'] == .01
            assert len(final_wallet['share_fill_receipts']) == 2
            assert final['total_pnl_pct'] == pytest.approx((final_wallet['balance']/1000-1)*100)
            return
        if real in ('missing_long', 'missing_short', 'missing_both'):
            assert final['startup_blocked']
            assert final_wallet == wallet
            assert final['last_tpv'] == 1000 and final['last_profit'] == 0
            connector.get_funding_income.assert_not_awaited()
            return
        assert final_wallet['balance'] == 1000
        assert final['virt_debt'] == 500
        assert final['funding_paid'] == .01
        assert final['last_tpv'] == pytest.approx(1000)
        assert final_wallet['positions'] == wallet['positions']
        assert 'legacy_funding_bar_ms' not in final
        assert len(final['funding_journal']) == 1
        if real == 'warning':
            assert any('Low margin ratio' in str(call) for call in notifier.send_message.call_args_list)
        return
    if terminal:
        from core.paper_close import close_wallet
        from core.backtest_rules import paper_legacy_funding
        paper_legacy_funding(wallet, state, cfg['portfolios'][0]['targets'], 'BTCUSDT', 100, final['strategy_bar_ms'])
        expected_wallet = close_wallet(wallet, 'BTCUSDT', 100, 'reference', '.0004', '.0005')
        assert final_wallet['balance'] == pytest.approx(expected_wallet['balance'], abs=1e-10)
        assert all(q == 0 for q in final_wallet['positions'].values())
        assert len(final_wallet['share_fill_receipts']) == 2
        assert final['paper_close_complete'] and final['trailing_stop_triggered']
        assert final['total_pnl_pct'] == pytest.approx((final_wallet['balance']/1000-1)*100)
        cooldown_key = 'max_drawdown_cooldown_days' if terminal == 'maxdd' else 'toxic_cooldown_days'
        assert final['restart_not_before'] == final['strategy_bar_ms']/1000 + int(cfg.get(cooldown_key, .1)*1440)*60
        return
    if not stress:
        assert final['strategy_bar_ms'] - final['strategy_origin_ms'] == 120000
    if opening:
        import pandas as pd
        from backtest.backtest_rebalance import run_backtest
        count = (final['strategy_bar_ms'] - final['strategy_origin_ms'])//60000 + 1
        sample = prices[:count]
        pd.DataFrame({'time': pd.date_range('2026-01-01', periods=count, freq='min', tz='UTC'),
                      'open': sample, 'high': sample, 'low': sample,
                      'close': sample, 'volume': [1.]*count}).to_feather(tmp_path / 'BTCUSDT_live.feather')
        oracle = await run_backtest(str(cfg_path), str(tmp_path), quiet=True)
        assert final_wallet['positions']['BTCUSDT_LONG'] == oracle['open_positions']['long']
        assert final_wallet['positions']['BTCUSDT_SHORT'] == oracle['open_positions']['short']
        assert float(final['funding_paid']) == pytest.approx(oracle['funding_paid'])
        assert final['last_tpv'] == pytest.approx(oracle['final_tpv'], abs=1e-9)
        assert final['rebalance_cycles'] == oracle['cycles']
        if stress:
            assert oracle['cycles'] > 1
        return
    expected = D(str(capital))*D('1.5')*D('.0002')/1440*3
    assert abs(D(str(final['funding_paid']))-expected) < D('1e-20')
    assert D(str(final_wallet['balance'])) == D(capital)
    assert abs(D(str(final['funding_reserve_balance']))-(D(str(cfg['portfolios'][0]['paper_account_free_margin']))-expected)) < D('1e-12')
    assert final_wallet['positions'] == wallet['positions']


@pytest.mark.asyncio
async def test_virtual_transfer_below_exchange_minimum_matches_backtest_cash_policy():
    from core.calculator import PortfolioCalculator
    from test_main_coverage import _make_config, _make_state, _make_paper_state, _run_rebalance
    config = _make_config(min_notional_usdt=7)
    portfolio = config['portfolios'][0]
    portfolio.update(initial_capital=1000, paper_initial_capital=1000, min_notional_usdt=7,
                     siphoning_threshold_pct=0, targets={
        'BASE_LONG': {'share': .25, 'leverage': 3}, 'BASE_SHORT': {'share': .25, 'leverage': 3},
        'VIRTUAL': {'share': .5, 'leverage': 1}})
    state = _make_state(virt_qty=1, virt_debt=437.65, initial_tpv=1000, reference_tpv=1000,
                        tpv_ath=1000, rebalance_cycles=1, started_at=123)
    wallet = _make_paper_state(balance=1000, positions={'BTCUSDT_LONG': 8.34, 'BTCUSDT_SHORT': 8.34},
                               long_entry_price=100, short_entry_price=100)
    saves, _, _ = await _run_rebalance(config, state, wallet, calculator_class=PortfolioCalculator,
                                      price=100, loop_limit=2)
    final = [v for p, v in saves if p == 'state.json'][-1]
    book = [v for p, v in saves if p == 'paper_state.json'][-1]
    assert final['virt_debt'] == pytest.approx(444)
    assert book['balance'] == 1000 and book['positions'] == wallet['positions']
    assert final['rebalance_cycles'] == 2
