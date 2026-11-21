"""Focused tests for the offline PAPER selection study."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('study', Path(__file__).resolve().parents[1]/'scripts/study_real_selection_paper.py')
study = importlib.util.module_from_spec(spec)
spec.loader.exec_module(study)


class SelectionStudyTests(unittest.TestCase):
    def row(self, start, cycles, net):
        return {'ticker':'TUSDT','started_at':start,'cycles':cycles,'net':net,'capital':1000,'spec':{}}

    def test_cumulative_cycles_survive_session_boundary(self):
        rows = [self.row(1,9,50),self.row(2,6,10)]
        self.assertFalse(study.qualification(rows,False)['TUSDT']['eligible_upper_bound'])
        self.assertTrue(study.qualification(rows)['TUSDT']['eligible_upper_bound'])
        rows[-1]['net'] = -70
        self.assertFalse(study.qualification(rows)['TUSDT']['eligible_upper_bound'])

    def test_duplicate_identity_is_rejected(self):
        with self.assertRaises(AssertionError):
            study.qualification([self.row(1,9,50),self.row(1,9,50)])

    def test_normalized_score_handles_different_capital(self):
        a=self.row(1,15,100)
        b=dict(a,capital=2000,net=200)
        self.assertEqual(study.qualification([a])['TUSDT']['score'],study.qualification([b])['TUSDT']['score'])

    def test_risk_samples_do_not_include_after_close(self):
        from datetime import datetime, timezone, timedelta
        start=datetime(2026,11,20,tzinfo=timezone(timedelta(hours=2))).timestamp()
        log='2026-11-20 00:00:01 INFO: Heartbeat: TPV=900 | Bar=1\n2026-11-20 00:00:03 INFO: Heartbeat: TPV=10 | Bar=2'
        risk=study.risk_samples(log,{'started_at':start,'observed_until':start+2,'capital':1000})
        self.assertEqual(risk['heartbeats'],1)
        self.assertEqual(risk['sampled_dd_lower_bound_pct'],10)


    def test_close_requires_receipt_and_exact_capital(self):
        archive={'state':{'base_ticker':'TUSDT','paper_close_complete':True,'rebalance_cycles':3,'started_at':1,'initial_tpv':1000},
                 'paper_state':{'base_ticker':'TUSDT','positions':{'TUSDT_LONG':0,'TUSDT_SHORT':0},'balance':1010,
                                'share_fill_receipts':{'fill':{'event_id':'trailing:1:3'}}},'close_event':'trailing:1:3'}
        self.assertEqual(study.validate_close(archive,'TUSDT',study.D(1000),study.D(1010)),3)
        with self.assertRaises(AssertionError):
            study.validate_close(archive,'TUSDT',study.D(999),study.D(1010))
        archive['paper_state']['share_fill_receipts']={}
        with self.assertRaises(AssertionError):
            study.validate_close(archive,'TUSDT',study.D(1000),study.D(1010))

    def test_observed_replay_uses_only_then_known_cycles(self):
        import tempfile
        from datetime import datetime, timezone, timedelta
        start=datetime(2026,11,20,tzinfo=timezone(timedelta(hours=2))).timestamp()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'core/logs').mkdir(parents=True)
            (root/'core/logs/paper_TUSDT.log').write_text('2026-11-20 00:00:01 INFO: Heartbeat: TPV=1100 | Bar=1\n2026-11-20 00:00:02 INFO: Rebalance #15 complete. TPV: 900')
            row=dict(self.row(start,15,100),observed_until=start+3,closed=False)
            replay=study.observed_replay(root,[row])
            self.assertEqual(replay['first_observed_eligibility'],{'per_session':{},'cumulative':{}})


if __name__=='__main__':
    unittest.main()
