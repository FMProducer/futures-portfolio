"""Public-only physical-route verification with socket evidence; no orders or credentials."""
import asyncio
import json
from pathlib import Path
import socket
import sys

import aiohttp
import requests
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.direct_network import configure_session, connected_socket, direct_connector, physical_interface, public_urlopen


def evidence(sock):
    index, address = physical_interface()
    local = sock.getsockname()
    actual_index = sock.getsockopt(socket.IPPROTO_IP, 31)
    if local[0] != address or actual_index != index:
        raise OSError('Physical Binance socket verification failed')
    return dict(local=local, remote=sock.getpeername(), interface_index=actual_index)


async def main():
    result = {'physical': physical_interface(), 'checks': []}
    session = configure_session(requests.Session())
    for host, path in [('fapi.binance.com', '/fapi/v1/time'), ('api1.binance.com', '/api/v3/time'),
                       ('data.binance.vision', '/')]:
        with session.get('https://' + host + path, timeout=15, stream=True) as response:
            response.raise_for_status()
            result['checks'].append(dict(transport='requests', host=host, status=response.status_code,
                                         **evidence(response.raw._connection.sock)))
    with public_urlopen('https://fapi.binance.com/fapi/v1/time', timeout=15) as response:
        result['checks'].append(dict(transport='urllib', status=response.status,
                                     **evidence(response.fp.raw._sock)))
    async with aiohttp.ClientSession(trust_env=False, connector=direct_connector()) as session:
        async with session.get('https://fapi.binance.com/fapi/v1/klines',
                params={'symbol': 'JCTUSDT', 'interval': '1m', 'limit': 2}) as response:
            response.raise_for_status()
            await response.read()
        sockets = [protocol.transport.get_extra_info('socket') for entries in session.connector._conns.values()
                   for protocol, _ in entries]
        if not sockets:
            raise OSError('No aiohttp physical socket evidence')
        result['checks'].append(dict(transport='aiohttp', status=response.status, **evidence(sockets[0])))
    sock = await connected_socket('fstream.binance.com', 443)
    async with websockets.connect('wss://fstream.binance.com/market/stream',
            sock=sock, proxy=None, open_timeout=15, compression=None) as stream:
        await stream.send(json.dumps({'method': 'SUBSCRIBE', 'params': ['btcusdt@aggTrade'], 'id': 1}))
        event = json.loads(await asyncio.wait_for(stream.recv(), 10))
        if event.get('id') != 1 or event.get('result', 'missing') is not None:
            raise OSError('Binance WebSocket subscription was not acknowledged')
        result['checks'].append(dict(transport='websocket', subscription_ack=True,
            **evidence(stream.transport.get_extra_info('socket'))))
    print(json.dumps(result, indent=2), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
