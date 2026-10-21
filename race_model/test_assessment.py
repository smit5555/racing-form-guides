import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock

from rules_v2.results import SavedResults,StopResults
from rules_v2.test_rules import guide_html,results_html
from .core import fit,canonical,digest
from .test_model import toy
from .assess import run,load_model,load_predictions,apply_model
from .settle import run as settle


class AssessmentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.p=Path(self.temp.name)
        self.guides=self.p/'guides';self.guides.mkdir()
        (self.guides/'WA_20260830_Test.html').write_text(guide_html())
        (self.guides/'manifest.json').write_text(json.dumps({'date':'2026-08-30','status':'incomplete','files':[],
                                                            'unavailable':[{'venue':'missing venue'}]}))
        self.folder=self.p/'model';self.folder.mkdir()
        self.model=fit([toy()])
        self.model.update(source_code_sha256=hashlib.sha256(Path(__file__).with_name('core.py').read_bytes()).hexdigest(),
                          training_end='2026-08-31',github_run_id='fixture')
        (self.folder/'model.json').write_bytes(canonical(self.model))
        (self.folder/'model.sha256').write_text(digest(self.model))

    def tearDown(self):self.temp.cleanup()

    def assess(self):
        return run(self.guides,self.folder,dt.date(2026,8,30),dt.date(2026,8,30),self.p/'predictions')

    def test_assess_freezes_model_and_preserves_coverage(self):
        before=copy.deepcopy(self.model);payload=self.assess()
        self.assertTrue(payload['earlier_than_training'])
        self.assertEqual(before,payload['model'])
        self.assertAlmostEqual(sum(r['probability'] for r in payload['runners']),1)
        self.assertEqual(sum(r['selected'] for r in payload['runners']),1)
        self.assertTrue(all(r['stake']==0 for r in payload['runners']))
        self.assertTrue((self.p/'predictions/download-reports/manifest.json').exists())
        self.assertEqual(digest(payload),digest(load_predictions(self.p/'predictions/probability-lock')))
        with self.assertRaises(FileExistsError):self.assess()

    def test_tampered_model_fails_before_assessment(self):
        self.model['weights'][0]+=1
        (self.folder/'model.json').write_bytes(canonical(self.model))
        with self.assertRaisesRegex(ValueError,'hash mismatch'):self.assess()
        self.assertFalse((self.p/'predictions').exists())

    def test_lock_validated_before_any_result_request(self):
        self.assess();folder=self.p/'predictions/probability-lock'
        data=json.loads((folder/'locked-predictions.json').read_text());data['runners'][0]['name']='WRONG'
        (folder/'locked-predictions.json').write_bytes(canonical(data))
        client=Mock()
        with self.assertRaisesRegex(ValueError,'hash mismatch'):settle(folder,self.p/'results',client)
        client.get.assert_not_called()

    def test_offline_results_replay_and_metrics(self):
        self.assess();client=Mock();client.get.return_value=results_html()
        folder=self.p/'predictions/probability-lock'
        summary=settle(folder,self.p/'results',client)
        self.assertEqual(client.get.call_count,1)
        self.assertEqual(summary['matched_full_field_races'],1)
        self.assertEqual(summary['model_matched_wins'],1)
        self.assertEqual(summary['v2_matched_wins'],1)
        archive=shutil.make_archive(str(self.p/'snapshot'),'zip',self.p/'results')
        reader=SavedResults(archive)
        try:replayed=settle(folder,self.p/'replayed',reader)
        finally:reader.close()
        self.assertEqual(replayed['race_log_loss'],summary['race_log_loss'])

    def test_block_preserves_unresolved_and_stops_requests(self):
        self.assess();client=Mock();client.get.side_effect=StopResults('CAPTCHA/block')
        with self.assertRaisesRegex(ValueError,'CAPTCHA'):settle(self.p/'predictions/probability-lock',self.p/'results',client)
        client.get.assert_called_once()
        summary=json.loads((self.p/'results/summary.json').read_text())
        self.assertEqual(summary['status'],'stopped');self.assertEqual(summary['top_picks_unresolved'],1)
        self.assertIsNone(summary['model_matched_win_rate'])


if __name__=='__main__':unittest.main()
