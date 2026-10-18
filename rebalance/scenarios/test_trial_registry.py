from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from rebalance.trial_registry import Registry


@pytest.fixture
def inputs(tmp_path):
    files={}
    for name in ('code','config','data','result'):
        path=tmp_path/(name+'.txt')
        path.write_text(name)
        files[name]=path
    return files


def register(registry,identity,inputs,mode='PROSPECTIVE'):
    registry.start(identity,{k:inputs[k] for k in ('code','config','data')},{'label':'fixture'},mode=mode)


def test_reopen_idempotency_and_incomplete_visible(tmp_path,inputs):
    path=tmp_path/'registry.sqlite'
    r=Registry(path)
    register(r,'one',inputs)
    before=r.report()
    assert before['counts']['INCOMPLETE']==1
    register(Registry(path),'one',inputs)
    assert r.report()==before
    r.finish('one','COMPLETED',evidence={'report':inputs['result']})
    before=r.report()
    Registry(path).finish('one','COMPLETED',evidence={'report':inputs['result']})
    assert r.report()==before and before['counts']['COMPLETED']==1
    assert not before['admission_ready'] and before['oos_status']=='NOT_ESTABLISHED'


def test_failure_abort_and_reused_data_are_never_filtered(tmp_path,inputs):
    r=Registry(tmp_path/'registry.sqlite')
    for identity in ('pending','failed','aborted','retrospective'):
        register(r,identity,inputs,mode='RETROSPECTIVE' if identity=='retrospective' else 'PROSPECTIVE')
    r.finish('failed','FAILED',reason='model guard')
    r.finish('aborted','ABORTED',reason='operator stopped')
    r.finish('retrospective','COMPLETED',evidence={'report':inputs['result']})
    report=r.report()
    assert report['total']==4 and set(report['counts'].values())=={1}
    assert list(report['data_hash_occurrences'].values())==[4]
    assert report['trials'][-1]['snapshot']['mode']=='RETROSPECTIVE'


def test_changed_artifacts_and_conflicting_registration(tmp_path,inputs):
    r=Registry(tmp_path/'registry.sqlite')
    register(r,'one',inputs)
    inputs['code'].write_text('new candidate')
    with pytest.raises(ValueError,match='conflicting'): register(r,'one',inputs)
    with pytest.raises(ValueError,match='changed'):
        r.finish('one','COMPLETED',evidence={'report':inputs['result']})
    assert r.report()['counts']['INCOMPLETE']==1
    r.finish('one','FAILED',reason='code changed')
    assert r.report()['counts']['FAILED']==1


def test_atomic_outcome_and_immutable_records(tmp_path,inputs):
    r=Registry(tmp_path/'registry.sqlite')
    register(r,'one',inputs)
    with r.connection() as db:
        db.execute("CREATE TRIGGER injected_failure BEFORE INSERT ON outcomes BEGIN SELECT RAISE(ABORT,'crash'); END")
    with pytest.raises(sqlite3.IntegrityError): r.finish('one','FAILED',reason='fixture')
    assert r.report()['counts']['INCOMPLETE']==1
    with r.connection() as db:
        db.execute('DROP TRIGGER injected_failure')
        with pytest.raises(sqlite3.IntegrityError): db.execute('DELETE FROM trials')
        with pytest.raises(sqlite3.IntegrityError): db.execute("UPDATE trials SET snapshot='{}'")
    r.finish('one','FAILED',reason='fixture')
    with r.connection() as db:
        with pytest.raises(sqlite3.IntegrityError): db.execute('DELETE FROM outcomes')


def test_racing_outcomes_cannot_overwrite(tmp_path,inputs):
    path=tmp_path/'registry.sqlite'
    r=Registry(path)
    register(r,'one',inputs)
    def finish(status):
        try:
            Registry(path).finish('one',status,reason=status)
            return 'written'
        except ValueError: return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(finish,('FAILED','ABORTED')))==['conflict','written']
    assert r.report()['total']==1 and r.report()['counts']['INCOMPLETE']==0


def test_foreign_database_unchanged(tmp_path):
    path=tmp_path/'foreign.sqlite'
    with sqlite3.connect(path) as db: db.execute('CREATE TABLE personal(value TEXT)')
    db.close()
    original=path.read_bytes()
    with pytest.raises(ValueError,match='foreign'): Registry(path)
    assert path.read_bytes()==original


def test_required_provenance_and_outcomes(tmp_path,inputs):
    r=Registry(tmp_path/'registry.sqlite')
    with pytest.raises(ValueError): r.start('one',{}, {},mode='PROSPECTIVE')
    with pytest.raises(ValueError): register(r,'one',inputs,mode='FRESH_OOS')
    register(r,'one',inputs)
    with pytest.raises(ValueError): r.finish('missing','FAILED',reason='unknown')
    with pytest.raises(ValueError): r.finish('one','PASS')
    with pytest.raises(ValueError): r.finish('one','FAILED')
    with pytest.raises(ValueError): r.finish('one','COMPLETED')
