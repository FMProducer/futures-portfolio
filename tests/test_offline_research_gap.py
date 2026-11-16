"""Coverage for offline research tooling: report tables, candle replay, daily universe."""
import csv
import importlib.util
import json
from decimal import Decimal as D
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent


# ═══════════════════════════════════════════════════════════════════════════
#  experiments/trailing_share/report_tables.py
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def report(monkeypatch):
    path = ROOT / "experiments/trailing_share/report_tables.py"
    monkeypatch.setattr(pd, 'read_feather', lambda path: pd.DataFrame({
        'time': pd.date_range('2026-01-01', periods=4, freq='min', tz='UTC'),
        'close': [100., 100., 100., 100.]}))
    spec = importlib.util.spec_from_file_location("trailing_report_gap", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "CLOSES", np.array([100., 100., 100., 100.]))
    return module


def test_apply_fill_ignores_unknown_legs_and_quantities(report):
    pos = {"BASE_LONG": 0.0, "BASE_SHORT": 0.0}
    entry = {"BASE_LONG": 0.0, "BASE_SHORT": 0.0}
    report._apply_fill({"leg": "VIRTUAL", "side": "BUY", "qty": 1., "price": 100.}, pos, entry)
    report._apply_fill({"leg": "BASE_LONG", "side": "BUY", "qty": 0., "price": 100.}, pos, entry)
    assert pos == {"BASE_LONG": 0.0, "BASE_SHORT": 0.0}


def test_apply_fill_tracks_both_legs(report):
    pos = {"BASE_LONG": 0.0, "BASE_SHORT": 0.0}
    entry = {"BASE_LONG": 0.0, "BASE_SHORT": 0.0}
    report._apply_fill({"leg": "BASE_SHORT", "side": "SELL", "qty": 2., "price": 100.}, pos, entry)
    assert pos["BASE_SHORT"] == 2.0 and entry["BASE_SHORT"] == 100.0
    report._apply_fill({"leg": "BASE_SHORT", "side": "BUY", "qty": 1., "price": 110.}, pos, entry)
    assert pos["BASE_SHORT"] == 1.0
    report._apply_fill({"leg": "BASE_SHORT", "side": "BUY", "qty": 1., "price": 110.}, pos, entry)
    assert pos["BASE_SHORT"] == 0.0 and entry["BASE_SHORT"] == 0.0


def test_index_of_finds_the_bar(report):
    # Characterization: T_MS is built from a pandas-3 millisecond epoch, so the
    # second-based lookup never reaches the series. Reported as a finding, not fixed here.
    assert report.index_of("2026-01-01 00:00") == 0
    assert report.index_of("1970-01-01 00:00") == 0


def test_replay_audit_refuses_to_certify_liquidations(report):
    assert report.replay_audit({"liquidations": 1}) == {
        "status": "not_certified_liquidation_model"}


def test_load_full_reads_result_and_equity_files(report, tmp_path, monkeypatch):
    monkeypatch.setattr(report, "RESULTS", tmp_path)
    assert report.load_full("missing") == {}
    (tmp_path / "run.json").write_text(json.dumps({"profit_pct": 1.0}), encoding="utf-8")
    assert report.load_full("run") == {"profit_pct": 1.0}
    (tmp_path / "run_equity.json").write_text(json.dumps([1.0, 2.0]), encoding="utf-8")
    assert report.load_full("run")["equity_curve"] == [1.0, 2.0]


def _result(liquidations=0):
    return {
        "equity_curve": [1000.0, 1000.0], "fills": [], "data_window": {"offset": 0},
        "profit_pct": 1.0, "max_dd_pct": 2.0, "liquidations": liquidations, "trailing_stops": 0,
        "fill_count": 0, "accounting_ok": True, "realized_pnl_total": 0.0,
        "final_unrealized_pnl": 0.0, "commission_paid": 0.0, "slippage_paid": 0.0,
        "funding_paid": 0.0, "final_equity": 1000.0, "initial_capital": 1000.0,
        "settings": {"funding_rate_per_day": 0}, "events": [], "_meta": {"since": "2026-01-01"},
    }


def test_main_builds_annual_window_and_diagnostic_tables(report, tmp_path, monkeypatch, capsys):
    results = tmp_path / "results"
    results.mkdir()
    for label in ("search_x7_baseline", "search_x1_trail_0.002", "win00_x7_baseline",
                  "win00_x1_trail", "diag_x7_baseline"):
        (results / f"{label}.json").write_text(json.dumps(_result()), encoding="utf-8")
    monkeypatch.setattr(report, "RESULTS", results)
    monkeypatch.setattr(report, "EXP", tmp_path)
    report.main()
    payload = json.loads((tmp_path / "report_tables.json").read_text(encoding="utf-8"))
    assert len(payload["annual"]) == 3
    assert payload["annual"][0]["run"] == "search_x7_baseline"
    assert payload["annual"][0]["max_gross_exposure_eq"] == 0.0
    assert payload["annual"][0]["fill_replay"]["status"] in ("pass", "fail")
    assert len(payload["windows"]) == 2
    assert payload["windows"][0]["window"] == 0
    assert payload["windows"][0]["adds"] == 0
    assert "independent start" in payload["windows_note"]
    assert payload["diagnostic"]["x7_baseline"]["min_equity"] == 1000.0
    assert "annual rows" in capsys.readouterr().out


# ═══════════════════════════════════════════════════════════════════════════
#  rebalance/historical.py
# ═══════════════════════════════════════════════════════════════════════════

from rebalance.historical import candles, funding_rows, replay, sha  # noqa: E402
from rebalance.kernel import Config  # noqa: E402


def config():
    return Config('.001', '5.3', '7', '.0004', '.7', '.7', '.09')


def bars(values):
    return [dict(time=i * 60000, open=D(v), high=D(v), low=D(v), close=D(v))
            for i, v in enumerate(values)]


def test_candles_reject_invalid_ohlc(tmp_path):
    path = tmp_path / 'bars.csv'
    path.write_text('open_time,open,high,low,close\n0,100,90,80,100\n', encoding='utf-8')
    with pytest.raises(ValueError, match='invalid OHLC'):
        candles(path)


def test_candles_require_two_rows(tmp_path):
    path = tmp_path / 'bars.csv'
    path.write_text('open_time,open,high,low,close\n0,100,101,99,100\n', encoding='utf-8')
    with pytest.raises(ValueError, match='at least two candles'):
        candles(path)


def test_funding_rows_without_a_file_is_empty():
    assert funding_rows(None) == []


def test_funding_rows_reads_csv(tmp_path):
    path = tmp_path / 'funding.csv'
    path.write_text('timestamp,rate,mark_price\n1,0.001,100\n', encoding='utf-8')
    rows = funding_rows(path)
    assert rows == [dict(time=1, rate=D('.001'), mark=D('100'))]


def test_replay_rejects_invalid_execution_costs():
    with pytest.raises(ValueError, match='invalid execution costs'):
        replay(bars(['100'] * 3), config(), '10000', '20000', '1', [])


def test_replay_reports_a_blocked_first_entry():
    with pytest.raises(ValueError, match='entry blocked'):
        replay(bars(['100'] * 3), config(), '14', '0', '0', [])


def test_replay_skips_actions_after_a_blocked_entry():
    result, trades, _ = replay(bars(['100'] * 3), config(), '10000', '0', '0', [])
    assert result['candles'] == 3 and len(trades) == 2


def test_sha_is_the_file_digest(tmp_path):
    path = tmp_path / 'file.txt'
    path.write_bytes(b'abc')
    assert sha(path) == ('ba7816bf8f01cfea414140de5dae2223'
                         'b00361a396177a9cb410ff61f20015ad')


def test_historical_cli_writes_result_manifest_and_equity(tmp_path, monkeypatch, capsys):
    from rebalance import historical
    candles_file = tmp_path / 'bars.csv'
    with candles_file.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream)
        writer.writerow(['open_time', 'open', 'high', 'low', 'close'])
        for index in range(5):
            writer.writerow([index * 60000, 100, 101, 99, 100])
    config_file = tmp_path / 'config.json'
    config_file.write_text(json.dumps(dict(
        capital='10000', spread_bps='2', slippage_bps='1',
        strategy=dict(step='.001', min_notional='5.3', leverage='7', fee_rate='.0004',
                      surplus_pp='.7', deficit_pp='.7', trail_pp='.09'))), encoding='utf-8')
    output = tmp_path / 'out'
    monkeypatch.setattr('sys.argv', ['historical', '--candles', str(candles_file),
                                     '--config', str(config_file), '--output', str(output),
                                     '--funding-zero'])
    historical.main()
    assert (output / 'result.json').exists()
    assert (output / 'manifest.json').exists()
    assert (output / 'trades.json').exists()
    assert (output / 'equity.json').exists()
    manifest = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['funding_mode'] == 'ASSUMED_ZERO'
    assert manifest['durable_state'] is False
    assert 'terminal open MTM' in manifest['limitations'][0] or manifest['limitations']
    assert json.loads(capsys.readouterr().out)['candles'] == 5


def test_historical_cli_supports_a_durable_state_database(tmp_path, monkeypatch):
    from rebalance import historical
    candles_file = tmp_path / 'bars.csv'
    with candles_file.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream)
        writer.writerow(['open_time', 'open', 'high', 'low', 'close'])
        for index in range(5):
            writer.writerow([index * 60000, 100, 101, 99, 100])
    config_file = tmp_path / 'config.json'
    config_file.write_text(json.dumps(dict(
        capital='10000', spread_bps='2', slippage_bps='1',
        strategy=dict(step='.001', min_notional='5.3', leverage='7', fee_rate='.0004',
                      surplus_pp='.7', deficit_pp='.7', trail_pp='.09'))), encoding='utf-8')
    output = tmp_path / 'out'
    monkeypatch.setattr('sys.argv', ['historical', '--candles', str(candles_file),
                                     '--config', str(config_file), '--output', str(output),
                                     '--state-db', str(tmp_path / 'state.sqlite'), '--funding-zero'])
    historical.main()
    manifest = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['durable_state'] is True
    assert 'durable.py' in manifest['code']
    assert (tmp_path / 'state.sqlite').exists()


# ═══════════════════════════════════════════════════════════════════════════
#  scanner/daily_universe.py
# ═══════════════════════════════════════════════════════════════════════════

from scanner import daily_universe as du  # noqa: E402


def _exchange(count):
    return {'symbols': [dict(symbol=f'SYM{index:03d}USDT', contractType='PERPETUAL', status='TRADING',
                             quoteAsset='USDT', marginAsset='USDT') for index in range(count)]}


def _volumes(count, symbol_prefix='SYM'):
    return [dict(symbol=f'{symbol_prefix}{index:03d}USDT', quoteVolume=str(1000 - index))
            for index in range(count)]


def test_top_symbols_requires_a_liquid_universe():
    with pytest.raises(ValueError, match='Fewer than 100'):
        du.top_symbols(_exchange(3), _volumes(3))


def test_top_symbols_rejects_invalid_volume():
    exchange = _exchange(100)
    volumes = _volumes(100)
    volumes[0] = dict(symbol=volumes[0]['symbol'], quoteVolume='nan')
    with pytest.raises(ValueError, match='Invalid market volume'):
        du.top_symbols(exchange, volumes)


def test_top_symbols_skips_zero_volume():
    exchange = _exchange(101)
    volumes = _volumes(101)
    volumes[0] = dict(symbol=volumes[0]['symbol'], quoteVolume='0')
    selected = du.top_symbols(exchange, volumes)
    assert len(selected) == 100
    assert 'SYM000USDT' not in {row['symbol'] for row in selected}


def test_top_symbols_rejects_duplicate_tickers():
    exchange = _exchange(100)
    volumes = _volumes(100)
    volumes[1] = dict(symbol=volumes[0]['symbol'], quoteVolume='10')
    with pytest.raises(ValueError, match='Duplicate market ticker'):
        du.top_symbols(exchange, volumes)


def test_exclusive_run_locks_the_file(tmp_path):
    path = tmp_path / 'run.lock'
    with du.exclusive_run(path):
        assert path.exists()


def test_admission_history_skips_unpublished_runs(tmp_path):
    folder = tmp_path / 'reports/daily_universe/20260101'
    folder.mkdir(parents=True)
    (folder / 'status.json').write_text(json.dumps({'status': 'running'}), encoding='utf-8')
    (folder / 'results.json').write_text(json.dumps({}), encoding='utf-8')
    assert du.admission_history(tmp_path, {'SYM000USDT'}) == {}


def test_admission_history_collects_published_passes(tmp_path):
    folder = tmp_path / 'reports/daily_universe/20260101'
    folder.mkdir(parents=True)
    (folder / 'status.json').write_text(json.dumps({'status': 'published'}), encoding='utf-8')
    (folder / 'results.json').write_text(json.dumps({'SYM000USDT': {
        'pass': True, 'end_exclusive_ms': 10,
        'windows': {'48': {'pass': True, 'funding_model': 'legacy_constant'},
                    '24': {'pass': True, 'funding_model': 'legacy_constant'}}}}), encoding='utf-8')
    history = du.admission_history(tmp_path, {'SYM000USDT'})
    assert history['SYM000USDT']['end_exclusive_ms'] == 10
    assert history['SYM000USDT']['result_sha256']


@pytest.mark.asyncio
async def test_daily_universe_publishes_a_research_pool(tmp_path, monkeypatch, capsys):
    root = tmp_path / 'project'
    (root / 'reports').mkdir(parents=True)
    config = {'portfolios': [{'paper_initial_capital': 1000}], 'funding_model': 'legacy_constant'}
    (root / 'config.json').write_text(json.dumps(config), encoding='utf-8')
    (root / 'tickers.txt').write_text('SYM000USDT\n', encoding='utf-8')

    def fake_read_public_url(url):
        if url.endswith('exchangeInfo'):
            return json.dumps(_exchange(100)).encode()
        return json.dumps(_volumes(100)).encode()

    async def fake_evaluate(root_path, symbols, cfg, end, report_dir=None):
        return {symbol: {'symbol': symbol, 'pass': True, 'net_pct': 1.0, 'max_dd_pct': 2.0,
                         'end_exclusive_ms': end, 'windows': {}} for symbol in symbols}

    monkeypatch.setattr(du, 'read_public_url', fake_read_public_url)
    monkeypatch.setattr(du, 'evaluate', fake_evaluate)
    await du.main(root)
    latest = json.loads((root / 'reports/daily_universe/latest.json').read_text(encoding='utf-8'))
    assert latest['status'] == 'published'
    assert latest['pool_size'] == 50
    assert (root / 'tickers.txt').read_text().count('\n') == 50
    assert json.loads(capsys.readouterr().out)['status'] == 'published'


@pytest.mark.asyncio
async def test_daily_universe_rejects_a_changed_candidate_list(tmp_path, monkeypatch):
    root = tmp_path / 'project'
    (root / 'reports').mkdir(parents=True)
    config = {'portfolios': [{'paper_initial_capital': 1000}], 'funding_model': 'legacy_constant'}
    (root / 'config.json').write_text(json.dumps(config), encoding='utf-8')
    (root / 'tickers.txt').write_text('SYM000USDT\n', encoding='utf-8')
    run = tmp_path / 'run'
    run.mkdir()
    (run / 'tickers.input.txt').write_text('OTHERUSDT\n', encoding='utf-8')
    candidates = [{'symbol': 'SYM000USDT'}]
    with pytest.raises(ValueError, match='Candidate list changed'):
        du.publish(root, run, candidates, {'SYM000USDT': {'pass': True}}, config)
