"""Coverage for operator scripts in scripts/ (offline: all I/O redirected to tmp_path).

Import-time scripts (visualize_*, run_*, run_zec_replay) compute their root from
__file__; the tests load the real repository source (coverage stays attributed to
the repo file) but point __file__ at a tmp mirror so every fixture and artifact
lives under tmp_path.
"""
import asyncio
import importlib.util
import json
import shutil
import sqlite3
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / 'scripts'


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_at(name, path, fake_file):
    """Exec the real repo source, but with __file__ redirected into a tmp tree."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(fake_file)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def mirror(tmp_path_factory):
    """tmp mirror of the repo layout with feather fixtures; no repo mutation."""
    import numpy as np
    import pandas as pd
    base = tmp_path_factory.mktemp('mirror')
    mirror_root = base / 'mirror'
    (mirror_root / 'scripts').mkdir(parents=True)
    (mirror_root / 'data').mkdir()
    (mirror_root / 'reports').mkdir()
    for rel in ('core/funding.py', 'core/main.py',
                'backtest/backtest_rebalance.py', 'backtest/dashboard_attribution.py'):
        target = mirror_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, target)
    shutil.copyfile(ROOT / 'config.json', mirror_root / 'config.json')
    (mirror_root / 'reports' / 'plotly-2.27.0.min.js').write_text('// offline plotly stub',
                                                                    encoding='utf-8')
    n = 40_000
    close = 200.0 + 0.1 * (np.arange(n) % 100) / 100.0
    # datetime column: run_zec_replay recomputes bar times via pd.to_datetime(df['time'], utc=True)
    zec_times = pd.to_datetime(1_791_590_400_000 + np.arange(n) * 60_000, unit='ms', utc=True)
    zec = pd.DataFrame({'time': zec_times, 'open': close, 'high': close + 0.5,
                        'low': close - 0.5, 'close': close, 'volume': np.full(n, 10.0)})
    zec.to_feather(mirror_root / 'data' / 'ZECUSDT_12m.feather')
    pd.DataFrame({'open': close, 'high': close + 0.5, 'low': close - 0.5,
                  'close': close, 'volume': np.full(n, 10.0)}
                 ).to_feather(mirror_root / 'data' / 'BTCUSDT_live_365d.feather')
    yield mirror_root
    if str(mirror_root) in sys.path:
        sys.path.remove(str(mirror_root))


PM2_LOG = (
    '2026-01-01 10:00:00,123 INFO: Heartbeat: TPV=100.5 | PnL=+1.2\n'
    '2026-01-01 10:05:00,123 INFO: Rebalance #4 complete. TPV: 101.5\n'
    '2026-01-01 10:06:00,123 INFO: \U0001F4DD PAPER: BUY 68.6 AAAUSDT_LONG @ 2.263\n'
    '2026-01-01 10:07:00,123 WARNING: Trailing stop armed at 2.0%\n'
)


def test_pm2_log_parser_parsing_db_and_cli(monkeypatch, tmp_path):
    module = _load('pm2_log_parser', SCRIPTS / 'pm2_log_parser.py')
    logs = tmp_path / 'pm2logs'
    logs.mkdir()
    (logs / 'real-aaa-out-123.log').write_text(PM2_LOG, encoding='utf-8')
    (logs / 'paper-bbb-out-456.log').write_text(PM2_LOG.replace('PAPER', 'REAL'), encoding='utf-8')
    (logs / 'paper-bbb-error-456.log').write_text(PM2_LOG, encoding='utf-8')
    (logs / 'notes.txt').write_text('not a log', encoding='utf-8')

    data = module.parse_log_file(str(logs / 'real-aaa-out-123.log'), 'aaa', 'real')
    assert data['heartbeats'][0]['tpv'] == 100.5
    assert data['rebalances'][0]['cycle'] == 4
    assert data['trades'][0]['side'] == 'BUY'
    assert data['guards'][0]['level'] == 'WARNING'

    conn = module.get_db(str(tmp_path / 'stats.db'))
    module.ensure_tables(conn)
    inserted = module.save_to_db(conn, data)
    conn.commit()
    assert inserted['heartbeats'] == 1
    assert module.save_to_db(conn, data)['heartbeats'] == 0  # UNIQUE(timestamp, ticker, mode)
    conn.close()

    assert module.scan_pm2_logs(str(tmp_path / 'nope')) == []
    found = module.scan_pm2_logs(str(logs), ticker='AAAUSDT', mode='real')
    assert [t for _, t, _ in found] == ['aaa'] and found[0][2] == 'real'
    assert [m for _, _, m in module.scan_pm2_logs(str(logs), ticker='BBBUSDT')] == ['paper']

    db = tmp_path / 'main.db'
    monkeypatch.setattr(sys, 'argv', ['pm2_log_parser', '--scan-all', '--dry-run',
                                      '--db', str(db), '--pm2-dir', str(logs)])
    module.main()
    monkeypatch.setattr(sys, 'argv', ['pm2_log_parser', '--scan-all', '--db', str(db),
                                      '--pm2-dir', str(logs)])
    module.main()
    check = sqlite3.connect(db)
    assert check.execute('SELECT COUNT(*) FROM heartbeats').fetchone()[0] == 2
    check.close()
    monkeypatch.setattr(sys, 'argv', ['pm2_log_parser'])
    module.main()  # prints help, no scan


def test_archive_to_sqlite_parsing_db_and_cli(monkeypatch, tmp_path):
    module = _load('archive_to_sqlite', SCRIPTS / 'archive_to_sqlite.py')
    hist = tmp_path / 'history'
    hist.mkdir()
    real = {'ticker': 'ZECUSDT', 'state': {'started_at': 1_750_000_000, 'last_update': 1_750_003_600,
                                            'initial_tpv': 1000, 'last_tpv': 1010, 'last_profit': 10,
                                            'total_pnl_pct': 1.0, 'rebalance_cycles': 3}}
    paper = {'base_ticker': 'AAAUSDT', 'started_at': 1_750_000_000, 'last_update': 1_750_003_600,
             'initial_tpv': 500, 'last_tpv': 499, 'last_profit': -1, 'total_pnl_pct': -0.2,
             'rebalance_cycles': 2, 'trailing_stop_triggered': True}
    (hist / 'archive_ZECUSDT_1750000000.json').write_text(json.dumps(real), encoding='utf-8')
    (hist / 'archive_1750000000_paper_state_AAAUSDT.json').write_text(json.dumps(paper), encoding='utf-8')
    (hist / 'archive_1750000000_paper_shadow_AAAUSDT.json').write_text('{}', encoding='utf-8')
    (hist / 'archive_1750000001_bad.json').write_text('{oops', encoding='utf-8')
    (hist / 'archive_1750000002_nostate.json').write_text('{"state": {}}', encoding='utf-8')
    (hist / 'archive_1750000003_zero.json').write_text(
        json.dumps({'state': {'started_at': 0, 'last_update': 0}}), encoding='utf-8')

    assert module.parse_archive_file(str(hist / 'archive_ZECUSDT_1750000000.json'))['mode'] == 'real'
    assert module.parse_archive_file(
        str(hist / 'archive_1750000000_paper_state_AAAUSDT.json'))['close_reason'] == 'trailing_stop'
    assert module.parse_archive_file(
        str(hist / 'archive_1750000000_paper_shadow_AAAUSDT.json')) is None
    sessions, errors = module.scan_archives(str(hist))
    assert len(sessions) == 2 and len(errors) == 3
    assert module.scan_archives(str(tmp_path / 'nope')) == (
        [], ['History dir not found: ' + str(tmp_path / 'nope')])

    db = tmp_path / 'stats.db'
    setup = sqlite3.connect(db)
    setup.executescript(
        'CREATE TABLE bot_sessions (id INTEGER PRIMARY KEY, ticker TEXT, mode TEXT, start_time TEXT, '
        'end_time TEXT, duration_sec INTEGER, tpv_start REAL, tpv_end REAL, pnl REAL, close_reason TEXT);'
        'CREATE TABLE daily_stats (id INTEGER PRIMARY KEY, date TEXT, ticker TEXT, mode TEXT, '
        'tpv_start REAL, tpv_end REAL, pnl REAL, pnl_pct REAL, rebal_count INTEGER, '
        'commission_usdt REAL, max_drawdown_pct REAL);')
    setup.commit()
    setup.close()

    conn = module.get_db(str(db))
    assert module.save_bot_session(conn, sessions[0]) is True
    assert module.save_bot_session(conn, sessions[0]) is False  # duplicate
    assert module.save_daily_stat(conn, sessions[1]) is True
    conn.close()

    argv = ['archive_to_sqlite', '--scan-all', '--db', str(db), '--history', str(hist)]
    monkeypatch.setattr(sys, 'argv', argv + ['--dry-run'])
    module.main()
    monkeypatch.setattr(sys, 'argv', argv)
    module.main()
    readback = sqlite3.connect(db)
    assert readback.execute('SELECT COUNT(*) FROM bot_sessions').fetchone()[0] == 2
    readback.close()
    monkeypatch.setattr(sys, 'argv', ['archive_to_sqlite'])
    module.main()


def test_db_count_snippets_use_a_fake_database(monkeypatch):
    """_count_rows/_query_counts/query_summary hardcode the runtime checkout DB; redirect to memory."""
    real_connect = sqlite3.connect

    def fake_connect(database, *args, **kwargs):
        conn = real_connect(':memory:')
        for table in ('bot_sessions', 'daily_stats', 'heartbeats', 'rebalances', 'pm2_trades', 'guards'):
            conn.execute(f'CREATE TABLE {table} (id INTEGER PRIMARY KEY)')
        return conn

    monkeypatch.setattr(sqlite3, 'connect', fake_connect)
    for name in ('_count_rows', '_query_counts', 'query_summary'):
        module = _load(name, SCRIPTS / f'{name}.py')
        assert module is not None


def test_reset_paper_support_operations(monkeypatch, tmp_path):
    module = _load('reset_paper_support', SCRIPTS / 'reset_paper_support.py')
    processes = json.dumps([{'name': 'paper-btc', 'pm2_env': {'status': 'online'}, 'pid': 1},
                             {'name': 'real-eth', 'pm2_env': {'status': 'stopped'}, 'pid': 2}])
    monkeypatch.setattr(sys, 'argv', ['reset', 'inventory'])
    monkeypatch.setattr(sys, 'stdin', type('IO', (), {'read': lambda self: processes})())
    output = []
    monkeypatch.setattr('builtins.print', lambda *args, **kwargs: output.append(' '.join(map(str, args))))
    module.main()
    rows = json.loads(output[0])
    assert rows[0]['name'] == 'paper-btc' and rows[1]['pid'] == 2

    root = tmp_path / 'bot'
    root.mkdir()
    (root / 'core').mkdir()  # initialize() cannot create its own parent directory
    # initialize(root, slots, capital, states): slots is a count, not a ticker list.
    (root / 'config.json').write_text(json.dumps({'paper_mode_bots': 2,
                                                  'portfolios': [{'paper_initial_capital': 100}]}),
                                      encoding='utf-8')
    # paper_budget leaks connections (plain and file:/// URI); close only this fixture's.
    tracked = []
    real_connect = sqlite3.connect

    def tracking(database, *args, **kwargs):
        conn = real_connect(database, *args, **kwargs)
        target = str(database).lower()
        if target.startswith('file:///'):
            from urllib.parse import unquote, urlparse
            target = unquote(urlparse(target).path.lstrip('/'))
        norm = target.replace('\\', '/').lower()
        prefix = str(root).lower().replace('\\', '/')
        if norm == prefix or norm.startswith(prefix + '/'):
            tracked.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, 'connect', tracking)
    monkeypatch.setattr(sys, 'argv', ['reset', 'initialize', str(root)])
    output.clear()
    module.main()
    budget = json.loads(output[0])
    assert budget['cash'] == budget['initial']
    for conn in tracked:
        conn.close()
    monkeypatch.setattr(sys, 'argv', ['reset', 'bogus'])
    with pytest.raises(ValueError):
        module.main()


def test_watch_paper_swarm_sample_and_once_loop(monkeypatch, tmp_path, mirror):
    import urllib.request
    import dashboard.data_collector
    module = _load_at('watch_paper_swarm', SCRIPTS / 'watch_paper_swarm.py',
                      mirror / 'scripts' / 'watch_paper_swarm.py')
    root = tmp_path / 'botroot'
    (root / 'core' / 'logs').mkdir(parents=True)
    (root / 'history').mkdir()
    (root / 'logs').mkdir()
    (root / 'core' / 'paper_AAA.json').write_text('{}', encoding='utf-8')
    (root / 'history' / 'archive_1750000000.json').write_text('{}', encoding='utf-8')
    (root / 'core' / 'logs' / 'paper_a.log').write_text('line one\n', encoding='utf-8')
    (root / 'logs' / 'supervisor.log').write_text('sup\n', encoding='utf-8')
    monkeypatch.setattr(dashboard.data_collector, '_get_pm2_list', lambda: [])
    db = sqlite3.connect(str(tmp_path / 'obs.sqlite'))
    summary = module.sample(db, root, now=1000.0)
    assert summary['classification_version'] == 2
    module.sample(db, root, now=1010.0)  # gap_seconds branch
    db.execute('SELECT max(gap_seconds) FROM observations').fetchone()
    db.close()

    # --once main loop against the (empty) mirror root; dashboard probe must not hit the network.
    tracked = []
    real_connect = sqlite3.connect

    def tracking(database, *args, **kwargs):
        conn = real_connect(database, *args, **kwargs)
        if str(database).startswith(str(mirror)):
            tracked.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, 'connect', tracking)
    monkeypatch.setattr(sys, 'argv', ['watch', '--once'])

    def no_opener(*args, **kwargs):
        raise OSError('no dashboard')

    monkeypatch.setattr(urllib.request, 'build_opener', no_opener)
    monkeypatch.setattr(module, 'sample', lambda db, root_path, now=None:
                       {'reported_closed_net': 0, 'open_net': 0, 'interrupted_net': 0,
                        'reported_cumulative_net': 0, 'verified_net': 0, 'fees_recorded': 0,
                        'session_count': 0, 'errors': []})
    module.main()
    for conn in tracked:
        conn.close()  # main() leaves the sqlite handle open; release it for tmp cleanup
    assert (mirror / 'reports' / 'paper_swarm_continuous' / 'observations.sqlite').exists()


def _kline_rows(count=2, start=1_750_000_000_000):
    return [[start + i * 60_000, '100', '101', '99', str(100.0 + i * 0.1), '10',
             start + i * 60_000 + 59_999, '100', 1, '1', '100', '0'] for i in range(count)]


class _AsyncResp:
    def __init__(self, json_value, status=200):
        self.json_value = json_value
        self.status = status

    async def json(self):
        return self.json_value

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def test_download_zec_12m_klines_depth_and_main(monkeypatch, tmp_path):
    import aiohttp
    module = _load('download_zec_12m', SCRIPTS / 'download_zec_12m.py')
    monkeypatch.setattr(module, 'DATA_DIR', str(tmp_path))

    async def instant_sleep(_seconds):
        pass

    monkeypatch.setattr(module.asyncio, 'sleep', instant_sleep)

    class KlinesSession:
        def __init__(self, fail_first):
            self.fail_first = fail_first
            self.calls = 0

        def get(self, url, **kwargs):
            self.calls += 1
            if 'klines' in url:
                data = {'error': 'rate limited'} if self.fail_first and self.calls == 1 else _kline_rows()
                return _AsyncResp(data)
            return _AsyncResp({'bids': [['100.0', '1']], 'asks': [['101.0', '1']]})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    df = asyncio.run(module.download_klines(KlinesSession(fail_first=False), 'ZECUSDT', 1))
    assert len(df) == 2 and df['close'].iloc[0] == 100.0
    retried = asyncio.run(module.download_klines(KlinesSession(fail_first=True), 'ZECUSDT', 1))
    assert len(retried) == 2
    assert len(asyncio.run(module.download_depth(KlinesSession(fail_first=False), 'ZECUSDT'))) == 2

    session = KlinesSession(fail_first=False)
    monkeypatch.setattr(aiohttp, 'ClientSession', lambda *args, **kwargs: session)
    asyncio.run(module.main())
    assert (tmp_path / 'ZECUSDT_12m.feather').exists()
    assert (tmp_path / 'ZECUSDT_depth_snapshot.json').exists()


def test_download_history_saves_feather(monkeypatch, tmp_path):
    import aiohttp
    module = _load('download_history', SCRIPTS / 'download_history.py')
    monkeypatch.setattr(module, 'DATA_DIR', str(tmp_path))

    async def instant_sleep(_seconds):
        pass

    monkeypatch.setattr(module.asyncio, 'sleep', instant_sleep)

    class Session:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def get(self, url, **kwargs):
            return _AsyncResp(_kline_rows())

    monkeypatch.setattr(aiohttp, 'ClientSession', Session)
    asyncio.run(module.download('AAAUSDT', 1))
    assert (tmp_path / 'AAAUSDT_live_1d.feather').exists()


def test_collect_depth_zec_offline(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock, Mock
    import websockets
    module = _load('collect_depth_zec', SCRIPTS / 'collect_depth_zec.py')
    monkeypatch.setattr(module, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(module, 'DURATION_SEC', 250)

    clock = {'now': 1000.0}

    def fake_time(*args):
        base = clock['now']
        clock['now'] = base + 200
        return base

    monkeypatch.setattr(module.time, 'time', fake_time)

    class FakeWS:
        def __init__(self):
            self.msgs = [json.dumps({'b': [['100.5', '2']] * 25, 'a': [['101.2', '2']] * 25, 'u': 42})]
            self.sent = []

        async def send(self, message):
            self.sent.append(json.loads(message))

        async def recv(self):
            if self.msgs:
                return self.msgs.pop(0)
            raise asyncio.TimeoutError()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    fake_socket = object()
    connected_socket = AsyncMock(return_value=fake_socket)
    monkeypatch.setattr(module, 'connected_socket', connected_socket)
    ws = FakeWS()
    connect = Mock(return_value=ws)
    monkeypatch.setattr(websockets, 'connect', connect)
    snapshots = asyncio.run(module.collect())
    connected_socket.assert_awaited_once_with('fstream.binance.com', 443)
    connect.assert_called_once_with(module.WS_URL, ping_interval=20, ping_timeout=10,
                                    close_timeout=5, proxy=None, sock=fake_socket)
    assert ws.sent == [{'method': 'SUBSCRIBE', 'params': ['zecusdt@depth20@100ms'], 'id': 1}]
    assert len(snapshots) == 1
    assert (tmp_path / 'ZECUSDT_depth_24h.feather').exists()
    assert (tmp_path / 'ZECUSDT_depth_24h.json').exists()


def _write_fixture(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1), encoding='utf-8')


def test_visualize_zec_backtest_import_builds_html(mirror):
    data = mirror / 'data'
    reports = mirror / 'reports'
    equity = [1000.0 + i * 0.05 for i in range(60)]
    _write_fixture(reports / 'zec_12m_backtest_result.json',
                   {'profit_pct': 1.5e-02, 'max_dd_pct': 2.0, 'sortino_ratio': 0.7,
                    'cycles': 12, 'initial_capital': 1000.0})
    _write_fixture(reports / 'zec_12m_equity.json', equity)
    module = _load_at('visualize_zec_backtest', SCRIPTS / 'visualize_zec_backtest.py',
                      mirror / 'scripts' / 'visualize_zec_backtest.py')
    assert (reports / 'zec_12m_backtest.html').exists()


def test_visualize_365d_backtest_and_dashboards_import_build_html(mirror):
    reports = mirror / 'reports'
    equity = [1000.0 + i * 0.05 for i in range(60)]
    result = {
        'profit_pct': 1.5, 'max_dd_pct': 2.0, 'sortino_ratio': 0.7, 'cycles': 4,
        'initial_capital': 1000.0, 'data_window': {'feather': 'BTCUSDT_live_365d.feather',
                                                   'until_utc': '2026-01-02T00:00:00'},
        'fills': [{'bar': 5, 'side': 'BUY', 'leg': 'BASE_LONG', 'qty': 0.001, 'price': 50000.0,
                   'commission': 0.1, 'realized_pnl': 1.0, 'tpv': 1001.0}],
        'events': [{'bar': 7, 'kind': 'trailing_stop', 'tpv': 998.0},
                   {'bar': 9, 'kind': 'liquidation', 'tpv': 997.0},
                   {'bar': 3, 'kind': 'other', 'tpv': 1000.0}],
        'siphoning_reserve': 5.0, 'liquidations': 1, 'trailing_stops': 1,
        'skipped_expansions': 2, 'trend_guard_blocks': 1, 'asset_chg_pct': 0.4,
        'fill_count': 1,
    }
    _write_fixture(reports / 'btc_365d_backtest_result.json', result)
    _write_fixture(reports / 'btc_365d_equity.json', equity)
    _load_at('visualize_365d_backtest', SCRIPTS / 'visualize_365d_backtest.py',
             mirror / 'scripts' / 'visualize_365d_backtest.py')
    assert (reports / 'btc_365d_backtest.html').exists()
    _load_at('visualize_365d_dashboard', SCRIPTS / 'visualize_365d_dashboard.py',
             mirror / 'scripts' / 'visualize_365d_dashboard.py')
    assert (reports / 'btc_365d_dashboard.html').exists()


def _patch_run_backtest(monkeypatch, fake):
    import backtest.backtest_rebalance as bt
    monkeypatch.setattr(bt, 'run_backtest', fake)
    return bt


def test_visualize_zec_dashboard_build_payload_and_main(monkeypatch, tmp_path):
    import numpy as np
    import pandas as pd
    module = _load('visualize_zec_dashboard', SCRIPTS / 'visualize_zec_dashboard.py')
    reports = tmp_path / 'reports'
    reports.mkdir()
    data = tmp_path / 'data'
    data.mkdir()
    n_small = 60
    close = 200.0 + 0.01 * np.arange(n_small)
    pd.DataFrame({'open': close, 'high': close + 0.5, 'low': close - 0.5,
                  'close': close, 'volume': np.full(n_small, 10.0)}
                 ).to_feather(data / 'ZECUSDT_live.feather')
    equity = [1000.0 + i * 0.05 for i in range(n_small)]
    result = {
        'ticker': 'ZECUSDT', 'data_window': {'feather': 'ZECUSDT_live.feather', 'offset': 0,
                                              'bars': n_small},
        'initial_capital': 1000.0, 'profit_pct': 0.3, 'max_dd_pct': 1.0, 'sortino_ratio': 0.2,
        'cycles': 1, 'accounting_ok': True, 'funding_model': 'signed_actual',
        'funding_source': 'portfolio',
        'fills': [{'bar': 3, 'side': 'BUY', 'leg': 'BASE_LONG', 'qty': 0.5, 'price': 200.2,
                    'mid': 200.0, 'commission': 0.04, 'realized_pnl': 0.1, 'tpv': 1000.2},
                   {'bar': 4, 'side': 'TRANSFER', 'leg': 'VIRTUAL', 'qty': 10.0, 'price': 0.0,
                    'mid': 0.0}],
        'events': [{'bar': 5, 'kind': 'cooldown_start', 'reason': 'dd', 'until_bar': 8},
                   {'bar': 8, 'kind': 'cooldown_end'},
                   {'bar': 2, 'kind': 'funding', 'funding_amount': 0.01}],
        'rebalances': [{'step': 3, 'price': 200.0, 'tpv': 1000.2, 'tpv_before': 1000.0,
                        'cycle_id': 1}],
        'siphoning_log': [{'bar': 9, 'amount': 1.0}],
    }
    _write_fixture(reports / 'zec_12m_backtest_result.json', result)
    _write_fixture(reports / 'zec_12m_equity.json', equity)
    (reports / 'plotly-2.27.0.min.js').write_text('// offline stub', encoding='utf-8')
    monkeypatch.setattr(module, 'REPORTS', str(reports))
    monkeypatch.setattr(module, 'DATA', str(data))
    payload = module.build_payload('zec_12m')
    assert payload['meta']['ticker'] == 'ZECUSDT'
    assert payload['meta']['time_source'] == 'mtime'
    assert payload['counts']['fills'] == 2
    assert payload['cooldowns'][0]['start_bar'] == 5 and payload['cooldowns'][0]['end_bar'] == 8

    # binning branch: > MAX_CANDLES rows force the 1440/2 aggregation path
    n_big = 37_000
    times = 1_791_590_400_000 + np.arange(n_big) * 60_000
    close_big = 200.0 + 0.001 * np.arange(n_big)
    pd.DataFrame({'time': times, 'open': close_big, 'high': close_big + 0.5,
                  'low': close_big - 0.5, 'close': close_big,
                  'volume': np.full(n_big, 10.0)}).to_feather(data / 'ZECUSDT_12m_big.feather')
    _write_fixture(reports / 'zec_big_backtest_result.json',
                   {'ticker': 'ZECUSDT', 'data_window': {'feather': 'ZECUSDT_12m_big.feather'},
                    'initial_capital': 1000.0})
    _write_fixture(reports / 'zec_big_equity.json', [1000.0 + i * 0.001 for i in range(n_big)])
    big = module.build_payload('zec_big')
    assert big['price']['ohlc']['bin'] != '1m'

    monkeypatch.setattr(module, 'TEMPLATE', str(ROOT / 'scripts' / 'dashboard_template.html'))
    assert module.main('zec_12m') == 0
    assert (reports / 'zec_12m_dashboard.html').exists()


def test_run_zec_backtest_with_a_faked_engine(monkeypatch, mirror):
    fake_results = []

    async def fake_run_backtest(config_path=None, data_dir=None, live_mode=False,
                                ticker_override=None, days=365.0, quiet=True, **kwargs):
        fake_results.append(ticker_override)
        return {'profit_pct': 0.001, 'max_dd_pct': 2.0, 'sortino_ratio': 0.1, 'cycles': 3,
                'liquidations': 0, 'trailing_stops': 0, 'skipped_expansions': 0,
                'accounting_ok': True, 'initial_capital': 1000.0, 'final_tpv': 1001.0,
                'equity_curve': [1000.0 + i * 0.1 for i in range(60)], 'fills': [], 'events': []}

    _patch_run_backtest(monkeypatch, fake_run_backtest)
    monkeypatch.setattr(sys, 'argv', ['run_zec_backtest'])
    monkeypatch.setattr(sys, 'path', [str(SCRIPTS)] + list(sys.path))
    import visualize_zec_dashboard as viz_module  # real repo module, no I/O at import time
    monkeypatch.setattr(viz_module, 'REPORTS', str(mirror / 'reports'))
    monkeypatch.setattr(viz_module, 'DATA', str(mirror / 'data'))
    monkeypatch.setattr(viz_module, 'TEMPLATE', str(ROOT / 'scripts' / 'dashboard_template.html'))
    _load_at('run_zec_backtest', SCRIPTS / 'run_zec_backtest.py',
             mirror / 'scripts' / 'run_zec_backtest.py')
    assert fake_results == ['ZECUSDT']
    reports = mirror / 'reports'
    assert (reports / 'zec_12m_backtest_result.json').exists()
    assert (reports / 'zec_12m_equity.json').exists()
    assert (reports / 'zec_12m_dashboard.html').exists()


def test_run_365d_backtest_with_a_faked_engine(monkeypatch, tmp_path, mirror):
    async def fake_run_backtest(**kwargs):
        return {'profit_pct': 0.5, 'cycles': 2, 'equity_curve': [1000.0, 1000.5]}

    _patch_run_backtest(monkeypatch, fake_run_backtest)
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'reports').mkdir()
    module = _load_at('run_365d_backtest', SCRIPTS / 'run_365d_backtest.py',
                      mirror / 'scripts' / 'run_365d_backtest.py')
    assert (tmp_path / 'reports' / 'btc_365d_backtest_result.json').exists()
    assert (tmp_path / 'reports' / 'btc_365d_equity.json').exists()
    assert module is not None

def test_run_zec_replay_precheck_failure_refuses_to_run(mirror, monkeypatch):
    """Fail-closed precheck: a stale SHA256 baseline refuses the run before any work."""
    import backtest.backtest_rebalance as bt

    async def _unused(**kwargs):
        raise AssertionError('backtest must not run when the precheck fails')

    monkeypatch.setattr(bt, 'run_backtest', _unused)
    baseline = mirror / 'reports' / 'b1_remaining_checks'
    baseline.mkdir(parents=True)
    baseline_file = baseline / 'SHA256_baseline.txt'
    baseline_file.write_text(
        'deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef * core/main.py\n'
        'f00d * scripts/run_zec_replay.py\n'
        'f00d * data/does_not_exist.json\n', encoding='utf-8')
    try:
        with pytest.raises(SystemExit) as exc:
            _load_at('run_zec_replay_fail', SCRIPTS / 'run_zec_replay.py',
                     mirror / 'scripts' / 'run_zec_replay.py')
        assert exc.value.code == 1
    finally:
        baseline_file.unlink()


def test_run_zec_replay_full_flow_offline(mirror, monkeypatch):
    """Full determinism gate with the engine faked; all artifacts stay under the mirror."""
    import backtest.backtest_rebalance as bt

    def fake_result(config_path, data_dir):
        scenario = json.loads((Path(str(data_dir)) / 'zec_funding_scenario_replay.json').read_text(encoding='utf-8'))
        total = 0.0
        funding_events = []
        for k, event in enumerate(scenario):
            day, hour = k // 3, (0, 8, 16)[k % 3]
            bar = day * 1440 + hour * 60  # feather bar for this event_time
            total += event['signed_amount']
            funding_events.append({'kind': 'funding', 'bar': bar, 'tpv': 1000.0,
                                   'detail': f"{event['event_id']} amount={event['signed_amount']}",
                                   'funding_amount': event['signed_amount']})
        final = 1000.0 + total
        halt_bar = funding_events[-1]['bar']  # halt on the last funding bar
        funding_events.append({'kind': 'funding_shortfall', 'bar': halt_bar, 'tpv': final,
                               'detail': 'halt'})
        eq = [1000.0] * halt_bar + [final]    # flat until halt, then final
        return {
            'final_tpv': final, 'total_funding_paid': -total, 'funding_model': 'signed_actual',
            'equity_curve': eq,
            'events': funding_events, 'fills': [], 'commission_paid': 0.0, 'slippage_paid': 0.0,
            'rebalances': [], 'cycles': 0, 'accounting': {'ok': True, 'residual': 0.0},
            'accounting_ok': True, 'liquidations': 0, 'fills_per_leg': {}, 'open_positions': {},
            'funding_events': len(scenario), 'funding_paid': -total, 'funding_from_reserve': 0.0,
            'funding_source': 'portfolio', 'funding_halt_active': True,
            'initial_capital': 1000.0, 'siphoning_log': [], 'data_window': {},
        }

    async def fake_run_backtest(config_path=None, data_dir=None, live_mode=False,
                                ticker_override=None, days=10.0, quiet=True, **kwargs):
        return fake_result(config_path, data_dir)

    monkeypatch.setattr(bt, 'run_backtest', fake_run_backtest)
    with pytest.raises(SystemExit) as exc:
        _load_at('run_zec_replay', SCRIPTS / 'run_zec_replay.py',
                 mirror / 'scripts' / 'run_zec_replay.py')
    assert exc.value.code == 0
    evidence = json.loads((mirror / 'reports' / 'b1_zec_replay' / 'evidence.json').read_text())
    assert evidence['run2_same_as_run1'] is True
    assert evidence['post_shortfall_invariants'] is True

def _intraday_module():
    return _load('download_intraday_90d', SCRIPTS / 'download_intraday_90d.py')


def _intraday_zip(csv_text):
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('klines.csv', csv_text)
    return buf.getvalue()


def _intraday_csv(module, period_index, count=5):
    start_ms = int(module.START.timestamp() * 1000)
    lines = ['open_time,open,high,low,close,volume,close_time,quote_volume,'
             'trades,taker_buy_volume,taker_buy_quote_volume,ignore']
    for i in range(count):
        ot = start_ms + ((period_index * count + i) * 60_000)
        lines.append(f'{ot},100.0,101.0,99.0,100.5,10.0,{ot + 59_999},1000.0,7,5.0,500.0,0')
    return '\n'.join(lines) + '\n'


def test_download_intraday_parse_archive_variants(monkeypatch):
    module = _intraday_module()
    good = _intraday_zip(_intraday_csv(module, 0))
    frame = module.parse_archive(good)
    assert len(frame) == 5 and str(frame['open_time'].dtype) == 'int64'
    # header row is stripped
    # 13 columns must be rejected
    bad_cols = _intraday_zip(chr(10).join([
        'open_time,open,high,low,close,volume,close_time,quote_volume,trades,taker_buy_volume,'
        'taker_buy_quote_volume,ignore,extra',
        '1,1,1,1,1,1,1,1,1,1,1,1,1']) + chr(10))
    with pytest.raises(ValueError):
        module.parse_archive(bad_cols)
    dup = _intraday_zip(_intraday_csv(module, 0, count=3) + _intraday_csv(module, 0, count=3))
    with pytest.raises(ValueError):
        module.parse_archive(dup)  # duplicated / unordered candle times
    bad_bounds = _intraday_zip(_intraday_csv(module, 1).replace('101.0', '98.0'))
    with pytest.raises(ValueError):
        module.parse_archive(bad_bounds)  # high below open/close/low


def test_download_intraday_fetch_paths(monkeypatch):
    import aiohttp
    module = _intraday_module()

    async def instant_sleep(_seconds):
        pass

    monkeypatch.setattr(module.asyncio, 'sleep', instant_sleep)

    class Content:
        def __init__(self, chunks):
            self.chunks = chunks

        def iter_chunked(self, _size):
            async def gen():
                for chunk in self.chunks:
                    yield chunk
            return gen()

    class FetchResp:
        def __init__(self, status, chunks, error=None):
            self.status = status
            self.content = Content(chunks)
            self.error = error

        async def __aenter__(self):
            if self.error:
                raise self.error
            return self

        async def __aexit__(self, *args):
            return False

        def raise_for_status(self):
            if self.status >= 400:
                raise aiohttp.ClientResponseError(None, None, f'{self.status}')

    def session_for(responder):
        class S:
            calls = 0

            def get(self, url, **kwargs):
                type(self).calls += 1
                return responder(url, type(self).calls)

        S.responder = responder
        return S()

    ok = session_for(lambda url, n: FetchResp(200, [b'ab', b'cd']))
    assert asyncio.run(module.fetch(ok, 'https://x/1.zip', 1024)) == b'abcd'

    assert asyncio.run(module.fetch(session_for(lambda url, n: FetchResp(404, [])),
                                   'https://x/2.zip', 1024)) is None
    with pytest.raises(RuntimeError):
        asyncio.run(module.fetch(session_for(lambda url, n: FetchResp(429, [])),
                                 'https://x/3.zip', 1024))
    big = session_for(lambda url, n: FetchResp(200, [b'x' * 100]))
    with pytest.raises(ValueError):
        asyncio.run(module.fetch(big, 'https://x/4.zip', 50))

    retried = [0]

    def flaky(url, n):
        retried[0] += 1
        if retried[0] == 1:
            return FetchResp(200, [], error=aiohttp.ClientError('boom'))
        return FetchResp(200, [b'z'])

    assert asyncio.run(module.fetch(session_for(flaky), 'https://x/5.zip', 1024)) == b'z'

def _period_ordinal(period):
    if period in ('2026-07', '2026-08', '2026-09'):
        return {'2026-07': 0, '2026-08': 1, '2026-09': 2}[period]
    day = int(period.split('-')[-1])
    return day + 2


def _bytes_resp(payload):
    class Resp:
        status = 200

        class Content:
            def iter_chunked(self, _size):
                async def gen():
                    yield payload

                return gen()

        def __init__(self):
            pass

        def raise_for_status(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    return Resp()


def test_download_intraday_symbol_offline(monkeypatch, tmp_path):
    import hashlib
    module = _intraday_module()
    ds = tmp_path / 'ds'
    (ds / 'raw' / 'AAAUSDT').mkdir(parents=True)
    (ds / 'candles').mkdir()
    (ds / 'symbols').mkdir()
    monkeypatch.setattr(module, 'DATASET', ds)

    # cached on-disk artifacts for the first monthly period (covers the read-from-disk branch)
    first_name = 'AAAUSDT-1m-2026-07.zip'
    first_zip = _intraday_zip(_intraday_csv(module, 0))
    raw_dir = ds / 'raw' / 'AAAUSDT'
    (raw_dir / first_name).write_bytes(first_zip)
    (raw_dir / (first_name + '.CHECKSUM')).write_bytes(
        f'{hashlib.sha256(first_zip).hexdigest()}  {first_name}'.encode())

    async def fake_fetch(session, url, max_bytes):
        name = url.split('/')[-1]
        base = name[:-len('.CHECKSUM')] if name.endswith('.CHECKSUM') else name
        period = base.split('-', 2)[2][:-len('.zip')]
        zip_bytes = _intraday_zip(_intraday_csv(module, _period_ordinal(period)))
        if name.endswith('.CHECKSUM'):
            if period == '2026-10-05':
                return None
            if period == '2026-10-06':
                return b'notahex  ' + base.encode()
            return f'{hashlib.sha256(zip_bytes).hexdigest()}  {base}'.encode()
        return zip_bytes

    monkeypatch.setattr(module, 'fetch', fake_fetch)
    result = asyncio.run(module.download_symbol(object(), 'AAAUSDT'))
    assert result['status'] == 'PARTIAL'
    assert result['rows'] == 24 * 5
    assert result['missing_minutes'] > 0
    statuses = [r['status'] for r in result['sources']]
    assert statuses.count('ERROR') == 1
    assert statuses.count('ARCHIVE_NOT_FOUND') == 1
    assert statuses.count('VERIFIED') == 24
    assert (ds / 'candles' / 'AAAUSDT_live.feather').exists()
    assert (ds / 'symbols' / 'AAAUSDT.json').exists()


def test_download_intraday_main_runs_offline(monkeypatch, tmp_path):
    import aiohttp
    import pandas as pd
    module = _intraday_module()
    root = tmp_path / 'root'
    ds = tmp_path / 'ds'
    (root / 'reports').mkdir(parents=True)
    (root / 'data').mkdir()
    (ds / 'raw').mkdir(parents=True)
    (ds / 'candles').mkdir()
    (ds / 'symbols').mkdir()
    tickers = [f'T{i:03d}USDT' for i in range(1, 155)]
    (root / 'reports' / 'sweep_1d_tickers.csv').write_text(
        'ticker' + chr(10) + chr(10).join(tickers) + chr(10), encoding='utf-8')
    for ticker in tickers:
        pd.DataFrame({'close': [1.0]}).to_feather(root / 'data' / f'{ticker}_live.feather')
    (root / 'config.json').write_text('{}', encoding='utf-8')
    monkeypatch.setattr(module, 'ROOT', root)
    monkeypatch.setattr(module, 'DATASET', ds)
    manifest0 = {'universe': tickers, 'start_utc': str(module.START),
                 'end_exclusive_utc': str(module.END)}
    (ds / 'manifest.json').write_text(json.dumps(manifest0), encoding='utf-8')

    async def fake_fetch(session, url, max_bytes):
        name = url.split('/')[-1]
        base = name[:-len('.CHECKSUM')] if name.endswith('.CHECKSUM') else name
        period = base.split('-', 2)[2][:-len('.zip')]
        import hashlib
        zip_bytes = _intraday_zip(_intraday_csv(module, _period_ordinal(period)))
        if name.endswith('.CHECKSUM'):
            return f'{hashlib.sha256(zip_bytes).hexdigest()}  {base}'.encode()
        return zip_bytes

    class Session:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def get(self, url, **kwargs):
            return _bytes_resp(b'')

    monkeypatch.setattr(module, 'fetch', fake_fetch)
    monkeypatch.setattr(aiohttp, 'ClientSession', Session)
    asyncio.run(module.main())
    manifest = json.loads((ds / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['status'] == 'FINISHED'
    assert len(manifest['results']) == 154
    assert manifest['original_inputs_unchanged'] is True
    assert manifest['status_counts'].get('PARTIAL') == 154
