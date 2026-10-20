import hashlib
import json
import sqlite3

import pytest

from rebalance.research_runner import run_study
from rebalance.research_verify import verify
from rebalance.scenarios.test_research_runner import study


@pytest.mark.parametrize('terminal', [False, True])
def test_completed_study_is_read_only(study,terminal):
    run_study(**study,terminal_closeout=terminal)
    before = {p: p.read_bytes() for p in study['output'].rglob('*') if p.is_file()}
    db_before = study['database'].read_bytes()
    result = verify(study['database'], study['output'])
    assert result['status'] == 'PASS' and result['replay_performed'] is False
    assert result['admission_ready'] is False
    assert result['terminal_closeout_validation'] == ('SEMANTIC_CONSISTENCY' if terminal else 'NOT_REQUESTED')
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


@pytest.mark.parametrize('mutation', ['missing','price','quantity','duplicate','fee','equity',
                                    'funding','drawdown','delta','claim','unexpected',
                                    'cycle_net','quarantine','pf'])
def test_terminal_semantics_reject_rehashed_corruption(study,mutation):
    run_study(**study,terminal_closeout=mutation!='unexpected')
    path=study['output']/'comparison.json'
    summary=json.loads(path.read_text())
    if mutation=='missing':
        del summary['terminal_closeout']
    elif mutation=='unexpected':
        summary['terminal_closeout']={}
    else:
        exit=summary['terminal_closeout']['candidate']
        if mutation=='price': exit['sell_price']='1'
        if mutation=='quantity': exit['quantities']['LONG']='1'
        if mutation=='duplicate': exit['fills'].append(exit['fills'][0])
        if mutation=='fee': exit['fills'][0]['fee']='0'
        if mutation=='equity': exit['equity_flat']='10001'
        if mutation=='funding': exit['funding']='1'
        if mutation=='drawdown': exit['max_close_plus_exit_drawdown_pct']='99'
        if mutation=='delta': summary['terminal_closeout']['delta_net_flat']='99'
        if mutation=='claim': exit['trading_authorized']=True
        if mutation=='cycle_net': summary['cycles']['candidate']['episodes'][0]['net']='99'
        if mutation=='quarantine': summary['cycles']['candidate']['real_quarantine_cycles']=1
        if mutation=='pf': summary['profit_factor']='99'
    path.write_text(json.dumps(summary))
    with sqlite3.connect(study['database']) as db:
        body=json.loads(db.execute('SELECT body FROM outcomes').fetchone()[0])
        body['evidence']['comparison'].update(bytes=path.stat().st_size,
                                             sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        db.execute('DROP TRIGGER outcomes_no_update')
        db.execute('UPDATE outcomes SET body=?',(json.dumps(body),))
    report_path=study['output']/'registry_report.json'
    report=json.loads(report_path.read_text())
    report['trials'][0]['outcome']=body
    report_path.write_text(json.dumps(report))
    with pytest.raises(ValueError,match='terminal|cycle/PF'):
        verify(study['database'],study['output'])
