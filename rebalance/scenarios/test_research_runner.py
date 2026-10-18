import json
import sys
from pathlib import Path

import pytest

from rebalance.research_runner import run_study
from rebalance.trial_registry import Registry


@pytest.fixture
def study(tmp_path):
    data=tmp_path/'candles.csv'
    data.write_text('open_time,open,high,low,close\n0,100,100,100,100\n60000,100,100,100,100\n')
    config=tmp_path/'config.json'
    config.write_text(json.dumps(dict(capital='10000',spread_bps='2',slippage_bps='1',strategy=dict(
        step='0.001',min_notional='5.3',leverage='7',fee_rate='0.0004',surplus_pp='0.7',deficit_pp='0.7',trail_pp='0.09'))))
    return dict(database=tmp_path/'registry.sqlite',identity='fixture',candles=data,config=config,
                output=tmp_path/'output',funding_zero=True)


def test_registered_before_compute_and_snapshot_import(study):
    outcome=run_study(**study)
    assert outcome['status']=='COMPLETED'
    report=Registry(study['database']).report()
    trial=report['trials'][0]
    assert trial['snapshot']['mode']=='PROSPECTIVE'
    manifest=json.loads((study['output']/'execution_manifest.json').read_text())
    assert manifest['executed_module']==str(study['output']/'frozen/code/rebalance/research_runner.py')
    assert manifest['artifacts']==trial['snapshot']['artifacts']
    assert len(json.loads((study['output']/'hold_trades.json').read_text()))==2
    assert not report['admission_ready']


def test_calculation_failure_recorded_not_discarded(study):
    study['candles'].write_text('bad,header\n')
    assert run_study(**study)['status']=='FAILED'
    trial=Registry(study['database']).report()['trials'][0]
    assert trial['outcome']['status']=='FAILED'
    assert (study['output']/'failure.json').exists()
    assert not (study['output']/'comparison.json').exists()


def test_same_output_and_trial_identity_never_rerun(study):
    run_study(**study)
    with pytest.raises(FileExistsError): run_study(**study)
    study['output']=study['output'].with_name('different_output')
    with pytest.raises(ValueError,match='conflicting'): run_study(**study)
    assert not (study['output']/'worker.claim').exists()
    assert Registry(study['database']).report()['total']==1


def test_original_file_change_cannot_change_frozen_input(study,monkeypatch):
    import rebalance.research_runner as module
    popen=module.subprocess.Popen
    def launch(*args,**kwargs):
        if args[0][0]==sys.executable:
            study['candles'].write_text('changed after registration')
        return popen(*args,**kwargs)
    monkeypatch.setattr(module.subprocess,'Popen',launch)
    assert run_study(**study)['status']=='COMPLETED'
    assert (study['output']/'frozen/inputs/data.csv').read_text().startswith('open_time')


def test_snapshot_tampering_blocks_calculation(study,monkeypatch):
    import rebalance.research_runner as module
    popen=module.subprocess.Popen
    def launch(*args,**kwargs):
        if args[0][0]==sys.executable:
            (study['output']/'frozen/inputs/data.csv').write_text('tampered')
        return popen(*args,**kwargs)
    monkeypatch.setattr(module.subprocess,'Popen',launch)
    assert run_study(**study)['status']=='FAILED'
    error=json.loads((study['output']/'failure.json').read_text())
    assert 'snapshot changed' in error['reason']
    assert not (study['output']/'candidate_result.json').exists()


def test_evidence_required_funding_mode_before_registration(study):
    study['funding_zero']=False
    with pytest.raises(ValueError): run_study(**study)
    assert not Path(study['database']).exists()
