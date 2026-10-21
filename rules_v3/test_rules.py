import copy
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from rules_v2.core import config_at, score_races
from rules_v2.run import assess
from rules_v2.results import load_lock
from rules_v2.test_rules import race, guide_html
from .compare import compare

class V3Tests(unittest.TestCase):
    def test_single_rule_change(self):
        a=config_at();b=config_at(Path(__file__).with_name('config.json'))
        self.assertEqual({k for k in a if a[k]!=b[k]}, {'version','minimum_known_weight'})
        self.assertEqual(b['minimum_known_weight'],100)

    def test_missing_factor_rejected_without_score_change(self):
        a=config_at();b=dict(a,minimum_known_weight=100)
        r=race()
        for runner in r['runners']:
            runner['history_text']=[x.replace('Good4','Unknown') for x in runner['history_text']]
        old=score_races([copy.deepcopy(r)],a);new=score_races([copy.deepcopy(r)],b)
        self.assertTrue(old[0]['selected'])
        self.assertFalse(new[0]['selected'])
        self.assertEqual([x['score'] for x in old],[x['score'] for x in new])

    def test_locks_and_shared_results(self):
        from rules_v2.test_rules import results_html
        from rules_v2.results import Client
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);g=root/'guides';g.mkdir()
            (g/'WA_20260830_Test.html').write_text(guide_html())
            for model,c in [('v2',None),('v3',Path(__file__).with_name('config.json'))]:
                assess(g,dt.date(2026,8,30),dt.date(2026,8,30),root/'locks'/model,c)
                self.assertTrue(load_lock(root/'locks'/model)['runners'][0]['selected'])
            with patch.object(Client,'get',return_value=results_html()) as request:
                compare(root/'locks',root/'results')
            self.assertEqual(request.call_count,1)
            self.assertTrue((root/'results/comparison.md').is_file())
            with self.assertRaises(FileExistsError):compare(root/'locks',root/'results')
