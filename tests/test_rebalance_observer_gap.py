"""Coverage for the observer checkpoint/stream paths that the scenarios do not reach.

Everything here is offline: the REST/ws transport is faked, no exchange is contacted.
"""
import asyncio
import io
import json
import sqlite3
import urllib.error

import pytest

from rebalance import checkpoint as cp
from rebalance import user_stream as us
from rebalance.binance_readonly import ReadError
from rebalance.checkpoint import Observer

ACCOUNT = dict(assets=[dict(asset='USDT', walletBalance='100', unrealizedProfit='0',
                            marginBalance='100', availableBalance='100',
                            initialMargin='0', maintMargin='0')])

EXCHANGE = dict(symbols=[dict(symbol='BTCUSDT', status='TRADING', baseAsset='BTC',
                              quoteAsset='USDT', marginAsset='USDT', filters=[])])


class FakeReader:
    """Minimal REST surface used by checkpoint.collect() and user_stream.monitor()."""

    def __init__(self, server_time=1000000, uids=(12345, 12345), algo_orders=None):
        self.server_time = server_time
        self.clock = server_time
        self.uids = list(uids)
        self.algo_orders = [] if algo_orders is None else algo_orders
        self.sync_calls = 0
        self._key = 'fixture-key'
        self._opener = self

    # -- transport used by start_stream() ---------------------------------
    def open(self, request, timeout):
        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

        return Response(json.dumps(dict(listenKey='ephemeral123')).encode())

    # -- REST surface ------------------------------------------------------
    def synchronize(self):
        self.sync_calls += 1
        return dict(server_time=self.get('/fapi/v1/time')['serverTime'], roundtrip_ms=1)

    def get(self, path, **params):
        if path == '/fapi/v1/time':
            self.clock += 1000
            return dict(serverTime=self.clock)
        if path == '/api/v3/account':
            uid = self.uids.pop(0) if len(self.uids) > 1 else self.uids[0]
            return dict(uid=uid)
        if path == '/fapi/v1/positionSide/dual':
            return dict(dualSidePosition=True)
        if path == '/fapi/v1/multiAssetsMargin':
            return dict(multiAssetsMargin=False)
        if path in ('/fapi/v3/account',):
            return ACCOUNT
        if path in ('/fapi/v3/positionRisk', '/fapi/v1/openOrders'):
            return []
        if path == '/fapi/v1/openAlgoOrders':
            return self.algo_orders
        if path == '/fapi/v1/exchangeInfo':
            return EXCHANGE
        if path == '/fapi/v1/userTrades':
            return []
        if path == '/fapi/v1/income':
            return []
        raise AssertionError('unexpected path ' + path)


def flat_proof(end=1000000, start=700000, before=999000, wallet='100'):
    return dict(binding=cp.identity(FakeReader(), 'BTCUSDT'), start=start, before=before, end=end,
                trades=[], incomes=[],
                observation=dict(blocking_reasons=[], rest_bracket_equal=True, active_positions=[],
                                 open_orders=0, initial_margin_usdt='0', unrealized_usdt='0',
                                 wallet_usdt=wallet))


@pytest.fixture
def observer(tmp_path):
    o = Observer(tmp_path / 'observer.sqlite')
    o.initialize(flat_proof())
    return o


# ─── checkpoint.collect ─────────────────────────────────────────────────────

def test_collect_returns_bounded_flat_proof():
    reader = FakeReader()
    proof = cp.collect(reader, 'BTCUSDT')
    assert proof['binding']['symbol'] == 'BTCUSDT'
    assert proof['observation']['blocking_reasons'] == []
    assert proof['observation']['rest_bracket_equal'] is True
    assert proof['start'] == max(0, proof['before'] - cp.OVERLAP)
    assert proof['trades'] == [] and proof['incomes'] == []


def test_collect_rejects_open_algo_orders():
    reader = FakeReader(algo_orders=[dict(orderId=1)])
    with pytest.raises(ValueError, match='algo orders'):
        cp.collect(reader, 'BTCUSDT')


def test_collect_rejects_account_change_during_collection():
    reader = FakeReader(uids=(12345, 99999))
    with pytest.raises(ValueError, match='account changed'):
        cp.collect(reader, 'BTCUSDT')


# ─── checkpoint.facts ───────────────────────────────────────────────────────

def test_facts_rejects_row_outside_history_interval():
    proof = flat_proof()
    proof['trades'] = [dict(symbol='BTCUSDT', id=1, time=proof['end'] + 1)]
    with pytest.raises(ValueError, match='outside history interval'):
        cp.facts(proof)


def test_facts_rejects_duplicate_history_fact():
    proof = flat_proof()
    row = dict(symbol='BTCUSDT', id=1, time=800000)
    proof['trades'] = [row, dict(row)]
    with pytest.raises(ValueError, match='duplicate history fact'):
        cp.facts(proof)


# ─── Observer state machine ────────────────────────────────────────────────

def test_state_requires_initialization(tmp_path):
    with pytest.raises(ValueError, match='not initialized'):
        Observer(tmp_path / 'empty.sqlite').state()


def test_ingest_requires_initialization(tmp_path):
    o = Observer(tmp_path / 'empty.sqlite')
    with pytest.raises(ValueError, match='not initialized'):
        o.ingest(flat_proof(end=1001000), 1000000)


def test_initialize_rejects_inverted_interval(tmp_path):
    o = Observer(tmp_path / 'observer.sqlite')
    proof = flat_proof(start=1000001, before=999000)
    with pytest.raises(ValueError, match='invalid checkpoint interval'):
        o.initialize(proof)


def test_conflicting_history_identity_is_rejected(tmp_path):
    o = Observer(tmp_path / 'observer.sqlite')
    seed = flat_proof(start=0, before=999000, end=1000000)
    seed['incomes'] = [dict(incomeType='TRANSFER', tranId=1, time=500000,
                            income='10', asset='USDT', symbol='', tradeId='')]
    o.initialize(seed)
    changed = flat_proof(start=700000, before=999000, end=1001000, wallet='110')
    changed['incomes'] = [dict(incomeType='TRANSFER', tranId=1, time=700000,
                               income='11', asset='USDT', symbol='', tradeId='')]
    with pytest.raises(ValueError, match='conflicting history identity'):
        o.ingest(changed, 1000000)


def test_blocked_history_bundle_is_rejected(observer):
    proof = flat_proof(end=1001000, wallet='103.96')
    proof['trades'] = [dict(symbol='BTCUSDT', id=1, orderId=1, positionSide='LONG', side='BUY',
                            qty='.4', price='100', commission='.04', commissionAsset='USDT',
                            realizedPnl='0', time=1000500)]
    with pytest.raises(ValueError, match='history bundle blocked'):
        observer.ingest(proof, 1000000)


# ─── checkpoint.main ───────────────────────────────────────────────────────

def test_main_initializes_and_then_ingests(tmp_path, monkeypatch, capsys):
    reader = FakeReader()
    monkeypatch.setattr(cp.Reader, 'from_env', staticmethod(lambda path: reader))
    database = tmp_path / 'observer.sqlite'
    output = tmp_path / 'out'
    monkeypatch.setattr('sys.argv', ['checkpoint', '--env-file', str(tmp_path / '.env'),
                                     '--database', str(database), '--output', str(output),
                                     '--symbol', 'BTCUSDT', '--initialize'])
    cp.main()
    assert json.loads(capsys.readouterr().out)['status'] == 'OBSERVER_INITIALIZED'
    assert (output / 'checkpoint.json').exists()
    cursor = Observer(database).state()[1]
    assert cursor == reader.clock

    reader.clock = cursor + 2000
    second = tmp_path / 'out2'
    monkeypatch.setattr('sys.argv', ['checkpoint', '--env-file', str(tmp_path / '.env'),
                                     '--database', str(database), '--output', str(second),
                                     '--symbol', 'BTCUSDT'])
    cp.main()
    assert json.loads(capsys.readouterr().out)['status'] == 'OBSERVER_CHECKPOINT_MATCHED'
    assert Observer(database).state()[1] == reader.clock


def test_main_reports_blocked_reader(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cp.Reader, 'from_env', staticmethod(lambda path: (_ for _ in ()).throw(
        ReadError('missing_credentials'))))
    output = tmp_path / 'out'
    monkeypatch.setattr('sys.argv', ['checkpoint', '--env-file', str(tmp_path / '.env'),
                                     '--database', str(tmp_path / 'observer.sqlite'),
                                     '--output', str(output), '--symbol', 'BTCUSDT'])
    with pytest.raises(SystemExit) as exc:
        cp.main()
    assert exc.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload['status'] == 'BLOCKED' and payload['trading_authorized'] is False
    assert json.loads((output / 'error.json').read_text())['kind'] == 'missing_credentials'


# ─── user_stream.start_stream ──────────────────────────────────────────────

class _Opener:
    def __init__(self, payload=b'{"listenKey":"ephemeral123"}', error=None):
        self.payload = payload
        self.error = error

    def open(self, request, timeout):
        if self.error is not None:
            raise self.error
        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

        return Response(self.payload)


def _reader_with_opener(opener):
    reader = FakeReader()
    reader._opener = opener
    return reader


def test_start_stream_rejects_non_alphanumeric_token():
    with pytest.raises(ReadError, match='invalid_stream_session'):
        us.start_stream(_reader_with_opener(_Opener(b'{"listenKey":"bad-token"}')))


def test_start_stream_maps_http_error():
    error = urllib.error.HTTPError('https://fapi.binance.com/fapi/v1/listenKey', 401, 'nope', {}, io.BytesIO(b''))
    with pytest.raises(ReadError) as exc:
        us.start_stream(_reader_with_opener(_Opener(error=error)))
    assert exc.value.details['kind'] == 'stream_session_http_error'
    assert exc.value.details['http_status'] == 401


def test_start_stream_maps_transport_failure():
    with pytest.raises(ReadError, match='stream_session_unavailable'):
        us.start_stream(_reader_with_opener(_Opener(error=OSError('down'))))


def test_start_stream_does_not_swallow_reader_errors():
    with pytest.raises(ReadError, match='invalid_stream_session'):
        us.start_stream(_reader_with_opener(_Opener(error=ReadError('invalid_stream_session'))))


# ─── Journal durability ────────────────────────────────────────────────────

def test_begin_rolls_back_when_journal_write_fails(observer, monkeypatch):
    journal = us.Journal(observer)

    def broken(db, state, kind, detail):
        raise sqlite3.OperationalError('injected')

    monkeypatch.setattr(journal, 'write', broken)
    with pytest.raises(sqlite3.OperationalError):
        journal.begin()
    assert journal.state() is None


def test_disconnect_rejects_stale_epoch(observer):
    journal = us.Journal(observer)
    first = journal.begin()
    journal.disconnect(first)
    second = journal.begin()
    with pytest.raises(ValueError, match='stale stream epoch'):
        journal.disconnect(first)
    assert journal.state()['epoch'] == second


def test_disconnect_rolls_back_when_journal_write_fails(observer, monkeypatch):
    journal = us.Journal(observer)
    epoch = journal.begin()

    def broken(db, state, kind, detail):
        raise sqlite3.OperationalError('injected')

    monkeypatch.setattr(journal, 'write', broken)
    with pytest.raises(sqlite3.OperationalError):
        journal.disconnect(epoch)


def test_append_rejects_malformed_event(observer):
    journal = us.Journal(observer)
    epoch = journal.begin()
    with pytest.raises(ReadError, match='malformed_stream_event'):
        journal.append(epoch, dict(not_an_event=1))


def test_append_rejects_non_integer_time(observer):
    journal = us.Journal(observer)
    epoch = journal.begin()
    with pytest.raises(ReadError, match='invalid_stream_time'):
        journal.append(epoch, dict(e='ACCOUNT_UPDATE', E='soon', T='soon'))


# ─── Journal.reconcile guards ──────────────────────────────────────────────

def account_event(stamp=1000100, wallet='100', asset='USDT', positions=()):
    return dict(e='ACCOUNT_UPDATE', E=stamp, T=stamp,
                a=dict(m='ORDER', B=[dict(a=asset, wb=wallet)], P=list(positions)))


def test_late_stream_event_invalidates_initial_checkpoint(observer):
    journal = us.Journal(observer)
    epoch = journal.begin()
    journal.append(epoch, account_event(stamp=999500))
    with pytest.raises(ValueError, match='late stream event'):
        journal.reconcile(flat_proof(end=1002000), 1000000, epoch, 1)


def balanced_proof(end=1002000):
    """Flat round trip: the ingest inventory check must pass before the guard runs."""
    proof = flat_proof(end=end)
    proof['trades'] = [
        dict(symbol='BTCUSDT', id=7, orderId=10, positionSide='LONG', side='BUY',
             qty='.1', price='100', commission='0', commissionAsset='USDT', realizedPnl='0', time=1000100),
        dict(symbol='BTCUSDT', id=8, orderId=11, positionSide='LONG', side='SELL',
             qty='.1', price='100', commission='0', commissionAsset='USDT', realizedPnl='0', time=1000200),
    ]
    return proof


def stream_order(**overrides):
    order = dict(s='BTCUSDT', i=10, c='client', S='BUY', ps='LONG', q='.1', z='.1',
                 x='TRADE', X='FILLED', t=7, l='.1', L='100', N='USDT', n='0', rp='0')
    order.update(overrides)
    return order


def order_evidence(status='FILLED'):
    return {'BTCUSDT:10': dict(symbol='BTCUSDT', orderId=10, clientOrderId='client',
                               side='BUY', positionSide='LONG', origQty='.1',
                               executedQty='.1', status=status, updateTime=1000100)}


def test_terminal_stream_order_conflicts_with_rest(observer):
    journal = us.Journal(observer)
    epoch = journal.begin()
    journal.append(epoch, dict(e='ORDER_TRADE_UPDATE', E=1000100, T=1000100, o=stream_order()))
    proof = balanced_proof()
    proof['order_evidence'] = order_evidence(status='CANCELED')
    with pytest.raises(ValueError, match='terminal stream order'):
        journal.reconcile(proof, 1000000, epoch, 1)


def test_stream_trade_missing_from_rest_window(observer):
    journal = us.Journal(observer)
    epoch = journal.begin()
    journal.append(epoch, dict(e='ORDER_TRADE_UPDATE', E=1000100, T=1000100,
                               o=stream_order(t=1)))
    proof = balanced_proof()
    proof['order_evidence'] = order_evidence()
    with pytest.raises(ValueError, match='missing from REST window'):
        journal.reconcile(proof, 1000000, epoch, 1)


def test_stream_trade_conflicts_with_rest(observer):
    journal = us.Journal(observer)
    epoch = journal.begin()
    journal.append(epoch, dict(e='ORDER_TRADE_UPDATE', E=1000100, T=1000100,
                               o=stream_order(l='.2')))
    proof = balanced_proof()
    proof['order_evidence'] = order_evidence()
    with pytest.raises(ValueError, match='conflicts with REST'):
        journal.reconcile(proof, 1000000, epoch, 1)


def test_unsupported_stream_balance_asset_is_rejected(observer):
    journal = us.Journal(observer)
    epoch = journal.begin()
    journal.append(epoch, account_event(asset='BNB'))
    with pytest.raises(ValueError, match='unsupported stream balance asset'):
        journal.reconcile(flat_proof(end=1002000), 1000000, epoch, 1)


def test_stream_position_conflicts_with_flat_checkpoint(observer):
    journal = us.Journal(observer)
    epoch = journal.begin()
    journal.append(epoch, account_event(positions=[dict(s='BTCUSDT', ps='LONG', pa='1')]))
    with pytest.raises(ValueError, match='positions conflict'):
        journal.reconcile(flat_proof(end=1002000), 1000000, epoch, 1)


# ─── user_stream.monitor ───────────────────────────────────────────────────

class FakeMessage:
    def __init__(self, kind, data):
        self.type = kind
        self.data = data


class FakeWS:
    def __init__(self, messages):
        self.messages = list(messages)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.messages:
            return self.messages.pop(0)
        await asyncio.Event().wait()


class FakeWSConnect:
    def __init__(self, messages):
        self.messages = messages

    async def __aenter__(self):
        return FakeWS(self.messages)

    async def __aexit__(self, *args):
        return False


class FakeSession:
    def __init__(self, messages):
        self.messages = messages

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def ws_connect(self, url, **kwargs):
        return FakeWSConnect(self.messages)


@pytest.mark.asyncio
async def test_monitor_observes_without_authorizing_trading(observer, monkeypatch):
    import aiohttp
    reader = FakeReader(server_time=1002000)
    messages = [FakeMessage(aiohttp.WSMsgType.TEXT, json.dumps(account_event()))]
    monkeypatch.setattr(aiohttp, 'ClientSession', lambda **kwargs: FakeSession(messages))
    result = await us.monitor(reader, observer, 0)
    assert result['status'] == 'STREAM_OBSERVATION_COMPLETED'
    assert result['trading_authorized'] is False
    assert result['rest_fences'] == 2
    assert result['received_events'] == 1


@pytest.mark.asyncio
async def test_monitor_rejects_account_binding_mismatch(observer, monkeypatch):
    import aiohttp
    monkeypatch.setattr(aiohttp, 'ClientSession', lambda **kwargs: FakeSession([]))
    reader = FakeReader(uids=(4242, 4242))
    with pytest.raises(ValueError, match='account binding mismatch'):
        await us.monitor(reader, observer, 0)


@pytest.mark.asyncio
async def test_monitor_maps_transport_failure_to_read_error(observer, monkeypatch):
    import aiohttp

    def broken(**kwargs):
        raise OSError('no network')

    monkeypatch.setattr(aiohttp, 'ClientSession', broken)
    reader = FakeReader()
    with pytest.raises(ReadError, match='stream_session_failed'):
        await us.monitor(reader, observer, 0)


# ─── user_stream.main ──────────────────────────────────────────────────────

def test_main_rejects_unbounded_duration(tmp_path, monkeypatch):
    monkeypatch.setattr('sys.argv', ['user_stream', '--env-file', str(tmp_path / '.env'),
                                     '--database', str(tmp_path / 'observer.sqlite'),
                                     '--output', str(tmp_path / 'out'), '--seconds', '0'])
    with pytest.raises(SystemExit):
        us.main()


def test_main_writes_blocked_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(us.Reader, 'from_env', staticmethod(lambda path: (_ for _ in ()).throw(
        ReadError('missing_credentials'))))
    output = tmp_path / 'out'
    monkeypatch.setattr('sys.argv', ['user_stream', '--env-file', str(tmp_path / '.env'),
                                     '--database', str(tmp_path / 'observer.sqlite'),
                                     '--output', str(output), '--seconds', '5'])
    with pytest.raises(SystemExit) as exc:
        us.main()
    assert exc.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload['status'] == 'BLOCKED' and payload['trading_authorized'] is False


def test_main_writes_observation_result(tmp_path, monkeypatch, capsys):
    async def fake_monitor(reader, observer, seconds):
        return dict(status='STREAM_OBSERVATION_COMPLETED', rest_fences=1, received_events=0,
                    final_state='NEEDS_REST', trading_authorized=False)

    monkeypatch.setattr(us, 'monitor', fake_monitor)
    monkeypatch.setattr(us.Reader, 'from_env', staticmethod(lambda path: FakeReader()))
    output = tmp_path / 'out'
    monkeypatch.setattr('sys.argv', ['user_stream', '--env-file', str(tmp_path / '.env'),
                                     '--database', str(tmp_path / 'observer.sqlite'),
                                     '--output', str(output), '--seconds', '5'])
    us.main()
    assert json.loads(capsys.readouterr().out)['status'] == 'STREAM_OBSERVATION_COMPLETED'
    assert json.loads((output / 'result.json').read_text())['trading_authorized'] is False
    assert (output / '.gitignore').read_text() == '*\n!.gitignore\n'
