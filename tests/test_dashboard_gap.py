"""Remaining branch coverage for dashboard/dashboard.py: config-missing exit,
authenticated /login redirect, control-disabled gate, pm2 action helpers with a
mocked subprocess, timeout and error handlers."""
import importlib.util
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD = ROOT / 'dashboard'


@pytest.fixture(scope='module')
def dashboard():
    # Pin the repo namespace package before the dashboard/ dir enters sys.path:
    # otherwise `import dashboard` would resolve to dashboard/dashboard.py
    # (a module) and shadow the package, breaking later `dashboard.<mod>` imports.
    import importlib
    importlib.import_module('dashboard')
    inserted = False
    if str(DASHBOARD) not in sys.path:
        sys.path.insert(0, str(DASHBOARD))
        inserted = True
    spec = importlib.util.spec_from_file_location('dashboard_app_gap', DASHBOARD / 'dashboard.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
    # Restore only what this fixture owned: the path entry it inserted and its
    # own module object. Shared top-level sibling modules (data_collector,
    # swarm_curve, ...) and the pinned namespace package stay: the pre-existing
    # runtime-surface fixture manages them persistently the same way.
    sys.modules.pop('dashboard_app_gap', None)
    if inserted:
        try:
            sys.path.remove(str(DASHBOARD))
        except ValueError:
            pass


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


class Result:
    returncode = 0
    stdout = ''
    stderr = ''


def test_load_config_missing_exits(dashboard, monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard, 'CONFIG_PATH', tmp_path / 'missing.yaml')
    with pytest.raises(SystemExit):
        dashboard.load_config()


def test_login_redirects_authenticated_user(client):
    _login(client)
    response = client.get('/login')
    assert response.status_code == 302


def test_control_disabled_returns_403(client, dashboard, monkeypatch):
    token = _login(client)
    monkeypatch.setitem(dashboard.config['control'], 'enabled', False)
    response = client.post('/api/control/pm2', json={'action': 'pm2_stop', 'target': 'x'},
                           headers={'X-CSRF-Token': token})
    assert response.status_code == 403
    assert 'disabled' in response.get_json()['error']


def _fake_subprocess(dashboard, monkeypatch, results):
    calls = []

    def fake_run(command, *args, **kwargs):
        calls.append(command)
        value = results.pop(0) if len(results) > 1 else results[0]
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr(dashboard.subprocess, 'run', fake_run)
    return calls


def test_control_action_commands_and_rate_limit(client, dashboard, monkeypatch):
    token = _login(client)
    # Compensate the known missing `import time` with an injected module global
    # so the rate-limit branch executes; the production defect stays unfixed.
    fake_time = types.SimpleNamespace(time=lambda: 0.0)
    fake_time_counter = {'now': 100.0}
    fake_time.time = lambda: fake_time_counter['now']
    monkeypatch.setattr(dashboard, 'time', fake_time, raising=False)
    calls = _fake_subprocess(dashboard, monkeypatch, [Result()])
    header = {'X-CSRF-Token': token}

    response = client.post('/api/control/pm2',
                           json={'action': 'pm2_restart', 'target': 'paper-btcusdt'},
                           headers=header)
    assert response.status_code == 200 and response.get_json()['success'] is True
    assert calls[-1][:3] == ['pm2', 'restart', 'paper-btcusdt']

    fake_time_counter['now'] += 1.0
    response = client.post('/api/control/pm2',
                           json={'action': 'pm2_restart', 'target': 'paper-btcusdt'},
                           headers=header)
    assert 'Rate limited' in response.get_json()['error']

    fake_time_counter['now'] += 40.0
    response = client.post('/api/control/pm2',
                           json={'action': 'pm2_start', 'target': 'paper-btcusdt',
                                 'ticker': 'BTCUSDT'},
                           headers=header)
    assert response.get_json()['success'] is True
    assert 'core/main.py' in calls[-1] and '--ticker' in calls[-1]

    response = client.post('/api/control/pm2', json={'action': 'pm2_delete', 'target': 'x'},
                           headers=header)
    assert calls[-1][:3] == ['pm2', 'delete', 'x']


def test_control_unknown_action_branch(client, dashboard, monkeypatch):
    token = _login(client)
    fake_time = types.SimpleNamespace(time=lambda: 0.0)
    monkeypatch.setattr(dashboard, 'time', fake_time, raising=False)
    _fake_subprocess(dashboard, monkeypatch, [Result()])
    monkeypatch.setitem(dashboard.config['control'], 'allowed_commands',
                        list(dashboard.config['control']['allowed_commands']) + ['pm2_foo'])
    response = client.post('/api/control/pm2', json={'action': 'pm2_foo', 'target': 'x'},
                           headers={'X-CSRF-Token': token})
    assert 'Unknown action' in response.get_json()['error']


def test_control_timeout_reports_error(client, dashboard, monkeypatch):
    token = _login(client)
    _fake_subprocess(dashboard, monkeypatch,
                     [subprocess.TimeoutExpired('pm2 stop x', 15)])
    response = client.post('/api/control/pm2', json={'action': 'pm2_stop', 'target': 'x'},
                          headers={'X-CSRF-Token': token})
    assert response.get_json()['error'] == 'Command timed out'


def test_non_api_not_found_renders_template(client):
    _login(client)
    response = client.get('/definitely-not-a-page')
    assert response.status_code == 404
    assert b'<!DOCTYPE html>' in response.data


def test_server_error_handlers(dashboard, monkeypatch):
    # Non-testing client so unhandled exceptions surface as 500 responses.
    app = dashboard.create_app()
    client = app.test_client()
    _login(client)
    monkeypatch.setattr('data_collector.get_system_overview',
                        lambda: (_ for _ in ()).throw(RuntimeError('boom')))
    monkeypatch.setattr(dashboard.subprocess, 'run',
                        lambda *args, **kwargs: type('R', (), {'returncode': 1, 'stdout': ''})())
    api = client.get('/api/overview')
    assert api.status_code == 500
    assert api.get_json()['error'] == 'Internal error'
    # /health is the one unguarded page route: its 500 renders the template branch.
    page = client.get('/health')
    assert page.status_code == 500
