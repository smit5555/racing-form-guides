"""Strict local parser for saved Racing Australia Recent Form HTML.

Never follows links, loads scripts, or fetches pages. Unsupported layouts fail closed.
"""
import datetime as dt
import hashlib
from html.parser import HTMLParser
from pathlib import Path
import re


def clean(value):
    return ' '.join(value.split())


class Node:
    def __init__(self, tag='', attrs=()):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []

    def has(self, name):
        return name in self.attrs.get('class', '').split()

    def walk(self):
        yield self
        for item in self.children:
            if isinstance(item, Node):
                yield from item.walk()

    def find(self, name):
        return next((n for n in self.walk() if n.has(name)), None)

    def text(self):
        if self.tag in ('script', 'style', 'noscript'):
            return ''
        return clean(' '.join(c.text() if isinstance(c, Node) else c for c in self.children))


class Tree(HTMLParser):
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.root = Node('root')
        self.stack = [self.root]
        self.feed(body)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, value):
        self.stack[-1].children.append(value)


SHORT_DATE = re.compile(r'\b(\d{1,2})[- /]?([A-Za-z]{3})[- /]?(\d{2}|\d{4})\b')
MONTHS = {m.lower(): n for n, m in enumerate(('Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'), 1)}


def dated_form(text):
    for match in SHORT_DATE.finditer(text):
        day, month, year = match.groups()
        if month.lower() in MONTHS:
            try:
                yield dt.date(int(year) + (2000 if len(year) == 2 else 0), MONTHS[month.lower()], int(day))
            except ValueError:
                continue


def parse_guide(path):
    path = Path(path)
    data = path.read_bytes()
    body = data.decode('utf-8-sig')
    match = re.fullmatch(r'(QLD|NSW|VIC|SA|WA)_(\d{8})_(.+)\.html', path.name, re.I)
    if not match:
        raise ValueError('Guide filename must be STATE_yyyyMMdd_Venue.html')
    state, stamp, file_venue = match.groups()
    date = dt.datetime.strptime(stamp, '%Y%m%d').date()
    root = Tree(body).root
    venue_node = root.find('race-venue')
    date_node = root.find('race-venue-date')
    if venue_node is None or date_node is None:
        raise ValueError('Missing meeting heading/date')
    expected_date = f'{date.day} {date.strftime("%B %Y")}'
    if expected_date not in date_node.text():
        raise ValueError('Filename date and guide heading disagree')
    heading = next((n.text() for n in venue_node.walk() if n.tag == 'h2'), '')
    venue = heading.split(':')[0].strip()
    if clean(venue.replace('_', ' ')).casefold() != clean(file_venue.replace('_', ' ')).casefold():
        raise ValueError('Filename venue and guide heading disagree')
    meeting = root.find('meeting-type')
    if meeting is None or not re.search(r'\(TAB MEETING\)', meeting.text(), re.I):
        raise ValueError('Guide is not confirmed TAB')
    # Confirm the source's meeting Key, including state, without opening any URL.
    from urllib.parse import urlsplit, parse_qs
    keys = []
    for n in root.walk():
        if n.tag == 'a' and '/Form.aspx' in n.attrs.get('href', ''):
            keys += parse_qs(urlsplit(n.attrs['href']).query).get('Key', [])
    key = f'{date.year}{date.strftime("%b")}{date.day:02d},{state.upper()},{venue}'
    if key not in keys:
        raise ValueError('Meeting/state Key not verified')
    conditions = root.find('race-venue-bottom')
    conditions = conditions.text() if conditions else ''
    digest = hashlib.sha256(data).hexdigest()
    races = []
    race = None
    for node in root.walk():
        if node.tag == 'table' and node.has('race-title'):
            num = re.search(r'\bRace\s+(\d+)\b', node.text())
            if not num:
                raise ValueError('Unnumbered race')
            race = {'date': date.isoformat(), 'state': state.upper(), 'meeting': venue,
                    'race': int(num[1]), 'conditions': conditions, 'title': node.text(),
                    'guide': path.name, 'guide_sha256': digest, 'runners': [], 'exclusions': []}
            races.append(race)
        elif node.tag == 'table' and node.has('race-strip-fields') and race is not None:
            if race['runners']:
                race['exclusions'].append('Multiple race fields')
            for row in (n for n in node.walk() if n.tag == 'tr'):
                cells = {n.attrs.get('class', ''): n for n in row.children if isinstance(n, Node) and n.tag == 'td'}
                if 'horse' not in cells:
                    continue
                number = cells.get('no')
                number = re.fullmatch(r'(\d+)(e)?', number.text() if number else '')
                if not number:
                    race['exclusions'].append('Unrecognised runner number')
                    continue
                horse_link = next((n for n in cells['horse'].walk() if n.tag == 'a'), None)
                name = horse_link.text() if horse_link else cells['horse'].text()
                race['runners'].append({'number': int(number[1]), 'name': name,
                    'scratched': 'scratched' in row.attrs.get('class', '').lower(),
                    'emergency': bool(number[2]), 'field': row.text(),
                    'field_values': {k:v.text() for k,v in cells.items()},
                    'history_text': [], 'form': '', 'form_verified': False})
        elif node.tag == 'table' and node.has('horse-form-table') and race is not None:
            number, name = node.find('horse-number'), node.find('horse-name')
            if number is None or name is None or not number.text().isdigit():
                race['exclusions'].append('Missing detailed-form identity')
                continue
            candidates = [r for r in race['runners'] if r['number'] == int(number.text()) and r['name'] == name.text()]
            if len(candidates) != 1 or candidates[0]['form_verified']:
                race['exclusions'].append('Detailed-form/field identity mismatch or duplicate')
                continue
            runner = candidates[0]
            runner['form'] = node.text()
            runner['form_verified'] = True
            for history in (n for n in node.walk() if n.tag=='table' and n.has('horse-last-start')):
                runner['history_text'].extend(n.text() for n in history.walk() if n.tag=='tr')
    if not races or len({r['race'] for r in races}) != len(races):
        raise ValueError('Missing or duplicate races')
    for race in races:
        if len({r['number'] for r in race['runners']}) != len(race['runners']):
            race['exclusions'].append('Duplicate runner number')
        active = [r for r in race['runners'] if not r['scratched']]
        if len(active) < 2:
            race['exclusions'].append('Fewer than two confirmed active runners')
        if re.search(r'abandon|cancelled|no race', race['title'], re.I):
            race['exclusions'].append('Race cancellation/abandonment notice')
        for runner in active:
            if not runner['form_verified'] or 'Record:' not in runner['form']:
                race['exclusions'].append(f'#{runner["number"]} missing detailed form')
            if runner['emergency']:
                race['exclusions'].append(f'#{runner["number"]} unresolved emergency')
            dates = [d for text in runner['history_text'] for d in dated_form(text)]
            if any(text.strip() and len(list(dated_form(text)))!=1 for text in runner['history_text']):
                race['exclusions'].append(f'#{runner["number"]} unrecognised dated-form row')
            if any(d >= date for d in dates):
                race['exclusions'].append('Same-day/future form contamination')
            if not any(d < date for d in dates):
                race['exclusions'].append(f'#{runner["number"]} no dated prior form')
        race['exclusions'] = list(dict.fromkeys(race['exclusions']))
        race['id'] = f'{date.isoformat()}|{state.upper()}|{venue}|{race["race"]}'
    return races
