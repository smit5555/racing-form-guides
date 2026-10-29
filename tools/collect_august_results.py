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
COLLECTOR_VERSION = "2026-10-29.6"
ROOT = Path(__file__).resolve().parents[1]
BLOCK = re.compile(r"access denied|access blocked|verify you are human|checking your browser|captcha|mimecast|unusual traffic|request blocked|security challenge", re.I)
CALENDAR_READY = """old => {
  const dates=[...document.querySelectorAll('[data-date]')]
    .map(e=>e.getAttribute('data-date')).sort();
  return dates.length>0 && (old===null || JSON.stringify(dates)!==old);
}"""
CALENDAR_DAY_READY = "target => [...document.querySelectorAll('[data-date]')].some(e => e.getAttribute('data-date').slice(0,19)===target)"
RESULT_READY = r"() => /\b(Final\s+Result|Abnd|Abandoned)\b/.test(document.body.innerText) && document.querySelectorAll('.race-entry-row').length>0"


def calendar_day_key(date):
    from datetime import timedelta
    return (datetime.fromisoformat(date).replace(tzinfo=timezone.utc) - timedelta(hours=10)).isoformat()[:19]


def has_final_result(text):
    return re.search(r"\bFinal\s+Result\b", text) is not None


def validate_capture(data, meeting):
    for key in ("source_file", "meeting", "date", "state", "guide_sha256"):
        if data.get(key) != meeting[key]:
            raise ValueError("Resume meeting identity mismatch: " + key)
    if data.get("provider") != "racing.com_browser_capture":
        raise ValueError("Unexpected resume provider")
    numbers = []
    for race in data.get("races", []):
        number = race_number(race["url"], meeting["date"])
        if number != race["race"] or number not in meeting["races"]:
            raise ValueError("Resume race identity mismatch")
        validate_title(race["heading"], meeting, number)
        if race.get("status") != "Final Result" or not race.get("rows") or "SP" not in race.get("headers", []):
            raise ValueError("Incomplete resume result")
        if any(not r.get("horse") or len(r["cells"]) != len(race["headers"]) for r in race["rows"]):
            raise ValueError("Invalid resume row")
        numbers.append(number)
    if len(numbers) != len(set(numbers)):
        raise ValueError("Duplicate resume race")
    if data.get("collection_complete") and sorted(numbers) != meeting["races"]:
        raise ValueError("Resume completeness mismatch")
    return set(numbers)


def seed_captures(out, meetings, batch):
    seed = ROOT / "inputs" / ("resume-" + batch)
    if not seed.exists():
        return
    hashes = json.loads((seed / "sha256.json").read_text(encoding="utf-8"))
    known = {Path(m["source_file"]).stem + ".racing_com.json": m for m in meetings}
    for name, digest in hashes.items():
        if name not in known:
            # Pilot only restores its selected meeting.
            continue
        raw = (seed / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("Resume evidence hash mismatch")
        validate_capture(json.loads(raw), known[name])
        target = out / "captures" / name
        if target.exists():
            raise ValueError("Output already contains captures; use an empty directory")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)


async def capture_remaining(collector, urls, meeting, capture, path, out, report):
    saved = {r["race"] for r in capture["races"]}
    for url in urls:
        number = race_number(url, meeting["date"])
        if number in saved:
            continue
        try:
            result = await collector.capture(url, meeting)
        except AccessBlocked:
            raise
        except Exception as exc:
            # A missing/interim/contradictory result is quarantined. Continue
            # independent races once the source is confirmed not to be blocked.
            text = await check_block(collector.page)
            issue = {"source_file": meeting["source_file"], "race": number, "url": url,
                     "error": str(exc), "title": await collector.page.title(), "page_text": text}
            report["errors"].append({k: v for k, v in issue.items() if k != "page_text"})
            stem = Path(meeting["source_file"]).stem + f"_race{number}"
            write_json(out / "issues" / (stem + ".json"), issue)
            try:
                await collector.page.screenshot(path=str(out / "issues" / (stem + ".png")), full_page=True)
            except Exception:
                pass
        else:
            capture["races"].append(result)
            saved.add(number)
        capture["races"].sort(key=lambda r: r["race"])
        capture["collection_complete"] = saved == set(meeting["races"])
        write_json(path, capture)


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

    async def dismiss_cookie_banner(self):
        consent = self.page.get_by_role("button", name=re.compile(r"Decline Non.?Essential Cookies", re.I))
        if await consent.count() == 1 and await consent.is_visible():
            await consent.click()

    async def calendar(self):
        await self.goto("https://www.racing.com/calendar")
        await self.dismiss_cookie_banner()
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
                        await self.wait_calendar_dates()
                # Filters reload asynchronously. The meeting step also waits for
                # its exact date, rather than assuming a fixed delay is enough.
                await self.wait_calendar_dates()
                await self.dismiss_cookie_banner()
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
        target = calendar_day_key(date)
        await self.page.wait_for_function(
            CALENDAR_DAY_READY, arg=target, timeout=60000)
        await check_block(self.page)
        await self.dismiss_cookie_banner()
        values = await cells.evaluate_all("es => es.map(e => e.getAttribute('data-date'))")
        matches = [v for v in values if v[:19] == target]
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
        # Result rows and the final-status label can render at different times.
        # Wait on the existing page; do not reload or repeat a source request.
        await self.page.wait_for_function(RESULT_READY, timeout=60000)
        text = await check_block(self.page)
        if not has_final_result(text):
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
    report = {"collector_version": COLLECTOR_VERSION, "browser_timezone": "Australia/Brisbane", "batch": args.batch, "pilot": args.pilot, "status": "running", "required_meetings": len(meetings), "expected_races": sum(len(m["races"]) for m in meetings), "captured_meetings": 0, "errors": [], "verified_for_backtest": False}
    out.mkdir(parents=True, exist_ok=True)
    seed_captures(out, meetings, args.batch)
    write_json(out / "status.json", report)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        # Calendar data-date is local midnight, so pin the same timezone used
        # for guide dates instead of inheriting the GitHub runner's UTC zone.
        context = await browser.new_context(timezone_id="Australia/Brisbane", locale="en-AU", viewport={"width": 1440, "height": 1000})
        calendar_page = await context.new_page()
        race_page = await context.new_page()
        limiter = {"last_action": 0}
        calendar = BrowserCollector(calendar_page, limiter)
        races = BrowserCollector(race_page, limiter)
        try:
            await calendar.calendar()
            for meeting in meetings:
                path = out / "captures" / (Path(meeting["source_file"]).stem + ".racing_com.json")
                try:
                    capture = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
                    saved = validate_capture(capture, meeting) if capture else set()
                    if saved == set(meeting["races"]):
                        report["captured_meetings"] += 1
                        report["resumed_meetings"] = report.get("resumed_meetings", 0) + 1
                        continue
                    urls = await calendar.meeting_links(meeting)
                    if capture is None:
                        capture = {"provider": "racing.com_browser_capture", **meeting, "captured_utc": datetime.now(timezone.utc).isoformat(), "races": []}
                    await capture_remaining(races, urls, meeting, capture, path, out, report)
                    report["captured_meetings"] += bool(capture.get("collection_complete"))
                    print(f"Captured {meeting['source_file']}", flush=True)
                except AccessBlocked:
                    raise
                except Exception as exc:
                    report["errors"].append({"source_file": meeting["source_file"], "error": str(exc)})
                    # Reset the calendar for the next independent meeting. Never
                    # repeat this failed meeting, and never continue after a block.
                    await check_block(calendar_page)
                    if race_page.url != "about:blank":
                        await check_block(race_page)
                    await calendar.calendar()
                finally:
                    write_json(out / "status.json", report)
            report["status"] = "captured_with_gaps_pending_verification" if report["errors"] else "captured_pending_verification"
        except Exception as exc:
            report["status"] = "stopped"
            report["stop_reason"] = str(exc)
            try:
                write_json(out / "calendar_diagnostics.json", {
                    "url": calendar_page.url, "title": await calendar_page.title(),
                    "date_cells": await calendar_page.locator("[data-date]").evaluate_all("es=>es.map(e=>e.getAttribute('data-date'))"),
                    "error_type": type(exc).__name__,
                })
            except Exception:
                pass
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
    return 2 if report["status"] == "stopped" else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", choices=BATCHES, required=True)
    parser.add_argument("--output", default="august-results")
    parser.add_argument("--pilot", action="store_true", help="Collect only the first meeting of the selected window")
    raise SystemExit(asyncio.run(collect(parser.parse_args())))
