"""Score saved guides offline and lock a deterministic research baseline."""
import argparse
import csv
import datetime as dt
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil

from .core import canonical, config_at, digest, score_races
from .guides import parse_guide


def now():return dt.datetime.now(dt.timezone.utc).isoformat()


def save(path,value):path.write_bytes(canonical(value))


def csv_file(path,rows,fields):
    with path.open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader()
        for row in rows:
            data={}
            for k in fields:
                v=row.get(k,'')
                if isinstance(v,(list,dict)):v=json.dumps(v,ensure_ascii=False)
                if isinstance(v,str) and v and v[:1] in '=+-@':v="'"+v
                data[k]=v
            writer.writerow(data)


def exports(out,payload):
    rows=payload['runners']
    fields=['date','state','meeting','race','number','name','rank','score','score_lead','selected','stake',
      'race_eligible','candidate','known_weight','prior_completed_starts','distance_metres','distance_starts',
      'going','going_starts','days_since_start','declared_weight','weight_change_benefit_kg','barrier','trainer','jockey',
      'factors','missing_factors','reasons','race_exclusions','guide','guide_sha256','citation','probability',
      'min_price','sp','bet','result','pre_race_qualification']
    csv_file(out/'all-runners.csv',rows,fields)
    selected=[r for r in rows if r['selected']]
    csv_file(out/'shortlist.csv',selected,fields)
    # Export target and historical dates separately; never overwrite the target date.
    starts=[{'target_date':r['date'],'prior_date':h['date'],'state':r['state'],'meeting':r['meeting'],
             'race':r['race'],'number':r['number'],'name':r['name'],**{k:v for k,v in h.items() if k!='date'}}
            for r in rows for h in r['history']]
    csv_file(out/'prior-starts.csv',starts,['target_date','prior_date','state','meeting','race','number','name','finish','field_size','distance','going','weight','margin','finish_percentile','evidence'])
    excluded=[{k:r[k] for k in ('date','meeting','race','guide','exclusions')} for r in payload['races'] if r['exclusions']]
    csv_file(out/'excluded-races.csv',excluded,['date','meeting','race','guide','exclusions'])
    lines=['# '+payload['version']+' deterministic paper test','',f'Rules: {payload["config"]["version"]}; config hash: {payload["config_hash"]}',
        f'Guides parsed: {len(payload["sources"])}; races: {len(payload["races"])}; eligible races: {sum(not r["exclusions"] for r in payload["races"])}.',
        f'Selections: {len(selected)}; total potential stake across dates: ${sum(r["stake"] for r in selected)}.',
        'No AI calls. Scores are not probabilities. Minimum prices and confidence are deliberately not invented.',
        'Historical reconstruction only: source timestamps/late scratches/going may not match what was available live.',
        'Coverage is limited to the supplied guides. Read coverage.json and issues.json before interpreting returns.',
        'SP, Bet and Result are blank until the separate results stage. Pre-race qualification is Unknown.','',
        '| Date | Meeting | Race | Selection | Score / 100 | Lead | Stake |',
        '|---|---|---|---|---:|---:|---:|']
    for r in selected:
        lines.append(f'| {r["date"]} | {r["meeting"].replace("|","/")} | {r["race"]} | #{r["number"]} {r["name"].replace("|","/")} | {r["score"]:.2f} | {r["score_lead"]:.2f} | ${r["stake"]} |')
    for r in selected:
        lines += ['',f'## {r["date"]} {r["meeting"]} R{r["race"]} #{r["number"]} {r["name"]}',
          'Factor values (0â€“1, unknown=null): '+json.dumps(r['factors']),
          f'Source: {r["citation"]}. Known factor weight {r["known_weight"]}/100.',
          'Risk flags: '+(', '.join(r['missing_factors']) or 'No missing scored factors; unscored class/pace and historical-source uncertainty still apply.')]
    lines += ['','Every runner, rejected race and selection reason is retained. No automatic parameter optimisation is performed.',
              'Review rules and data on development dates. Reserve a separate later period before testing profitability.']
    text='\n'.join(lines)+'\n'
    (out/'report.md').write_text(text,encoding='utf-8')
    (out/'report.html').write_text('<!doctype html><meta charset="utf-8"><title>V2-R1</title><style>body{font:16px system-ui;max-width:1100px;margin:30px auto;padding:20px}pre{white-space:pre-wrap;line-height:1.5}</style><pre>'+html.escape(text)+'</pre>',encoding='utf-8')


def assess(folder,start,end,output,config_path=None,exposed=None):
    if end<start or (end-start).days>30:raise ValueError('Choose an inclusive range of 1â€“31 days')
    c=config_at(config_path)
    out=Path(output);out.mkdir(parents=True,exist_ok=False);(out/'sources').mkdir()
    races=[];sources=[];issues=[];seen={};race_ids=set()
    for path in sorted(Path(folder).rglob('*.html')):
        match=re.fullmatch(r'(QLD|NSW|VIC|SA|WA)_(\d{8})_(.+)\.html',path.name,re.I)
        if not match:continue
        date=dt.datetime.strptime(match[2],'%Y%m%d').date()
        if not start<=date<=end:continue
        try:parsed=parse_guide(path)
        except (ValueError,UnicodeError) as exc:
            issues.append({'guide':path.name,'date':date.isoformat(),'reason':str(exc)});continue
        sha=parsed[0]['guide_sha256']
        if path.name in seen:
            if sha!=seen[path.name]:raise ValueError('Conflicting copies of a guide')
            continue
        seen[path.name]=sha
        for race in parsed:
            if race['id'] in race_ids:raise ValueError('Duplicate race identity')
            race_ids.add(race['id'])
        races.extend(parsed);sources.append({'guide':path.name,'date':date.isoformat(),'sha256':sha})
        shutil.copyfile(path,out/'sources'/path.name)
    if not races:raise ValueError('No readable matching guides for the selected dates')
    if exposed:
        with Path(exposed).open(encoding='utf-8-sig',newline='') as f:
            known={f'{r["date"]}|{r["state"]}|{r["meeting"]}|{int(r["race"])}' for r in csv.DictReader(f)}
        for race in races:
            if race['id'] in known:race['exclusions'].append('Outcome previously exposed in register')
    coverage=[]
    for n in range((end-start).days+1):
        day=(start+dt.timedelta(days=n)).isoformat()
        manifests=[]
        for path in Path(folder).rglob('manifest.json'):
            m=json.loads(path.read_text(encoding='utf-8-sig'))
            if m.get('date')==day:
                expected={r['file'] for r in m.get('files',[])}
                actual={r['guide'] for r in sources if r['date']==day}
                manifests.append({'status':m.get('status'),'states':m.get('states',[]),'missing_saved_guides':sorted(expected-actual),'lookup':m.get('lookup')})
        coverage.append({'date':day,'parsed_races':sum(r['date']==day for r in races),'manifests':manifests,
                         'note':'Supplied guide coverage, not independent proof of all meetings'})
        if not any(r['date']==day for r in races):issues.append({'date':day,'reason':'No readable supplied guides'})
    rows=score_races(races,c)
    payload={'version':c['version'],'created_utc':now(),'locked_utc':now(),'config':c,'config_hash':digest(c),
        'source_code_hashes':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')},
        'github_run_id':os.environ.get('GITHUB_RUN_ID','local'),'git_commit':os.environ.get('GITHUB_SHA','local'),
        'guide_run_id':os.environ.get('GUIDE_RUN_ID','local'),'purpose':'development / historical reconstruction',
        'races':races,'runners':rows,'sources':sources,'issues':issues,'coverage':coverage}
    save(out/'locked-predictions.json',payload)
    (out/'locked-predictions.sha256').write_text(digest(payload)+'\n',encoding='ascii')
    save(out/'coverage.json',coverage);save(out/'issues.json',issues);save(out/'config.json',c)
    exports(out,payload)
    print(f'{len(races)} races parsed; {sum(not r["exclusions"] for r in races)} eligible; {sum(r["selected"] for r in rows)} selections. No AI calls.')
    return payload


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--guides',required=True);p.add_argument('--start-date',required=True);p.add_argument('--end-date',required=True)
    p.add_argument('--output',required=True);p.add_argument('--config');p.add_argument('--exposed-races')
    a=p.parse_args()
    try:assess(a.guides,dt.date.fromisoformat(a.start_date),dt.date.fromisoformat(a.end_date),a.output,a.config,a.exposed_races)
    except (ValueError,OSError,KeyError) as exc:p.exit(1,str(exc)+'\n')


if __name__=='__main__':main()
