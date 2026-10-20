"""Download one recorded calendar month into separate human-readable day folders."""
import argparse
import calendar
import datetime as dt
import json
from pathlib import Path
import subprocess
import sys
import time

import download_forms as forms
from historical_calendar import lookup

MONTH_NAMES = ('January','February','March','April','May','June','July','August','September','October','November','December')

def process_month(first, states, run=subprocess.run, sleep=time.sleep, output_root=Path('outputs')):
    dates = [first.replace(day=day) for day in range(1,calendar.monthrange(first.year,first.month)[1]+1)]
    if any(lookup(day, states) is None for day in dates):
        raise ValueError('A full recorded calendar is required for this month and the selected states.')
    root = (Path(output_root) / f'{MONTH_NAMES[first.month-1]} {first.year}').resolve()
    root.mkdir(parents=True, exist_ok=True)
    report = {'month':first.strftime('%Y-%m'), 'states':states, 'status':'running',
              'coverage':'Relative to the supplied calendar snapshots; source names/availability validated per guide.',
              'days':[]}
    code = 0
    try:
        for number, day in enumerate(dates):
            # Separate child processes must still leave 20 seconds between days.
            if number:
                sleep(20)
            print(f'\nMONTH PROGRESS: day {number+1}/{len(dates)} - {day.isoformat()}', flush=True)
            command = [sys.executable, str(Path(__file__).with_name('download_forms.py').resolve()),
                       '--states', ','.join(states), '--date', day.isoformat(), '--limit', '30',
                       '--output-root', str(root), '--folder-style', 'readable']
            completed = run(command, cwd=str(Path(__file__).resolve().parent), check=False)
            path = forms.output_folder(root, day, 'readable') / 'manifest.json'
            manifest = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
            entry = {'date':day.isoformat(), 'folder':path.parent.name,
                     'status':manifest.get('status','missing_report'), 'exit_code':completed.returncode,
                     'saved':len(manifest.get('files',[])),
                     'unavailable':len(manifest.get('unavailable',[])),
                     'calendar_status_unconfirmed':len(manifest.get('calendar_status_unconfirmed',[]))}
            report['days'].append(entry)
            if completed.returncode not in (0,2) or entry['status'] in ('stopped','missing_report'):
                report['status'] = 'stopped'
                code = 1
                print('MONTH STOPPED: inspect the failed day. No retries or further days.', flush=True)
                break
            if entry['status'] in ('incomplete','limited') or completed.returncode == 2:
                code = 2
            save_report(root, report)
        if report['status'] != 'stopped':
            report['status'] = 'incomplete' if code else 'complete'
        return code
    finally:
        if report['status'] == 'running':
            report['status'] = 'stopped'
        save_report(root, report)

def save_report(root, report):
    (root / 'month-manifest.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    lines = [f'Month: {report["month"]}', f'Status: {report["status"]}',
             f'Days processed: {len(report["days"])}',
             f'Guides saved: {sum(d["saved"] for d in report["days"])}',
             'Coverage is relative to the supplied recorded calendar.',
             'Unconfirmed calendar statuses are listed; a saved guide does not prove a meeting went ahead.', '']
    for day in report['days']:
        lines.append(f'{day["folder"]}: {day["status"]}; {day["saved"]} guides; {day["unavailable"]} unavailable/unresolved; {day["calendar_status_unconfirmed"]} calendar statuses unconfirmed')
    (root / 'month-summary.txt').write_text('\n'.join(lines)+'\n', encoding='utf-8')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--month', required=True, help='YYYY-MM, currently 2026-05 through 2026-08')
    parser.add_argument('--states', default='QLD,NSW,VIC,SA,WA')
    args = parser.parse_args()
    try:
        if len(args.month) != 7:
            raise ValueError('Use YYYY-MM for the month.')
        first = dt.date.fromisoformat(args.month+'-01')
        states = list(dict.fromkeys(s.strip().upper() for s in args.states.split(',')))
        if not states or any(s not in forms.STATES for s in states):
            raise ValueError('Use only QLD, NSW, VIC, SA and WA.')
        return process_month(first, states)
    except ValueError as exc:
        parser.error(str(exc))

if __name__ == '__main__':
    raise SystemExit(main())
