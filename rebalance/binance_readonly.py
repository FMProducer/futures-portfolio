"""GET-only USD-M observer. No order submission or local state mutation.

REST observations are NOT an atomic exchange snapshot or a trading permit.
Credential material and signed URLs must never be included in diagnostics.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request

from .kernel import decimal


class ReadError(Exception):
    def __init__(self, kind, *, status=None, code=None):
        self.details = dict(kind=kind, http_status=status, api_code=code)
        super().__init__(kind)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ReadError('redirect_blocked')


class Reader:
    # Paths and query keys are closed lists. No generic signed transport API.
    ROUTES = {
        '/fapi/v1/time': (False, set()),
        '/fapi/v1/exchangeInfo': (False, set()),
        '/fapi/v3/account': (True, set()),
        '/fapi/v3/positionRisk': (True, set()),
        '/fapi/v1/positionSide/dual': (True, set()),
        '/fapi/v1/multiAssetsMargin': (True, set()),
        '/fapi/v1/openOrders': (True, set()),
        '/fapi/v1/order': (True, {'symbol', 'origClientOrderId'}),
        '/fapi/v1/userTrades': (True, {'symbol', 'startTime', 'endTime', 'limit'}),
        '/fapi/v1/income': (True, {'startTime', 'endTime', 'limit'}),
    }

    def __init__(self, key, secret):
        if not key or not secret:
            raise ReadError('missing_credentials')
        self._key, self._secret, self.offset = key, secret, 0
        # Bypass environment/system HTTP proxies; an OS-level TUN may still route traffic.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    @classmethod
    def from_env(cls, path):
        from dotenv import dotenv_values
        try:
            values = dotenv_values(path, interpolate=False)
        except Exception:
            raise ReadError('credential_file_unreadable') from None
        return cls(values.get('BINANCE_API_KEY'), values.get('BINANCE_SECRET_KEY'))

    def get(self, path, **params):
        if path not in self.ROUTES or set(params)-self.ROUTES[path][1]:
            raise ValueError('endpoint or parameters outside GET allowlist')
        signed = self.ROUTES[path][0]
        headers = {}
        if signed:
            params.update(timestamp=int(time.time()*1000)+self.offset, recvWindow=5000)
            headers['X-MBX-APIKEY'] = self._key
        query = urllib.parse.urlencode(params)
        if signed:
            signature = hmac.new(self._secret.encode(), query.encode(), hashlib.sha256).hexdigest()
            query += '&signature='+signature
        request = urllib.request.Request('https://fapi.binance.com'+path+('?' + query if query else ''),
                                         headers=headers, method='GET')
        try:
            with self._opener.open(request, timeout=15) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            code = None
            try:
                value = json.loads(exc.read()).get('code')
                if type(value) is int:
                    code = value
            except Exception:
                pass
            raise ReadError('http_error', status=exc.code, code=code) from None
        except ReadError:
            raise
        except Exception:
            raise ReadError('network_or_response_error') from None

    def synchronize(self):
        before = int(time.time()*1000)
        server = self.get('/fapi/v1/time')['serverTime']
        after = int(time.time()*1000)
        self.offset = int(server)-(before+after)//2
        return dict(server_time=int(server), roundtrip_ms=after-before)


def financial_fingerprint(account, positions, orders):
    """All account financial fields/position bases included; ignore updateTime."""
    def clean(value):
        if isinstance(value, dict):
            return {k:clean(v) for k,v in value.items() if k != 'updateTime'}
        if isinstance(value, list):
            return sorted((clean(v) for v in value), key=lambda v:json.dumps(v, sort_keys=True))
        return value
    body = json.dumps(clean([account, positions, orders]), sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()


def normalize(account, positions, hedge, multi):
    if type(hedge.get('dualSidePosition')) is not bool or type(multi.get('multiAssetsMargin')) is not bool:
        raise ReadError('invalid_mode_response')
    usdt = [a for a in account['assets'] if a['asset'] == 'USDT']
    # An empty USDT bucket is not assumed to be zero without exchange evidence.
    if len(usdt) != 1:
        raise ReadError('missing_or_duplicate_usdt_asset')
    asset = usdt[0]
    wallet, upl, margin = map(decimal, (asset['walletBalance'], asset['unrealizedProfit'], asset['marginBalance']))
    active = []
    for p in positions:
        qty = decimal(p['positionAmt'])
        if qty:
            active.append(dict(symbol=p['symbol'], side=p['positionSide'], quantity=str(qty),
                entry_price=str(decimal(p['entryPrice'])), mark_price=str(decimal(p['markPrice'])),
                unrealized=str(decimal(p['unRealizedProfit'])), margin_type=p.get('marginType', 'UNSPECIFIED')))
    reasons = []
    if not hedge['dualSidePosition']:
        reasons.append('ONE_WAY_MODE')
    if multi['multiAssetsMargin']:
        reasons.append('MULTI_ASSET_MODE_UNSUPPORTED')
    if wallet+upl != margin:
        reasons.append('USDT_EQUITY_IDENTITY_MISMATCH')
    # Do not add position notional or reserved margin to economic Equity.
    return dict(wallet_usdt=str(wallet), unrealized_usdt=str(upl), equity_usdt=str(margin),
        available_usdt=str(decimal(asset['availableBalance'])),
        initial_margin_usdt=str(decimal(asset['initialMargin'])),
        maintenance_margin_usdt=str(decimal(asset['maintMargin'])), active_positions=active,
        hedge_mode=hedge['dualSidePosition'], multi_asset_mode=multi['multiAssetsMargin'],
        blocking_reasons=reasons, trading_authorized=False)


def capture(reader, symbol):
    if not re.fullmatch('[A-Z0-9]{5,24}', symbol):
        raise ValueError('invalid symbol')
    start = datetime.now(timezone.utc).isoformat()
    timing = reader.synchronize()
    hedge = reader.get('/fapi/v1/positionSide/dual')
    multi = reader.get('/fapi/v1/multiAssetsMargin')
    first = reader.get('/fapi/v3/account')
    positions = reader.get('/fapi/v3/positionRisk')
    orders = reader.get('/fapi/v1/openOrders')
    second_positions = reader.get('/fapi/v3/positionRisk')
    second_orders = reader.get('/fapi/v1/openOrders')
    second = reader.get('/fapi/v3/account')
    stable = financial_fingerprint(first, positions, orders) == financial_fingerprint(second, second_positions, second_orders)
    result = normalize(second, second_positions, hedge, multi)
    result.update(started_at=start, finished_at=datetime.now(timezone.utc).isoformat(),
                  timing=timing, rest_bracket_equal=stable, open_orders=len(second_orders),
                  coherence='REST_OBSERVATION_ONLY')
    if not stable:
        result['blocking_reasons'].append('CHANGED_DURING_REST_COLLECTION')
    info = reader.get('/fapi/v1/exchangeInfo')
    matches = [s for s in info['symbols'] if s['symbol'] == symbol]
    if len(matches) != 1:
        raise ReadError('symbol_not_found')
    s = matches[0]
    result['instrument'] = {k:s[k] for k in ('symbol', 'status', 'baseAsset', 'quoteAsset', 'marginAsset', 'filters')}
    # Equal brackets do not establish event-stream consistency or capture all non-trading flows.
    return result


def unresolved(path):
    """Read SQLite without Store initialization/mutations; never use replay DB as live evidence."""
    uri = Path(path).resolve().as_uri()+'?mode=ro'
    db = sqlite3.connect(uri, uri=True)
    try:
        return [o for (body,) in db.execute('SELECT body FROM orders')
                if (o := json.loads(body))['status'] not in ('FILLED', 'CANCELED', 'REJECTED')]
    finally:
        db.close()


def inspect_order(reader, symbol, intent):
    client_id = intent['client_id']
    try:
        order = reader.get('/fapi/v1/order', symbol=symbol, origClientOrderId=client_id)
    except ReadError as exc:
        return dict(client_id=client_id, resolution='KEEP_UNKNOWN', error=exc.details)
    if order.get('clientOrderId') != client_id or order.get('symbol') != symbol:
        raise ReadError('order_identity_mismatch')
    expected_side = ('BUY' if intent['action']['side'] == 'LONG' else 'SELL')
    if intent['action']['kind'] == 'REDUCE':
        expected_side = 'SELL' if expected_side == 'BUY' else 'BUY'
    if order.get('positionSide') != intent['action']['side'] or order.get('side') != expected_side:
        raise ReadError('order_direction_mismatch')
    return dict(client_id=client_id, resolution='EVIDENCE_ONLY_NO_STATE_CHANGE',
        status=order['status'], executed_qty=str(decimal(order['executedQty'])),
        original_qty=str(decimal(order['origQty'])),
        note='Query result alone does not reconstruct fills, fees or non-trading flows')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--symbol', default='BTCUSDT')
    parser.add_argument('--state-db', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    # Account data stay local and excluded from git, unlike sanitized test evidence.
    (args.output/'.gitignore').write_text('*\n!.gitignore\n', encoding='utf-8')
    try:
        reader = Reader.from_env(args.env_file)
        result = capture(reader, args.symbol)
        if args.state_db:
            result['order_observations'] = [inspect_order(reader, args.symbol, o) for o in unresolved(args.state_db)]
        (args.output/'observation.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(dict(status='READ_ONLY_CAPTURED', rest_bracket_equal=result['rest_bracket_equal'],
                             active_positions=len(result['active_positions']), open_orders=result['open_orders'],
                             blocking_reasons=result['blocking_reasons'], trading_authorized=False)))
    except ReadError as exc:
        (args.output/'error.json').write_text(json.dumps(exc.details, indent=2), encoding='utf-8')
        print(json.dumps(dict(status='BLOCKED', **exc.details)))
        raise SystemExit(2) from None


if __name__ == '__main__':
    main()
