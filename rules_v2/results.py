"""Results stage, invoked only on a saved prediction lock. No guide scoring here."""
import argparse
import datetime as dt
import json
import math
from pathlib import Path
import re
import time
import urllib.error
from urllib.parse import quote
from urllib.request import Request, HTTPRedirectHandler, build_opener

from .core import digest
from .guides import Tree, Node
from .run import csv_file, now, save


class StopResults(RuntimeError):pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise StopResults('Redirect: inspect source before requesting again')


class Client:
    def __init__(self):self.last=None;self.opener=build_opener(NoRedirect())
    def get(self,url):
        if self.last is not None:time.sleep(max(0,20-(time.monotonic()-self.last)))
        self.last=time.monotonic()
        try:
            with self.opener.open(Request(url,headers={'User-Agent':'RacingFormPaperResearch/2.0 (sequential requests)'}),timeout=45) as r:
                body=r.read().decode('utf-8-sig')
        except (urllib.error.URLError,UnicodeError) as exc:raise StopResults(f'Result request stopped: {exc}') from None
        if any(s in body.lower() for s in ('captcha','access denied','verify you are human','checking your browser','cf-chl-',
                'unusual traffic','request blocked','just a moment','incapsula','challenge-platform','enable javascript and cookies')):
            raise StopResults('CAPTCHA/block detected: no retries or further meetings')
        if '<html' not in body.lower():raise StopResults('Unexpected non-HTML response')
        return body


def load_lock(folder):
    p=Path(folder)
    payload=json.loads((p/'locked-predictions.json').read_text(encoding='utf-8'))
    if digest(payload)!=(p/'locked-predictions.sha256').read_text().strip():raise ValueError('Locked predictions hash mismatch')
    if not payload.get('locked_utc') or not payload['version'].startswith('V2-R'):
        raise ValueError('A deterministic V2 prediction lock is required')
    locked=dt.datetime.fromisoformat(payload['locked_utc'])
    if locked.tzinfo is None or locked>dt.datetime.now(dt.timezone.utc):raise ValueError('Invalid prediction-lock timestamp')
    return payload


def result_page(body,identity):
    root=Tree(body).root
    node=root.find('race-venue');date_node=root.find('race-venue-date')
    if node is None or date_node is None:raise StopResults('Missing results meeting identity')
    heading=next((n.text() for n in node.walk() if n.tag=='h2'),'')
    date=dt.date.fromisoformat(identity['date'])
    if heading.split(':')[0].strip()!=identity['meeting'] or f'{date.day} {date.strftime("%B %Y")}' not in date_node.text():
        raise StopResults('Results meeting/date mismatch')
    # The exact state/date/venue must appear in the page's own Results tab Key.
    from urllib.parse import parse_qs,urlsplit
    expected=f'{date.year}{date.strftime("%b")}{date.day:02d},{identity["state"]},{identity["meeting"]}'
    keys=[k for n in root.walk() if n.tag=='a' and '/Results.aspx' in n.attrs.get('href','')
          for k in parse_qs(urlsplit(n.attrs['href']).query).get('Key',[])]
    if expected not in keys:raise StopResults('Results state/meeting Key not verified')
    output={};race=None
    for node in root.walk():
        if node.tag!='table':continue
        if node.has('race-title'):
            m=re.search(r'\bRace\s+(\d+)\b',node.text())
            if not m:raise StopResults('Unnumbered results race')
            race=int(m[1]);continue
        if race is None:continue
        trs=[n for n in node.walk() if n.tag=='tr']
        if not trs:continue
        headers=[n.text().lower().rstrip('.') for n in trs[0].children if isinstance(n,Node) and n.tag=='th']
        if not {'finish','no','horse','starting price'}.issubset(set(headers)):continue
        if race in output:raise StopResults('Duplicate result field table')
        entries=[]
        for tr in trs[1:]:
            cells=[n.text() for n in tr.children if isinstance(n,Node) and n.tag=='td']
            if not cells:continue
            if len(cells)!=len(headers):raise StopResults('Results table columns changed')
            # RA retains the emergency suffix on promoted starters and scratches.
            # It is a label, not part of the numerical runner identity.
            row=dict(zip(headers,cells));num=re.fullmatch(r'(\d+)(e)?',row['no'],re.I)
            if not num:raise StopResults('Unsupported result runner number')
            finish=row['finish'].strip();sp=row['starting price'].replace(' ','')
            # Explicit source status takes precedence over displayed odds or an
            # administrative finish code (e.g. SB on a Scratched row).
            scratched='scratched' in tr.attrs.get('class','').lower().split()
            if scratched:
                entry={'number':int(num[1]),'name':row['horse'],'finish':None,'sp':None,'status':'scratched'}
            elif finish.isdigit():
                price=re.fullmatch(r'\$(\d+(?:\.\d+)?)(?:[eE]?[fF])?',sp)
                if not price:raise StopResults('Unrecognised Starting Price; no dividend substitution')
                value=float(price[1])
                if not math.isfinite(value) or value<=1:raise StopResults('Invalid SP')
                entry={'number':int(num[1]),'name':row['horse'],'finish':int(finish),'sp':value,'status':'finished'}
            elif finish.upper() in ('F','PU','UR','LR','DNF','TFF'):
                price=re.fullmatch(r'\$(\d+(?:\.\d+)?)(?:[eE]?[fF])?',sp)
                if not price:raise StopResults('Missing DNF SP')
                value=float(price[1])
                if not math.isfinite(value) or value<=1:raise StopResults('Invalid DNF SP')
                entry={'number':int(num[1]),'name':row['horse'],'finish':None,'sp':value,'status':'dnf'}
            elif not finish and not sp:
                entry={'number':int(num[1]),'name':row['horse'],'finish':None,'sp':None,'status':'nonstarter_or_unresolved'}
            else:
                # Keep the raw notation for review, without guessing a position,
                # return or dead-heat adjustment. Only this race is withheld.
                entry={'number':int(num[1]),'name':row['horse'],'finish':None,'sp':None,'status':'unresolved_finish'}
            entry['number_label']=row['no']
            entry['finish_label']=finish
            entry['reported_starting_price']=row['starting price']
            entries.append(entry)
        output[race]=entries
    if not output:raise StopResults('No recognised official result tables')
    return output


def summarise(payload,outcomes):
    rows=[];issues=[];races={}
    for row in payload['runners']:
        if row['race_eligible']:races.setdefault(row['id'],[]).append(row)
    for race_id,field in races.items():
        results=outcomes.get(race_id,[])
        starters=[r for r in results if r['status'] in ('finished','dnf')]
        expected={(r['number'],r['name']) for r in field}
        actual={(r['number'],r['name']) for r in starters}
        reason=''
        if not results:reason='No verified result available'
        elif len({r['number'] for r in results})!=len(results):reason='Duplicate result runners'
        elif any(r['status']=='unresolved_finish' for r in results):reason='Unsupported finish notation/dead heat; race withheld for review'
        elif expected!=actual:reason='Active field changed or runner identity mismatch; race withheld'
        elif sum(r['finish']==1 for r in starters)!=1:reason='No unique winner; race withheld'
        elif any(r['finish'] is not None and not 1<=r['finish']<=len(starters) for r in starters):reason='Finish outside field size; race withheld'
        elif len([r['finish'] for r in starters if r['finish'] is not None])!=len({r['finish'] for r in starters if r['finish'] is not None}):reason='Tied/duplicate finish positions; race withheld'
        if reason:issues.append({'race_id':race_id,'reason':reason})
        indexed={(r['number'],r['name']):r for r in starters}
        for runner in field:
            row=dict(runner);row.update({'settlement_status':'unresolved' if reason else 'settled','settlement_note':reason,
                'win':'','finish':'','sp':'','paper_pl':'','market_favourite':False})
            if not reason:
                result=indexed[(runner['number'],runner['name'])]
                row.update(result);row['win']=int(row['finish']==1)
                row['paper_pl']=row['stake']*(row['sp']*row['win']-1) if row['selected'] else ''
                # Descriptive benchmark only, AFTER predictions. Tie favourites split one $10 stake.
                row['market_favourite']=row['sp']==min(r['sp'] for r in starters)
            rows.append(row)
    settled=[r for r in rows if r['selected'] and r['settlement_status']=='settled']
    staked=sum(r['stake'] for r in settled);ret=sum(r['stake']*r['sp']*r['win'] for r in settled)
    # Dates with unresolved selections remain visible; never silently call this a full-month return.
    summary={'version':payload['version'],'prediction_hash':digest(payload),'rules_hash':payload['config_hash'],
        'selected_total':sum(r['selected'] for r in payload['runners']),'selected_settled':len(settled),
        'selected_unresolved':sum(r['selected'] and r['settlement_status']=='unresolved' for r in rows),
        'wins':sum(r['win'] for r in settled),'staked':staked,'gross_return':round(ret,2),
        'paper_profit_loss':round(ret-staked,2),'roi':(ret-staked)/staked if staked else None,
        'win_rate':sum(r['win'] for r in settled)/len(settled) if settled else None,
        'races_unresolved':len(issues),'probabilities':'Not produced by this ranking baseline',
        'price_qualification':'Not applicable: no minimum prices in V2-R1','pre_race_qualification':'Unknown',
        'historical_reconstruction':True}
    running=peak=max_drawdown=0.0;losing=longest=0
    for r in sorted(settled,key=lambda r:(r['date'],r['id'])):
        running+=r['paper_pl'];peak=max(peak,running);max_drawdown=max(max_drawdown,peak-running)
        losing=0 if r['win'] else losing+1;longest=max(longest,losing)
    summary.update({'max_drawdown':round(max_drawdown,2),'longest_losing_sequence':longest,
                    'sequence_note':'Dates then state/meeting/race identity; not actual race-time order'})
    return summary,rows,issues


def settle(folder,output,client=None):
    payload=load_lock(folder)  # This must happen before creating a network client or fetching results.
    out=Path(output);out.mkdir(parents=True,exist_ok=False);(out/'official-pages').mkdir()
    client=client or Client();meetings={};outcomes={};requests=[];error=None
    for race in payload['races']:
        if not race['exclusions']:meetings[(race['date'],race['state'],race['meeting'])]=race
    try:
        for (date,state,venue),identity in sorted(meetings.items()):
            day=dt.date.fromisoformat(date);key=f'{day.year}{day.strftime("%b")}{day.day:02d},{state},{venue}'
            url='https://racingaustralia.horse/FreeFields/Results.aspx?Key='+quote(key,safe='')
            body=client.get(url)
            path=out/'official-pages'/f'{state}_{date}_{venue.replace("/","_")}.html'
            path.write_text(body,encoding='utf-8')
            parsed=result_page(body,identity)
            source={'date':date,'state':state,'meeting':venue,'url':url,'retrieved_utc':now(),
                    'body_sha256':__import__('hashlib').sha256(body.encode('utf-8')).hexdigest()}
            requests.append(source)
            for race,entries in parsed.items():
                outcomes[f'{date}|{state}|{venue}|{race}']=[r|{'source_url':url,'retrieved_utc':source['retrieved_utc']} for r in entries]
            save(out/'result-checkpoint.json',outcomes)
    except (StopResults,OSError,ValueError) as exc:error=str(exc)
    summary,rows,issues=summarise(payload,outcomes)
    summary.update({'status':'stopped' if error else 'processed_with_unresolved' if issues else 'processed',
                    'stopped_reason':error,'official_meetings_fetched':len(requests),'created_utc':now()})
    save(out/'summary.json',summary);save(out/'unresolved.json',issues);save(out/'sources.json',requests)
    warnings=[{'race_id':race_id,'number':r['number'],'name':r['name'],
               'finish_label':r['finish_label'],'reported_starting_price':r['reported_starting_price']}
              for race_id,entries in outcomes.items() for r in entries if r['status']=='unresolved_finish']
    save(out/'result-warnings.json',warnings)
    # Retain excluded runner features too, but never attach unverified outcomes to them.
    excluded=[r|{'settlement_status':'excluded_race','win':'','finish':'','sp':'','paper_pl':''} for r in payload['runners'] if not r['race_eligible']]
    all_rows=rows+excluded
    fields=list(dict.fromkeys(k for r in all_rows for k in r))
    csv_file(out/'research-data.csv',all_rows,fields)
    csv_file(out/'settled-selections.csv',[r for r in rows if r['selected']],fields)
    groups=[]
    for factor in ('rank','date','state'):
        for value in sorted({str(r[factor]) for r in rows}):
            pool=[r for r in rows if str(r[factor])==value and r['settlement_status']=='settled']
            groups.append({'group':factor,'value':value,'runners':len(pool),'wins':sum(r['win'] for r in pool),
                           'win_rate':sum(r['win'] for r in pool)/len(pool) if pool else None})
    csv_file(out/'diagnostics.csv',groups,['group','value','runners','wins','win_rate'])
    (out/'summary.md').write_text('# V2-R1 historical paper results\n\n'+
        'Predictions were loaded from a saved hash-checked lock before results requests. No scores were changed.\n\n'+
        'No minimum-price rule or calibrated probabilities exist in this baseline. Returns use official decimal SP and fixed paper stakes.\n\n'+
        '```json\n'+json.dumps(summary,indent=2)+'\n```\n',encoding='utf-8')
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--locked-folder',required=True);p.add_argument('--output',required=True)
    a=p.parse_args()
    try:
        result=settle(a.locked_folder,a.output)
        if result['status']=='stopped':p.exit(1,result['stopped_reason']+'\n')
    except (ValueError,OSError) as exc:p.exit(1,str(exc)+'\n')


if __name__=='__main__':main()
