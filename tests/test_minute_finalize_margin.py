"""Ingestion-finalization margin for strategy_minutes (MINUTE_FINALIZE_MARGIN_MS).

Covers: 0/4999/5000/5001ms boundary; no-fetch and no-mutation on defer; the
deferred bar is processed exactly once after the margin; no fake gap; genuine gap
handling unchanged; future clock / invalid rows still fail closed; and the
delayed-final-close fixture (early pre-final price ignored, final price accepted)
through the REAL (paper_mode=False) ingestion path feeding the real calculator.
"""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from core.backtest_rules import MINUTE_FINALIZE_MARGIN_MS, strategy_minutes
from core.calculator import PortfolioCalculator

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
PORTFOLIO = CFG['portfolios'][0]

# A minute boundary; the just-closed bar opens at OPEN_MS - 60000 = 120000.
OPEN_MS = 180000
ROWS = [[60000, 100.0, 100.0, 100.0, 100.0, 1000.0], [120000, 100.0, 100.0, 100.0, 100.0, 1000.0]]


def kline_rows(opens, close):
    # fapi kline row layout: [open_time, open, high, low, close, volume, ...]
    return [[t, close, close, close, close, 1000.0] for t in opens]


def make_connector(rows):
    from unittest.mock import AsyncMock, Mock
    return Mock(get_futures_klines=AsyncMock(return_value=rows),
                get_positions=AsyncMock(return_value={}))


@pytest.mark.asyncio
@pytest.mark.parametrize('offset,deferred', [(0, True), (4999, True), (5000, False), (5001, False)])
async def test_finalize_margin_boundary(offset, deferred):
    connector = make_connector(ROWS)
    now_ms = OPEN_MS + offset
    assert now_ms - (now_ms // 60000 * 60000) == offset
    result = await strategy_minutes(connector, 'TEST', {}, CFG, PORTFOLIO, now_ms)
    if deferred:
        assert result == []
        assert connector.get_futures_klines.call_count == 0, 'deferred call must not fetch'
    else:
        assert result == [(120000, 100.0)]
        assert connector.get_futures_klines.call_count == 1


@pytest.mark.asyncio
async def test_defer_is_no_fetch_no_mutation_and_retries_same_bar_exactly_once():
    connector = make_connector(ROWS)
    state, wallet = {}, {'positions': {'TEST_LONG': 0, 'TEST_SHORT': 0},
                         'long_entry_price': 0, 'short_entry_price': 0}
    snapshot = deepcopy((state, wallet))
    # inside the margin: deferred, no fetch, nothing mutated
    assert await strategy_minutes(connector, 'TEST', state, CFG, PORTFOLIO, OPEN_MS + 3000, wallet) == []
    assert (state, wallet) == snapshot, 'deferred call must not mutate state or wallet'
    assert connector.get_futures_klines.call_count == 0
    # after the margin (same minute) the same just-closed bar is processed exactly once
    assert await strategy_minutes(connector, 'TEST', state, CFG, PORTFOLIO, OPEN_MS + 8000, wallet) == [(120000, 100.0)]
    assert 'strategy_gap_reconciliation' not in state, 'contiguous retry must not be reported as a gap'
    state['strategy_bar_ms'] = 120000
    # the next poll is refused as a duplicate (exactly-once)
    assert await strategy_minutes(connector, 'TEST', state, CFG, PORTFOLIO, OPEN_MS + 14000, wallet) == []


@pytest.mark.asyncio
async def test_genuine_gap_handling_unchanged_past_margin():
    # previous bar 60000, fetched candles contiguous up to 180000 -> a genuinely missed 120000
    connector = make_connector(kline_rows([60000, 120000, 180000], 100.0))
    state = {'strategy_bar_ms': 60000, 'strategy_origin_ms': 60000, 'rebalance_cycles': 1, 'virt_debt': 0}
    wallet = {'positions': {'TEST_LONG': 5, 'TEST_SHORT': 0}, 'long_entry_price': 100, 'short_entry_price': 0}
    now_ms = 240000 + 5000  # past the finalization margin, so the genuine-gap path is reached
    with pytest.raises(ValueError, match='Missed'):
        await strategy_minutes(connector, 'TEST', state, CFG, PORTFOLIO, now_ms, wallet, True)


@pytest.mark.asyncio
@pytest.mark.parametrize('rows', [
    kline_rows([60000, 180000], 100.0),           # non-contiguous candle rows
    kline_rows([60000, 120000], float('nan')),     # non-finite close
    kline_rows([60000, 120000], 0.0),              # non-positive close
], ids=['gapped-rows', 'nan-close', 'zero-close'])
async def test_invalid_rows_still_fail_closed_past_margin(rows):
    connector = make_connector(rows)
    with pytest.raises(ValueError, match='candles'):
        await strategy_minutes(connector, 'TEST', {}, CFG, PORTFOLIO, OPEN_MS + 5000)


@pytest.mark.asyncio
async def test_future_clock_still_fails_closed():
    # now_ms far beyond the available candles: the latest closed bar can never reach the boundary
    connector = make_connector(kline_rows([60000, 120000], 100.0))
    with pytest.raises(ValueError, match='candles'):
        await strategy_minutes(connector, 'TEST', {}, CFG, PORTFOLIO, 960000 + 5000)


@pytest.mark.asyncio
async def test_delayed_final_close_early_ignored_late_accepted_real_ingestion():
    # The just-closed bar's close finalizes a few seconds after the minute boundary
    # (last-second trade settlement). The exchange close is modeled as mutable: pre-final
    # (100.0) inside the margin, finalized (100.5) once we fetch after the margin.
    exchange = {'close': 100.0, 'fetches': 0}

    class LateConnector:
        async def get_futures_klines(self, *args):
            exchange['fetches'] += 1
            return kline_rows([60000, 120000], exchange['close'])

        async def get_positions(self):
            return {}

    state, wallet = {}, {'positions': {'TEST_LONG': 0, 'TEST_SHORT': 0},
                         'long_entry_price': 0, 'short_entry_price': 0}
    # 1) inside the margin: deferred -> the pre-final read is never fetched/used
    assert await strategy_minutes(LateConnector(), 'TEST', state, CFG, PORTFOLIO,
                                  OPEN_MS + 2000, wallet, paper_mode=False) == []
    assert exchange['fetches'] == 0, 'no fetch may occur inside the finalization margin'
    # the exchange finalizes the close
    exchange['close'] = 100.5
    # 2) past the margin: the finalized close is the one ingested, exactly once
    bars = await strategy_minutes(LateConnector(), 'TEST', state, CFG, PORTFOLIO,
                                  OPEN_MS + 8000, wallet, paper_mode=False)
    assert bars == [(120000, 100.5)], 'the accepted price must be the finalized close'
    assert exchange['fetches'] == 1
    # 3) the accepted price feeds the real calculator without error (no profit claim)
    targets = PORTFOLIO['targets']
    calc = PortfolioCalculator(positions={'TEST_LONG': 0.0, 'TEST_SHORT': 0.0}, spot_price=100.5,
                               real_equity=1000.0, virt_qty=0.0, base_ticker='TEST', targets=targets,
                               initial_capital=1000.0, last_rebalance_price=0.0)
    result = calc.calculate_rebalance(targets, 0.04, 0.04, calc.tpv)
    assert {'actions', 'tpv', 'share_long_pct'} <= set(result)


def test_constant_is_fixed_not_configurable():
    import core.backtest_rules as rules
    assert rules.MINUTE_FINALIZE_MARGIN_MS == 5000
    assert type(rules.MINUTE_FINALIZE_MARGIN_MS) is int
    source = Path(rules.__file__).read_text(encoding='utf-8')
    fn = source[source.index('async def strategy_minutes'):source.index('def paper_legacy_funding')]
    assert 'MINUTE_FINALIZE_MARGIN_MS' in fn
