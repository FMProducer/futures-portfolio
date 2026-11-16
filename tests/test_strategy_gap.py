from copy import deepcopy
from unittest.mock import AsyncMock, Mock

import pytest

from core.backtest_rules import reconcile_paper_gap, strategy_minutes


def snapshot():
    state = dict(base_ticker='TEST', strategy_bar_ms=60000, strategy_origin_ms=60000,
                 rebalance_cycles=3, virt_debt=500, valuation_price=100, last_tpv=1000,
                 tpv_ath=1100, max_drawdown_reference=1100, trailing_stop_violation_start=45,
                 share_trail={'BASE_LONG': {'peak': '.3', 'trough': '.2'}})
    wallet = dict(base_ticker='TEST', balance=1000, last_price=100,
                  positions={'TEST_LONG': 2, 'TEST_SHORT': 3}, long_entry_price=100, short_entry_price=100)
    return state, wallet


@pytest.mark.asyncio
async def test_paper_gap_preserves_wallet_risk_and_checkpoint_then_resumes_latest_closed_minute():
    state, wallet = snapshot()
    before, before_wallet = deepcopy(state), deepcopy(wallet)
    connector = Mock(get_futures_klines=AsyncMock(return_value=[
        [t, 0, 0, 0, 100 + t / 60000] for t in (120000, 180000, 240000, 300000)]))
    bars = await strategy_minutes(connector, 'TEST', state, {'funding_model': 'disabled'}, {}, 305000, wallet)
    assert bars[-1] == (240000, 104)
    assert wallet == before_wallet
    assert {k: v for k, v in state.items() if k != 'strategy_gap_reconciliation'} == before
    assert state['strategy_gap_reconciliation'] == dict(
        policy='hold_positions_no_retroactive_trades', previous_bar_ms=60000,
        resume_bar_ms=240000, missed_minutes=2, observed_at_ms=305000,
        snapshot_equity='1000', equity_residual='0')
    state['strategy_bar_ms'] = bars[-1][0]
    assert await strategy_minutes(connector, 'TEST', state, {'funding_model': 'disabled'}, {}, 305000, wallet) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('paper,model', [(False, 'disabled'), (True, 'legacy_constant'), (True, 'signed_actual')])
async def test_gap_does_not_skip_real_or_unreconciled_funding(paper, model):
    state, wallet = snapshot()
    connector = Mock(get_futures_klines=AsyncMock(return_value=[[t, 0, 0, 0, 100] for t in (180000, 240000, 300000)]))
    with pytest.raises(ValueError, match='Missed'):
        await strategy_minutes(connector, 'TEST', state, {'funding_model': model}, {}, 305000, wallet, paper)
    assert 'strategy_gap_reconciliation' not in state


@pytest.mark.parametrize('field', ['startup_blocked', 'rebalance_inflight', 'initial_build_pending',
                                  'trailing_stop_triggered', 'paper_close_complete', 'cash_invariant_violation'])
def test_gap_rejects_unresolved_execution(field):
    state, wallet = snapshot()
    state[field] = True
    with pytest.raises(ValueError, match='unresolved'):
        reconcile_paper_gap('TEST', state, wallet)


@pytest.mark.parametrize('case', ['equity', 'price', 'missing_leg', 'foreign_ticker', 'negative', 'nan',
                                 'entry', 'funding', 'profit_budget', 'receipt', 'quantity_text'])
def test_gap_rejects_corrupt_or_mismatched_snapshots_without_mutating_them(case):
    state, wallet = snapshot()
    if case == 'equity': wallet['balance'] += 1
    if case == 'price': wallet['last_price'] += 1
    if case == 'missing_leg': del wallet['positions']['TEST_SHORT']
    if case == 'foreign_ticker': wallet['base_ticker'] = 'OTHER'
    if case == 'negative': wallet['positions']['TEST_LONG'] = -1
    if case == 'nan': wallet['balance'] = float('nan')
    if case == 'entry': wallet['long_entry_price'] = 0
    if case == 'funding': state['funding_journal'] = [{'status': 'received'}]
    if case == 'profit_budget': wallet['profit_budget'] = {'blocked': True}
    if case == 'receipt': state['share_trail_last_fill'] = 'missing'
    if case == 'quantity_text': wallet['positions']['TEST_LONG'] = 'invalid'
    before = repr((state, wallet))
    with pytest.raises(ValueError):
        reconcile_paper_gap('TEST', state, wallet)
    assert repr((state, wallet)) == before


@pytest.mark.asyncio
async def test_gap_still_rejects_missing_or_stale_market_history():
    state, wallet = snapshot()
    connector = Mock(get_futures_klines=AsyncMock(return_value=[[t, 0, 0, 0, 100] for t in (60000, 240000, 300000)]))
    with pytest.raises(ValueError, match='contiguous'):
        await strategy_minutes(connector, 'TEST', state, {'funding_model': 'disabled'}, {}, 305000, wallet)
    assert 'strategy_gap_reconciliation' not in state
