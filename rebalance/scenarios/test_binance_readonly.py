import hashlib
import io
import json
import urllib.error

import pytest

from rebalance.binance_readonly import Reader, ReadError, capture, inspect_order, normalize, unresolved
from rebalance.durable import Store
from rebalance.kernel import Action, Config, Portfolio


def account():
    return {'assets':[dict(asset='USDT', walletBalance='100', unrealizedProfit='2',
        marginBalance='102', availableBalance='80', initialMargin='20', maintMargin='1')]}


class FakeReader:
    def __init__(self, changed=False):
        self.calls = []
        self.changed = changed

    def synchronize(self):
        return dict(server_time=1, roundtrip_ms=1)

    def get(self, path, **params):
        self.calls.append(path)
        if path.endswith('positionSide/dual'):
            return dict(dualSidePosition=True)
        if path.endswith('multiAssetsMargin'):
            return dict(multiAssetsMargin=False)
        if path.endswith('account'):
            a = account()
            if self.changed and self.calls.count(path) == 2:
                a['assets'][0]['walletBalance'] = '101'
                a['assets'][0]['marginBalance'] = '103'
            return a
        if path.endswith('positionRisk') or path.endswith('openOrders'):
            return []
        if path.endswith('exchangeInfo'):
            return dict(symbols=[dict(symbol='BTCUSDT', status='TRADING', baseAsset='BTC',
                                     quoteAsset='USDT', marginAsset='USDT', filters=[])])
        raise AssertionError(path)


def test_equal_rest_brackets_never_authorize_trading():
    result = capture(FakeReader(), 'BTCUSDT')
    assert result['rest_bracket_equal'] is True
    assert result['coherence'] == 'REST_OBSERVATION_ONLY'
    assert result['trading_authorized'] is False
    assert result['equity_usdt'] == '102'


def test_changed_bracket_is_blocked():
    result = capture(FakeReader(changed=True), 'BTCUSDT')
    assert not result['rest_bracket_equal']
    assert 'CHANGED_DURING_REST_COLLECTION' in result['blocking_reasons']


def test_wrong_mode_and_equity_identity_reported():
    a = account()
    a['assets'][0]['marginBalance'] = '200'
    result = normalize(a, [], dict(dualSidePosition=False), dict(multiAssetsMargin=True))
    assert set(result['blocking_reasons']) == {
        'ONE_WAY_MODE', 'MULTI_ASSET_MODE_UNSUPPORTED', 'USDT_EQUITY_IDENTITY_MISMATCH'}


def test_missing_usdt_is_not_assumed_zero():
    with pytest.raises(ReadError, match='usdt'):
        normalize({'assets':[]}, [], dict(dualSidePosition=True), dict(multiAssetsMargin=False))


@pytest.mark.parametrize('path,params', [('/fapi/v1/leverage', {}),
    ('/fapi/v1/order', {'side':'BUY'}), ('https://example.com', {}),
    ('/fapi/v3/account', {'signature':'bad'})])
def test_transport_rejects_non_allowlisted_operations(path, params):
    with pytest.raises(ValueError, match='allowlist'):
        Reader('test-key', 'test-secret').get(path, **params)


def test_transport_is_get_signed_and_errors_do_not_leak_credentials():
    r = Reader('test-key', 'test-secret')
    class Opener:
        def open(self, request, timeout):
            assert request.method == 'GET'
            assert 'signature=' in request.full_url
            assert request.get_header('X-mbx-apikey') == 'test-key'
            raise urllib.error.HTTPError(request.full_url, 401, 'test-key', {},
                                        io.BytesIO(b'{"code":-2015,"msg":"test-secret"}'))
    r._opener = Opener()
    with pytest.raises(ReadError) as exc:
        r.get('/fapi/v3/account')
    assert exc.value.details == dict(kind='http_error', http_status=401, api_code=-2015)
    assert 'test-key' not in str(exc.value) and 'test-secret' not in str(exc.value)


def test_order_not_found_keeps_unknown():
    class NotFound:
        def get(self, *a, **k):
            raise ReadError('http_error', status=400, code=-2013)
    intent = dict(client_id='stable', action=dict(kind='ADD', side='LONG'))
    assert inspect_order(NotFound(), 'BTCUSDT', intent)['resolution'] == 'KEEP_UNKNOWN'


def test_terminal_order_is_evidence_only_without_fills():
    class Order:
        def get(self, *a, **k):
            return dict(symbol='BTCUSDT', clientOrderId='stable', side='BUY', positionSide='LONG',
                        status='FILLED', executedQty='.1', origQty='.1')
    intent = dict(client_id='stable', action=dict(kind='ADD', side='LONG'))
    result = inspect_order(Order(), 'BTCUSDT', intent)
    assert result['resolution'] == 'EVIDENCE_ONLY_NO_STATE_CHANGE'


def test_identity_mismatch_not_used_to_resolve_unknown():
    class Order:
        def get(self, *a, **k):
            return dict(symbol='BTCUSDT', clientOrderId='someone-else')
    with pytest.raises(ReadError, match='identity'):
        inspect_order(Order(), 'BTCUSDT', dict(client_id='stable'))


def test_sqlite_order_inspection_does_not_mutate_database(tmp_path):
    path = tmp_path/'local.sqlite'
    store = Store(path)
    store.initialize(Portfolio(Config('.001','5.3','7','.0004','.7','.7','.09'), '10000'))
    store.prepare('prepare','stable',Action('ADD','LONG',1000),'100','1','100',0,entry=True)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    assert unresolved(path)[0]['client_id'] == 'stable'
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_env_file_is_read_without_modifying_process_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('BINANCE_API_KEY','process-value')
    path = tmp_path/'.env'
    path.write_text('BINANCE_API_KEY=file-value\nBINANCE_SECRET_KEY=file-secret\n')
    Reader.from_env(path)
    import os
    assert os.environ['BINANCE_API_KEY'] == 'process-value'
