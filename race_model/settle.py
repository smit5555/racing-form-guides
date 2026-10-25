"""Evaluate immutable probability locks; official results fetched once, sequentially."""
import argparse
import datetime as dt
import hashlib
import json
import math
import re
from pathlib import Path
from urllib.parse import quote

from rules_v2.results import Client, SavedResults, StopResults, result_page, summarise
from rules_v2.guides import Tree
from rules_v2.run import csv_file, now
from .assess import load_predictions
from .core import canonical, digest


BLOCK_MARKERS=('captcha','access denied','verify you are human','checking your browser','cf-chl-',
               'unusual traffic','request blocked','just a moment','incapsula','challenge-platform',
               'enable javascript and cookies')

def abandoned_notice(body):
    text=Tree(body).root.text()
    return (bool(re.search(r'Total Number of starters for this meeting \(including emergencies\)\s*0\b',text,re.I))
            and bool(re.search(r'\bMeeting Abandoned\.',text,re.I)))

def abandoned_race_numbers(body,parsed):
    """Read explicit per-race markers only after parse_result verifies identity."""
    current=None;abandoned=set()
    for node in Tree(body).root.walk():
        if node.tag=='table' and node.has('race-title'):
            match=re.match(r'Race\s+(\d+)\b',node.text())
            current=int(match[1]) if match else None
        elif node.tag=='div' and node.has('Scratched') and node.text().strip()=='Race Abandoned.':
            if current is None:raise StopResults('Abandonment marker without race identity')
            if current in parsed:raise StopResults('Race has both results and abandonment marker')
            abandoned.add(current)
    return abandoned


def parse_result(body,identity):
    """Only a verified matching meeting's explicit unavailable notice is recoverable."""
    if any(marker in body.lower() for marker in BLOCK_MARKERS):
        raise StopResults('CAPTCHA/block detected in result page; no further meetings')
    try:return result_page(body,identity),False
    except StopResults as exc:
        # result_page checks heading, date and the page's own state/venue Key
        # before reaching this specific no-table error. Other failures still stop.
        if (str(exc)=='No recognised official result tables'
            and ('results for this meeting are not currently available' in body.lower() or abandoned_notice(body))
            and Tree(body).root.find('race-title') is None):
            return {},True
        raise


class ResumeResults(SavedResults):
    """Reuse verified archive pages, then request only meetings not in the archive."""
    def __init__(self,path,meetings):
        super().__init__(path)
        self.live=Client()
        archive_sha=hashlib.sha256(Path(path).read_bytes()).hexdigest()
        try:
            if 'summary.json' in self.archive.namelist():
                prior=json.loads(self.archive.read('summary.json'))
                reason=prior.get('stopped_reason')
                if prior.get('status')=='stopped' and reason!='No recognised official result tables':
                    raise ValueError('Saved run stopped on an access/parser error; inspect it before any resume request')
            for (date,state,venue),identity in meetings.items():
                day=dt.date.fromisoformat(date)
                key=f'{day.year}{day.strftime("%b")}{day.day:02d},{state},{venue}'
                url='https://racingaustralia.horse/FreeFields/Results.aspx?Key='+quote(key,safe='')
                name=f'official-pages/{state}_{date}_{venue.replace("/","_")}.html'
                if url in self.sources or name not in self.archive.namelist():continue
                # Older code wrote the failed page before recording source metadata.
                # Accept that extra page ONLY if identity and explicit unavailable
                # status pass inspection. Never silently refetch an unknown/block page.
                body=self.archive.read(name).decode('utf-8-sig')
                _,unavailable=parse_result(body,identity)
                if not unavailable:raise ValueError('Unindexed saved page needs review before resume')
                self.sources[url]=dict(url=url,date=date,state=state,meeting=venue,retrieved_utc=None,
                    body_sha256=hashlib.sha256(body.encode()).hexdigest(),source_status='explicit_unavailable',
                    recovered_from_archive_sha256=archive_sha,original_source_manifest_entry_missing=True,
                    recovery_verified_utc=now())
        except Exception:
            self.close();raise

    def get(self,url):
        if url in self.sources:return super().get(url)
        return self.live.get(url)


def metrics(payload,rows):
    grouped={}
    for row in rows:
        if row['race_eligible']:grouped.setdefault(row['id'],[]).append(row)
    top=[r for r in rows if r['selected'] and r['settlement_status']=='settled']
    pairs=[];loss=baseline_loss=brier=0.0;bins=[{'lower':i/10,'n':0,'prob_sum':0.0,'wins':0} for i in range(10)]
    for field in grouped.values():
        # Do not use post-lock scratches to renormalise pre-lock probabilities.
        if any(r['settlement_status']!='settled' for r in field):continue
        if sum(r['win'] for r in field)!=1:continue
        winner=next(r for r in field if r['win'])
        loss-=math.log(max(winner['probability'],1e-300));baseline_loss+=math.log(len(field))
        brier+=sum((r['probability']-r['win'])**2 for r in field)
        model_top=next(r for r in field if r['rank']==1)
        v2_top=next(r for r in field if r['v2_rank']==1)
        pairs.append((model_top['win'],v2_top['win']))
        for r in field:
            b=bins[min(int(r['probability']*10),9)];b['n']+=1;b['prob_sum']+=r['probability'];b['wins']+=r['win']
    n=len(pairs)
    for b in bins:
        b.update(mean_probability=b['prob_sum']/b['n'] if b['n'] else None,observed_win_rate=b['wins']/b['n'] if b['n'] else None)
    return dict(model_sha256=payload['model_sha256'],prediction_sha256=digest(payload),purpose=payload['purpose'],
        model_training_run=payload['model']['github_run_id'],guide_run=payload['guide_run_id'],
        top_picks_settled=len(top),top_pick_wins=sum(r['win'] for r in top),
        top_pick_win_rate=sum(r['win'] for r in top)/len(top) if top else None,
        top_picks_void=sum(r['selected'] and r['settlement_status']=='void' for r in rows),
        top_picks_unresolved=sum(r['selected'] and r['settlement_status']=='unresolved' for r in rows),
        matched_full_field_races=n,model_matched_wins=sum(a for a,b in pairs),v2_matched_wins=sum(b for a,b in pairs),
        model_matched_win_rate=sum(a for a,b in pairs)/n if n else None,v2_matched_win_rate=sum(b for a,b in pairs)/n if n else None,
        paired_model_only_wins=sum(a and not b for a,b in pairs),paired_v2_only_wins=sum(b and not a for a,b in pairs),
        race_log_loss=loss/n if n else None,uniform_log_loss=baseline_loss/n if n else None,multiclass_brier=brier/n if n else None,
        calibration=bins,betting_policy='No bets or stake allocation. Official SP is settlement evidence only, not an assessment feature.')


def run(folder,output,client=None,resume_zip=None):
    payload=load_predictions(folder)  # Validate before constructing any network client.
    out=Path(output);out.mkdir(parents=True,exist_ok=False);(out/'official-pages').mkdir()
    outcomes={};sources=[];error=None;unavailable=[];abandoned=[];abandoned_races=[];cached_count=0;fresh_count=0
    meetings={(r['date'],r['state'],r['meeting']):r for r in payload['races'] if not r['exclusions']}
    if resume_zip is not None and client is not None:raise ValueError('Choose a client or resume archive')
    client=client or (ResumeResults(resume_zip,meetings) if resume_zip is not None else Client())
    try:
        for (date,state,venue),identity in sorted(meetings.items()):
            day=dt.date.fromisoformat(date);key=f'{day.year}{day.strftime("%b")}{day.day:02d},{state},{venue}'
            url='https://racingaustralia.horse/FreeFields/Results.aspx?Key='+quote(key,safe='')
            cached=isinstance(client,SavedResults) and url in client.sources
            if not cached:fresh_count+=1
            body=client.get(url)
            path=out/'official-pages'/f'{state}_{date}_{venue.replace("/","_")}.html'
            path.write_text(body,encoding='utf-8',newline='')
            source=dict(date=date,state=state,meeting=venue,url=url,retrieved_utc=now(),body_sha256=hashlib.sha256(body.encode()).hexdigest())
            if isinstance(client,SavedResults) and url in client.sources:
                source['retrieved_utc']=client.sources[url]['retrieved_utc'];source['replayed_utc']=now()
                for k in ('recovered_from_archive_sha256','original_source_manifest_entry_missing','recovery_verified_utc'):
                    if k in client.sources[url]:source[k]=client.sources[url][k]
                source['replayed_from_saved_archive']=True;cached_count+=1
            # Preserve provenance even if parsing this page stops the run.
            sources.append(source)
            entries,is_unavailable=parse_result(body,identity)
            for race_number in sorted(abandoned_race_numbers(body,entries)):
                abandoned_races.append(dict(date=date,state=state,meeting=venue,race=race_number,
                    race_id=f'{date}|{state}|{venue}|{race_number}',url=url,
                    reason='Explicit Race Abandoned marker under verified race heading'))
            if is_unavailable and abandoned_notice(body):
                source['source_status']='explicit_abandoned'
                abandoned.append(dict(date=date,state=state,meeting=venue,url=url,
                    reason='Matching official page explicitly states meeting abandoned and zero starters'))
                continue
            if is_unavailable:
                source['source_status']='explicit_unavailable'
                unavailable.append(dict(date=date,state=state,meeting=venue,url=url,
                    reason='Verified meeting page explicitly says results are not currently available; outcomes remain unknown'))
                continue
            source['source_status']='parsed'
            for race,rows in entries.items():outcomes[f'{date}|{state}|{venue}|{race}']=[r|{'source_url':url,'retrieved_utc':source['retrieved_utc']} for r in rows]
            (out/'checkpoint.json').write_bytes(canonical(outcomes))
    except (StopResults,ValueError,OSError) as exc:error=str(exc)
    finally:
        if resume_zip is not None:client.close()
    _,rows,issues=summarise(payload,outcomes)
    void_ids={r['id'] for r in payload['races'] if any(
        (r['date'],r['state'],r['meeting'])==(m['date'],m['state'],m['meeting']) for m in abandoned)}
    void_ids.update(r['race_id'] for r in abandoned_races)
    issues=[issue for issue in issues if issue['race_id'] not in void_ids]
    for row in rows:
        if row['id'] in void_ids:
            row.update(settlement_status='void',settlement_note='Official meeting or individual race explicitly abandoned',
                       win='',finish='',sp='',paper_pl=0 if row['selected'] else '',settled_stake=0)
    summary=metrics(payload,rows)
    summary.update(status='stopped' if error else 'processed_with_unresolved' if issues else 'processed',stopped_reason=error,
        official_meetings_fetched=len(sources),cached_meetings_reused=cached_count,new_meetings_requested=fresh_count,
        meetings_explicitly_unavailable=len(unavailable),
        meetings_explicitly_abandoned=len(abandoned),
        races_explicitly_abandoned=len(abandoned_races),
        result_mode='resume_saved_then_missing_only' if resume_zip is not None else 'saved_archive_no_network' if isinstance(client,SavedResults) else 'live_requests',created_utc=now())
    for name,value in (('summary.json',summary),('unresolved.json',issues),('sources.json',sources),('unavailable-meetings.json',unavailable),('abandoned-meetings.json',abandoned),('abandoned-races.json',abandoned_races)):(out/name).write_bytes(canonical(value))
    if rows:
        fields=list(dict.fromkeys(k for row in rows for k in row))
        csv_file(out/'research-data.csv',rows,fields)
        csv_file(out/'top-pick-results.csv',[r for r in rows if r['selected']],fields)
    def percent(v):return 'unavailable' if v is None else f'{v:.1%}'
    text=['# Frozen probability model: results','',payload['purpose'],
        f"Full matched fields without post-lock withdrawals: {summary['matched_full_field_races']}.",
        f"Model top-pick win rate on matched fields: {percent(summary['model_matched_win_rate'])}.",
        f"V2 top-pick win rate on those same fields: {percent(summary['v2_matched_win_rate'])}.",
        f"Top picks unresolved: {summary['top_picks_unresolved']}; void: {summary['top_picks_void']}.",
        'These are winner/probability diagnostics, not a betting shortlist or proof of profit.',
        'Only original full fields without post-lock withdrawals enter paired accuracy/probability diagnostics.',
        'Predictions were loaded from the published lock. No retraining, rescoring or replacement picks occurred.',
        'Read summary.json and unresolved.json for coverage and stopping details.']
    (out/'START_HERE.md').write_text('\n'.join(text)+'\n',encoding='utf-8')
    if error:raise ValueError(error)
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--locked-folder',required=True);p.add_argument('--output',required=True)
    archive=p.add_mutually_exclusive_group()
    archive.add_argument('--saved-results-zip')
    archive.add_argument('--resume-results-zip',help='Reuse saved pages and request only meetings absent from this archive')
    a=p.parse_args();reader=None
    try:
        if a.saved_results_zip:reader=SavedResults(a.saved_results_zip)
        run(a.locked_folder,a.output,reader,a.resume_results_zip)
    except (ValueError,OSError,KeyError,StopResults) as exc:p.exit(1,str(exc)+'\n')
    finally:
        if reader is not None:reader.close()


if __name__=='__main__':main()
