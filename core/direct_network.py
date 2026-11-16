"""Bind Binance connections to a physical Windows adapter; never fall back to TUN."""
import asyncio
import http.client
import json
import socket
import subprocess
import sys
import urllib.request
from functools import lru_cache
from urllib.parse import urlsplit

import aiohttp
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection


@lru_cache(maxsize=1)
def physical_interface():
    if sys.platform != 'win32':
        raise OSError('Physical Binance routing requires Windows adapter discovery')
    command = """
    @(Get-NetAdapter -Physical | Where-Object Status -eq 'Up' | ForEach-Object {
        $configuration = Get-NetIPConfiguration -InterfaceIndex $_.ifIndex
        if ($configuration.IPv4DefaultGateway) {
            foreach ($address in $configuration.IPv4Address) {
                [pscustomobject]@{index=$_.ifIndex; address=$address.IPAddress}
            }
        }
    }) | ConvertTo-Json -Compress
    """
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
        check=True, capture_output=True, text=True, timeout=20,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    rows = json.loads(result.stdout)
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list) or len(rows) != 1:
        raise OSError('Exactly one connected physical IPv4 gateway is required for Binance')
    index, address = int(rows[0]['index']), rows[0]['address']
    if index <= 0:
        raise OSError('Invalid physical interface index')
    socket.inet_pton(socket.AF_INET, address)
    return index, address


def socket_options():
    index, _ = physical_interface()
    # Winsock IP_UNICAST_IF=31 takes the interface index in network byte order.
    return HTTPConnection.default_socket_options + [(socket.IPPROTO_IP, 31, socket.htonl(index))]


def physical_socket(address_info):
    family, kind, protocol, _, _ = address_info
    if family != socket.AF_INET:
        raise OSError('Binance direct transport requires physical IPv4')
    _, address = physical_interface()
    sock = socket.socket(family, kind, protocol)
    try:
        for level, option, value in socket_options():
            sock.setsockopt(level, option, value)
        sock.bind((address, 0))
        return sock
    except BaseException:
        sock.close()
        raise


class PhysicalAdapter(HTTPAdapter):
    def send(self, request, **kwargs):
        if urlsplit(request.url).scheme != 'https':
            raise OSError('Binance direct transport requires HTTPS')
        return super().send(request, **kwargs)

    def init_poolmanager(self, *args, **kwargs):
        _, address = physical_interface()
        kwargs.update(source_address=(address, 0), socket_options=socket_options())
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args, **kwargs):
        raise OSError('Binance proxy connections are forbidden')


def configure_session(session):
    session.trust_env = False
    session.proxies.clear()
    adapter = PhysicalAdapter()
    session.mount('https://', adapter)
    session.mount('http://', adapter)
    return session


def direct_connector(**kwargs):
    return aiohttp.TCPConnector(family=socket.AF_INET, socket_factory=physical_socket, **kwargs)


async def connected_socket(host, port, timeout=15):
    loop = asyncio.get_running_loop()
    addresses = await loop.getaddrinfo(host, port, family=socket.AF_INET, type=socket.SOCK_STREAM)
    last_error = OSError('No physical IPv4 destination for Binance')
    for info in addresses:
        sock = physical_socket(info)
        sock.setblocking(False)
        try:
            await asyncio.wait_for(loop.sock_connect(sock, info[4]), timeout)
            return sock
        except BaseException as error:
            sock.close()
            if not isinstance(error, OSError):
                raise
            last_error = error
    raise last_error


class PhysicalHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        if self._tunnel_host:
            raise OSError('Binance HTTP tunnels are forbidden')
        last_error = OSError('No physical IPv4 destination for Binance')
        for info in socket.getaddrinfo(self.host, self.port, socket.AF_INET, socket.SOCK_STREAM):
            sock = physical_socket(info)
            sock.settimeout(self.timeout)
            try:
                sock.connect(info[4])
                self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
                return
            except OSError as error:
                sock.close()
                last_error = error
        raise last_error


class PhysicalHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, request):
        return self.do_open(PhysicalHTTPSConnection, request, context=self._context,
                            check_hostname=getattr(self, '_check_hostname', None))


class RejectHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, request):
        raise OSError('Binance direct transport requires HTTPS')


def direct_opener(*handlers):
    return urllib.request.build_opener(urllib.request.ProxyHandler({}),
        RejectHTTPHandler(), PhysicalHTTPSHandler(), *handlers)


def public_urlopen(url, timeout=30):
    if urlsplit(url).scheme != 'https':
        raise ValueError('Binance public transport requires HTTPS')
    return direct_opener().open(url, timeout=timeout)
