import unittest
from unittest.mock import patch
from pipeline_preflight import validate


class PipelinePreflightTests(unittest.TestCase):
    def test_range_and_states_validated_without_network(self):
        with patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('No site requests')):
            start,end,states=validate('2026-08-01','2026-08-07',' qld, NSW,qld ')
        self.assertEqual(start.day,1);self.assertEqual(end.day,7);self.assertEqual(states,['QLD','NSW'])

    def test_invalid_or_oversized_ranges_rejected(self):
        for start,end in [('2026-08-07','2026-08-01'),('2026-08-01','2026-09-01'),('20260801','2026-08-07'),('2026-02-30','2026-03-01')]:
            with self.assertRaises(ValueError):validate(start,end,'QLD')

    def test_unsupported_calendar_or_states_rejected(self):
        for start,end,states in [('2026-04-30','2026-05-01','QLD'),('2026-09-30','2026-10-01','QLD'),('2026-08-01','2026-08-07','NT'),('2026-08-01','2026-08-07','')]:
            with self.assertRaises(ValueError):validate(start,end,states)

    def test_calendar_hole_rejected(self):
        with patch('pipeline_preflight.lookup',return_value=None):
            with self.assertRaises(ValueError):validate('2026-08-01','2026-08-07','QLD')

    def test_missing_rules_rejected_before_download(self):
        with patch('pipeline_preflight.config_at',side_effect=ValueError('Missing rules')):
            with self.assertRaises(ValueError):validate('2026-08-01','2026-08-07','QLD')


if __name__=='__main__':unittest.main()
