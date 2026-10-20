from copy import deepcopy
import io
import sqlite3

import pytest

from rebalance.binance_readonly import ReadError
from rebalance.checkpoint import Observer
from rebalance.user_stream import Journal, start_stream


def proof(end=1001000,wallet='100'):
    return dict(binding=dict(account='a'*64,market='USD-M',symbol='BTCUSDT',asset='USDT',purpose='OBSERVER_ONLY_V1'),
        start=700000,before=999000,end=end,trades=[],incomes=[],
        observation=dict(blocking_reasons=[],rest_bracket_equal=True,active_positions=[],open_orders=0,
                         initial_margin_usdt='0',unrealized_usdt='0',wallet_usdt=wallet))


def account_event(t=1000100,wallet='100'):
    return dict(e='ACCOUNT_UPDATE',E=t,T=t,a=dict(m='ORDER',B=[dict(a='USDT',wb=wallet)],P=[]))


@pytest.fixture
def journal(tmp_path):
    observer = Observer(tmp_path/'observer.sqlite')
    observer.initialize(proof(end=1000000))
    return Journal(observer)


def fence(journal,epoch,seq=0,checkpoint=None,through=1000000):
    return journal.reconcile(checkpoint or proof(),through,epoch,seq)


def test_connect_does_not_certify_and_reopen_keeps_state(journal):
    epoch = journal.begin()
    assert journal.state()['status'] == 'NEEDS_REST'
    assert Journal(Observer(journal.observer.path)).state()['epoch'] == epoch
    assert journal.state()['trading_authorized'] is False


def test_quiet_fence_is_observation_not_trading_permit(journal):
    epoch = journal.begin()
    fence(journal,epoch)
    assert journal.state()['status'] == 'OBSERVING_NOT_CERTIFIED'
    assert journal.state()['trading_authorized'] is False


def test_event_dirties_state_and_duplicate_does_not_double_count(journal):
    epoch = journal.begin()
    fence(journal,epoch)
    message = account_event()
    assert journal.append(epoch,message) is True
    assert journal.append(epoch,message) is False
    assert journal.state()['status'] == 'NEEDS_REST' and journal.state()['last_seq'] == 1
    fence(journal,epoch,1,proof(end=1002000),1001000)


def test_receive_race_keeps_cursor_at_old_value(journal):
    epoch = journal.begin()
    journal.append(epoch,account_event())
    with pytest.raises(ValueError,match='fence'):
        fence(journal,epoch,0)
    assert journal.observer.state()[1] == 1000000


def test_disconnect_and_new_connection_require_new_rest(journal):
    epoch = journal.begin()
    fence(journal,epoch)
    journal.disconnect(epoch)
    assert journal.state()['status'] == 'DISCONNECTED_NEEDS_REST'
    new = journal.begin()
    assert new != epoch and journal.state()['status'] == 'NEEDS_REST'
    with pytest.raises(ValueError,match='stale'):
        journal.append(epoch,account_event())
    fence(journal,new,0,proof(end=1002000),1001000)


def test_out_of_order_same_type_blocks_until_reconnect(journal):
    epoch = journal.begin()
    journal.append(epoch,account_event(1000200))
    journal.append(epoch,account_event(1000100))
    assert journal.state()['fault'] == 'OUT_OF_ORDER'
    with pytest.raises(ValueError,match='fence'):
        fence(journal,epoch,2)
    new = journal.begin()
    fence(journal,new)


def test_expiry_token_is_redacted_and_new_epoch_can_reconcile(journal):
    epoch = journal.begin()
    journal.append(epoch,dict(e='listenKeyExpired',E=1000100,listenKey='SENSITIVE_TOKEN'))
    with pytest.raises(ValueError):
        fence(journal,epoch,1)
    with journal.observer.connect() as db:
        body = db.execute('SELECT body FROM stream_messages').fetchone()[0]
    assert 'SENSITIVE_TOKEN' not in body and 'listenKey"' not in body
    fence(journal,journal.begin())


@pytest.mark.parametrize('event',['MARGIN_CALL','ACCOUNT_CONFIG_UPDATE','NEW_UNKNOWN_EVENT'])
def test_risk_and_unknown_event_types_block(journal,event):
    epoch = journal.begin()
    journal.append(epoch,dict(e=event,E=1000100,T=1000100))
    with pytest.raises(ValueError):
        fence(journal,epoch,1)
    assert journal.observer.state()[1] == 1000000


def test_wallet_conflict_never_advances_cursor(journal):
    epoch = journal.begin()
    journal.append(epoch,account_event(wallet='999'))
    with pytest.raises(ValueError,match='wallet'):
        fence(journal,epoch,1)
    assert journal.observer.state()[1] == 1000000


def test_event_after_rest_end_requires_another_fence(journal):
    epoch = journal.begin()
    journal.append(epoch,account_event(1002000))
    with pytest.raises(ValueError,match='beyond'):
        fence(journal,epoch,1)


def test_trade_stream_matches_rest_without_posting_fees_again(journal):
    epoch = journal.begin()
    o = dict(s='BTCUSDT',i=10,c='client',S='BUY',ps='LONG',q='.1',z='.1',x='TRADE',X='FILLED',
             t=1,l='.1',L='100',N='USDT',n='0',rp='0')
    journal.append(epoch,dict(e='ORDER_TRADE_UPDATE',E=1000100,T=1000100,o=o))
    p = proof()
    p['trades'] = [dict(symbol='BTCUSDT',orderId=10,id=1,side='BUY',positionSide='LONG',
        qty='.1',price='100',commissionAsset='USDT',commission='0',realizedPnl='0',time=1000100)]
    p['order_evidence'] = {'BTCUSDT:10':dict(symbol='BTCUSDT',orderId=10,clientOrderId='client',
        side='BUY',positionSide='LONG',origQty='.1',executedQty='.1',status='FILLED',updateTime=1000100)}
    p['trades'].append(dict(p['trades'][0],id=2,orderId=11,side='SELL',time=1000200))
    p['order_evidence']['BTCUSDT:11'] = dict(p['order_evidence']['BTCUSDT:10'],
        orderId=11,clientOrderId='close',side='SELL',updateTime=1000200)
    journal.append(epoch,dict(e='ORDER_TRADE_UPDATE',E=1000200,T=1000200,
        o=dict(o,i=11,c='close',S='SELL',t=2)))
    fence(journal,epoch,2,p)
    assert journal.state()['status'] == 'OBSERVING_NOT_CERTIFIED'


def test_terminal_cancel_requires_query_order_even_without_fills(journal):
    epoch = journal.begin()
    journal.append(epoch,dict(e='ORDER_TRADE_UPDATE',E=1000100,T=1000100,
        o=dict(s='BTCUSDT',i=10,c='client',S='BUY',ps='LONG',q='.1',z='0',x='CANCELED',X='CANCELED')))
    with pytest.raises(ValueError,match='lifecycle'):
        fence(journal,epoch,1)
    p = proof()
    p['order_evidence'] = {'BTCUSDT:10':dict(symbol='BTCUSDT',orderId=10,clientOrderId='client',
        side='BUY',positionSide='LONG',origQty='.1',executedQty='0',status='CANCELED',updateTime=1000100)}
    fence(journal,epoch,1,p)


def test_stream_and_rest_fence_roll_back_together_on_db_failure(journal):
    epoch = journal.begin()
    with journal.observer.connect() as db:
        db.execute("CREATE TRIGGER injected BEFORE INSERT ON observer_checks BEGIN SELECT RAISE(ABORT,'fault'); END")
    with pytest.raises(sqlite3.IntegrityError):
        fence(journal,epoch)
    assert journal.state()['status'] == 'NEEDS_REST'
    assert journal.observer.state()[1] == 1000000


def test_session_transport_only_opens_exact_non_financial_endpoint():
    class Response(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self,*args): self.close()
    class Opener:
        def open(self,request,timeout):
            assert request.method == 'POST'
            assert request.full_url == 'https://fapi.binance.com/fapi/v1/listenKey'
            assert request.get_header('X-mbx-apikey') == 'fixture-key'
            return Response(b'{"listenKey":"ephemeral123"}')
    class Reader:
        _key = 'fixture-key'
        _opener = Opener()
    assert start_stream(Reader()) == 'ephemeral123'


def test_failed_second_rest_invalidates_prior_fence_without_cursor_progress(journal):
    epoch = journal.begin()
    fence(journal,epoch)
    with pytest.raises(ValueError,match='wallet bridge'):
        fence(journal,epoch,0,proof(end=1002000,wallet='999'),1001000)
    assert journal.state()['status'] == 'NEEDS_REST'
    assert journal.observer.state()[1] == 1001000


def test_cancel_cannot_hide_executed_quantity_missing_from_history(journal):
    epoch = journal.begin()
    journal.append(epoch,dict(e='ORDER_TRADE_UPDATE',E=1000100,T=1000100,
        o=dict(s='BTCUSDT',i=10,c='client',S='BUY',ps='LONG',q='.1',z='.05',x='CANCELED',X='CANCELED')))
    p = proof()
    p['order_evidence'] = {'BTCUSDT:10':dict(symbol='BTCUSDT',orderId=10,clientOrderId='client',
        side='BUY',positionSide='LONG',origQty='.1',executedQty='.05',status='CANCELED',updateTime=1000100)}
    with pytest.raises(ValueError,match='not covered'):
        fence(journal,epoch,1,p)
    assert journal.observer.state()[1] == 1000000
