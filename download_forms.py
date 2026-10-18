"""Sequential Racing Australia HTML downloader. Python 3.12+, no dependencies."""
import argparse
import datetime as dt
import json
import logging
from pathlib import Path
import re
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, parse_qs
from urllib.request import Request, build_opener, HTTPRedirectHandler
from zoneinfo import ZoneInfo

BASE = 'https://racingaustralia.horse'
STATES = ('QLD', 'NSW', 'VIC', 'SA', 'WA')
MONTHS = {m: i for i, m in enumerate(('Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'), 1)}

class StopRun(RuntimeError):
    pass

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise StopRun('Redirect received; inspect the site manually before another run.')

def validate_html(body):
    lower = body.lower()
    signals = ('captcha', 'access denied', 'verify you are human', 'checking your browser',
               'cf-chl-', 'unusual traffic', 'request blocked', 'just a moment',
               'enable javascript and cookies', 'incapsula', 'challenge-platform')
    if any(s in lower for s in signals):
        raise StopRun('Possible CAPTCHA/block page detected. No more requests will be made.')
    if '<html' not in lower:
        raise StopRun('Unexpected response: not an HTML page.')

class Client:
    def __init__(self, delay=20):
        self.delay = delay
        self.last = None
        self.opener = build_opener(NoRedirect())

    def get(self, url):
        parts = urlsplit(url)
        if parts.scheme != 'https' or parts.netloc != 'racingaustralia.horse':
            raise StopRun('Unexpected destination; stopping.')
        if self.last is not None:
            time.sleep(max(0, self.delay - (time.monotonic() - self.last)))
        logging.info('GET %s', url)
        try:
            with self.opener.open(Request(url, headers={'User-Agent': 'RacingFormGuides/0.1 (personal sequential downloader)'}), timeout=45) as response:
                if 'text/html' not in response.headers.get('Content-Type', '').lower():
                    raise StopRun('Unexpected content type.')
                raw = response.read(10_000_001)
                if len(raw) > 10_000_000:
                    raise StopRun('Response exceeds size limit.')
                body = raw.decode(response.headers.get_content_charset() or 'utf-8', errors='replace')
        finally:
            self.last = time.monotonic()
        validate_html(body)
        return body

class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            href = dict(attrs).get('href', '')
            if href:
                self.links.append(href)

class PageText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self.hidden:
            self.hidden -= 1
    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)

def is_tab_meeting(body):
    parser = PageText()
    parser.feed(body)
    text = re.sub(r'\s+', ' ', ' '.join(parser.parts))
    # Match the meeting's classification, never a mention elsewhere in the guide.
    match = re.search(r'(?<!Apprentice )Meeting Type\s*:\s*([^:()]{1,100})\(([^()]{1,60})\)', text, re.I)
    if not match:
        raise StopRun('Meeting Type classification missing or unrecognized; refusing to save.')
    category = match.group(2).strip().upper()
    if category == 'TAB MEETING':
        return True
    if re.fullmatch(r'NON[ -]?TAB MEETING', category):
        return False
    raise StopRun(f'Unrecognized Meeting Type category: {category}; refusing to save.')

def discover(body, state, date, historical=False):
    if ('race fields &amp; form' not in body.lower() and 'race fields & form' not in body.lower()
            and not (historical and 'horse racing results' in body.lower())):
        raise StopRun('Calendar layout not recognized; inspect manually.')
    parser = Links()
    parser.feed(body)
    meetings = {}
    form_links = 0
    for href in parser.links:
        url = urljoin(BASE + '/FreeFields/', href)
        parts = urlsplit(url)
        expected_path = '/freefields/results.aspx' if historical else '/freefields/form.aspx'
        if parts.path.lower() != expected_path:
            continue
        form_links += 1
        key = parse_qs(parts.query).get('Key', [''])[0]
        match = re.fullmatch(r'(\d{4})([A-Za-z]{3})(\d{2}),([A-Z]{2,3}),(.+)', key)
        if not match:
            raise StopRun('Meeting key format changed; inspect manually.')
        year, month, day, found_state, venue = match.groups()
        meeting_date = dt.date(int(year), MONTHS[month], int(day))
        if found_state != state or meeting_date != date:
            continue
        if re.search(r'trial|picnic|amateur|jump\s*out', venue, re.I):
            logging.info('Skip excluded meeting: %s', venue)
            continue
        meetings[key] = (url, venue)
    if not form_links:
        raise StopRun('No form links found; calendar may have changed or guides are unavailable.')
    return list(meetings.values())

def historical_form_link(body, results_url):
    parser = Links()
    parser.feed(body)
    key = parse_qs(urlsplit(results_url).query).get('Key')
    for href in parser.links:
        url = urljoin(results_url, href)
        parts = urlsplit(url)
        if (parts.scheme == 'https' and parts.netloc == 'racingaustralia.horse'
                and parts.path.lower() == '/freefields/form.aspx'
                and parse_qs(parts.query).get('Key') == key):
            return url
    raise StopRun('Historical meeting has no matching advertised form link; stopping.')

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--states', default='QLD')
    p.add_argument('--date', default='')
    p.add_argument('--limit', type=int, default=1, help='Maximum guides across all states')
    args = p.parse_args()
    states = list(dict.fromkeys(args.states.upper().split(',')))
    if not states or any(s not in STATES for s in states) or not 1 <= args.limit <= 30:
        p.error('Use QLD,NSW,VIC,SA,WA and a limit from 1 to 30.')
    date = dt.date.fromisoformat(args.date) if args.date else dt.datetime.now(ZoneInfo('Australia/Brisbane')).date()
    historical = date < dt.datetime.now(ZoneInfo('Australia/Brisbane')).date()
    folder = Path('outputs') / date.strftime('%Y%m%d')
    folder.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                        handlers=[logging.StreamHandler(), logging.FileHandler(folder / 'run.log', encoding='utf-8')])
    manifest = {'date': date.isoformat(), 'states': states, 'files': [], 'skipped': [], 'status': 'running'}
    client = Client()
    try:
        for state in states:
            calendar = 'Calendar_Results.aspx' if historical else 'Calendar.aspx'
            logging.info('Using %s calendar for %s', 'recent results' if historical else 'current', date)
            body = client.get(f'{BASE}/FreeFields/{calendar}?State={state}')
            meetings = discover(body, state, date, historical=historical)
            logging.info('%s: %d eligible meeting links for %s', state, len(meetings), date)
            for url, venue in meetings:
                if len(manifest['files']) >= args.limit:
                    break
                safe = re.sub(r'[^\w-]+', '_', venue).strip('_')[:120]
                path = folder / f'{state}_{date:%Y%m%d}_{safe}.html'
                if path.exists():
                    raise StopRun(f'File already exists: {path}; refusing to overwrite.')
                guide = client.get(url)
                if historical:
                    url = historical_form_link(guide, url)
                    guide = client.get(url)
                if not re.search(r'Race\s*1\b', guide, re.I) or 'Prizemoney' not in guide:
                    raise StopRun('Expected race form content missing; stopping without saving this page.')
                if not is_tab_meeting(guide):
                    logging.info('Skip non-TAB meeting: %s (%s)', venue, state)
                    manifest['skipped'].append({'venue': venue, 'url': url, 'reason': 'non-TAB meeting'})
                    continue
                path.write_text(guide, encoding='utf-8')
                manifest['files'].append({'file': path.name, 'url': url, 'venue': venue})
                logging.info('Saved %s', path)
            if len(manifest['files']) >= args.limit:
                break
        manifest['status'] = 'complete' if manifest['files'] else 'no_guides'
        logging.info('Finished: %d guides saved.', len(manifest['files']))
        return 0
    except Exception as exc:
        manifest['status'] = 'stopped'
        manifest['reason'] = str(exc)
        logging.error('STOP: %s. No retries or further states.', exc)
        return 1
    finally:
        (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')

if __name__ == '__main__':
    raise SystemExit(main())
