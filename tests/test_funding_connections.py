import asyncio
from contextlib import closing
import sqlite3
from threading import Event

import pytest

from core import funding_store
from tests.test_funding_reserves import account


def test_task_scope_reuses_connections_and_closes_them(tmp_path):
    path=account(tmp_path)
    with funding_store.connection_session():
        with closing(funding_store.connect(path)) as first:
            assert first.execute('pragma synchronous').fetchone()[0]==2
            assert first.execute('pragma journal_mode').fetchone()[0]=='wal'
        with closing(funding_store.connect(path)) as second:
            assert first is second
    with pytest.raises(sqlite3.ProgrammingError,match='closed'):
        first.execute('select 1')


@pytest.mark.asyncio
async def test_worker_thread_connections_close_with_runtime_scope(tmp_path):
    path=account(tmp_path)
    connections=[]

    def worker():
        with closing(funding_store.connect(path)) as first:
            connections.append(first)
        with closing(funding_store.connect(path)) as second:
            assert first is second

    @funding_store.with_connections
    async def run():
        await asyncio.to_thread(worker)

    await run()
    with pytest.raises(sqlite3.ProgrammingError,match='closed'):
        connections[0].execute('select 1')


@pytest.mark.asyncio
async def test_cancelled_runtime_does_not_close_inflight_thread_connection(tmp_path):
    path=account(tmp_path)
    started,release,finished=Event(),Event(),Event()
    errors=[]
    connections=[]

    def worker():
        try:
            with closing(funding_store.connect(path)) as db:
                connections.append(db);started.set()
                assert release.wait(5)
                assert db.execute('select 1').fetchone()[0]==1
        except Exception as error:errors.append(error)
        finally:finished.set()

    @funding_store.with_connections
    async def run():
        await asyncio.to_thread(worker)

    task=asyncio.create_task(run())
    assert await asyncio.to_thread(started.wait,5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    release.set()
    assert await asyncio.to_thread(finished.wait,5)
    assert not errors
    with pytest.raises(sqlite3.ProgrammingError,match='closed'):
        connections[0].execute('select 1')
