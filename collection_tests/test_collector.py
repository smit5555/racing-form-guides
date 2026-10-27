import importlib.util
import json
import shutil
import subprocess
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("collector", ROOT / "tools/collect_august_results.py")
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


class CollectionTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is required for the browser predicate regression check")
    def test_calendar_month_transition(self):
        # Exercise the actual browser predicate against old -> empty -> new DOMs.
        script = "const ready=" + c.CALENDAR_READY + "; let cells=[]; global.document={querySelectorAll:()=>cells.map(d=>({getAttribute:()=>d}))}; const old=JSON.stringify(['2026-09-15T14:00:00.000Z']); const check=(values,previous,expected)=>{cells=values;if(ready(previous)!==expected)throw Error('Incorrect calendar readiness: '+JSON.stringify(values));}; check([],null,false);check([],old,false);check(['2026-09-15T14:00:00.000Z'],old,false);check(['2026-08-15T14:00:00.000Z'],old,true);check(['2026-08-15T14:00:00.000Z'],null,true);"
        subprocess.run([shutil.which("node"), "-e", script], check=True, capture_output=True, text=True)

    @unittest.skipUnless(shutil.which("node"), "Node is required for the browser predicate regression check")
    def test_day_waits_for_filter_reload(self):
        target = c.calendar_day_key("2026-08-01")
        self.assertEqual(target, "2026-07-31T14:00:00")
        self.assertEqual(c.calendar_day_key("2026-08-31"), "2026-08-30T14:00:00")
        script = "const ready=" + c.CALENDAR_DAY_READY + "; let cells=[]; global.document={querySelectorAll:()=>cells.map(d=>({getAttribute:()=>d}))}; const target=" + json.dumps(target) + "; if(ready(target))throw Error('Empty filter reload treated as ready'); cells=['2026-08-01T00:00:00.000Z']; if(ready(target))throw Error('UTC midnight treated as Brisbane midnight'); cells=['2026-07-31T14:00:00.000Z']; if(!ready(target))throw Error('Expected Brisbane day was rejected');"
        subprocess.run([shutil.which("node"), "-e", script], check=True, capture_output=True, text=True)

    def test_manifest(self):
        data = json.loads((ROOT / "inputs/august_2026_meetings.json").read_text())
        self.assertEqual(len(data), 171)
        self.assertEqual(sum(len(m["races"]) for m in data), 1328)
        self.assertEqual(len({m["source_file"] for m in data}), 171)
        self.assertEqual([sum(a <= int(m["date"][-2:]) <= b for m in data) for a, b in c.BATCHES.values()], [55, 53, 63])
        for m in data:
            self.assertTrue(m["date"].startswith("2026-08-"))
            self.assertEqual(m["races"], list(range(1, len(m["races"]) + 1)))

    def test_source_identity(self):
        self.assertEqual(c.race_number("https://www.racing.com/form/2026-08-01/rosehill-gardens/race/2", "2026-08-01"), 2)
        for url in ["https://evil.example/form/2026-08-01/a/race/2", "https://www.racing.com/form/2026-08-02/a/race/2", "https://www.racing.com/form/2026-08-01/a/race/2?x=1"]:
            with self.assertRaises(ValueError):
                c.race_number(url, "2026-08-01")

    def test_matching_is_conservative(self):
        self.assertEqual(c.norm("bet365 Park Kilmore"), c.norm("Park Kilmore"))
        self.assertNotEqual(c.norm("Port Macquarie"), c.norm("Port Augusta"))

    def test_challenge_detection(self):
        for text in ["Access denied", "Mimecast", "Verify you are human", "Request blocked"]:
            self.assertIsNotNone(c.BLOCK.search(text))
        self.assertIsNone(c.BLOCK.search("Final Result Horse Barrier SP"))

    def test_rendered_identity(self):
        meeting = dict(meeting="Gilgandra", date="2026-08-01", state="NSW")
        title = "Form | Results | Gilgandra | Race 1 | Saturday | 1 August 2026 | NSW | Racing.com"
        c.validate_title(title, meeting, 1)
        for bad in [title.replace("Race 1", "Race 2"), title.replace("1 August", "2 August"), title.replace("Gilgandra", "Dubbo"), title.replace("NSW", "QLD")]:
            with self.assertRaises(ValueError):
                c.validate_title(bad, meeting, 1)


if __name__ == "__main__":
    unittest.main()
