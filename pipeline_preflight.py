"""Validate a combined historical run before making any site requests."""
import argparse
import datetime as dt
from pathlib import Path

from historical_calendar import START, END, STATES, lookup
from rules_v2.core import config_at


def validate(start_text,end_text,states_text):
    if len(start_text)!=10 or len(end_text)!=10:
        raise ValueError('Enter both dates as YYYY-MM-DD')
    start,end=dt.date.fromisoformat(start_text),dt.date.fromisoformat(end_text)
    if not 0<=(end-start).days<=30:
        raise ValueError('Choose an inclusive range of 1–31 days, with end on or after start')
    states=list(dict.fromkeys(s.strip().upper() for s in states_text.split(',')))
    if not states or any(s not in STATES for s in states):
        raise ValueError('Use only QLD, NSW, VIC, SA and WA')
    dates=[start+dt.timedelta(days=n) for n in range((end-start).days+1)]
    if any(lookup(date,states) is None for date in dates):
        raise ValueError(f'Recorded calendar required for every day; currently {START.isoformat()} through {END.isoformat()}')
    # Fail before downloading if the assessment program/rules are not installed.
    config_at()
    if not Path(__file__).with_name('rules_v2').joinpath('exposed-races.csv').is_file():
        raise ValueError('Missing rules_v2/exposed-races.csv; install the full rules_v2 folder')
    return start,end,states


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--start-date',required=True);p.add_argument('--end-date',required=True)
    p.add_argument('--states',default='QLD,NSW,VIC,SA,WA')
    args=p.parse_args()
    try:
        start,end,states=validate(args.start_date,args.end_date,args.states)
        print(f'Validated {(end-start).days+1} days: {start} to {end}; states {",".join(states)}. No requests made by preflight.')
    except ValueError as exc:p.exit(1,str(exc)+'\n')


if __name__=='__main__':main()
