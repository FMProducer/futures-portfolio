import copy
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import test_main_coverage as author
from futures_portfolio.core.calculator import PortfolioCalculator
from futures_portfolio.core.executor import PortfolioExecutor


@pytest.mark.asyncio
async def test_paper_cash_reserve_does_not_expand_initial_positions_or_become_virtual():
    config = author._make_config(equity_trailing_stop_activation_pct=9000.0, max_drawdown_limit=200.0)
    portfolio = config['portfolios'][0]
    portfolio.update(initial_capital=200, paper_initial_capital=200, cash_reserve_usdt=461,
                     siphoning_threshold_pct=1000, reinvestment_ratio=0)
    portfolio['targets'] = {
        'BASE_LONG': {'share': .5, 'leverage': 3},
        'BASE_SHORT': {'share': .5, 'leverage': 3},
        'VIRTUAL': {'share': 0, 'leverage': 1},
    }
    state = author._make_state(virt_qty=0, virt_debt=0, rebalance_cycles=0,
                               initial_tpv=200, reference_tpv=200, tpv_ath=200)
    paper = author._make_paper_state(balance=200)
    saves, _, _ = await author._run_rebalance(config, state, paper, loop_limit=2,
                                              calculator_class=PortfolioCalculator)
    final = [value for path, value in saves if path == 'paper_state.json'][-1]
    final_state = [value for path, value in saves if path == 'state.json'][-1]
    assert final_state['virt_debt'] == 0
    assert final_state.get('cash_invariant_violation') is None
    assert final['positions']['BTCUSDT_LONG'] * 60000 / 3 == pytest.approx(100, abs=21)
    assert final['positions']['BTCUSDT_SHORT'] * 60000 / 3 == pytest.approx(100, abs=21)
    assert final_state['initial_tpv'] == 200


@pytest.mark.asyncio
async def test_external_reserve_does_not_require_new_portfolio_funding():
    config = author._make_config()
    config['portfolios'][0].update(initial_capital=200, paper_initial_capital=200, cash_reserve_usdt=461)
    state = author._make_state(initial_tpv=200, reference_tpv=200, tpv_ath=200)
    paper = author._make_paper_state(balance=200)
    await author._run_rebalance(config, state, paper, loop_limit=1)


@pytest.mark.asyncio
async def test_real_paper_initial_restart_only_missing_leg():
    config = author._make_config(equity_trailing_stop_activation_pct=9000.0,
                                 max_drawdown_limit=200.0)
    portfolio = config['portfolios'][0]
    portfolio.update(initial_capital=1000.0, paper_initial_capital=1000.0,
                     siphoning_threshold_pct=1000.0, reinvestment_ratio=0.0)
    portfolio['targets'] = {
        'BASE_LONG': {'share': .33, 'leverage': 7},
        'BASE_SHORT': {'share': .33, 'leverage': 7},
        'VIRTUAL': {'share': .34, 'leverage': 1},
    }
    state = author._make_state(virt_qty=0.0, rebalance_cycles=0, initial_tpv=1000.0,
                               reference_tpv=1000.0, tpv_ath=1000.0)
    paper = author._make_paper_state(balance=1000.0)
    calls = []
    result_facts = []
    phase = {'reject_short': True}

    def factory(connector, *args, **kwargs):
        real = PortfolioExecutor(connector, *args, **kwargs)
        wrapper = MagicMock()

        async def execute(actions, price, paper_mode, cfg, sizes, ps, **budget_args):
            calls.append([a['key'] for a in actions])
            failed_phase = phase['reject_short']
            valid = [a for a in actions if not (failed_phase and a['key'] == 'BASE_SHORT')]
            results = await real.execute_actions(valid, price, paper_mode, cfg, sizes, ps, **budget_args)
            result_facts.extend(copy.deepcopy(results))
            iterator = iter(results)
            return [({'status': 'ERROR', 'message': 'review-injected rejection'}
                     if failed_phase and a['key'] == 'BASE_SHORT' else next(iterator))
                    for a in actions]

        wrapper.execute_actions = AsyncMock(side_effect=execute)
        return wrapper

    with patch(f'{author.M}.PortfolioExecutor', side_effect=factory):
        saves, _, _ = await author._run_rebalance(config, state, paper, loop_limit=3, calculator_class=PortfolioCalculator)
    pending_state = [value for path, value in saves if path == 'state.json'][-1]
    pending_paper = [value for path, value in saves if path == 'paper_state.json'][-1]
    assert pending_state['initial_build_pending']['missing'] == ['BASE_SHORT']
    long_qty = pending_paper['positions']['BTCUSDT_LONG']
    assert long_qty > 0 and pending_paper['positions']['BTCUSDT_SHORT'] == 0
    first_count = len(calls)
    phase['reject_short'] = False

    with patch(f'{author.M}.PortfolioExecutor', side_effect=factory):
        saves, _, _ = await author._run_rebalance(config, pending_state, pending_paper, loop_limit=3, calculator_class=PortfolioCalculator)
    final_state = [value for path, value in saves if path == 'state.json'][-1]
    final_paper = [value for path, value in saves if path == 'paper_state.json'][-1]
    assert calls[0] == ['BASE_LONG', 'BASE_SHORT']
    assert all(keys == ['BASE_SHORT'] for keys in calls[1:first_count])
    assert calls[first_count] == ['BASE_SHORT']
    assert final_paper['positions']['BTCUSDT_LONG'] == long_qty
    assert final_paper['positions']['BTCUSDT_SHORT'] > 0
    assert final_state['initial_build_pending'] is None
    assert final_state.get('cash_invariant_violation') is None
    v = final_state['virt_debt']
    calc = PortfolioCalculator(positions=final_paper['positions'], spot_price=60000.0,
        real_equity=final_paper['balance'] - v, virt_qty=final_state['virt_qty'],
        virt_debt=v, long_entry_price=final_paper['long_entry_price'],
        short_entry_price=final_paper['short_entry_price'], targets=portfolio['targets'],
        initial_capital=1000, base_ticker='BTCUSDT')
    assert abs(calc.val_cash) < Decimal('1e-9'), str(calc.val_cash)
    assert sum(f['type'] == 'LONG' and not f['reduce_only'] for f in result_facts) == 1, (
        'Paper restart cleared the first leg and opened LONG again; the first successful fill must survive')
    assert final_paper['balance'] == pytest.approx(
        1000 - sum(f['commission'] for f in result_facts), abs=0.00011)
