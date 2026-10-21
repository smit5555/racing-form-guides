"""Apply a frozen model to saved guides; no results access, training or price rules."""
import argparse
import copy
import datetime as dt
import hashlib
import json
import os
import shutil
from pathlib import Path

import numpy as np

from rules_v2.run import assess as baseline_assess, csv_file, now
from .core import FEATURE_NAMES, VERSION, canonical, digest, encode


def load_model(folder):
    folder=Path(folder)
    model=json.loads((folder/'model.json').read_text(encoding='utf-8'))
    if digest(model)!=(folder/'model.sha256').read_text().strip():raise ValueError('Frozen model hash mismatch')
    if model['version']!=VERSION or model['features']!=FEATURE_NAMES:raise ValueError('Unexpected model schema')
    if model['source_code_sha256']!=hashlib.sha256(Path(__file__).with_name('core.py').read_bytes()).hexdigest():
        raise ValueError('Installed feature encoder differs from the frozen training code')
    weights=np.asarray(model['weights'],dtype=float);scales=np.asarray(model['scales'],dtype=float)
    if len(weights)!=len(FEATURE_NAMES) or len(scales)!=len(weights) or not np.isfinite(weights).all() or not np.isfinite(scales).all() or (scales<=0).any():raise ValueError('Invalid model parameters')
    if not model['converged']:raise ValueError('Refusing an unconverged training model')
    return model


def apply_model(baseline,model):
    payload=copy.deepcopy(baseline)
    by_race={}
    for row in payload['runners']:
        row.update(v2_rank=row['rank'],v2_score=row['score'],selected=False,stake=0,probability=None,rank=None,
                   min_price='',pre_race_qualification='Unknown')
        by_race.setdefault(row['id'],[]).append(row)
    for race in payload['races']:
        rows=by_race.get(race['id'],[])
        if race['exclusions']:continue
        expected={(r['number'],r['name']) for r in race['runners'] if not r['scratched']}
        if expected!={(r['number'],r['name']) for r in rows}:raise ValueError('Incomplete prediction field')
        x=np.asarray([encode(r,race) for r in rows],dtype=float)
        x=(x-x.mean(axis=0))/np.asarray(model['scales'])
        utilities=x@np.asarray(model['weights']);exponent=np.exp(utilities-utilities.max());p=exponent/exponent.sum()
        if not np.isfinite(p).all() or abs(float(p.sum())-1)>1e-12:raise ValueError('Invalid race probabilities')
        ordered=sorted(range(len(rows)),key=lambda i:(-p[i],rows[i]['number']))
        for rank,i in enumerate(ordered,1):
            row=rows[i]
            row.update(rank=rank,probability=float(p[i]),selected=rank==1,candidate=rank==1,
                       reasons=[] if rank==1 else ['Not top ranked by frozen probability model'])
    earlier=any(r['date']<=model['training_end'] for r in payload['races'])
    payload.update(version=VERSION,created_utc=now(),locked_utc=now(),model=model,model_sha256=digest(model),
        config={'version':VERSION,'model_sha256':digest(model),'policy':'Top pick per eligible race; zero stakes; no price gate'},
        purpose='historical replay: dates precede or overlap training' if earlier else 'later-than-training assessment; untouched outcomes not independently verified',
        earlier_than_training=earlier,guide_run_id=os.environ.get('GUIDE_RUN_ID','local'),
        github_run_id=os.environ.get('GITHUB_RUN_ID','local'),git_commit=os.environ.get('GITHUB_SHA','local'),
        assessment_code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        probabilities_note='Estimated chances, not independently calibrated or proven; no bets or minimum prices.')
    payload['config_hash']=digest(payload['config'])
    return payload


def load_predictions(folder):
    folder=Path(folder)
    payload=json.loads((folder/'locked-predictions.json').read_text(encoding='utf-8'))
    if digest(payload)!=(folder/'locked-predictions.sha256').read_text().strip():raise ValueError('Prediction lock hash mismatch')
    if payload['version']!=VERSION or payload['model_sha256']!=digest(payload['model']) or payload['config_hash']!=digest(payload['config']):raise ValueError('Model/config identity mismatch')
    stamp=dt.datetime.fromisoformat(payload['locked_utc'])
    if stamp.tzinfo is None or stamp>dt.datetime.now(dt.timezone.utc):raise ValueError('Invalid lock timestamp')
    if any(r['stake']!=0 for r in payload['runners']):raise ValueError('This experiment has zero betting stakes')
    return payload


def run(guides,model_folder,start,end,output):
    model=load_model(model_folder)
    out=Path(output);out.mkdir(parents=True,exist_ok=False)
    baseline=baseline_assess(guides,start,end,out/'v2-reference')
    for source in Path(guides).rglob('*'):
        if source.is_file() and (source.name.endswith('manifest.json') or source.name.endswith('summary.txt')):
            target=out/'download-reports'/source.relative_to(Path(guides))
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
    payload=apply_model(baseline,model)
    destination=out/'probability-lock';destination.mkdir()
    (destination/'locked-predictions.json').write_bytes(canonical(payload))
    (destination/'locked-predictions.sha256').write_text(digest(payload)+'\n',encoding='ascii')
    fields=['date','state','meeting','race','number','name','rank','probability','v2_rank','v2_score','selected','stake','race_eligible','race_exclusions','guide','citation']
    csv_file(destination/'all-runners.csv',payload['runners'],fields)
    top=[r for r in payload['runners'] if r['selected']]
    csv_file(destination/'top-picks.csv',top,fields)
    text=['# Frozen probability model: saved-guide assessment','',payload['purpose'],
          f"Guides: {len(payload['sources'])}; parsed races: {len(payload['races'])}; top picks: {len(top)}.",
          f"Model training run: {model['github_run_id']}; guide run: {payload['guide_run_id']}.",
          'Top picks are research predictions with zero stakes. No bets, fair-price threshold or profitability claim.',
          'Prediction lock is published before optional official results retrieval.',
          'Read v2-reference/coverage.json and issues.json: source coverage is not proof of all meetings.',
          'Download run may be incomplete. Saved guides can be assessed while missing meetings remain explicitly unknown.']
    (out/'READ_FIRST.md').write_text('\n'.join(text)+'\n',encoding='utf-8')
    return payload


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('guides','model-folder','start-date','end-date','output'):p.add_argument('--'+name,required=True)
    a=p.parse_args()
    try:run(a.guides,a.model_folder,dt.date.fromisoformat(a.start_date),dt.date.fromisoformat(a.end_date),a.output)
    except (ValueError,OSError,KeyError) as exc:p.exit(1,str(exc)+'\n')


if __name__=='__main__':main()
