"""Collection only. Public rendered Racing.com pages; no predictions or settlement.

Stops immediately on an access challenge. A saved capture is evidence for later
verification, never a claim that a meeting or starting price has been verified.
"""
import argparse
import asyncio
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

BATCHES = {"01-10": (1, 10), "11-20": (11, 20), "21-31": (21, 31)}
ROOT = Path(__file__).resolve().parents[1]
BLOCK = re.compile(r"access denied|access blocked|verify you are human|checking your browser|captcha|mimecast|unusual traffic|request blocked|security challenge", re.I)
CALENDAR_READY = """old => {
  const dates=[...document.querySelectorAll('[data-date]')]
    .map(e=>e.getAttribute('data-date')).sort();
  return dates.length>0 && (old===null || JSON.stringify(dates)!==old);
}"""


class AccessBlocked(RuntimeError):
    pass


def norm(text):
    # Explicit provider venue prefixes only. Never fuzzy-match horses or dates.
    text = re.sub(r"\b(bet365|sportsbet|ladbrokes|aquis park)\b", "", text, flags=re.I)
    return re.sub(r"[^a-z0-9]", "", text.lower())


def race_number(url, date):
    p = urlparse(url)
    if p.scheme != "https" or p.hostname != "www.racing.com":
        raise ValueError("Unexpected source host")
    m = re.fullmatch(r"/form/" + re.escape(date) + r"/[^/]+/race/(\d+)/?", p.path)
    if not m or p.query or p.fragment:
        raise ValueError("Unexpected race URL/date")
    return int(m[1])


def validate_title(title, meeting, number):
    fields = [s.strip() for s in title.split("|")]
    date = datetime.fromisoformat(meeting["date"])
    display_date = f"{date.day} {date.strftime('%B %Y')}"
    if (len(fields) < 8 or fields[:2] != ["Form", "Results"]
            or norm(fields[2]) != norm(meeting["meeting"])
            or fields[3] != f"Race {number}"
            or fields[5] != display_date or fields[6] != meeting["state"]):
        raise ValueError("Rendered title does not match meeting/date/state/race")


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


async def check_block(page, response=None):
    text = await page.locator("body").inner_text(timeout=30000)
    if (response and response.status in (401, 403, 429)) or BLOCK.search(text):
        raise AccessBlocked("Source access challenge/block detected; no retries")
    if urlparse(page.url).hostname != "www.racing.com":
        raise AccessBlocked("Unexpected source redirect; stopped")
    return text


class BrowserCollector:
    def __init__(self, page, limiter):
        self.page = page
        self.limiter = limiter

    async def pace(self):
        await asyncio.sleep(max(0, 20 - (time.monotonic() - self.limiter["last_action"])))
        self.limiter["last_action"] = time.monotonic()

    async def goto(self, url):
        await self.pace()
        response = await self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await check_block(self.page, response)

    async def wait_calendar_dates(self, previous=None):
        # Month changes first clear the grid, then populate the new dates.
        # An empty grid is not a completed transition.
        await self.page.wait_for_function(
            CALENDAR_READY, arg=previous, timeout=60000)
        await check_block(self.page)
        return await self.page.locator("[data-date]").evaluate_all("es => es.map(e => e.getAttribute('data-date')).sort()")

    async def calendar(self):
        await self.goto("https://www.racing.com/calendar")
        consent = self.page.get_by_role("button", name=re.compile(r"Decline Non.?Essential Cookies", re.I))
        if await consent.count() == 1:
            await consent.click()
        # The calendar normally opens on the current month. Use its public controls.
        dates = await self.wait_calendar_dates()
        for _ in range(24):
            if await self.page.locator('[data-date^="2026-08-15"]').count():
                # Select the four race-meeting types; trials can share venue names.
                for label in ("Metro", "Provincial", "Country", "Picnic"):
                    button = self.page.get_by_role("button", name=label, exact=True)
                    if "calendar__pill-filter--selected" not in (await button.get_attribute("class") or ""):
                        await self.pace()
                        await button.click()
                # Wait for filter updates before examining meeting buttons.
                await self.page.wait_for_timeout(2000)
                await check_block(self.page)
                return
            middle = dates[len(dates) // 2][:7]
            label = "Previous month" if middle > "2026-08" else "Next month"
            button = self.page.get_by_role("button", name=label, exact=True)
            if await button.count() != 1:
                raise ValueError("Calendar month control changed")
            await self.pace()
            await button.click()
            dates = await self.wait_calendar_dates(previous=json.dumps(dates))
        raise ValueError("August calendar was not found within 24 months")

    async def meeting_links(self, meeting):
        # Calendar dates are local midnight in Brisbane, encoded as UTC.
        date = meeting["date"]
        cells = self.page.locator("[data-date]")
        values = await cells.evaluate_all("es => es.map(e => e.getAttribute('data-date'))")
        from datetime import timedelta
        target = datetime.fromisoformat(date).replace(tzinfo=timezone.utc) - timedelta(hours=10)
        matches = [v for v in values if v[:19] == target.isoformat()[:19]]
        if len(matches) != 1:
            raise ValueError("Expected exactly one calendar day")
        cell = self.page.locator('[data-date="' + matches[0] + '"]')
        more = cell.get_by_role("button", name=re.compile(r"Show \d+ more", re.I))
        scope = cell
        if await more.count() == 1:
            await self.pace()
            await more.click()
            scope = self.page.locator(".calendar__panel-wrap")
            await scope.get_by_role("heading", name="All Meetings", exact=True).wait_for(timeout=30000)
        buttons = scope.get_by_role("button")
        labels = await buttons.all_text_contents()
        candidates = []
        for i, label in enumerate(labels):
            # The rendered button is R <venue> <state>. Exclude trial/jumpout labels.
            s = re.sub(r"\s+", " ", label).strip()
            m = re.fullmatch(r"[RI]\s*(.*?)\s*(NSW|VIC|QLD|SA|WA|TAS|NT|ACT)", s)
            if m and m[2] == meeting["state"] and norm(m[1]) == norm(meeting["meeting"]):
                candidates.append(i)
        if len(candidates) != 1:
            close = self.page.get_by_role("button", name="Close panel", exact=True)
            if await close.count() == 1:
                await close.click()
            raise ValueError(f"Calendar venue match count {len(candidates)}; manual review required")
        await self.pace()
        await buttons.nth(candidates[0]).click()
        links = self.page.locator('a[href*="/form/"][href*="/race/"]')
        # Opening a panel can briefly retain a previous meeting's links.
        await self.page.wait_for_function(r"""expected => {
          const a=[...document.querySelectorAll('a[href*="/form/"][href*="/race/"]')];
          const normal=s=>s.replace(/\b(bet365|sportsbet|ladbrokes|aquis park)\b/gi,'').toLowerCase().replace(/[^a-z0-9]/g,'');
          return a.length>0 && a.every(e=>{
            const p=new URL(e.href).pathname.split('/');
            return p[2]===expected.date && normal(p[3].replace(/-/g,' '))===expected.venue;
          });
        }""", arg={"date": date, "venue": norm(meeting["meeting"])}, timeout=30000)
        await check_block(self.page)
        urls = sorted(set(await links.evaluate_all("es => es.map(e => e.href)")))
        numbers = [race_number(u, date) for u in urls]
        close = self.page.get_by_role("button", name="Close panel", exact=True)
        if await close.count() != 1:
            raise ValueError("Calendar close control changed")
        await close.click()
        if len(numbers) != len(set(numbers)) or sorted(numbers) != meeting["races"]:
            raise ValueError("Calendar races differ from the saved guide; manual review required")
        return sorted(urls, key=lambda u: race_number(u, date))

    async def capture(self, url, meeting):
        number = race_number(url, meeting["date"])
        await self.goto(url)
        await self.page.locator(".race-entry-row").first.wait_for(timeout=30000)
        text = await check_block(self.page)
        if "Final Result" not in text:
            raise ValueError("Final Result not displayed")
        title = await self.page.title()
        validate_title(title, meeting, number)
        data = await self.page.evaluate("""() => {
          const h=document.querySelector('.results-header');
          const rows=[...document.querySelectorAll('.race-entry-row')];
          return {heading:document.title,
            headers:h?[...h.children].map(e=>e.innerText.trim()):[],
            html:(h?h.outerHTML:'')+rows.map(e=>e.outerHTML).join(''),
            rows:rows.map(e=>({horse:e.querySelector('a')?.innerText?.trim()||'', cells:[...e.children].map(c=>c.innerText.trim())}))};
        }""")
        if not data["rows"] or not data["headers"] or "SP" not in data["headers"]:
            raise ValueError("Rendered results structure changed")
        if any(not r["horse"] or len(r["cells"]) != len(data["headers"]) for r in data["rows"]):
            raise ValueError("Incomplete result rows")
        # No odds or position inference. Preserve the exact displayed values and DOM.
        return dict(race=number, url=self.page.url, status="Final Result", **data)


async def collect(args):
    from playwright.async_api import async_playwright
    lo, hi = BATCHES[args.batch]
    manifest = json.loads((ROOT / "inputs/august_2026_meetings.json").read_text(encoding="utf-8"))
    meetings = [m for m in manifest if lo <= int(m["date"][-2:]) <= hi]
    if args.pilot:
        meetings = meetings[:1]
    out = Path(args.output)
    report = {"batch": args.batch, "pilot": args.pilot, "status": "running", "required_meetings": len(meetings), "expected_races": sum(len(m["races"]) for m in meetings), "captured_meetings": 0, "errors": [], "verified_for_backtest": False}
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "status.json", report)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        calendar_page = await browser.new_page(viewport={"width": 1440, "height": 1000})
        race_page = await browser.new_page(viewport={"width": 1440, "height": 1000})
        limiter = {"last_action": 0}
        calendar = BrowserCollector(calendar_page, limiter)
        races = BrowserCollector(race_page, limiter)
        try:
            await calendar.calendar()
            for meeting in meetings:
                path = out / "captures" / (Path(meeting["source_file"]).stem + ".racing_com.json")
                # Existing evidence is not silently trusted/reused. Restore is a separate reviewed action.
                if path.exists():
                    raise ValueError("Output already contains captures; choose an empty output directory")
                try:
                    urls = await calendar.meeting_links(meeting)
                    capture = {"provider": "racing.com_browser_capture", **meeting, "captured_utc": datetime.now(timezone.utc).isoformat(), "races": []}
                    # Per-race checkpoint: partial meetings are retained but never labelled complete.
                    for url in urls:
                        capture["races"].append(await races.capture(url, meeting))
                        capture["collection_complete"] = len(capture["races"]) == len(urls)
                        write_json(path, capture)
                    report["captured_meetings"] += 1
                    print(f"Captured {meeting['source_file']}", flush=True)
                except AccessBlocked:
                    raise
                except Exception as exc:
                    report["errors"].append({"source_file": meeting["source_file"], "error": str(exc)})
                    # Unknown UI state: stop instead of clicking onward or repeating requests.
                    raise RuntimeError("Collection stopped for review; partial evidence saved") from exc
                finally:
                    write_json(out / "status.json", report)
            report["status"] = "captured_pending_verification"
        except Exception as exc:
            report["status"] = "stopped"
            report["stop_reason"] = str(exc)
            for name, page in (("calendar", calendar_page), ("race", race_page)):
                try:
                    await page.screenshot(path=str(out / (name + "_stopped.png")), full_page=True)
                except Exception:
                    pass
        finally:
            write_json(out / "status.json", report)
            await browser.close()
    files = {str(f.relative_to(out)): hashlib.sha256(f.read_bytes()).hexdigest() for f in out.rglob("*") if f.is_file()}
    write_json(out / "sha256.json", files)
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "captured_pending_verification" else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", choices=BATCHES, required=True)
    parser.add_argument("--output", default="august-results")
    parser.add_argument("--pilot", action="store_true", help="Collect only the first meeting of the selected window")
    raise SystemExit(asyncio.run(collect(parser.parse_args())))
