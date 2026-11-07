"""Matched snapshots, mismatched restores, and all shared config writer paths."""
from contextlib import closing
import json
import shutil
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from core import funding, funding_store, storage
from scripts.backup_funding_config import backup, verify
from tests.test_funding_reserves import account, event


@pytest.mark.parametrize('old_file', ['config', 'database'])
def test_mixed_snapshot_blocks_existing_and_new_events(tmp_path, old_file):
    path = account(tmp_path)
    funding.settle_funding_reserve(str(path), 'paper', 'TESTUSDT', event('1'))
    snapshot = tmp_path / 'backup'
    backup(path, snapshot)
    funding.settle_funding_reserve(str(path), 'paper', 'TESTUSDT', event('2'))
    if old_file == 'config':
        shutil.copyfile(snapshot / 'config.json', path)
    else:
        shutil.copyfile(snapshot / 'config.json.funding.sqlite', funding_store.database_path(path))
    for identity in ('1', '2', '3'):
        with pytest.raises(ValueError, match='checkpoint mismatch'):
            funding.settle_funding_reserve(str(path), 'paper', 'TESTUSDT', event(identity))
    with pytest.raises(ValueError, match='checkpoint mismatch'):
        storage.safe_save_json_sync(str(path), json.loads(path.read_text()))


def test_paired_backup_restore_and_tampered_bundle(tmp_path):
    path = account(tmp_path)
    funding.settle_funding_reserve(str(path), 'paper', 'TESTUSDT', event())
    snapshot = tmp_path / 'backup'
    backup(path, snapshot)
    verify(snapshot)
    restored = tmp_path / 'restored'
    restored.mkdir()
    target = restored / 'config.json'
    shutil.copyfile(snapshot / 'config.json', target)
    shutil.copyfile(snapshot / 'config.json.funding.sqlite', funding_store.database_path(target))
    funding.settle_funding_reserve(str(target), 'paper', 'TESTUSDT', event())
    assert json.loads(target.read_text())['portfolios'][0]['paper_account_free_margin'] == 4997
    funding.settle_funding_reserve(str(target), 'paper', 'TESTUSDT', event('2'))
    assert json.loads(target.read_text())['portfolios'][0]['paper_account_free_margin'] == 4994
    (snapshot / 'config.json').write_text('{}')
    with pytest.raises(ValueError, match='checksum mismatch'):
        verify(snapshot)


def test_snapshot_recovers_pending_and_captures_sqlite_wal(tmp_path, monkeypatch):
    path = account(tmp_path)
    funding.settle_funding_reserve(str(path), 'paper', 'TESTUSDT', event())
    with closing(sqlite3.connect(funding_store.database_path(path))) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        with monkeypatch.context() as scoped:
            scoped.setattr(storage, '_atomic_save_json', lambda *a, **kw: (_ for _ in ()).throw(OSError('write failed')))
            with pytest.raises(OSError):
                funding.settle_funding_reserve(str(path), 'paper', 'TESTUSDT', event('2'))
        snapshot = tmp_path / 'backup'
        backup(path, snapshot)
    verify(snapshot)
    assert json.loads((snapshot / 'config.json').read_text())['portfolios'][0]['paper_account_free_margin'] == 4994


def test_updated_writers_and_backups_share_one_lock(tmp_path):
    path = account(tmp_path)
    funding.settle_funding_reserve(str(path), 'real', 'TESTUSDT', event('initial', '0'))
    stale = json.loads(path.read_text())

    def write(index):
        if index % 4 == 0:
            funding.settle_funding_reserve(str(path), 'real', 'TESTUSDT', event(str(index)))
        elif index % 4 == 1:
            storage.safe_save_json_sync(str(path), stale)
        elif index % 4 == 2:
            storage.update_json_sync(str(path), lambda config: config.update(test_setting=index))
        else:
            backup(path, tmp_path / f'backup_{index}')

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(20)))
    assert Decimal(str(json.loads(path.read_text())['portfolios'][0]['cash_reserve_usdt'])) == 4985
    for directory in tmp_path.glob('backup_*'):
        verify(directory)


def test_zero_and_net_zero_events_keep_distinct_checkpoints(tmp_path):
    path = account(tmp_path)
    funding.settle_funding_reserve(str(path), 'paper', 'TESTUSDT', event('1', '0'))
    first = path.read_bytes()
    funding.settle_funding_reserve(str(path), 'paper', 'TESTUSDT', event('2', '3'))
    funding.settle_funding_reserve(str(path), 'paper', 'TESTUSDT', event('3', '-3'))
    path.write_bytes(first)
    with pytest.raises(ValueError, match='checkpoint mismatch'):
        funding.settle_funding_reserve(str(path), 'paper', 'TESTUSDT', event('3', '-3'))


def test_all_config_writers_refuse_missing_database(tmp_path):
    path = account(tmp_path)
    funding.settle_funding_reserve(str(path), 'paper', 'TESTUSDT', event())
    config = json.loads(path.read_text())
    __import__('pathlib').Path(funding_store.database_path(path)).unlink()
    for write in (lambda: storage.safe_save_json_sync(str(path), config),
                  lambda: storage.update_json_sync(str(path), lambda value: value.update(test_setting=1))):
        with pytest.raises(ValueError, match='database is missing'):
            write()
    assert json.loads(path.read_text()) == config


def test_stripping_checkpoint_cannot_hide_net_zero_history(tmp_path):
    path = account(tmp_path)
    funding.settle_funding_reserve(str(path), 'paper', 'TESTUSDT', event('1', '0'))
    old = json.loads(path.read_text())
    old.pop('funding_reserve_checkpoint')
    funding.settle_funding_reserve(str(path), 'paper', 'TESTUSDT', event('2', '3'))
    funding.settle_funding_reserve(str(path), 'paper', 'TESTUSDT', event('3', '-3'))
    path.write_text(json.dumps(old))
    with pytest.raises(ValueError, match='checkpoint mismatch'):
        funding.settle_funding_reserve(str(path), 'paper', 'TESTUSDT', event('3', '-3'))


def test_interrupted_first_checkpoint_initialization_recovers(tmp_path, monkeypatch):
    path = account(tmp_path)
    with monkeypatch.context() as scoped:
        scoped.setattr(storage, '_atomic_save_json', lambda *a, **kw: (_ for _ in ()).throw(PermissionError('initial write failed')))
        with pytest.raises(PermissionError):
            funding.settle_funding_reserve(str(path), 'paper', 'TESTUSDT', event())
    for _ in range(2):
        funding.settle_funding_reserve(str(path), 'paper', 'TESTUSDT', event())
    assert json.loads(path.read_text())['portfolios'][0]['paper_account_free_margin'] == 4997
    with closing(sqlite3.connect(funding_store.database_path(path))) as db:
        assert db.execute('SELECT COUNT(*) FROM receipts').fetchone()[0] == 1


def test_separate_process_writers_preserve_funding_and_siphon(tmp_path):
    path = account(tmp_path)
    stale = tmp_path / 'stale.json'
    stale.write_bytes(path.read_bytes())
    source = '''
import json, sys
from decimal import Decimal
from pathlib import Path
from core import funding, storage, siphoning
path, mode = sys.argv[1:]
stale = json.loads((Path(path).parent / 'stale.json').read_text())
for i in range(8):
    if mode.startswith('funding'):
        event = funding.FundingEvent(event_id=str(i), signed_amount=Decimal('-1'), event_time=i)
        funding.settle_funding_reserve(path, 'real', 'TESTUSDT', event)
    elif mode == 'stale':
        storage.safe_save_json_sync(path, stale)
    else:
        state = dict(stop_reason='siphon', siphon_amount=1, siphoning_reserve=1,
                     started_at=i+1, rebalance_cycles=1)
        storage.update_json_sync(path, lambda config: siphoning.credit_reserve(config, 'real', 'TESTUSDT', state))
'''

    def run(mode):
        result = subprocess.run([sys.executable, '-c', source, str(path), mode], capture_output=True, text=True, timeout=45)
        assert result.returncode == 0, result.stderr

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(run, ('funding_1', 'funding_2', 'stale', 'siphon')))
    config = json.loads(path.read_text())
    assert config['portfolios'][0]['cash_reserve_usdt'] == 5000
    assert len(config['siphoning_credits']) == 8
    with closing(sqlite3.connect(funding_store.database_path(path))) as db:
        assert db.execute('SELECT COUNT(*) FROM receipts').fetchone()[0] == 8
