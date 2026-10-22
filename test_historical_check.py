import datetime as dt
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from check_historical_guides import run
from download_forms import StopRun
from rules_v2.test_rules import guide_html


def guide():
    body=guide_html().replace('2026Aug30,WA,Test','2026Jun01,NSW,Corowa')
    body=body.replace('Test: Club','Corowa: Club').replace('Sunday, 30 August 2026','Monday, 1 June 2026')
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


if __name__=='__main__':unittest.main()
