import ast
from copy import deepcopy
import json
from pathlib import Path
import random
from types import SimpleNamespace

import pytest

from core.backtest_rules import closed_minutes, filter_actions, maxdd_reached, strategy_minutes, paper_legacy_funding

ROOT = Path(__file__).resolve().parents[1]
TREE = ast.parse((ROOT / 'backtest/backtest_rebalance.py').read_text(encoding='utf-8'))
NODES = [n for n in ast.walk(TREE) if isinstance(n, ast.If) and n.col_offset == 12 and 972 <= n.lineno <= 1037]
assert len(NODES) == 4
ORACLE = compile(ast.Module(body=NODES, type_ignores=[]), 'backtest_01e4df4_guards', 'exec')


def oracle(actions, prices, cfg, memory, building, flat):
    safety = cfg['portfolios'][0]['safety_guards']
    trend = cfg.get('trend_guard', {})
    state = SimpleNamespace(log_event=lambda *a, **k: None, get_tpv_fast=lambda p: 1000,
                            initial_build_active=building, pos_long=0 if flat else 1, pos_short=0 if flat else 1)
    ns = dict(actions=actions, i=len(prices)-1, close_prices=prices, state=state, mid_price=1,
              portfolio_cfg=cfg['portfolios'][0], tg_trend_bars=int(safety['trend_lookback_bars']),
              tg_min_move_pct=trend.get('min_move_pct', safety['trend_min_move_pct'])/100,
              tg_eff_threshold=trend.get('eff_threshold', safety['trend_eff_threshold']),
              tg_nmg_bars=max(1, trend.get('net_move_window_sec', safety['net_move_window_sec'])//60),
              tg_nmg_pct=trend.get('net_move_block_pct', safety['net_move_block_pct'])/100,
              vg_cooldown_bars=memory.get('velocity_cooldown_bars', 0), vg_blocked_count=0,
              vg_window_bars=max(1, safety['velocity_window_sec']//60),
              vg_velocity_pct=safety['max_price_velocity_pct']/100, trend_add_blocks=0, tg_blocked_count=0)
    exec(ORACLE, ns)
    memory['velocity_cooldown_bars'] = ns['vg_cooldown_bars']
    return ns['actions']


@pytest.mark.parametrize('seed', range(8))
@pytest.mark.parametrize('building,flat', [(False, False), (True, True)])
def test_runtime_guards_match_committed_backtest_on_identical_paths(seed, building, flat):
    cfg = json.loads((ROOT / 'config.json').read_text())
    rng = random.Random(seed)
    prices = [100.]
    for _ in range(89):
        prices.append(prices[-1] * (1 + rng.uniform(-.04, .04)))
    expected_memory, actual_memory = {}, {}
    for end in range(1, len(prices)+1):
        actions = [{'key': 'BASE_LONG', 'is_reduction': False},
                   {'key': 'BASE_SHORT', 'is_reduction': False},
                   {'key': 'BASE_LONG', 'is_reduction': True}, {'key': 'VIRTUAL'}]
        if rng.random() < .2:
            actions = []
        expected = oracle(deepcopy(actions), prices[:end], cfg, expected_memory, building, flat)
        actual = filter_actions(deepcopy(actions), prices[:end], cfg, cfg['portfolios'][0],
                                actual_memory, building, flat)
        assert actual == expected
        assert actual_memory.get('velocity_cooldown_bars', 0) == expected_memory['velocity_cooldown_bars']


@pytest.mark.parametrize('rows', [[], [[60000, 0, 0, 0, 100]],
    [[0, 0, 0, 0, 100], [120000, 0, 0, 0, 100]],
    [[120000, 0, 0, 0, float('nan')]], [[120000, 0, 0, 0, 100]]*2])
def test_closed_minutes_reject_stale_gapped_duplicate_or_nonfinite_data(rows):
    with pytest.raises(ValueError):
        closed_minutes(rows, 180000)


def test_closed_minutes_excludes_open_candle():
    rows = [[t, 0, 0, 0, 100] for t in [60000, 120000, 180000]]
    assert closed_minutes(rows, 185000) == [(60000, 100.), (120000, 100.)]


def test_maxdd_matches_realized_equity_and_disabled_limit():
    assert maxdd_reached(850, -150, 1000, 1000, 10) == (False, 1000)
    assert maxdd_reached(850, 0, 1000, 1000, 10) == (True, 1000)
    assert maxdd_reached(-100, -1100, 1000, 1000, 100) == (False, 1000)
    assert maxdd_reached(1200, 0, 1000, 1000, 100) == (False, 1000)


@pytest.mark.asyncio
async def test_strategy_clock_uses_only_closed_bars_and_refuses_duplicate_or_missed_steps():
    from unittest.mock import AsyncMock, Mock
    cfg = json.loads((ROOT / 'config.json').read_text())
    connector = Mock(get_futures_klines=AsyncMock(return_value=[[t, 0, 0, 0, 100] for t in [60000, 120000, 180000]]))
    state = {}
    assert await strategy_minutes(connector, 'TEST', state, cfg, cfg['portfolios'][0], 185000) == [(120000, 100.)]
    state.update(strategy_origin_ms=120000, strategy_bar_ms=120000)
    assert await strategy_minutes(connector, 'TEST', state, cfg, cfg['portfolios'][0], 185000) == []
    connector.get_futures_klines.return_value = [[t, 0, 0, 0, 100] for t in [120000, 180000, 240000, 300000]]
    with pytest.raises(ValueError, match='Missed'):
        await strategy_minutes(connector, 'TEST', state, cfg, cfg['portfolios'][0], 305000)


@pytest.mark.parametrize('cash', ['0', '100', '-1'])
def test_paper_legacy_funding_matches_backtest_and_is_idempotent(cash):
    from decimal import Decimal as D
    from backtest.backtest_rebalance import BacktestState
    targets = {'BASE_LONG': {'leverage': 3}, 'BASE_SHORT': {'leverage': 3}}
    bt = BacktestState(initial_capital=D(1000), base_ticker='TEST', val_cash=D(cash),
                       pos_long=D(3), pos_short=D(3), long_entry_price=D(100), short_entry_price=D(100),
                       virt_debt=D(500))
    wallet = {'balance': str(D(700) + D(cash)), 'positions': {'TEST_LONG': 3, 'TEST_SHORT': 3},
              'long_entry_price': 100, 'short_entry_price': 100}
    state = {'virt_debt': 500}
    bt.pay_funding_legacy(D(600)*D('.0002')/1440)
    amount = paper_legacy_funding(wallet, state, targets, 'TEST', 100, 60000)
    assert D(state['virt_debt']) == bt.virt_debt
    assert D(state['funding_paid']) == bt.funding_paid == amount
    assert abs(D(wallet['balance']) - D(state['virt_debt']) - 200 - bt.val_cash) < D('1e-24')
    before = deepcopy((wallet, state))
    assert paper_legacy_funding(wallet, state, targets, 'TEST', 100, 60000) == 0
    assert (wallet, state) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('paper,exchange_qty,cycles,quantity,allowed', [
    (True, 0, 0, 0, True), (False, 0, 0, 0, True),
    (False, 1, 0, 0, False), (True, 0, 1, 0, False), (True, 0, 0, 1, False)])
async def test_restart_gap_requires_clean_flat_session_and_real_exchange_proof(paper, exchange_qty, cycles, quantity, allowed):
    from unittest.mock import AsyncMock, Mock
    cfg = json.loads((ROOT / 'config.json').read_text())
    connector = Mock(get_futures_klines=AsyncMock(return_value=[[t, 0, 0, 0, 100] for t in [180000, 240000, 300000]]),
                     get_positions=AsyncMock(return_value={'TEST_LONG': {'qty': exchange_qty}}))
    state = dict(strategy_bar_ms=60000, strategy_origin_ms=60000, rebalance_cycles=cycles, virt_debt=0)
    wallet = {'positions': {'TEST_LONG': quantity, 'TEST_SHORT': 0}, 'long_entry_price': 0, 'short_entry_price': 0}
    if allowed:
        bars = await strategy_minutes(connector, 'TEST', state, cfg, cfg['portfolios'][0], 305000, wallet, paper)
        assert bars[-1][0] == 240000
    else:
        with pytest.raises(ValueError, match='Missed'):
            await strategy_minutes(connector, 'TEST', state, cfg, cfg['portfolios'][0], 305000, wallet, paper)
