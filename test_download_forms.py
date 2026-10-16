import datetime as dt
import unittest
from unittest.mock import patch
import download_forms as d

class Tests(unittest.TestCase):
    def test_discovery_filters_and_deduplicates(self):
        body = '''<html>RACE FIELDS &amp; FORM
        <a href="Form.aspx?Key=2026Oct17%2CQLD%2CDoomben">Form</a>
        <a href="Form.aspx?Key=2026Oct17%2CQLD%2CDoomben">Venue</a>
        <a href="Form.aspx?Key=2026Oct17%2CQLD%2CDoomben%20(Trial)">Trial</a>
        <a href="Form.aspx?Key=2026Oct18%2CQLD%2CTownsville">Next day</a>
        <a href="Form.aspx?Key=2026Oct17%2CNSW%2CRandwick">Other state</a></html>'''
        self.assertEqual([v for _, v in d.discover(body, 'QLD', dt.date(2026,10,17))], ['Doomben'])

    def test_blocks_stop_even_with_success_status(self):
        for signal in ('CAPTCHA', 'Access Denied', 'cf-chl-', 'Verify you are human'):
            with self.subTest(signal=signal), self.assertRaises(d.StopRun):
                d.validate_html('<html>' + signal + '</html>')

    def test_unknown_calendar_stops(self):
        with self.assertRaises(d.StopRun):
            d.discover('<html>maintenance</html>', 'QLD', dt.date(2026,10,17))

    def test_no_redirect_following(self):
        with self.assertRaises(d.StopRun):
            d.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.com')

    def test_conservative_request_spacing(self):
        client = d.Client()
        client.last = 100
        with patch.object(d.time, 'monotonic', return_value=105), patch.object(d.time, 'sleep') as sleep:
            with patch.object(client.opener, 'open', side_effect=OSError('offline')):
                with self.assertRaises(OSError):
                    client.get(d.BASE + '/FreeFields/Calendar.aspx?State=QLD')
            sleep.assert_called_once_with(15)

if __name__ == '__main__':
    unittest.main()
