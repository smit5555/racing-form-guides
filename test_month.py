import datetime as dt
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import download_forms as forms
import download_month as month
import historical_calendar as calendar

class MonthTests(unittest.TestCase):
    def test_august31_and_whole_month_coverage(self):
        day = dt.date(2026,8,31)
        found = calendar.lookup(day, calendar.STATES)
        self.assertEqual(found['NSW'], ['Corowa','Taree'])
        self.assertEqual(found['VIC'], ['Sportsbet-Ballarat Synthetic'])
        self.assertEqual(found['QLD'], [])
        for number, days in ((5,31),(6,30),(7,31),(8,31)):
            for d in range(1,days+1):
                record = calendar.lookup(dt.date(2026,number,d), calendar.STATES)
                self.assertEqual(set(record), set(calendar.STATES))
                self.assertTrue(all(len(v)==len(set(v)) for v in record.values()))

    def test_abandoned_excluded_and_pending_retained(self):
        day = dt.date(2026,8,3)
        self.assertNotIn('Lismore', calendar.lookup(day,['NSW'])['NSW'])
        self.assertEqual(calendar.details(day,['NSW'])['excluded_abandoned'][0]['venue'], 'Lismore')
        details = calendar.details(dt.date(2026,7,4),['QLD'])
        self.assertTrue(any(r['venue']=='Picklebet Park Warwick' and r['status']=='Nominations' for r in details['calendar_status_unconfirmed']))

    def test_readable_names(self):
        self.assertEqual(forms.output_folder('outputs',dt.date(2026,8,1),'readable').name,'1 Aug_Sat')
        self.assertEqual(forms.output_folder('outputs',dt.date(2026,8,2),'readable').name,'2 Aug_Sun')
        self.assertEqual(forms.output_folder('outputs',dt.date(2026,8,3),'readable').name,'3 Aug_Mon')
        self.assertEqual(forms.output_folder('outputs',dt.date(2026,8,31)).name,'20260831')

    def run_month(self, failures=None, wholly_unavailable=False):
        calls=[]; sleeps=[]
        failures=failures or {}
        with tempfile.TemporaryDirectory(prefix='.test-month-',dir=Path(__file__).parent) as folder:
            def run(command, **kwargs):
                date=dt.date.fromisoformat(command[command.index('--date')+1])
                calls.append(date)
                code=failures.get(date.day,0)
                root=command[command.index('--output-root')+1]
                path=forms.output_folder(root,date,'readable')
                path.mkdir(parents=True)
                status={0:'complete',1:'stopped',2:'incomplete'}[code]
                files = [] if wholly_unavailable or code!=2 else [{'file':'one-saved-guide.html'}]
                unavailable = [{'venue':'Unavailable meeting'}] if code==2 else []
                (path/'manifest.json').write_text(json.dumps({'status':status,'files':files, 'unavailable':unavailable, 'calendar_status_unconfirmed':[]}),encoding='utf-8')
                return SimpleNamespace(returncode=code)
            result=month.process_month(dt.date(2026,8,1),list(calendar.STATES),run=run,sleep=sleeps.append,output_root=Path(folder))
            report=json.loads((Path(folder)/'August 2026/month-manifest.json').read_text(encoding='utf-8'))
        return result,report,calls,sleeps

    def test_entire_august(self):
        result,report,calls,sleeps=self.run_month()
        self.assertEqual((result,report['status'],len(calls)),(0,'complete',31))
        self.assertEqual(sleeps,[20]*30)
        self.assertEqual(report['days'][0]['folder'],'1 Aug_Sat')
        self.assertEqual(report['days'][-1]['folder'],'31 Aug_Mon')

    def test_block_stops_further_days(self):
        result,report,calls,sleeps=self.run_month({2:1})
        self.assertEqual((result,report['status'],len(calls)),(1,'stopped',2))
        self.assertEqual(sleeps,[20])

    def test_unavailable_day_continues_but_reports_incomplete(self):
        result,report,calls,_=self.run_month({2:2})
        self.assertEqual((result,report['status'],len(calls)),(2,'incomplete',31))

    def test_zero_guides_unavailable_stops_after_first_day(self):
        result,report,calls,sleeps=self.run_month({1:2},wholly_unavailable=True)
        self.assertEqual((result,report['status'],len(calls)),(2,'stopped',1))
        self.assertEqual(sleeps,[])
        self.assertIn('No guides saved',report['stop_reason'])

    def test_previous_progress_preserved_on_zero_guide_day(self):
        result,report,calls,sleeps=self.run_month({2:2},wholly_unavailable=True)
        self.assertEqual((result,report['status'],len(calls)),(2,'stopped',2))
        self.assertEqual(report['days'][0]['status'],'complete')
        self.assertEqual(sleeps,[20])

    def test_selected_range_only(self):
        with tempfile.TemporaryDirectory(prefix='.test-range-',dir=Path(__file__).parent) as folder:
            calls=[]
            def run(command, **kwargs):
                day=dt.date.fromisoformat(command[command.index('--date')+1])
                calls.append(day)
                path=forms.output_folder(command[command.index('--output-root')+1],day,'readable')
                path.mkdir(parents=True)
                (path/'manifest.json').write_text(json.dumps({'status':'complete','files':[]}))
                return SimpleNamespace(returncode=0)
            month.process_range(dt.date(2026,8,15),dt.date(2026,8,17),list(calendar.STATES),run=run,sleep=lambda _:None,output_root=Path(folder))
            self.assertEqual([d.day for d in calls],[15,16,17])
            self.assertTrue((Path(folder)/'August 2026/15 Aug_Sat').exists())
            calls.clear()
            month.process_range(dt.date(2026,9,20),dt.date(2026,9,30),list(calendar.STATES),run=run,sleep=lambda _:None,output_root=Path(folder))
            self.assertEqual([d.day for d in calls],list(range(20,31)))
            self.assertTrue((Path(folder)/'September 2026/30 Sep_Wed').exists())


    def test_invalid_range_before_requests(self):
        for start,end in ((dt.date(2026,8,17),dt.date(2026,8,15)),(dt.date(2026,9,30),dt.date(2026,10,1))):
            with self.assertRaises(ValueError):
                month.process_range(start,end,list(calendar.STATES),run=lambda *a,**k:self.fail('must not request'))

    def test_september_extension(self):
        for day in range(1,31):
            self.assertEqual(set(calendar.lookup(dt.date(2026,9,day),calendar.STATES)),set(calendar.STATES))
        self.assertEqual(calendar.lookup(dt.date(2026,9,20),['NSW'])['NSW'],['Armidale','Nowra'])
        self.assertEqual(calendar.lookup(dt.date(2026,9,30),['WA'])['WA'],['Geraldton'])
        for day,state,venue in ((19,'WA','Mount Magnet'),(26,'NSW','Gunnedah'),(27,'WA','Toodyay')):
            date=dt.date(2026,9,day)
            self.assertNotIn(venue,calendar.lookup(date,[state])[state])
            self.assertTrue(any(r['venue']==venue for r in calendar.details(date,[state])['excluded_abandoned']))

    def test_october_rejected_before_network(self):
        with self.assertRaises(ValueError):
            month.process_month(dt.date(2026,10,1),list(calendar.STATES),run=lambda *a,**k: self.fail('must not request'))

if __name__ == '__main__':
    unittest.main()
