import datetime as dt
from pathlib import Path
import tempfile
import unittest
import json
import hashlib
from unittest.mock import Mock

from check_historical_guides import run,check_saved_all_form
from download_forms import StopRun
from rules_v2.test_rules import guide_html


def guide():
    body=guide_html().replace('2026Aug30,WA,Test','2026Jun01,NSW,Corowa')
    body=body.replace('Test: Club','Corowa: Club').replace('Sunday, 30 August 2026','Monday, 1 June 2026')
    body=body.replace('15Aug26','15Apr26').replace('01Aug26','01Apr26')
    return body+' Prizemoney'


class HistoricalCheckTests(unittest.TestCase):
    def test_available_direct_saves_guide_only_one_request(self):
        client=Mock();client.get.return_value=guide()
        with tempfile.TemporaryDirectory() as temp:
            report=run('June',Path(temp)/'output',client)
            self.assertEqual(report['targets'][0]['status'],'available')
            self.assertEqual(len(list((Path(temp)/'output/verified-guides').glob('*.html'))),1)
        client.get.assert_called_once()

    def test_follows_only_different_source_advertised_form_link(self):
        client=Mock();client.get.side_effect=['<html>Form information for this meeting is not currently available</html>',
            '<html><a href="/FreeFields/Form.aspx?Key=2026Jun01,NSW,Corowa&amp;recentForm=Y">Recent Form</a></html>',guide()]
        with tempfile.TemporaryDirectory() as temp:
            report=run('June',Path(temp)/'output',client)
            self.assertEqual(report['targets'][0]['status'],'available')
        self.assertEqual(client.get.call_count,3)
        self.assertIn('recentForm=Y',client.get.call_args[0][0])

    def test_same_advertised_link_not_retried(self):
        client=Mock();client.get.side_effect=['<html>Form information for this meeting is not currently available</html>',
            '<html><a href="/FreeFields/Form.aspx?Key=2026Jun01,NSW,Corowa">Recent Form</a></html>']
        with tempfile.TemporaryDirectory() as temp:
            report=run('June',Path(temp)/'output',client)
            self.assertEqual(report['targets'][0]['status'],'unavailable_no_distinct_advertised_form_link')
        self.assertEqual(client.get.call_count,2)

    def test_block_or_redirect_stops_without_next_month(self):
        for message in ('CAPTCHA/block','Redirect received'):
            client=Mock();client.get.side_effect=StopRun(message)
            with tempfile.TemporaryDirectory() as temp:
                with self.assertRaises(StopRun):run('both',Path(temp)/'output',client)
                self.assertTrue((Path(temp)/'output/diagnosis.json').exists())
            client.get.assert_called_once()

    def test_wrong_identity_not_saved(self):
        client=Mock();client.get.return_value=guide().replace('Corowa: Club','Wagga: Club')
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(StopRun):run('June',Path(temp)/'output',client)
            self.assertEqual(list((Path(temp)/'output/verified-guides').glob('*')),[])

    def saved_source(self,path):
        path.mkdir()
        body='<html><a href="/FreeFields/AllForm.aspx?Key=2026Jun01,NSW,Corowa&amp;recentForm=N">All Form</a></html>'
        (path/'source.html').write_text(body,encoding='utf-8')
        report={'status':'processed','requests':[{'phase':'June-source-links','file':'source.html',
            'url':'https://racingaustralia.horse/FreeFields/Results.aspx?Key=2026Jun01,NSW,Corowa',
            'sha256':hashlib.sha256(body.encode()).hexdigest()}]}
        (path/'diagnosis.json').write_text(json.dumps(report))

    def test_cached_all_form_needs_only_one_new_request(self):
        client=Mock();client.get.return_value=guide()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.saved_source(root/'saved')
            report=check_saved_all_form(root/'saved','June',root/'output',client)
            self.assertEqual(report['targets'][0]['status'],'available')
            self.assertEqual(report['targets'][0]['model_parser']['same_day_future_form_races'],0)
        client.get.assert_called_once()
        self.assertIn('/AllForm.aspx?',client.get.call_args[0][0])

    def test_tampered_cached_source_never_requested(self):
        client=Mock()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.saved_source(root/'saved');(root/'saved/source.html').write_text('changed')
            with self.assertRaisesRegex(StopRun,'hash mismatch'):check_saved_all_form(root/'saved','June',root/'output',client)
        client.get.assert_not_called()


if __name__=='__main__':unittest.main()
