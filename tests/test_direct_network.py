import json
import socket
import ssl
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from core import direct_network as network


@pytest.fixture(autouse=True)
def clear_interface_cache():
    clear = network.physical_interface.cache_clear
    clear()
    yield
    clear()


@pytest.mark.parametrize('rows', [[], [{'index': 11, 'address': '192.168.1.3'},
                                      {'index': 12, 'address': '192.168.2.3'}]])
def test_ambiguous_or_missing_physical_gateway_fails_closed(monkeypatch, rows):
    monkeypatch.setattr(network.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout=json.dumps(rows)))
    with pytest.raises(OSError, match='Exactly one'):
        network.physical_interface()


def test_physical_gateway_is_selected_from_native_discovery(monkeypatch):
    monkeypatch.setattr(network.subprocess, 'run', lambda *a, **k:
        SimpleNamespace(stdout='{"index":11,"address":"192.168.1.3"}'))
    assert network.physical_interface() == (11, '192.168.1.3')


def test_proxy_environment_cannot_redirect_requests(monkeypatch):
    monkeypatch.setattr(network, 'physical_interface', lambda: (11, '192.168.1.3'))
    for key in ['HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY']:
        monkeypatch.setenv(key, 'http://127.0.0.1:1')
    session = network.configure_session(requests.Session())
    settings = session.merge_environment_settings('https://fapi.binance.com', {}, False, True, None)
    assert settings['proxies'] == {}
    adapter = session.get_adapter('https://fapi.binance.com')
    assert adapter.poolmanager.connection_pool_kw['source_address'] == ('192.168.1.3', 0)
    with pytest.raises(OSError, match='proxy connections are forbidden'):
        adapter.proxy_manager_for('http://127.0.0.1:1')
    with pytest.raises(OSError, match='requires HTTPS'):
        session.get('http://fapi.binance.com')


def test_binding_failure_closes_socket_without_fallback(monkeypatch):
    monkeypatch.setattr(network, 'physical_interface', lambda: (11, '192.168.1.3'))
    sock = Mock()
    sock.bind.side_effect = OSError('interface unavailable')
    monkeypatch.setattr(network.socket, 'socket', lambda *args: sock)
    with pytest.raises(OSError, match='interface unavailable'):
        network.physical_socket((socket.AF_INET, socket.SOCK_STREAM, 6, '', ('1.1.1.1', 443)))
    sock.setsockopt.assert_any_call(socket.IPPROTO_IP, 31, socket.htonl(11))
    sock.close.assert_called_once()
    sock.connect.assert_not_called()


def test_ipv6_cannot_escape_physical_ipv4_binding():
    with pytest.raises(OSError, match='requires physical IPv4'):
        network.physical_socket((socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('::1', 443)))


def test_urllib_preserves_tls_and_rejects_http_redirect_target():
    connection = network.PhysicalHTTPSConnection('fapi.binance.com')
    assert connection._context.verify_mode == ssl.CERT_REQUIRED
    assert connection._context.check_hostname
    with pytest.raises(OSError, match='requires HTTPS'):
        network.direct_opener().open('http://fapi.binance.com')
