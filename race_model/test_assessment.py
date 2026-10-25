import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock,patch
import zipfile

from rules_v2.results import SavedResults,StopResults
from rules_v2.test_rules import guide_html,results_html
from .core import fit,canonical,digest
from .test_model import toy
from .assess import run,load_model,load_predictions,apply_model
from .settle import run as settle,parse_result


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

    def unavailable_page(self):
        return results_html().split('<table')[0]+'<p>Results for this meeting are not currently available.</p></html>'

    def test_abandoned_whole_meeting_is_void_not_loss(self):
        self.assess();client=Mock()
        client.get.return_value=results_html().split('<table')[0]+'<p>Total Number of starters for this meeting (including emergencies) 0</p><p>Meeting Abandoned.</p></html>'
        s=settle(self.p/'predictions/probability-lock',self.p/'results',client)
        self.assertEqual(s['status'],'processed')
        self.assertEqual(s['top_picks_void'],1)
        self.assertEqual(s['top_picks_unresolved'],0)
        self.assertEqual(s['top_picks_settled'],0)
        self.assertEqual(s['meetings_explicitly_abandoned'],1)
        self.assertEqual(s['meetings_explicitly_unavailable'],0)

    def test_abandonment_notice_without_zero_starters_cannot_skip(self):
        identity={'date':'2026-08-30','state':'WA','meeting':'Test'}
        page=results_html().split('<table')[0]+'<p>Meeting Abandoned.</p></html>'
        with self.assertRaises(StopResults):parse_result(page,identity)
        zero=page.replace('Meeting Abandoned.','Total Number of starters for this meeting (including emergencies) 0 Meeting Abandoned.')
        with self.assertRaises(StopResults):parse_result(zero,dict(identity,meeting='Wrong'))
        with self.assertRaisesRegex(StopResults,'CAPTCHA'):parse_result(zero+' CAPTCHA',identity)

    def test_explicit_unavailable_is_unknown_not_whole_run_error(self):
        self.assess();client=Mock();client.get.return_value=self.unavailable_page()
        s=settle(self.p/'predictions/probability-lock',self.p/'results',client)
        self.assertEqual(s['status'],'processed_with_unresolved')
        self.assertEqual(s['meetings_explicitly_unavailable'],1)
        self.assertEqual(s['top_picks_unresolved'],1)
        self.assertEqual(s['top_picks_settled'],0)
        self.assertIsNone(s['model_matched_win_rate'])
        self.assertEqual(len(json.loads((self.p/'results/sources.json').read_text())),1)

    def test_unknown_layout_still_stops_and_keeps_page_provenance(self):
        self.assess();client=Mock();client.get.return_value=self.unavailable_page().replace('Results for this meeting are not currently available.','Unexpected content')
        with self.assertRaisesRegex(ValueError,'No recognised'):settle(self.p/'predictions/probability-lock',self.p/'results',client)
        s=json.loads((self.p/'results/summary.json').read_text())
        self.assertEqual(s['status'],'stopped')
        self.assertEqual(len(json.loads((self.p/'results/sources.json').read_text())),1)

    def test_unavailable_notice_cannot_hide_identity_or_block_error(self):
        identity={'date':'2026-08-30','state':'WA','meeting':'Test'}
        with self.assertRaisesRegex(StopResults,'mismatch'):
            parse_result(self.unavailable_page().replace('Test: Club','WRONG: Club'),identity)
        with self.assertRaisesRegex(StopResults,'CAPTCHA'):
            parse_result(self.unavailable_page()+' CAPTCHA',identity)
        with self.assertRaisesRegex(StopResults,'No recognised'):
            parse_result(results_html('Other Odds')+'Results for this meeting are not currently available.',identity)

    def test_resume_recovers_legacy_unindexed_unavailable_and_fetches_only_next(self):
        body=guide_html().replace('2026Aug30','2026Aug31').replace('Sunday, 30 August 2026','Monday, 31 August 2026')
        (self.guides/'WA_20260831_Test.html').write_text(body)
        run(self.guides,self.folder,dt.date(2026,8,30),dt.date(2026,8,31),self.p/'predictions')
        legacy=self.p/'legacy.zip'
        with zipfile.ZipFile(legacy,'w') as z:
            z.writestr('sources.json','[]')
            z.writestr('summary.json',json.dumps({'status':'stopped','stopped_reason':'No recognised official result tables'}))
            z.writestr('official-pages/WA_2026-08-30_Test.html',self.unavailable_page())
        result=results_html().replace('2026Aug30','2026Aug31').replace('Sunday, 30 August 2026','Monday, 31 August 2026')
        with patch('race_model.settle.Client.get',return_value=result) as live:
            s=settle(self.p/'predictions/probability-lock',self.p/'results',resume_zip=legacy)
        live.assert_called_once()
        self.assertIn('2026Aug31',live.call_args[0][0])
        self.assertEqual(s['cached_meetings_reused'],1)
        self.assertEqual(s['new_meetings_requested'],1)
        self.assertEqual(s['matched_full_field_races'],1)
        self.assertEqual(s['top_picks_unresolved'],1)
        recovered=json.loads((self.p/'results/sources.json').read_text())[0]
        self.assertTrue(recovered['original_source_manifest_entry_missing'])
        self.assertIsNone(recovered['retrieved_utc'])

    def test_resume_refuses_saved_block_before_live_request(self):
        self.assess();archive=self.p/'blocked.zip'
        with zipfile.ZipFile(archive,'w') as z:
            z.writestr('sources.json','[]')
            z.writestr('summary.json',json.dumps({'status':'stopped','stopped_reason':'CAPTCHA/block'}))
        with patch('race_model.settle.Client.get') as live:
            with self.assertRaisesRegex(ValueError,'inspect'):settle(self.p/'predictions/probability-lock',self.p/'results',resume_zip=archive)
        live.assert_not_called()


if __name__=='__main__':unittest.main()
