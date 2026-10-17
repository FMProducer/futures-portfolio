from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal as D
import json
import sqlite3

import pytest

from rebalance.durable import Store, pack
from rebalance.kernel import Action, Config, Portfolio
from rebalance.historical import replay


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path/'trading.sqlite')
    p = Portfolio(Config('.001', '5.3', '7', '.0004', '.7', '.7', '.09'), '10000')
    s.initialize(p)
    return s


def prepare(store, client='one', units=1000, kind='ADD', revision=None):
    if revision is None:
        revision = store.load()[1]
    return store.prepare('prepare-'+client, client, Action(kind, 'LONG', units),
                         '100', '1', '100', revision, entry=kind == 'ADD')


def dispatch(store):
    return store.transition('dispatch-one', 'one', 'DISPATCHING')


def test_state_roundtrip_keeps_decimal_extrema_and_receipts(store):
    store.cash_event('funding', '-2.1', 'FUNDING')
    store.observe('tick', '100', '99', '101')
    before, revision = store.load()
    restored, r = Store(store.path).load()
    assert restored == before and r == revision
    assert restored.extrema['LONG']['peak'] is None
    assert restored.receipts['funding'] == ('FUNDING', '-2.1')


def test_prepared_reserve_survives_restart_without_cash_spend(store):
    order = prepare(store)
    p, _ = Store(store.path).load()
    assert p.wallet == 10000 and p.legs['LONG'].units == 0
    assert p.order_reserve == D(order['reserved']) > 0
    assert Store(store.path).orders()[0]['status'] == 'PREPARED'


def test_dispatch_claim_cannot_be_replayed_as_permission_to_send(store):
    prepare(store)
    assert dispatch(store)['dispatch_granted'] is True
    assert dispatch(Store(store.path))['dispatch_granted'] is False
    with pytest.raises(ValueError, match='redispatch'):
        store.transition('dispatch-new-id', 'one', 'DISPATCHING')


def test_unknown_holds_reserve_and_blocks_new_intent_and_planning(store):
    prepare(store)
    dispatch(store)
    store.transition('timeout', 'one', 'UNKNOWN')
    before, _ = store.load()
    restored = Store(store.path)
    assert restored.observe('tick', '110', '110', '110')['reason'] == 'outstanding_intent'
    with pytest.raises(ValueError, match='outstanding'):
        prepare(restored, 'two')
    assert restored.load()[0].order_reserve == before.order_reserve
    assert restored.orders()[0]['status'] == 'UNKNOWN'


def test_partial_fill_and_duplicate_are_atomic_across_restart(store):
    original = prepare(store)
    dispatch(store)
    store.transition('accepted', 'one', 'OPEN')
    result = store.fill('trade-1', 'one', 400, '100', '.04', '100')
    assert result['status'] == 'PARTIAL'
    p, revision = store.load()
    assert p.wallet == D('9999.96') and p.legs['LONG'].initial_units == 400
    assert p.order_reserve == D(original['initial_reserved'])*D('.6')
    store = Store(store.path)
    assert store.fill('trade-1', 'one', 400, '100', '.04', '100') == result
    assert store.load()[1] == revision
    with pytest.raises(ValueError, match='conflicting'):
        store.fill('trade-1', 'one', 400, '100', '.05', '100')
    store.fill('trade-2', 'one', 600, '100', '.06', '100')
    p, _ = store.load()
    assert p.order_reserve == 0 and p.legs['LONG'].units == 1000
    assert p.legs['LONG'].initial_units == 1000 and p.fees == D('.1')


def test_cancel_fill_race_requires_all_fills_before_release(store):
    prepare(store)
    dispatch(store)
    store.transition('open', 'one', 'OPEN')
    store.transition('cancel', 'one', 'CANCEL_PENDING')
    store.fill('late', 'one', 250, '100', '.025', '100')
    assert store.orders()[0]['status'] == 'CANCEL_PENDING'
    before, revision = store.load()
    with pytest.raises(ValueError, match='reconciliation'):
        store.finalize('bad-final', 'one', 'CANCELED', 300)
    assert store.load() == (before, revision)
    final = store.finalize('final', 'one', 'CANCELED', 250)
    assert final['reserved'] == '0'
    assert store.load()[0].order_reserve == 0
    assert store.load()[0].legs['LONG'].units == 250
    assert store.finalize('final', 'one', 'CANCELED', 250) == final


def test_unknown_partial_fill_does_not_prove_remainder_is_open(store):
    prepare(store)
    dispatch(store)
    store.transition('timeout', 'one', 'UNKNOWN')
    assert store.fill('partial', 'one', 100, '100', '.01', '100')['status'] == 'UNKNOWN'
    store.finalize('reconciled-cancel', 'one', 'CANCELED', 100)
    assert store.load()[0].order_reserve == 0


def test_reject_release_and_client_id_not_reused(store):
    prepare(store)
    dispatch(store)
    store.finalize('reject', 'one', 'REJECTED', 0)
    assert store.load()[0].wallet == 10000
    with pytest.raises(ValueError, match='already used'):
        store.prepare('new-prepare', 'one', Action('ADD', 'LONG', 1000),
                      '100', '1', '100', store.load()[1])


def test_stale_plan_revision_rejected(store):
    _, revision = store.load()
    store.observe('observation', '100', '100', '100')
    with pytest.raises(ValueError, match='stale'):
        prepare(store, revision=revision)


def test_transaction_failure_rolls_back_fill_state_order_and_journal(store):
    prepare(store)
    dispatch(store)
    before, revision = store.load()
    orders = store.orders()
    with sqlite3.connect(store.path) as db:
        db.execute("CREATE TRIGGER simulated_crash BEFORE INSERT ON events "
                   "BEGIN SELECT RAISE(ABORT,'simulated disk failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.fill('trade', 'one', 500, '100', '.05', '100')
    assert Store(store.path).load() == (before, revision)
    assert store.orders() == orders
    with sqlite3.connect(store.path) as db:
        db.execute('DROP TRIGGER simulated_crash')
    store.fill('trade', 'one', 500, '100', '.05', '100')
    assert store.load()[0].legs['LONG'].units == 500


def test_concurrent_intents_cannot_double_reserve(store):
    def attempt(client):
        try:
            return prepare(Store(store.path), client, revision=0)['status']
        except ValueError:
            return 'BLOCKED'
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(attempt, ('a', 'b')))
    assert sorted(result) == ['BLOCKED', 'PREPARED']
    assert len(store.orders()) == 1


def test_bad_actual_fill_preserved_as_blocked_evidence(store):
    prepare(store)
    dispatch(store)
    result = store.fill('expensive', 'one', 1000, '1000000', '1', '100')
    assert result['status'] == 'RECONCILIATION_REQUIRED'
    p, _ = store.load()
    assert p.wallet == 10000 and p.legs['LONG'].units == 0 and p.order_reserve > 0
    with sqlite3.connect(store.path) as db:
        raw = json.loads(db.execute("SELECT request FROM events WHERE event_id='trade:expensive'").fetchone()[0])
    assert raw['price'] == '1000000'
    with pytest.raises(ValueError, match='reconciliation'):
        store.finalize('cancel', 'one', 'CANCELED', 0)


def test_filled_trade_replay_after_terminal_does_not_double_post(store):
    prepare(store)
    dispatch(store)
    result = store.fill('full', 'one', 1000, '100', '.1', '100')
    assert result['status'] == 'FILLED'
    before = pack(store.load()[0])
    Store(store.path).fill('full', 'one', 1000, '100', '.1', '100')
    assert pack(store.load()[0]) == before


def test_durable_replay_matches_in_memory_economics_and_restores_extrema(tmp_path):
    config = Config('.001', '5.3', '7', '.0004', '.7', '.7', '.09')
    prices = [D(100)+D(i)/10 for i in range(100)] + [D(110)-D(i)/10 for i in range(200)]
    bars = [dict(time=i*60000, open=p, high=p, low=p, close=p) for i,p in enumerate(prices)]
    args = (bars, config, '10000', '2', '1', [])
    baseline = replay(*args)
    s = Store(tmp_path/'replay.sqlite')
    assert replay(*args, store=s) == baseline
    p, _ = Store(s.path).load()
    assert p.extrema and p.receipts and p.order_reserve == 0
    assert all(o['status'] == 'FILLED' for o in s.orders())


def test_reduce_partial_then_cancel_keeps_only_confirmed_cash_postings(store):
    prepare(store)
    dispatch(store)
    store.fill('entry', 'one', 1000, '100', '.1', '100')
    order = prepare(store, 'reduce', units=500, kind='REDUCE')
    store.transition('dispatch-reduce', 'reduce', 'DISPATCHING')
    store.fill('reduce-trade', 'reduce', 200, '110', '.022', '110')
    before = store.load()[0]
    store.finalize('cancel-reduce', 'reduce', 'CANCELED', 200)
    after = store.load()[0]
    assert after.legs['LONG'].units == 800
    assert after.wallet == before.wallet == D('10001.878')
    assert after.order_reserve == 0 and D(order['reserved']) == 1
