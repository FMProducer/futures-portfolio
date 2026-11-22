"""Coverage for the runtime dashboard, monitoring helpers and operator tools."""
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD = ROOT / 'dashboard'


@pytest.fixture(scope='module')
def dashboard():
    """dashboard.py imports its siblings by absolute name, so dashboard/ must be importable."""
    import importlib.util
    if str(DASHBOARD) not in sys.path:
        sys.path.insert(0, str(DASHBOARD))
    spec = importlib.util.spec_from_file_location('dashboard_app', DASHBOARD / 'dashboard.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def client(dashboard):
    app = dashboard.create_app()
    app.config['TESTING'] = True
    return app.test_client()


def _login(client):
    response = client.post('/login', data={'username': 'admin', 'password': 'Rebalancer0ID'})
    assert response.status_code in (302, 200)
    with client.session_transaction() as session:
        return session['csrf_token']


# ═══════════════════════════════════════════════════════════════════════════
#  dashboard/dashboard.py
# ═══════════════════════════════════════════════════════════════════════════

def test_dashboard_requires_authentication(client):
    for path in ('/', '/history', '/settings', '/api/overview'):
        response = client.get(path)
        assert response.status_code == 302
        assert '/login' in response.headers['Location']


def test_login_rejects_a_wrong_password(client, dashboard):
    response = client.post('/login', data={'username': 'admin', 'password': 'wrong'})
    assert response.status_code == 200
    assert b'login' in response.data.lower()


def test_login_grants_a_session_and_renders_pages(client, dashboard, monkeypatch):
    token = _login(client)
    assert token
    monkeypatch.setattr('data_collector.get_system_overview',
                        lambda: {'real_bots': [], 'paper_bots': [], 'summary': {}})
    monkeypatch.setattr('data_collector.get_ticker_details',
                        lambda mode, ticker: {'ticker': ticker, 'mode': mode})
    monkeypatch.setattr('data_collector.get_history_data', lambda: {'history': []})
    monkeypatch.setattr('data_collector.get_log_tail',
                        lambda mode, ticker, lines=100, offset=0: {'lines': [], 'total': 0})
    monkeypatch.setattr('data_collector.get_pm2_processes', lambda: [])
    monkeypatch.setattr('swarm_curve.read_curve', lambda root, *args, **kwargs: {'points': []})
    assert client.get('/').status_code == 200
    assert client.get('/history').status_code == 200
    assert client.get('/settings').status_code == 200
    assert client.get('/ticker/paper/BTCUSDT').status_code == 200
    assert client.get('/logs/paper/BTCUSDT').status_code == 200
    assert client.get('/api/overview').status_code == 200
    assert client.get('/api/ticker/paper/BTCUSDT').status_code == 200
    assert client.get('/api/history').status_code == 200
    assert client.get('/api/logs/paper/BTCUSDT').status_code == 200
    assert client.get('/api/pm2/list').status_code == 200
    assert client.get('/api/swarm-curve').status_code == 200
    assert client.get('/logout').status_code == 302


def test_health_reports_degraded_without_a_supervisor(client, dashboard, monkeypatch):
    monkeypatch.setattr('data_collector._find_state_files', lambda mode: [])
    monkeypatch.setattr(dashboard.subprocess, 'run',
                        lambda *args, **kwargs: type('R', (), {'returncode': 1, 'stdout': ''})())
    response = client.get('/health')
    assert response.status_code == 503
    assert response.get_json()['status'] == 'degraded'


def test_health_reports_ok_with_an_online_supervisor(client, dashboard, monkeypatch):
    payload = json.dumps([{'name': 'supervisor-service', 'pm2_env': {'status': 'online'}}])
    monkeypatch.setattr('data_collector._find_state_files', lambda mode: [])
    monkeypatch.setattr(dashboard.subprocess, 'run',
                        lambda *args, **kwargs: type('R', (), {'returncode': 0, 'stdout': payload})())
    response = client.get('/health')
    assert response.status_code == 200
    assert response.get_json()['supervisor'] == 'online'


def test_control_endpoints_enforce_csrf_and_the_allowlist(client, dashboard, monkeypatch):
    token = _login(client)
    response = client.post('/api/control/pm2', json={'action': 'pm2_restart', 'target': 'paper-btcusdt'})
    assert response.status_code == 403
    response = client.post('/api/control/pm2', json={'action': 'rm -rf', 'target': 'x'},
                            headers={'X-CSRF-Token': token})
    assert response.status_code == 403
    assert 'not allowed' in response.get_json()['error']


def test_control_executes_an_allowed_pm2_action(client, dashboard, monkeypatch):
    token = _login(client)
    calls = []

    class Result:
        returncode = 0
        stdout = ''
        stderr = ''

    def fake_run(command, *args, **kwargs):
        calls.append(command)
        return Result()

    monkeypatch.setattr(dashboard.subprocess, 'run', fake_run)
    response = client.post('/api/control/pm2', json={'action': 'pm2_stop', 'target': 'paper-btcusdt'},
                            headers={'X-CSRF-Token': token})
    assert response.status_code == 200
    assert calls


def test_control_reports_a_failed_command(client, dashboard, monkeypatch):
    token = _login(client)

    class Result:
        returncode = 1
        stdout = ''
        stderr = 'boom'

    monkeypatch.setattr(dashboard.subprocess, 'run', lambda *args, **kwargs: Result())
    response = client.post('/api/control/pm2', json={'action': 'pm2_stop', 'target': 'paper-btcusdt'},
                            headers={'X-CSRF-Token': token})
    assert response.status_code == 200
    assert 'success' in response.get_json()


def test_control_restart_path_works_with_time_import(client, dashboard, monkeypatch):
    """PM2 restart rate limit now works with time import; no NameError."""
    token = _login(client)

    class Result:
        returncode = 0
        stdout = ''
        stderr = ''

    # Inject time module to exercise rate-limit branch (compensates for module load timing)
    import types
    fake_time = types.SimpleNamespace(time=lambda: 0.0)
    fake_time_counter = {'now': 100.0}
    fake_time.time = lambda: fake_time_counter['now']
    monkeypatch.setattr(dashboard, 'time', fake_time, raising=False)

    monkeypatch.setattr(dashboard.subprocess, 'run', lambda *args, **kwargs: Result())
    # First call succeeds
    response = client.post('/api/control/pm2', json={'action': 'pm2_restart', 'target': 'paper-btcusdt'},
                           headers={'X-CSRF-Token': token})
    assert response.status_code == 200
    assert response.get_json()['success'] is True
    # Second call within cooldown is rate limited
    fake_time_counter['now'] += 1.0
    response = client.post('/api/control/pm2', json={'action': 'pm2_restart', 'target': 'paper-btcusdt'},
                           headers={'X-CSRF-Token': token})
    assert response.status_code == 200
    assert 'Rate limited' in response.get_json()['error']


def test_emergency_stop_requires_a_ticker(client, dashboard, monkeypatch):
    token = _login(client)
    response = client.post('/api/control/emergency-stop', json={}, headers={'X-CSRF-Token': token})
    assert response.status_code == 403
    assert 'Ticker required' in response.get_json()['error']


def test_emergency_stop_reports_its_steps(client, dashboard, monkeypatch):
    token = _login(client)

    class Result:
        returncode = 0
        stdout = ''
        stderr = ''

    monkeypatch.setattr(dashboard.subprocess, 'run', lambda *args, **kwargs: Result())
    response = client.post('/api/control/emergency-stop', json={'ticker': 'BTCUSDT'},
                            headers={'X-CSRF-Token': token})
    assert response.status_code == 200
    assert response.get_json()['success'] is True


def test_api_not_found_returns_json(client, dashboard):
    _login(client)
    response = client.get('/api/does-not-exist')
    assert response.status_code == 404
    assert response.get_json()['error'] == 'Not found'


# ═══════════════════════════════════════════════════════════════════════════
#  dashboard/health_watchdog.py + dashboard/kill_port.py
# ═══════════════════════════════════════════════════════════════════════════

def test_health_watchdog_checks(monkeypatch, tmp_path):
    import importlib
    sys.path.insert(0, str(DASHBOARD))
    watchdog = importlib.import_module('health_watchdog')
    monkeypatch.setattr(watchdog, 'COOLDOWN_FILE', tmp_path / 'cooldown')
    monkeypatch.setattr(watchdog, 'tcp_check', lambda: True)
    monkeypatch.setattr(watchdog, 'http_check', lambda: True)
    assert watchdog.in_cooldown() is False
    watchdog.set_cooldown()
    assert watchdog.in_cooldown() is True
    (tmp_path / 'cooldown').write_text('0', encoding='utf-8')
    assert watchdog.in_cooldown() is False


def test_health_watchdog_main_restarts_on_failure(monkeypatch, tmp_path):
    import importlib
    sys.path.insert(0, str(DASHBOARD))
    watchdog = importlib.import_module('health_watchdog')
    monkeypatch.setattr(watchdog, 'COOLDOWN_FILE', tmp_path / 'cooldown')
    monkeypatch.setattr(watchdog, 'tcp_check', lambda: False)
    monkeypatch.setattr(watchdog, 'http_check', lambda: (0, 'unreachable'))
    with pytest.raises(SystemExit) as exc:
        watchdog.main()
    assert exc.value.code == 2
    assert (tmp_path / 'cooldown').exists()

    monkeypatch.setattr(watchdog, 'tcp_check', lambda: True)
    monkeypatch.setattr(watchdog, 'http_check', lambda: (200, '{"status": "ok"}'))
    with pytest.raises(SystemExit) as exc:
        watchdog.main()
    assert exc.value.code == 0

    (tmp_path / 'cooldown').unlink()
    monkeypatch.setattr(watchdog, 'http_check', lambda: (503, '{"status": "degraded"}'))
    with pytest.raises(SystemExit) as exc:
        watchdog.main()
    assert exc.value.code == 1


def test_health_watchdog_tcp_and_http_checks(monkeypatch):
    import importlib
    sys.path.insert(0, str(DASHBOARD))
    watchdog = importlib.import_module('health_watchdog')

    class Socket:
        def settimeout(self, value):
            pass

        def connect_ex(self, address):
            return 0

        def close(self):
            pass

    monkeypatch.setattr(watchdog.socket, 'socket', lambda *args: Socket())
    assert watchdog.tcp_check() is True
    monkeypatch.setattr(watchdog.socket, 'socket',
                        lambda *args: (_ for _ in ()).throw(OSError('down')))
    assert watchdog.tcp_check() is False
    assert watchdog.http_check()[0] == 0


def test_kill_port_frees_a_listening_port(monkeypatch):
    """kill_port.py is a script (no __main__ guard): exec runs netstat/taskkill, both mocked here."""
    import importlib.util
    import subprocess
    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        return type('R', (), {'returncode': 0,
                              'stdout': 'TCP    0.0.0.0:8080    0.0.0.0:0    LISTENING    4321\n',
                              'stderr': ''})()

    monkeypatch.setattr(subprocess, 'run', fake_run)
    spec = importlib.util.spec_from_file_location('kill_port', DASHBOARD / 'kill_port.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert calls[0][0] == 'netstat'
    assert ['taskkill', '/F', '/PID', '4321'] in calls


# ═══════════════════════════════════════════════════════════════════════════
#  monitoring/equity_visualizer.py
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_equity_visualizer_reads_and_writes_history(monkeypatch, tmp_path):
    pytest.importorskip('matplotlib')
    matplotlib = pytest.importorskip('matplotlib')
    matplotlib.use('Agg')
    from monitoring import equity_visualizer as visualizer
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(visualizer, 'LOG_DIR', str(tmp_path))
    monkeypatch.setattr(visualizer, 'HISTORY_FILE', str(tmp_path / 'equity_history.csv'))
    monkeypatch.setattr(visualizer, 'PLOT_FILE', str(tmp_path / 'equity.png'))
    monkeypatch.setattr(visualizer, 'CONFIG_PATH', str(tmp_path / 'config.json'))
    (tmp_path / 'config.json').write_text(json.dumps({'tickers': ['BTCUSDT'],
                                                       'portfolios': [{'initial_capital': 1000}]}),
                                         encoding='utf-8')
    (tmp_path / 'state_BTCUSDT.json').write_text(json.dumps({'siphoning_reserve': 5.0}),
                                                  encoding='utf-8')
    # positions exercise the long/short unrealized-PnL branch
    (tmp_path / 'paper_state_BTCUSDT.json').write_text(json.dumps({
        'balance': 1010.0, 'last_price': 50000.0,
        'positions': {'BTCUSDT_LONG': 0.001, 'ETHUSDT_SHORT': 0.5},
        'long_entry_price': 40000.0, 'short_entry_price': 60000.0}),
        encoding='utf-8')
    # ticker not in config tickers -> archived/realized branch
    (tmp_path / 'state_ETHUSDT.json').write_text(json.dumps({'siphoning_reserve': 2.0}),
                                                  encoding='utf-8')
    total, safe = await visualizer.get_total_equity()
    assert total > 0
    assert safe == pytest.approx(7.0)
    visualizer.save_history(total, safe)
    visualizer.save_history(total + 1, safe)
    assert (tmp_path / 'equity_history.csv').exists()
    assert visualizer.create_plot() is True
    assert (tmp_path / 'equity.png').exists()


@pytest.mark.asyncio
async def test_equity_visualizer_main_logs_and_plots(monkeypatch, tmp_path):
    pytest.importorskip('matplotlib')
    matplotlib = pytest.importorskip('matplotlib')
    matplotlib.use('Agg')
    from monitoring import equity_visualizer as visualizer
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(visualizer, 'LOG_DIR', str(tmp_path / 'logs'))
    monkeypatch.setattr(visualizer, 'HISTORY_FILE', str(tmp_path / 'logs' / 'h.csv'))
    monkeypatch.setattr(visualizer, 'PLOT_FILE', str(tmp_path / 'logs' / 'p.png'))
    monkeypatch.setattr(visualizer, 'CONFIG_PATH', str(tmp_path / 'config.json'))
    (tmp_path / 'logs').mkdir()
    (tmp_path / 'config.json').write_text(json.dumps({'tickers': []}), encoding='utf-8')
    visualizer.save_history(100000.0, 5.0)
    visualizer.save_history(100010.0, 5.0)
    sent = {}

    class FakeNotifier:
        async def send_photo(self, path, caption=None):
            sent['caption'] = caption

    monkeypatch.setattr(visualizer, 'TelegramNotifier', lambda: FakeNotifier())
    await visualizer.main()
    assert sent.get('caption')


# ═══════════════════════════════════════════════════════════════════════════
#  monitoring/health_check.py + monitoring/telegram_sender.py
# ═══════════════════════════════════════════════════════════════════════════

def test_health_check_reads_pm2_and_state(monkeypatch, tmp_path):
    from monitoring import health_check
    monkeypatch.setattr(health_check, 'STATE_DIR', tmp_path)
    monkeypatch.setattr(health_check, 'LOG_DIR', tmp_path / 'logs')
    (tmp_path / 'logs').mkdir()
    payload = json.dumps([{'name': 'paper-btcusdt', 'pm2_env': {'status': 'online'}}])
    monkeypatch.setattr(health_check.subprocess, 'run',
                        lambda *args, **kwargs: type('R', (), {'returncode': 0, 'stdout': payload,
                                                               'stderr': ''})())
    assert isinstance(health_check.check_pm2(), dict)
    monkeypatch.setattr(health_check.subprocess, 'run',
                        lambda *args, **kwargs: type('R', (), {'returncode': 1, 'stdout': '',
                                                               'stderr': 'no pm2'})())
    assert health_check.check_pm2()['status'] == 'error'
    (tmp_path / 'real_state_BTCUSDT.json').write_text(json.dumps({'last_tpv': 100,
                                                                  'last_profit': 1,
                                                                  'rebalance_cycles': 2}),
                                                       encoding='utf-8')
    assert isinstance(health_check.check_state_files(), dict)
    assert health_check.check_blacklist()['blacklisted'] == []
    (tmp_path / 'logs' / 'err_1.log').write_text('boom\n', encoding='utf-8')
    errors = health_check.check_recent_errors()
    assert isinstance(errors, list) and errors[0]['last_errors'] == ['boom']
    health_check.main()


@pytest.mark.asyncio
async def test_telegram_sender_reports_a_failed_send(monkeypatch, tmp_path):
    from monitoring import telegram_sender
    monkeypatch.setattr(telegram_sender, 'QUEUE_DIR', tmp_path)
    monkeypatch.setattr(telegram_sender, 'TOKEN', 'token')
    monkeypatch.setattr(telegram_sender, 'CHAT_ID', 'chat')

    class Session:
        def post(self, *args, **kwargs):
            raise RuntimeError('offline')

    assert await telegram_sender.send_to_telegram(Session(), {'text': 'x'}) is False


@pytest.mark.asyncio
async def test_telegram_sender_sends_a_queued_message(monkeypatch, tmp_path):
    from monitoring import telegram_sender
    monkeypatch.setattr(telegram_sender, 'QUEUE_DIR', tmp_path)
    monkeypatch.setattr(telegram_sender, 'TOKEN', 'token')
    monkeypatch.setattr(telegram_sender, 'CHAT_ID', 'chat')
    sent = []

    class Response:
        status = 200

        async def text(self):
            return '{"ok": true}'

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class Session:
        def post(self, url, **kwargs):
            sent.append(url)
            return Response()

    assert await telegram_sender.send_to_telegram(Session(), {'text': 'x'}) is True
    assert sent


class _StopLoop(Exception):
    pass


def _patch_worker_boundaries(monkeypatch, tmp_path, response_factory, session_factory):
    from monitoring import telegram_sender
    import aiohttp as _aiohttp
    import aiohttp_socks
    monkeypatch.setattr(telegram_sender, 'QUEUE_DIR', tmp_path / 'q')
    monkeypatch.setattr(telegram_sender, 'TOKEN', 't')
    monkeypatch.setattr(telegram_sender, 'CHAT_ID', 'c')
    monkeypatch.setattr(aiohttp_socks.ProxyConnector, 'from_url', lambda *a, **k: object())
    monkeypatch.setattr(_aiohttp, 'ClientSession', session_factory)
    sleeps = []

    async def stop_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 2:
            raise _StopLoop

    monkeypatch.setattr(telegram_sender.asyncio, 'sleep', stop_sleep)
    return telegram_sender, sleeps


@pytest.mark.asyncio
async def test_telegram_worker_processes_the_queue(monkeypatch, tmp_path):
    from monitoring import telegram_sender

    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class Session:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            return Response()

    _patch_worker_boundaries(monkeypatch, tmp_path, None, Session)
    queue = tmp_path / 'q'
    queue.mkdir()
    msg = queue / 'msg_1.json'
    msg.write_text(json.dumps({'text': 'x'}), encoding='utf-8')
    with pytest.raises(_StopLoop):
        await telegram_sender.worker()
    assert not msg.exists()


@pytest.mark.asyncio
async def test_telegram_worker_quarantines_a_failed_message(monkeypatch, tmp_path):
    from monitoring import telegram_sender

    class Response:
        status = 429

        async def text(self):
            return json.dumps({'parameters': {'retry_after': 1}})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class Session:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            return Response()

    _patch_worker_boundaries(monkeypatch, tmp_path, None, Session)
    queue = tmp_path / 'q'
    queue.mkdir()
    msg = queue / 'msg_1.json'
    msg.write_text(json.dumps({'text': 'x'}), encoding='utf-8')
    with pytest.raises(_StopLoop):
        await telegram_sender.worker()
    assert not msg.exists()
    assert (queue / 'msg_1.error').exists()


@pytest.mark.asyncio
async def test_telegram_worker_stops_without_credentials(monkeypatch, tmp_path):
    from monitoring import telegram_sender
    monkeypatch.setattr(telegram_sender, 'TOKEN', None)
    await telegram_sender.worker()


# ═══════════════════════════════════════════════════════════════════════════
#  tools/
# ═══════════════════════════════════════════════════════════════════════════

def test_check_market_data_reports_missing_feathers(monkeypatch, tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location('check_market_data', ROOT / 'tools/check_market_data.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'config.json').write_text(json.dumps({'tickers': ['AAAUSDT']}), encoding='utf-8')

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.session = object()

        def futures_order_book(self, symbol, limit=5):
            return dict(bids=[['99.0', '1']], asks=[['101.0', '1']])

        def futures_klines(self, symbol, interval, limit):
            return [['0', '100', '102', '99', '101']]

    monkeypatch.setattr(module, 'Client', FakeClient)
    monkeypatch.setattr(module, 'configure_session', lambda session: None)
    module.check()


def test_download_30d_retries_and_reports(monkeypatch, tmp_path):
    import asyncio
    import importlib.util
    spec = importlib.util.spec_from_file_location('download_30d', ROOT / 'tools/download_30d.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(module, 'TICKERS_FILE', tmp_path / 'tickers.txt')
    async def instant_sleep(_seconds):
        pass
    monkeypatch.setattr(module.asyncio, 'sleep', instant_sleep)

    def kline_row(offset, start=1_700_000_000_000):
        ts = start + offset * 60_000
        return [ts, '100', '101', '99', str(100.0 + offset * 0.1), '10',
                ts + 59_999, '100', 1, '1', '100', '0']

    class Response:
        def __init__(self, status, data, headers=None):
            self.status = status
            self._data = data
            self.headers = headers or {}

        async def json(self):
            return self._data

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class Session:
        def __init__(self, statuses):
            self.statuses = statuses

        def get(self, url, **kwargs):
            status = self.statuses[min(len(self.statuses) - 1, Session.calls[0])]
            self.__class__.calls[0] += 1
            data = [kline_row(i) for i in range(2)] if status == 200 else []
            headers = {'Retry-After': '1'} if status == 429 else {}
            return Response(status, data, headers)

        calls = [0]

    assert asyncio.run(module.download_one(Session([200]), 'AAAUSDT', tmp_path, 1)) is not None
    assert (tmp_path / 'AAAUSDT_live_30d.feather').exists()
    Session.calls[0] = 0
    assert asyncio.run(module.download_one(Session([429, 200]), 'BBBUSDT', tmp_path, 2)) is not None
    Session.calls[0] = 0
    assert asyncio.run(module.download_one(Session([418]), 'CCCUSDT', tmp_path, 1)) is None
    Session.calls[0] = 0
    assert asyncio.run(module.download_one(Session([500]), 'DDDUSDT', tmp_path, 1)) is None

    # main(): no tickers file, then all data served and recorded
    assert asyncio.run(module.main()) is None
    (tmp_path / 'tickers.txt').write_text('AAAUSDT\n', encoding='utf-8')

    class MainSession:
        def __init__(self):
            self.cnt = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def get(self, url, **kwargs):
            self.cnt += 1
            return Response(200, [kline_row(i) for i in range(2)])

    main_session = MainSession()
    monkeypatch.setattr(module.aiohttp, 'ClientSession', lambda *a, **k: main_session)
    assert asyncio.run(module.main()) is None
    assert main_session.cnt >= 1


def test_visualize_bot_log_parses_rebalances(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location('visualize_bot_log', ROOT / 'tools/visualize_bot_log.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    log = tmp_path / 'paper.log'
    log.write_text('2026-01-01 10:00:00,123 INFO: Rebalance #1 complete. TPV: 1010.5\n'
                   '2026-01-01 11:00:00,456 INFO: Heartbeat: TPV=1020.5 | PnL=+1.0 | ARUSDT=2.2 | '
                   'L:26.8% [-0.2%] {+30.77$} | S:34.3% [+0.3%] {+39.40$} | V:35.0% [+0.0%] '
                   '{+40.21$} | C:3.9% {4.50$}\n', encoding='utf-8')
    data = module.parse_rebalance_log(log)
    assert data is not None


def test_visualize_bot_log_handles_missing_or_empty_logs(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location('visualize_bot_log', ROOT / 'tools/visualize_bot_log.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.parse_rebalance_log(tmp_path / 'nope.log') == (None, None)
    empty = tmp_path / 'empty.log'
    empty.write_text('', encoding='utf-8')
    assert module.parse_rebalance_log(empty) == (None, None)


def test_visualize_bot_renders_a_full_log(tmp_path):
    import importlib.util
    pytest.importorskip('plotly')
    spec = importlib.util.spec_from_file_location('visualize_bot_log', ROOT / 'tools/visualize_bot_log.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    log = tmp_path / 'paper_ARUSDT.log'
    log.write_text(
        '2026-01-01 10:00:00,123 INFO: Heartbeat: TPV=1010.5 | PnL=+1.0 | ARUSDT=2.2 | '
        'L:26.8% [-0.2%] {+30.77$} | S:34.3% [+0.3%] {+39.40$} | V:35.0% [+0.0%] {+40.21$} | C:3.9% {4.50$}\n'
        'TPV: 1011.2\n'
        '2026-01-01 10:05:00,456 INFO: \U0001F4DD PAPER: BUY 68.6 ARUSDT_LONG @ 2.263\n'
        '2026-01-01 10:06:00,789 INFO: \U0001F4DD PAPER: SELL 68.6 ARUSDT_SHORT @ 2.210\n'
        '2026-01-01 10:07:00,000 INFO: Rebalance #2 complete. TPV: 1012.4\n'
        '2026-01-01 10:08:00,000 INFO: SAFE ACTIVATED: Siphoned 5.0 USDT. New Reserve: 10.0\n'
        '2026-01-01 10:09:00,000 INFO: Heartbeat: TPV=1013.1 | PnL=+1.1 | ARUSDT=2.21 | Cycles=2\n'
        '2026-01-01 10:10:00,000 INFO: Heartbeat: Balance=1014.0 | SAFE:10.0 | ARUSDT=2.2 | '
        'L:30.0% S:20.0% V:40.0% C:10.0%\n',
        encoding='utf-8')
    data, trades = module.parse_rebalance_log(log)
    assert data is not None and trades is not None
    assert not trades.empty
    module.visualize_bot(log)
    assert (tmp_path / 'visualize_ARUSDT.html').exists()


def test_save_cache_writes_a_pickle(tmp_path):
    import importlib.util
    import pickle
    spec = importlib.util.spec_from_file_location('save_cache', ROOT / 'tools/save_cache.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module is not None


def test_debug_scan_scans_offline(monkeypatch, tmp_path):
    import asyncio
    import importlib.util
    import multiprocessing

    # tools/debug_scan.py has no __main__ guard: importing it runs a LIVE scan.
    # Patch asyncio.run around exec_module so the module-level call is a no-op.
    captured = {}
    real_run = asyncio.run

    def fake_run(coro, *args, **kwargs):
        captured['coro'] = coro

    monkeypatch.setattr(asyncio, 'run', fake_run)
    spec = importlib.util.spec_from_file_location('debug_scan', ROOT / 'tools/debug_scan.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(asyncio, 'run', real_run)
    if 'coro' in captured:
        captured['coro'].close()

    monkeypatch.chdir(tmp_path)
    assert module.os_path_exists('config.json') is False
    (tmp_path / 'config.json').write_text(json.dumps({'scanner_period_days': 1,
                                                      'black_list': ['BBBUSDT']}),
                                          encoding='utf-8')
    (tmp_path / 'tickers.txt').write_text('AAAUSDT\n', encoding='utf-8')

    def kline_row(offset):
        ts = 1_700_000_000_000 + offset * 60_000
        return [ts, '100', '101', '99', str(100.0 + offset * 0.1), '10',
                ts + 59_999, '100', 1, '1', '100', '0']

    payloads = {
        'ticker/24hr': [{'symbol': 'AAAUSDT', 'quoteVolume': '30000000'},
                        {'symbol': 'ZZZUSDT', 'quoteVolume': '5'}],
        'premiumIndex': [{'symbol': 'AAAUSDT', 'lastFundingRate': '0.0001'}],
        'klines': [kline_row(i) for i in range(10)],
    }

    class Response:
        status = 200

        def __init__(self, url):
            self.url = url

        async def json(self):
            for key, value in payloads.items():
                if key in self.url:
                    return value
            return []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class Session:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def get(self, url, **kwargs):
            return Response(url)

    class FakePool:
        """ProcessPoolExecutor substitute: spawn pickles nested defs on Windows."""

        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def submit(self, fn, *args, **kwargs):
            from concurrent.futures import Future
            future = Future()
            future.set_result(fn(*args, **kwargs))
            return future

    def fake_run_in_executor(self, executor, func, *args):
        # run the ranker in-thread instead of spawning a process
        future = self.create_future()
        try:
            future.set_result(func(*args))
        except Exception as exc:
            future.set_exception(exc)
        return future

    monkeypatch.setattr(module.aiohttp, 'ClientSession', Session)
    monkeypatch.setattr(multiprocessing, 'get_context', lambda name: object())
    monkeypatch.setattr('concurrent.futures.ProcessPoolExecutor', FakePool)
    monkeypatch.setattr(asyncio.BaseEventLoop, 'run_in_executor', fake_run_in_executor)
    ranked = asyncio.run(module.main())
    assert ranked is not None
