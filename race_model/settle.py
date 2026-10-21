"""Evaluate immutable probability locks; official results fetched once, sequentially."""
import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
from urllib.parse import quote

from rules_v2.results import Client, SavedResults, StopResults, result_page, summarise
from rules_v2.run import csv_file, now
from .assess import load_predictions
from .core import canonical, digest


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


def run(folder,output,client=None):
    payload=load_predictions(folder)  # Validate before constructing any network client.
    out=Path(output);out.mkdir(parents=True,exist_ok=False);(out/'official-pages').mkdir()
    client=client or Client();outcomes={};sources=[];error=None
    meetings={(r['date'],r['state'],r['meeting']):r for r in payload['races'] if not r['exclusions']}
    try:
        for (date,state,venue),identity in sorted(meetings.items()):
            day=dt.date.fromisoformat(date);key=f'{day.year}{day.strftime("%b")}{day.day:02d},{state},{venue}'
            url='https://racingaustralia.horse/FreeFields/Results.aspx?Key='+quote(key,safe='')
            body=client.get(url)
            path=out/'official-pages'/f'{state}_{date}_{venue.replace("/","_")}.html'
            path.write_text(body,encoding='utf-8',newline='')
            entries=result_page(body,identity)
            source=dict(date=date,state=state,meeting=venue,url=url,retrieved_utc=now(),body_sha256=hashlib.sha256(body.encode()).hexdigest())
            if isinstance(client,SavedResults):
                source['retrieved_utc']=client.sources[url]['retrieved_utc'];source['replayed_utc']=now()
            sources.append(source)
            for race,rows in entries.items():outcomes[f'{date}|{state}|{venue}|{race}']=[r|{'source_url':url,'retrieved_utc':source['retrieved_utc']} for r in rows]
            (out/'checkpoint.json').write_bytes(canonical(outcomes))
    except (StopResults,ValueError,OSError) as exc:error=str(exc)
    _,rows,issues=summarise(payload,outcomes)
    summary=metrics(payload,rows)
    summary.update(status='stopped' if error else 'processed_with_unresolved' if issues else 'processed',stopped_reason=error,
        official_meetings_fetched=len(sources),result_mode='saved_archive_no_network' if isinstance(client,SavedResults) else 'live_requests',created_utc=now())
    for name,value in (('summary.json',summary),('unresolved.json',issues),('sources.json',sources)):(out/name).write_bytes(canonical(value))
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
    p.add_argument('--saved-results-zip')
    a=p.parse_args();reader=None
    try:
        if a.saved_results_zip:reader=SavedResults(a.saved_results_zip)
        run(a.locked_folder,a.output,reader)
    except (ValueError,OSError,KeyError) as exc:p.exit(1,str(exc)+'\n')
    finally:
        if reader is not None:reader.close()


if __name__=='__main__':main()
