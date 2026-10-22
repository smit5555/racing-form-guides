"""Results stage, invoked only on a saved prediction lock. No guide scoring here."""
import argparse
import datetime as dt
import json
import hashlib
import math
from pathlib import Path
import re
import time
import urllib.error
from urllib.parse import quote, urlsplit, parse_qs
import zipfile
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


class SavedResults:
    """Read and verify an earlier artifact, with no network fallback."""
    def __init__(self,path):
        self.archive=zipfile.ZipFile(path)
        self.sources={r['url']:r for r in json.loads(self.archive.read('sources.json'))}

    def get(self,url):
        if url not in self.sources:raise StopResults('No verified saved page for this URL; no network fallback')
        stamp,state,venue=parse_qs(urlsplit(url).query)['Key'][0].split(',',2)
        date=dt.datetime.strptime(stamp,'%Y%b%d').date().isoformat()
        name=f'official-pages/{state}_{date}_{venue.replace("/","_")}.html'
        try:body=self.archive.read(name).decode('utf-8-sig')
        except KeyError:raise StopResults('Saved result page missing; no network fallback') from None
        if hashlib.sha256(body.encode('utf-8')).hexdigest()!=self.sources[url]['body_sha256']:
            raise StopResults('Saved result page hash does not match its original source record')
        return body

    def close(self):self.archive.close()


def load_lock(folder):
    p=Path(folder)
    payload=json.loads((p/'locked-predictions.json').read_text(encoding='utf-8'))
    if digest(payload)!=(p/'locked-predictions.sha256').read_text().strip():raise ValueError('Locked predictions hash mismatch')
    if not payload.get('locked_utc') or not payload['version'].startswith(('V2-R', 'V3-A-')):
        raise ValueError('A deterministic V2 or V3-A prediction lock is required')
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
            # FF = Failed to Finish; LP = Left at Post (official form key).
            # Explicit Scratched status above continues to take precedence.
            elif finish.upper() in ('F','PU','UR','LR','DNF','TFF','FF','LP'):
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
        scratches={(r['number'],r['name']) for r in results if r['status']=='scratched'}
        confirmed_withdrawals=expected & scratches
        # A confirmed scratch is not an identity mismatch. Every locked active
        # runner must be an exact matched starter or an explicit matched scratch;
        # no unpredicted starter, fuzzy name match or missing runner is allowed.
        field_matches=(expected-confirmed_withdrawals)==actual
        reason=''
        if not results:reason='No verified result available'
        elif len({r['number'] for r in results})!=len(results):reason='Duplicate result runners'
        elif any(r['status']=='unresolved_finish' for r in results):reason='Unsupported finish notation/dead heat; race withheld for review'
        elif not field_matches:reason='Unexplained active-field change or runner identity mismatch; race withheld'
        elif sum(r['finish']==1 for r in starters)!=1:reason='No unique winner; race withheld'
        elif any(r['finish'] is not None and not 1<=r['finish']<=len(starters) for r in starters):reason='Finish outside field size; race withheld'
        # A tied non-winning position does not affect a win-only return. A tied
        # winning position still fails the unique-winner check above.
        if reason:issues.append({'race_id':race_id,'reason':reason})
        indexed={(r['number'],r['name']):r for r in results}
        for runner in field:
            row=dict(runner);row.update({'settlement_status':'unresolved' if reason else 'settled','settlement_note':reason,
                'win':'','finish':'','sp':'','paper_pl':'','settled_stake':0,'market_favourite':False,
                'field_changed_by_confirmed_scratches':bool(confirmed_withdrawals),
                'confirmed_withdrawals':sorted([{'number':n,'name':name} for n,name in confirmed_withdrawals],key=lambda r:r['number'])})
            if not reason:
                result=indexed[(runner['number'],runner['name'])]
                row.update(result)
                if result['status']=='scratched':
                    row.update({'settlement_status':'void','settlement_note':'Explicitly confirmed scratch: original selection retained, zero outlay/return',
                                'win':'','finish':'','sp':'','paper_pl':0 if row['selected'] else ''})
                else:
                    row['win']=int(row['finish']==1)
                    row['settled_stake']=row['stake'] if row['selected'] else 0
                    row['paper_pl']=row['settled_stake']*(row['sp']*row['win']-1) if row['selected'] else ''
                    # Descriptive benchmark flag only; never an assessment input.
                    row['market_favourite']=row['sp']==min(r['sp'] for r in starters)
            rows.append(row)
    settled=[r for r in rows if r['selected'] and r['settlement_status']=='settled']
    staked=sum(r['stake'] for r in settled);ret=sum(r['stake']*r['sp']*r['win'] for r in settled)
    # Dates with unresolved selections remain visible; never silently call this a full-month return.
    summary={'version':payload['version'],'prediction_hash':digest(payload),'rules_hash':payload['config_hash'],
        'selected_total':sum(r['selected'] for r in payload['runners']),'selected_settled':len(settled),
        'selected_unresolved':sum(r['selected'] and r['settlement_status']=='unresolved' for r in rows),
        'selected_void':sum(r['selected'] and r['settlement_status']=='void' for r in rows),
        'void_original_potential_stake':sum(r['stake'] for r in rows if r['selected'] and r['settlement_status']=='void'),
        'settlement_policy':'v1.1: exact confirmed scratches may reduce field; void scratched selections without substitution; non-winning ties accepted; winning dead heats withheld',
        'wins':sum(r['win'] for r in settled),'staked':staked,'gross_return':round(ret,2),
        'paper_profit_loss':round(ret-staked,2),'roi':(ret-staked)/staked if staked else None,
        'win_rate':sum(r['win'] for r in settled)/len(settled) if settled else None,
        'races_unresolved':len(issues),'probabilities':'Not produced by this ranking baseline',
        'price_qualification':'Not applicable: no minimum prices in this ranking model','pre_race_qualification':'Unknown',
        'historical_reconstruction':True}
    running=peak=max_drawdown=0.0;losing=longest=0
    for r in sorted(settled,key=lambda r:(r['date'],r['id'])):
        running+=r['paper_pl'];peak=max(peak,running);max_drawdown=max(max_drawdown,peak-running)
        losing=0 if r['win'] else losing+1;longest=max(longest,losing)
    summary.update({'max_drawdown':round(max_drawdown,2),'longest_losing_sequence':longest,
                    'sequence_note':'Dates then state/meeting/race identity; not actual race-time order'})
    return summary,rows,issues


def settle(folder,output,client=None,parser=None):
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
            path.write_text(body,encoding='utf-8',newline='')
            parsed=(parser or result_page)(body,identity)
            source={'date':date,'state':state,'meeting':venue,'url':url,'retrieved_utc':now(),
                    'body_sha256':hashlib.sha256(body.encode('utf-8')).hexdigest()}
            if isinstance(client,SavedResults) and url in client.sources:
                source['retrieved_utc']=client.sources[url]['retrieved_utc']
                source['replayed_utc']=now()
                source['replayed_from_saved_archive']=True
            requests.append(source)
            for race,entries in parsed.items():
                outcomes[f'{date}|{state}|{venue}|{race}']=[r|{'source_url':url,'retrieved_utc':source['retrieved_utc']} for r in entries]
            save(out/'result-checkpoint.json',outcomes)
    except (StopResults,OSError,ValueError) as exc:error=str(exc)
    summary,rows,issues=summarise(payload,outcomes)
    summary.update({'status':'stopped' if error else 'processed_with_unresolved' if issues else 'processed',
                    'stopped_reason':error,'official_meetings_fetched':len(requests),'created_utc':now(),
                    'result_retrieval_mode':'resume_saved_then_missing_only' if getattr(client,'resuming',False) else 'saved_archive_no_network' if isinstance(client,SavedResults) else 'live_requests',
                    'results_parser_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
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
    (out/'summary.md').write_text('# '+payload['version']+' historical paper results\n\n'+
        'Predictions were loaded from a saved hash-checked lock before results requests. No scores were changed.\n\n'+
        'No minimum-price rule or calibrated probabilities exist in this baseline. Returns use official decimal SP and fixed paper stakes.\n\n'+
        '```json\n'+json.dumps(summary,indent=2)+'\n```\n',encoding='utf-8')
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--locked-folder',required=True);p.add_argument('--output',required=True)
    p.add_argument('--saved-results-zip',help='Optional existing results artifact: offline replay, no requests or fallback')
    a=p.parse_args()
    reader=None
    try:
        reader=SavedResults(a.saved_results_zip) if a.saved_results_zip else None
        result=settle(a.locked_folder,a.output,reader)
        if result['status']=='stopped':p.exit(1,result['stopped_reason']+'\n')
    except (ValueError,OSError) as exc:p.exit(1,str(exc)+'\n')
    finally:
        if reader is not None:reader.close()


if __name__=='__main__':main()
