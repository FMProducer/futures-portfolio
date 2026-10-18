import hashlib
import json
import sqlite3

import pytest

from rebalance.research_runner import run_study
from rebalance.research_verify import verify
from rebalance.scenarios.test_research_runner import study


def test_completed_study_is_read_only(study):
    run_study(**study)
    before = {p: p.read_bytes() for p in study['output'].rglob('*') if p.is_file()}
    db_before = study['database'].read_bytes()
    result = verify(study['database'], study['output'])
    assert result['status'] == 'PASS' and result['replay_performed'] is False
    assert result['admission_ready'] is False
    assert before == {p: p.read_bytes() for p in study['output'].rglob('*') if p.is_file()}
    assert db_before == study['database'].read_bytes()


@pytest.mark.parametrize('name', ['frozen/inputs/data.csv', 'candidate_result.json',
                                'frozen/code/rebalance/kernel.py'])
def test_changed_bytes_fail(study, name):
    run_study(**study)
    path = study['output']/name
    path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(ValueError, match='hash/size'):
        verify(study['database'], study['output'])


def test_missing_evidence_fails(study):
    run_study(**study)
    (study['output']/'hold_equity.json').unlink()
    with pytest.raises(FileNotFoundError):
        verify(study['database'], study['output'])


def test_failed_study_is_not_pass(study):
    study['candles'].write_text('bad,header\n')
    run_study(**study)
    with pytest.raises(ValueError, match='not COMPLETED'):
        verify(study['database'], study['output'])


def test_missing_database_not_created(study):
    run_study(**study)
    missing = study['database'].with_name('missing.sqlite')
    with pytest.raises(sqlite3.OperationalError):
        verify(missing, study['output'])
    assert not missing.exists()


@pytest.mark.parametrize('mutation', ['delta', 'curve', 'fees'])
def test_semantic_inconsistency_even_with_updated_hashes(study, mutation):
    run_study(**study)
    name = {'delta': 'comparison', 'curve': 'candidate_equity', 'fees': 'candidate_trades'}[mutation]
    path = study['output']/(name+'.json')
    value = json.loads(path.read_text())
    if mutation == 'delta':
        value['delta_net_mtm'] = '123'
    elif mutation == 'curve':
        value[0]['time'] += 1
    else:
        value[0]['fee'] = '123'
    path.write_text(json.dumps(value))
    # Model a coherent rewrite of local hashes: SHA alone cannot check meaning.
    with sqlite3.connect(study['database']) as db:
        body = json.loads(db.execute('SELECT body FROM outcomes').fetchone()[0])
        body['evidence'][name].update(bytes=path.stat().st_size,
                                     sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        db.execute('DROP TRIGGER outcomes_no_update')
        db.execute('UPDATE outcomes SET body=?', (json.dumps(body),))
    report_path = study['output']/'registry_report.json'
    report = json.loads(report_path.read_text())
    report['trials'][0]['outcome'] = body
    report_path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match={'delta': 'delta', 'curve': 'bounds', 'fees': 'fees'}[mutation]):
        verify(study['database'], study['output'])
