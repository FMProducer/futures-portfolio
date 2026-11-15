"""Error-branch coverage for rebalance.durable Store and rebalance.public_session."""
import json
import sqlite3
from decimal import Decimal as D

import pytest

from rebalance.durable import Store, pack, unpack
from rebalance.kernel import Action, Config, Portfolio
from rebalance.public_session import Session, SessionReader, exclusive
from rebalance.public_paper import Paper
from rebalance.binance_readonly import ReadError


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / 'trading.sqlite')
    s.initialize(Portfolio(Config('.001', '5.3', '7', '.0004', '.7', '.7', '.09'), '10000'))
    return s


def _prepare(store, client='one', units=1000, kind='ADD'):
    revision = store.load()[1]
    return store.prepare('prepare-' + client, client, Action(kind, 'LONG', units),
                         '100', '1', '100', revision, entry=(kind == 'ADD'))


def test_durable_initialize_twice_rejected(store):
    p = Portfolio(Config('.001', '5.3', '7', '.0004', '.7', '.7', '.09'), '10000')
    with pytest.raises(ValueError, match='already initialized'):
        Store(store.path).initialize(p)


def test_durable_initialize_with_reserve_rejected(tmp_path):
    s = Store(tmp_path / 't.sqlite')
    p = Portfolio(Config('.001', '5.3', '7', '.0004', '.7', '.7', '.09'), '10000')
    p.order_reserve = D('1')
    with pytest.raises(ValueError, match='outbox owns order_reserve'):
        s.initialize(p)


def test_durable_load_uninitialized_rejected(tmp_path):
    with pytest.raises(ValueError, match='not initialized'):
        Store(tmp_path / 'none.sqlite').load()


def test_durable_unknown_intent(store):
    _prepare(store)
    with pytest.raises(ValueError, match='unknown intent'):
        store.transition('x', 'ghost', 'DISPATCHING')


def test_durable_missing_event_identity(store):
    with pytest.raises(ValueError, match='event identity'):
        store.prepare('', 'one', Action('ADD', 'LONG', 100), '100', '1', '100', 0)


def test_durable_apply_before_initialize_rejected(tmp_path):
    s = Store(tmp_path / 'n.sqlite')
    with pytest.raises(ValueError, match='not initialized'):
        s.prepare('e1', 'one', Action('ADD', 'LONG', 100), '100', '1', '100', 0)


@pytest.mark.parametrize('mutate', [
    lambda a: Action('ADD', 'LONG', 0),
    lambda a: Action('HELD', 'LONG', 100),
    lambda a: Action('ADD', 'LONG', 100.5),
])
def test_durable_invalid_intent_shape(store, mutate):
    action = mutate(Action('ADD', 'LONG', 100))
    revision = store.load()[1]
    with pytest.raises(ValueError, match='invalid intent'):
        store.prepare('bad', 'one', action, '100', '1', '100', revision)


def test_durable_prepare_validation_matrix(store):
    revision = store.load()[1]
    with pytest.raises(ValueError, match='invalid intent'):
        store.prepare('long-id', 'x' * 37, Action('ADD', 'LONG', 100), '100', '1', '100', revision)
    with pytest.raises(ValueError, match='invalid intent'):
        store.prepare('neg-fee', 'one', Action('ADD', 'LONG', 100), '100', '-1', '100', revision)
    with pytest.raises(ValueError, match='invalid intent'):
        store.prepare('bad-entry', 'one', Action('ADD', 'LONG', 100), '100', '1', '100',
                      revision, entry='yes')
    with pytest.raises(ValueError, match='invalid intent'):
        store.prepare('bad-rev', 'one', Action('ADD', 'LONG', 100), '100', '1', '100',
                      str(revision))


def test_durable_entry_requires_uninitialized_leg(store):
    order = _prepare(store)
    store.transition('dispatch', 'one', 'DISPATCHING')
    store.transition('open', 'one', 'OPEN')
    store.fill('t1', 'one', 1000, '100', '.1', '100')
    assert store.load()[0].legs['LONG'].units == 1000
    with pytest.raises(ValueError, match='entry requires uninitialized'):
        store.prepare('e2', 'e2', Action('ADD', 'LONG', 100), '100', '1', '100',
                      store.load()[1], entry=True)


def test_durable_over_close_rejected(store):
    _prepare(store)
    store.transition('dispatch', 'one', 'DISPATCHING')
    store.transition('open', 'one', 'OPEN')
    store.fill('t1', 'one', 1000, '100', '.1', '100')
    with pytest.raises(ValueError, match='over-close'):
        store.prepare('red', 'red', Action('REDUCE', 'LONG', 2000), '100', '1', '100',
                      store.load()[1])


def test_durable_below_minimum_notional_rejected(store):
    with pytest.raises(ValueError, match='minimum notional'):
        store.prepare('small', 'small', Action('ADD', 'LONG', 100), '0.001', '1', '100',
                      store.load()[1])


def test_durable_insufficient_reserve_rejected(store):
    with pytest.raises(ValueError, match='insufficient shared reserve'):
        store.prepare('rich', 'rich', Action('ADD', 'LONG', 100), '100', '999999', '100',
                      store.load()[1])


def test_durable_unsupported_transition(store):
    _prepare(store)
    with pytest.raises(ValueError, match='unsupported transition'):
        store.transition('f', 'one', 'FILLED')


def test_durable_fill_missing_trade_identity(store):
    _prepare(store)
    store.transition('d', 'one', 'DISPATCHING')
    with pytest.raises(ValueError, match='trade identity'):
        store.fill('', 'one', 100, '100', '.01', '100')


def test_durable_fill_invalid_trade(store):
    _prepare(store)
    store.transition('d', 'one', 'DISPATCHING')
    with pytest.raises(ValueError, match='invalid trade'):
        store.fill('t0', 'one', 0, '100', '.01', '100')


def test_durable_fill_in_prepared_state_rejected(store):
    _prepare(store)
    with pytest.raises(ValueError, match='inconsistent with order state'):
        store.fill('early', 'one', 100, '100', '.01', '100')


def test_durable_finalize_invalid_evidence(store):
    _prepare(store)
    store.transition('d', 'one', 'DISPATCHING')
    with pytest.raises(ValueError, match='terminal evidence'):
        store.finalize('bad', 'one', 'FILLED', 0)
    with pytest.raises(ValueError, match='terminal evidence'):
        store.finalize('bad2', 'one', 'CANCELED', -1)


def test_durable_reject_with_fills_rejected(store):
    _prepare(store)
    store.transition('d', 'one', 'DISPATCHING')
    store.transition('o', 'one', 'OPEN')
    store.fill('f1', 'one', 200, '100', '.02', '100')
    with pytest.raises(ValueError, match='cannot be rejected'):
        store.finalize('rej', 'one', 'REJECTED', 200)


def test_durable_unpack_tampered_quantity_rejected(store):
    body = json.loads(pack(store.load()[0]))
    body['portfolio']['legs']['LONG']['units'] = -1
    with pytest.raises(ValueError, match='invalid stored quantity'):
        unpack(json.dumps(body))


# ── public_session ────────────────────────────────────────────────────────────

def _inputs():
    settings = dict(capital='1000', slippage_bps='1', strategy=dict(step='0.001', min_notional='5',
                 leverage='7', fee_rate='0.0004', surplus_pp='0.7', deficit_pp='0.7', trail_pp='0.09'))
    filters = {key: dict(minQty='0.001', maxQty='100', stepSize='0.001')
               for key in ('LOT_SIZE', 'MARKET_LOT_SIZE')}
    filters['MIN_NOTIONAL'] = dict(notional='5')
    return settings, filters


def _sample(stamp=600000):
    return dict(symbol='BTCUSDT', before=stamp - 50, after=stamp,
                mark=dict(symbol='BTCUSDT', time=stamp - 40, markPrice='100',
                          nextFundingTime=3600000),
                book=dict(symbol='BTCUSDT', time=stamp - 20, bidPrice='99.9',
                          askPrice='100.1', bidQty='100', askQty='100'))


class Clock:
    def __init__(self):
        self.now = 0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Feed:
    def __init__(self, values, funding=None):
        self.values = iter(values)
        self.rate = funding

    def capture(self, symbol):
        value = next(self.values)
        if isinstance(value, BaseException):
            raise value
        import copy
        return copy.deepcopy(value)

    def funding(self, symbol, stamp):
        import copy
        if isinstance(self.rate, BaseException):
            raise self.rate
        return copy.deepcopy(self.rate)


def test_session_reader_funding_not_published(monkeypatch):
    reader = SessionReader()

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b'[]'

    monkeypatch.setattr(reader.opener, 'open', lambda request, timeout: Response())
    with pytest.raises(ReadError, match='funding_not_published'):
        reader.funding('BTCUSDT', 650000)


def test_session_rejects_non_btc_symbol(tmp_path):
    settings, filters = _inputs()
    paper = Paper(tmp_path / 'p.sqlite', 'ETHUSDT', settings, filters)
    with pytest.raises(ValueError, match='BTCUSDT only'):
        Session(paper, Feed([]))


def test_session_binding_change_rejected(tmp_path):
    settings, filters = _inputs()
    paper = Paper(tmp_path / 'p.sqlite', 'BTCUSDT', settings, filters)
    Session(paper, Feed([]), monotonic=Clock().monotonic, sleep=Clock().sleep)
    with paper.store.connection() as db:
        db.execute("UPDATE session_state SET binding='tampered'")
    with pytest.raises(ValueError, match='binding changed'):
        Session(paper, Feed([]))


def test_session_init_rollback_on_crash(tmp_path):
    settings, filters = _inputs()
    paper = Paper(tmp_path / 'p.sqlite', 'BTCUSDT', settings, filters)
    with paper.store.connection() as db:
        db.execute('CREATE TABLE IF NOT EXISTS session_state '
                   '(id INTEGER PRIMARY KEY CHECK(id=1), binding TEXT NOT NULL, '
                   'status TEXT NOT NULL, reason TEXT)')
        db.execute("CREATE TRIGGER crash BEFORE INSERT ON session_state "
                   "BEGIN SELECT RAISE(ABORT,'crash'); END")
    with pytest.raises(sqlite3.IntegrityError):
        Session(paper, Feed([]))


def test_session_log_rollback_on_crash(tmp_path):
    settings, filters = _inputs()
    paper = Paper(tmp_path / 'p.sqlite', 'BTCUSDT', settings, filters)
    session = Session(paper, Feed([]), monotonic=Clock().monotonic, sleep=Clock().sleep)
    with paper.store.connection() as db:
        db.execute("CREATE TRIGGER crash BEFORE UPDATE ON session_state "
                   "BEGIN SELECT RAISE(ABORT,'crash'); END")
    with pytest.raises(sqlite3.IntegrityError):
        session.log('RUNNING')


@pytest.mark.parametrize('kwargs', [dict(duration=0), dict(interval=1), dict(max_errors=0)])
def test_session_invalid_schedule_rejected(tmp_path, kwargs):
    settings, filters = _inputs()
    paper = Paper(tmp_path / 'p.sqlite', 'BTCUSDT', settings, filters)
    session = Session(paper, Feed([]), monotonic=Clock().monotonic, sleep=Clock().sleep)
    with pytest.raises(ValueError, match='invalid bounded session schedule'):
        session.run(**kwargs)


def test_session_read_retries_exhaustion_freezes(tmp_path):
    settings, filters = _inputs()
    paper = Paper(tmp_path / 'p.sqlite', 'BTCUSDT', settings, filters)
    session = Session(paper, Feed([]), monotonic=Clock().monotonic, sleep=Clock().sleep)
    # Feed with a ReadError on every capture; bounded schedule exhausts retries.
    class FailingFeed:
        def capture(self, symbol):
            raise ReadError('public_request_failed')

        def funding(self, symbol, stamp):
            raise ReadError('funding_request_failed')
    failing = Session(paper, FailingFeed(), monotonic=Clock().monotonic, sleep=Clock().sleep)
    state = failing.run(duration=6, interval=2, max_errors=2)
    assert state['status'] == 'FROZEN'
    assert 'retries exhausted' in state['reason']
    with paper.store.connection() as db:
        assert db.execute('SELECT count(*) FROM session_log').fetchone()[0] >= 2


def test_session_operator_interrupt_stops_cleanly(tmp_path):
    settings, filters = _inputs()
    paper = Paper(tmp_path / 'p.sqlite', 'BTCUSDT', settings, filters)
    values = [_sample(), KeyboardInterrupt()]
    session = Session(paper, Feed(values), monotonic=Clock().monotonic, sleep=Clock().sleep)
    state = session.run(duration=30, interval=2)
    assert state['status'] == 'STOPPED'
    assert state['reason'] == 'operator interrupt'


def test_exclusive_lock_contention(tmp_path):
    settings, filters = _inputs()
    paper = Paper(tmp_path / 'p.sqlite', 'BTCUSDT', settings, filters)
    with exclusive(paper.store.path):
        with pytest.raises(ValueError, match='another paper session'):
            with exclusive(paper.store.path):
                pass
