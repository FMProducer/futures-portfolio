"""Verify the annual driver uses the requested symbol and the real runtime."""
import json
from pathlib import Path

import pandas as pd
import pytest

from backtest.backtest_rebalance import run_backtest
from scripts.verify_runtime_parity import replay, ReplayFailure
from scripts.verify_real_runtime_parity import replay as real_replay, ReplayLogger


def test_real_driver_does_not_turn_a_protective_cash_block_into_a_crash():
    logger = ReplayLogger()
    logger.error('Cash invariant violation active: blocked 1 increase action(s).')
    assert logger.cash_blocks == 1
    with pytest.raises(ReplayFailure, match='Unexpected accounting error'):
        logger.error('Unexpected accounting error')


@pytest.mark.asyncio
async def test_paper_driver_replays_agt_instead_of_hardcoded_zec(tmp_path):
    config = json.loads((Path(__file__).resolve().parents[1]/'config.json').read_text())
    path = tmp_path/'config.json'; path.write_text(json.dumps(config))
    feather = tmp_path/'AGTUSDT_live.feather'
    count = 90
    pd.DataFrame({'time': pd.date_range('2026-01-01', periods=count, freq='min', tz='UTC'),
                  'open': 100., 'high': 100., 'low': 100., 'close': 100., 'volume': 1.}).to_feather(feather)
    paper = await replay(path, feather, count)
    reference = await run_backtest(str(path), str(tmp_path), ticker_override='AGTUSDT', days=count/1440)
    assert paper['ticker'] == 'AGTUSDT' and paper['mode'] == 'PAPER'
    assert set(paper['positions']) == {'AGTUSDT_LONG', 'AGTUSDT_SHORT'}
    assert all(f['symbol'].startswith('AGTUSDT_') for f in paper['_fills'])
    assert paper['cycles'] == reference['cycles']
    assert paper['_curve'] == pytest.approx(reference['equity_curve'], abs=1e-8)


@pytest.mark.asyncio
async def test_real_archived_adapter_matches_native_journal_and_confirmed_exchange_facts(tmp_path):
    config = json.loads((Path(__file__).resolve().parents[1]/'config.json').read_text())
    path = tmp_path/'config.json'; path.write_text(json.dumps(config))
    feather = tmp_path/'AGTUSDT_live.feather'
    count = 90
    pd.DataFrame({'time': pd.date_range('2026-01-01', periods=count, freq='min', tz='UTC'),
                  'open': 100., 'high': 100., 'low': 100., 'close': 100., 'volume': 1.}).to_feather(feather)
    native = await real_replay(path, feather, count)
    archived = await real_replay(path, feather, count, archive_applied=True)
    for key in ('_curve', '_fills', 'funding', 'cycles', 'stops', 'positions'):
        assert native[key] == archived[key]
    assert len(archived['_funding_receipts']) == native['funding_events'] == count-1
    assert archived['funding_events'] == count-1
    assert all(row['status'] == 'applied' for row in archived['_funding_receipts'])
    assert len(archived['final_state']['funding_journal']) == 1
    paper = await replay(path, feather, count)
    assert paper['_curve'] == pytest.approx(archived['_curve'], abs=1e-8)
    assert paper['positions'] == archived['positions']
    assert float(paper['funding_reserve']) == float(archived['funding_reserve'])
    assert float(paper['min_cash']) >= 0 and float(archived['min_cash']) >= 0
    assert float(paper['min_virtual']) >= 0 and float(archived['min_virtual']) >= 0
    memory_real = await real_replay(path, feather, count, archive_applied=True, memory_reserve_store=True)
    memory_paper = await replay(path, feather, count, memory_reserve_store=True)
    for disk, memory in ((archived, memory_real), (paper, memory_paper)):
        for key in ('_curve', '_fills', '_funding', 'funding', 'funding_reserve', 'cycles', 'stops', 'positions'):
            assert disk[key] == memory[key], key
    native_real = await real_replay(path, feather, count, archive_applied=True, native_persistence=True)
    native_paper = await replay(path, feather, count, native_persistence=True)
    for expected, native in ((archived, native_real), (paper, native_paper)):
        for key in ('_curve', '_fills', '_funding', 'funding', 'funding_reserve', 'cycles', 'stops', 'positions'):
            assert expected[key] == native[key], key
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(Path(native['persistence_root'])/'config.json.funding.sqlite')) as db:
            assert db.execute('select count(*) from applied_events').fetchone()[0] == count-1


@pytest.mark.asyncio
async def test_real_close_confirmation_delays_do_not_advance_historical_bar(tmp_path):
    config = json.loads((Path(__file__).resolve().parents[1]/'config.json').read_text())
    config.update(equity_trailing_stop_activation_pct=.1, toxic_cooldown_days=0)
    targets = config['portfolios'][0]['targets']
    for key, share in (('BASE_LONG', .5), ('BASE_SHORT', .1), ('VIRTUAL', .4)):
        targets[key]['share'] = share
    path = tmp_path/'config.json'; path.write_text(json.dumps(config))
    feather = tmp_path/'AGTUSDT_live.feather'
    prices = [100., 105., 103., 102., 102., 102.]
    pd.DataFrame({'time': pd.date_range('2026-01-01', periods=6, freq='min', tz='UTC'),
                  'open': prices, 'high': prices, 'low': prices, 'close': prices, 'volume': 1.}).to_feather(feather)
    result = await real_replay(path, feather, 6, archive_applied=True)
    native = await real_replay(path, feather, 6)
    assert native['_curve'] == result['_curve'] and native['_fills'] == result['_fills']
    assert result['stops'] == 1
    stop_bar = result['_sessions'][0]['bar']
    close = [row for row in result['_fills'] if row['bar'] == stop_bar]
    assert len(close) == 2 and {row['side'] for row in close} == {'BUY', 'SELL'}
    native_disk = await real_replay(path, feather, 6, archive_applied=True, native_persistence=True)
    assert native_disk['_curve'] == result['_curve'] and native_disk['_fills'] == result['_fills']
    assert native_disk['final_state']['funding_journal_archived']
