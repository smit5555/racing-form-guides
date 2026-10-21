import copy
import csv
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from .core import config_at, features, history_row, digest, score_races, recency_score
from .guides import parse_guide
from .run import assess, csv_file
from .results import result_page, summarise, load_lock, settle, StopResults


def start(day='15Aug26',finish=1,size=8,margin='0.00'):
    return f'{finish} of {size} TEST {day} 1200m Good4 BM64 $30,000 Rider 58kg Barrier 2 1st OTHER 55kg 1:10.00, {margin}L, 2nd@400m, $4/$5'


def race():
    runners=[]
    for n,name in ((1,'ALPHA'),(2,'BETA')):
        runners.append({'number':n,'name':name,'scratched':False,'emergency':False,
           'field_values':{'weight':'58kg','jockey':'A Rider','barrier':str(n)},
           'history_text':[start(finish=1 if n==1 else 8,margin='0' if n==1 else '9'),
                           start('01Aug26',finish=1 if n==1 else 7,margin='0' if n==1 else '8')],
           'form_verified':True,'form':'Record: 4:2-0-0','field':f'{n} {name}'})
    return {'id':'2026-08-30|WA|Test|1','date':'2026-08-30','state':'WA','meeting':'Test','race':1,
            'guide':'WA_20260830_Test.html','guide_sha256':'example','conditions':'Track Condition: Good 4',
            'title':'Race 1 Test (1200 METRES)','exclusions':[],'runners':runners}


def guide_html(same_day=False,mismatch=False):
    r=race();parts=['<html><a href="/FreeFields/Form.aspx?Key=2026Aug30,WA,Test">Recent Form</a>',
      '<div class="race-venue"><h2>Test: Club<span class="race-venue-date">Sunday, 30 August 2026</span></h2><span class="meeting-type">Meeting Type: Country (TAB MEETING)</span></div>',
      '<div class="race-venue-bottom">Track Condition: Good 4</div>',
      '<table class="race-title"><tr><th>Race 1 Test (1200 METRES)</th></tr></table>',
      '<table class="race-strip-fields"><tr><th>No</th><th>Horse</th></tr>']
    for runner in r['runners']:
        parts.append(f'<tr><td class="no">{runner["number"]}</td><td class="horse"><a>{runner["name"]}</a></td><td class="weight">58kg</td><td class="jockey">Rider</td></tr>')
    parts.append('</table>')
    for runner in r['runners']:
        name='WRONG' if mismatch and runner['number']==1 else runner['name']
        parts.append(f'<table class="horse-form-table"><tr><td><span class="horse-number">{runner["number"]}</span><span class="horse-name">{name}</span> Record: 4:2-0-0<table class="horse-last-start">')
        for text in runner['history_text']:
            if same_day:text=text.replace('15Aug26','30Aug26')
            parts.append(f'<tr><td>{text}</td></tr>')
        parts.append('</table></td></tr></table>')
    return ''.join(parts)+'</html>'


def results_html(price_header='Starting Price'):
    return f'''<html><a href="/FreeFields/Results.aspx?Key=2026Aug30,WA,Test">Results</a>
      <div class="race-venue"><h2>Test: Club<span class="race-venue-date">Sunday, 30 August 2026</span></h2></div>
      <table class="race-title"><tr><th>Race 1 Test</th></tr></table>
      <table><tr><th>Finish</th><th>No.</th><th>Horse</th><th>{price_header}</th></tr>
      <tr><td>1</td><td>1</td><td>ALPHA</td><td>$3.20F</td></tr>
      <tr><td>2</td><td>2</td><td>BETA</td><td>$4</td></tr></table></html>'''


class RulesTests(unittest.TestCase):
    def setUp(self):self.c=config_at()

    def test_prior_start_and_trial(self):
        row=history_row(start(finish=3,size=9,margin='2.50'),dt.date(2026,8,30))
        self.assertEqual(row['margin'],2.5);self.assertEqual(row['weight'],58)
        self.assertEqual(row['finish_percentile'],.75)
        self.assertIsNone(history_row('T '+start(),dt.date(2026,8,30)))
        self.assertIsNone(history_row(start('30Aug26'),dt.date(2026,8,30)))

    def test_weight_claim_is_not_guessed(self):
        r=race();r['runners'][0]['field_values']['jockey']='Rider (a2/54kg)'
        f=features(r,r['runners'][0],self.c)
        self.assertIsNone(f['factors']['weight_change']);self.assertEqual(f['known_weight'],95)

    def test_recent_records_sorted_and_margin_capped(self):
        r=race();runner=r['runners'][0];runner['history_text']=[start('01Aug26',finish=8,margin='25'),start('15Aug26')]
        f=features(r,runner,self.c)
        self.assertEqual(f['history'][0]['date'],'2026-08-15')
        self.assertAlmostEqual(f['factors']['recent_margin'],.625)

    def test_score_and_selection(self):
        rows=score_races([race()],self.c)
        self.assertTrue(rows[0]['selected']);self.assertFalse(rows[1]['selected'])
        self.assertEqual(rows[0]['score'],97.5);self.assertEqual(rows[0]['stake'],10)
        self.assertEqual(rows[0]['probability'],'');self.assertEqual(rows[0]['min_price'],'')

    def test_no_forced_selection(self):
        r=race();r['runners'][1]['history_text']=r['runners'][0]['history_text']
        rows=score_races([r],self.c);self.assertFalse(any(r['selected'] for r in rows))

    def test_missing_opponent_excludes_whole_race(self):
        r=race();r['runners'][1]['history_text']=[]
        rows=score_races([r],self.c);self.assertFalse(any(r['selected'] for r in rows))
        self.assertFalse(any(r['race_eligible'] for r in rows))

    def test_daily_cap(self):
        races=[]
        for n in range(30):
            r=race();r['race']=n+1;r['id']=r['id'].rsplit('|',1)[0]+f'|{n+1}';races.append(r)
        rows=score_races(races,self.c)
        self.assertEqual(sum(r['stake'] for r in rows),270)
        self.assertEqual(sum(r['selected'] for r in rows),27)

    def test_recency_and_duplicate_start(self):
        self.assertEqual(recency_score(20),1);self.assertEqual(recency_score(100),.2)
        r=race();r['runners'][0]['history_text']*=2
        with self.assertRaises(ValueError):features(r,r['runners'][0],self.c)

    def test_parse_identity_and_same_day(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            p=Path(folder)/'WA_20260830_Test.html';p.write_text(guide_html(),encoding='utf-8')
            parsed=parse_guide(p);self.assertEqual(len(parsed),1);self.assertEqual(parsed[0]['exclusions'],[])
            p.write_text(guide_html(same_day=True),encoding='utf-8')
            self.assertIn('Same-day/future form contamination',parse_guide(p)[0]['exclusions'])
            p.write_text(guide_html(mismatch=True),encoding='utf-8')
            self.assertTrue(parse_guide(p)[0]['exclusions'])

    def test_offline_lock_and_overwrite_refusal(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root=Path(folder);guides=root/'input';guides.mkdir();(guides/'WA_20260830_Test.html').write_text(guide_html(),encoding='utf-8')
            with patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('No network in scoring')):
                payload=assess(guides,dt.date(2026,8,30),dt.date(2026,8,30),root/'out')
            self.assertEqual(load_lock(root/'out')['config_hash'],digest(self.c))
            with self.assertRaises(FileExistsError):assess(guides,dt.date(2026,8,30),dt.date(2026,8,30),root/'out')
            data=copy.deepcopy(payload);data['runners'][0]['score']=1
            (root/'out/locked-predictions.json').write_text(json.dumps(data),encoding='utf-8')
            with self.assertRaises(ValueError):load_lock(root/'out')

    def test_result_identity_sp_and_no_tote(self):
        result=result_page(results_html(),race())
        self.assertEqual(result[1][0]['sp'],3.2)
        with self.assertRaises(StopResults):result_page(results_html('Win Dividend'),race())
        with self.assertRaises(StopResults):result_page(results_html().replace('Sunday, 30','Monday, 31'),race())

    def test_settlement_and_full_field_validation(self):
        rows=score_races([race()],self.c)
        payload={'version':'V2-R1-experimental','config_hash':digest(self.c),'runners':rows}
        outcomes={rows[0]['id']:result_page(results_html(),race())[1]}
        summary,joined,issues=summarise(payload,outcomes)
        self.assertEqual(summary['paper_profit_loss'],22);self.assertEqual(summary['selected_settled'],1);self.assertEqual(issues,[])
        outcomes[rows[0]['id']]=outcomes[rows[0]['id']][:1]
        summary,_,issues=summarise(payload,outcomes)
        self.assertEqual(summary['selected_settled'],0);self.assertEqual(summary['selected_unresolved'],1)

    def test_block_stops_no_retry_and_keeps_partial(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root=Path(folder);guides=root/'input';guides.mkdir();(guides/'WA_20260830_Test.html').write_text(guide_html(),encoding='utf-8')
            assess(guides,dt.date(2026,8,30),dt.date(2026,8,30),root/'out')
            client=Mock();client.get.side_effect=StopResults('CAPTCHA blocked')
            result=settle(root/'out',root/'results',client)
            self.assertEqual(result['status'],'stopped');self.assertEqual(client.get.call_count,1)
            self.assertTrue((root/'results/summary.json').exists())

    def test_csv_formula_text(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path=Path(folder)/'out.csv';csv_file(path,[{'name':'=FORMULA()','sp':'','stake':0}],['name','sp','stake'])
            self.assertIn("'=FORMULA()",path.read_text(encoding='utf-8-sig'))
            with path.open(encoding='utf-8-sig',newline='') as f:row=next(csv.DictReader(f))
            self.assertEqual(row['sp'],'');self.assertEqual(row['stake'],'0')


if __name__=='__main__':unittest.main()
