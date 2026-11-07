"""Applied history survives pruning, overlapping income and interrupted WAL writes."""
from contextlib import closing
from decimal import Decimal
from pathlib import Path
import json
import sqlite3

import pytest

from core import funding, funding_store


def setup(tmp_path, mode='paper'):
    config=tmp_path/'config.json'
    config.write_text(json.dumps({'portfolios':[{'paper_account_free_margin':5000,'cash_reserve_usdt':5000}]}))
    state={'base_ticker':'TESTUSDT','virt_debt':500}
    if mode=='real':state['live_funding']={'symbol':'TESTUSDT','snapshot_ms':1,'boundary_ms':10000}
    wallet={'balance':1000,'positions':{}}
    journal,halt=funding.load_funding_state(state,str(config))
    return config,state,wallet,journal,halt,str(tmp_path/'strategy.json'),str(tmp_path/'wallet.json')


def apply(item, identity, mode='paper'):
    config,state,wallet,journal,halt,sp,wp=item
    event=funding.FundingEvent(event_id=f'binance:FUNDING_FEE:{identity}',signed_amount=Decimal('-1'),
                              event_time=identity,source_ref=str(identity),symbol='TESTUSDT')
    assert journal.receive(event)=='new'
    funding.save_funding_boundary(sp,wp,state,wallet,journal,halt)
    receipt=funding.settle_funding_reserve(str(config),mode,'TESTUSDT',event)
    funding.apply_funding_paper(event.signed_amount,wallet,state,receipt)
    journal.mark_applied(event.event_id)
    return event


def test_older_archive_cannot_acknowledge_newer_worker_snapshot(tmp_path):
    item=setup(tmp_path)
    config,state,wallet,journal,halt,sp,wp=item
    for identity in (1,2,3):
        apply(item,identity)
        funding.save_funding_boundary(sp,wp,state,wallet,journal,halt)
    with closing(sqlite3.connect(funding_store.database_path(config))) as db, db:
        db.execute('delete from applied_events where rowid=?',(state['funding_archive_sequence'],))
    restarted,_=funding.load_funding_state(state,str(config))
    event=funding.FundingEvent(event_id='new',signed_amount=Decimal('-1'),event_time=4,symbol='TESTUSDT')
    with pytest.raises(ValueError,match='checkpoint is missing'):
        restarted.receive(event)
    assert state['funding_paid']==3


@pytest.mark.parametrize('mode',['paper','real'])
def test_full_archive_and_bounded_hot_journal(tmp_path,mode):
    item=setup(tmp_path,mode)
    config,state,wallet,journal,halt,sp,wp=item
    for identity in range(1,21):
        apply(item,identity,mode)
        funding.save_funding_boundary(sp,wp,state,wallet,journal,halt)
        assert len(journal)<=2
    assert state['funding_journal_archived'] and state['funding_paid']==20
    assert len(json.loads(Path(sp).read_text())['funding_journal'])==2
    with closing(sqlite3.connect(funding_store.database_path(config))) as db:
        assert db.execute('select count(*) from applied_events').fetchone()[0]==20
    old=funding.FundingEvent(event_id='binance:FUNDING_FEE:1',signed_amount=Decimal('-1'),event_time=1,
                             source_ref='1',symbol='TESTUSDT')
    restarted,_=funding.load_funding_state(json.loads(Path(sp).read_text()),str(config))
    assert restarted.receive(old)=='duplicate' and not restarted.get_received()
    old.signed_amount=Decimal('-2')
    assert restarted.receive(old)=='conflict'


@pytest.mark.parametrize('stage',['archive','after_strategy','strategy','wallet'])
def test_crash_after_archive_restores_exactly_once(tmp_path,monkeypatch,stage):
    item=setup(tmp_path)
    config,state,wallet,journal,halt,sp,wp=item
    for identity in (1,2):
        apply(item,identity)
        funding.save_funding_boundary(sp,wp,state,wallet,journal,halt)
    event=apply(item,3)
    original_save,original_archive=funding.atomic_save_json,funding_store.archive_events
    intents=[0]

    def save(target,data):
        if target==sp+'.funding_intent':intents[0]+=1
        if ((stage=='strategy' and target==sp) or (stage=='wallet' and target==wp)):
            raise OSError('injected archive boundary failure')
        original_save(target,data)
        if stage=='after_strategy' and target==sp:
            raise OSError('injected failure after strategy replacement')

    def archive(*args):
        result = original_archive(*args)
        if stage=='archive':raise OSError('injected post-archive failure')
        return result

    with monkeypatch.context() as scoped:
        scoped.setattr(funding,'atomic_save_json',save)
        scoped.setattr(funding_store,'archive_events',archive)
        with pytest.raises(OSError):funding.save_funding_boundary(sp,wp,state,wallet,journal,halt)
    status,restored,restored_wallet=funding.recover_funding_boundary(sp,wp)
    assert status=='RECOVERED' and restored['funding_paid']==3 and restored_wallet['balance']==1000
    restarted,recovered_halt=funding.load_funding_state(restored,str(config))
    assert restarted.receive(event)=='duplicate' and not restarted.get_received()
    funding.save_funding_boundary(sp,wp,restored,restored_wallet,restarted,recovered_halt)
    funding.settle_funding_reserve(str(config),'paper','TESTUSDT',event)
    assert json.loads(config.read_text())['portfolios'][0]['paper_account_free_margin']==4997
    assert len(restarted)==2


@pytest.mark.asyncio
async def test_real_overlapping_income_uses_archived_identities(tmp_path):
    from unittest.mock import AsyncMock
    item=setup(tmp_path,'real')
    config,state,wallet,journal,halt,sp,wp=item
    for identity in range(1,8):
        apply(item,identity,'real')
        funding.save_funding_boundary(sp,wp,state,wallet,journal,halt)
    connector=type('Connector',(),{})()
    connector.get_funding_income=AsyncMock(return_value=[dict(tranId=i,time=i*1000,income='-1') for i in range(1,8)])
    state['funding_model']='signed_actual'
    loaded=await funding.poll_live_funding(connector,state,'TESTUSDT',10001,str(config))
    assert len(loaded)==2 and not loaded.get_received()
    assert state['funding_paid']==7


def test_pending_and_quarantine_are_not_pruned(tmp_path):
    item=setup(tmp_path)
    config,state,wallet,journal,halt,sp,wp=item
    for identity in range(1,5):
        apply(item,identity)
        funding.save_funding_boundary(sp,wp,state,wallet,journal,halt)
    pending=funding.FundingEvent(event_id='pending',signed_amount=Decimal('-1'),event_time=100)
    journal.receive(pending)
    quarantined=funding.FundingEvent(event_id='quarantined',signed_amount=Decimal('-1'),event_time=101)
    journal.receive(quarantined);journal.mark_quarantined('quarantined','test')
    funding.save_funding_boundary(sp,wp,state,wallet,journal,halt)
    assert len(journal)==4 and journal.events['pending'].status=='received'
    assert journal.events['quarantined'].status=='quarantined'


@pytest.mark.parametrize('damage',['database','table','worker_rows'])
def test_missing_archive_blocks_new_receives(tmp_path,damage):
    item=setup(tmp_path)
    config,state,wallet,journal,halt,sp,wp=item
    for identity in range(1,5):
        apply(item,identity)
        funding.save_funding_boundary(sp,wp,state,wallet,journal,halt)
    if damage=='database':Path(funding_store.database_path(config)).unlink()
    else:
        with closing(sqlite3.connect(funding_store.database_path(config))) as db,db:
            db.execute('drop table applied_events' if damage=='table' else 'delete from applied_events')
    with pytest.raises(ValueError,match='missing'):
        journal.receive(funding.FundingEvent(event_id='new',signed_amount=Decimal('-1'),event_time=99))


def test_backup_restore_preserves_archived_duplicates(tmp_path):
    import shutil
    from scripts.backup_funding_config import backup,verify
    item=setup(tmp_path)
    config,state,wallet,journal,halt,sp,wp=item
    for identity in range(1,6):
        apply(item,identity)
        funding.save_funding_boundary(sp,wp,state,wallet,journal,halt)
    bundle=tmp_path/'bundle';backup(config,bundle);verify(bundle)
    restored=tmp_path/'restored';restored.mkdir()
    copied=restored/'config.json'
    shutil.copyfile(bundle/'config.json',copied)
    shutil.copyfile(bundle/'config.json.funding.sqlite',funding_store.database_path(copied))
    restored_journal,_=funding.load_funding_state(json.loads(Path(sp).read_text()),str(copied))
    old=funding.FundingEvent(event_id='binance:FUNDING_FEE:1',signed_amount=Decimal('-1'),event_time=1,
                             source_ref='1',symbol='TESTUSDT')
    assert restored_journal.receive(old)=='duplicate'
    assert json.loads(copied.read_text())['portfolios'][0]['paper_account_free_margin']==4995


@pytest.mark.asyncio
async def test_older_bot_snapshot_replays_newer_archive_without_second_reserve_debit(tmp_path):
    from unittest.mock import AsyncMock
    item=setup(tmp_path,'real')
    config,state,wallet,journal,halt,sp,wp=item
    state['funding_model']='signed_actual'
    for identity in (1,2):
        apply(item,identity,'real')
        funding.save_funding_boundary(sp,wp,state,wallet,journal,halt)
    state['live_funding']['boundary_ms']=2000
    old=json.loads(json.dumps(state))
    for identity in (3,4):
        apply(item,identity,'real')
        funding.save_funding_boundary(sp,wp,state,wallet,journal,halt)
    connector=type('Connector',(),{})()
    connector.get_funding_income=AsyncMock(return_value=[dict(tranId=i,time=i*1000,income='-1') for i in (1,2,3,4)])
    recovered=await funding.poll_live_funding(connector,old,'TESTUSDT',5000,str(config))
    assert len(recovered.get_received())==2
    for event in recovered.get_received():
        receipt=funding.settle_funding_reserve(str(config),'real','TESTUSDT',event)
        funding.apply_funding_paper(event.signed_amount,wallet,old,receipt)
        recovered.mark_applied(event.event_id)
        funding.save_funding_boundary(sp,wp,old,wallet,recovered,halt)
    assert old['funding_paid']==4 and len(recovered)==2
    assert json.loads(config.read_text())['portfolios'][0]['cash_reserve_usdt']==4996
    assert old['funding_archive_sequence']==state['funding_archive_sequence']
