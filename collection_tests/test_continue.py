import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("collector", ROOT / "tools/collect_august_results.py")
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


class FakePage:
    url = "https://www.racing.com/form/2026-08-09/ballarat/race/2"

    def locator(self, selector):
        return self

    async def inner_text(self, **kwargs):
        return "Abnd"

    async def title(self):
        return "Contradictory status saved for review"

    async def screenshot(self, **kwargs):
        pass


class ContinueTests(unittest.TestCase):
    def drive(self, coroutine):
        # The fake page operations finish synchronously. Drive the coroutine
        # directly so these offline checks do not need a Windows I/O event loop.
        try:
            coroutine.send(None)
        except StopIteration as done:
            return done.value
        finally:
            coroutine.close()
        self.fail("Unexpected asynchronous I/O in offline fake")

    async def run_case(self, error):
        class Collector:
            page = FakePage()
            calls = []

            async def capture(self, url, meeting):
                number = c.race_number(url, meeting["date"])
                self.calls.append(number)
                if number == 2:
                    raise error
                return {"race": number}

        collector = Collector()
        meeting = dict(source_file="VIC_20260809_Ballarat.html", date="2026-08-09", races=[1, 2, 3])
        urls = [f"https://www.racing.com/form/2026-08-09/ballarat/race/{n}" for n in meeting["races"]]
        capture = {"races": []}
        report = {"errors": []}
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            out = Path(folder)
            self.assertEqual(out.resolve().parent, ROOT.resolve())
            if isinstance(error, c.AccessBlocked):
                with self.assertRaises(c.AccessBlocked):
                    await c.capture_remaining(collector, urls, meeting, capture, out / "capture.json", out, report)
                self.assertEqual(collector.calls, [1, 2])
            else:
                await c.capture_remaining(collector, urls, meeting, capture, out / "capture.json", out, report)
                self.assertEqual(collector.calls, [1, 2, 3])
                self.assertEqual([r["race"] for r in capture["races"]], [1, 3])
                self.assertFalse(capture["collection_complete"])
                self.assertEqual(len(report["errors"]), 1)
                self.assertTrue((out / "issues/VIC_20260809_Ballarat_race2.json").exists())

    def test_gap_is_quarantined_and_later_races_continue(self):
        self.drive(self.run_case(ValueError("Non-final result")))

    def test_access_block_stops_all_further_races(self):
        self.drive(self.run_case(c.AccessBlocked("Source access block")))
