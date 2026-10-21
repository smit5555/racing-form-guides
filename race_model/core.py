"""Regularised within-race softmax likelihood, with explicit safe features."""
import hashlib
import json
import math
import re
import statistics

import numpy as np

VERSION = 'RACE-P1-development'
FEATURES = ('recent_finish','recent_margin','distance','going','log_days',
            'weight_delta','handicap_rating','benchmark_step','barrier_fraction')
FEATURE_NAMES = list(FEATURES)+[name+'_missing' for name in FEATURES]
# Fixed before fitting. No threshold/regularisation search is performed.
SPEC = dict(version=VERSION,features=FEATURE_NAMES,l2=0.1,learning_rate=0.5,
            iterations=1200,gradient_tolerance=1e-7,train_end='2026-08-31',
            validation_start='2026-09-01',validation_end='2026-09-30',seed=0)


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')


def digest(value):return hashlib.sha256(canonical(value)).hexdigest()


def bm(text):
    match=re.search(r'\b(?:BM|BENCHMARK)\s*(\d{2,3})\b',text,re.I)
    return int(match[1]) if match else None


def numeric(text):
    text=str(text).strip()
    return float(text) if re.fullmatch(r'\d+(?:\.\d+)?',text) else None


def encode(row, race):
    """Allowlist only: result, current SP and winner labels are never inspected."""
    raw=next(r for r in race['runners'] if r['number']==row['number'] and r['name']==row['name'])
    rating=numeric(raw['field_values'].get('hcp',''))
    if rating is not None and not 1<=rating<=200:rating=None
    current=bm(race['title'])
    prior=[bm(h['evidence']) for h in row['history'][:3]]
    prior=[v for v in prior if v is not None]
    step=(statistics.median(prior)-current)/20 if current is not None and prior else None
    barrier=numeric(row['barrier'])
    field=[r for r in race['runners'] if not r['scratched']]
    max_barrier=max([numeric(r['field_values'].get('barrier','')) or 0 for r in field]+[1])
    days=row['days_since_start']
    delta=row['weight_change_benefit_kg']
    values=[row['factors'][k] for k in FEATURES[:4]]+[
        min(math.log1p(days)/math.log(91),2) if days is not None else None,
        max(-3,min(3,delta/5)) if delta is not None else None,
        rating/20 if rating is not None else None,
        max(-3,min(3,step)) if step is not None else None,
        barrier/max_barrier if barrier is not None else None]
    if any(v is not None and not math.isfinite(v) for v in values):raise ValueError('Non-finite feature')
    # Missing values have their own indicator; zero is not silently treated as known.
    return [0.0 if v is None else v for v in values]+[float(v is None) for v in values]


def validate(records):
    ids=set()
    for race in records:
        if race['id'] in ids:raise ValueError('Duplicate race')
        ids.add(race['id'])
        runners=race['runners']
        if len(runners)<2 or len({r['number'] for r in runners})!=len(runners):raise ValueError('Invalid field')
        if any(type(r['win']) is not int or r['win'] not in (0,1) for r in runners):raise ValueError('Invalid labels')
        if sum(r['win'] for r in runners)!=1:raise ValueError('Race requires one verified winner')
        if any(len(r['x'])!=len(FEATURE_NAMES) or not all(math.isfinite(v) for v in r['x']) for r in runners):raise ValueError('Invalid features')


def matrix(records, scales=None):
    validate(records)
    x=np.asarray([r['x'] for race in records for r in race['runners']],dtype=float)
    y=np.asarray([r['win'] for race in records for r in race['runners']],dtype=float)
    counts=np.array([len(r['runners']) for r in records],dtype=int)
    starts=np.r_[0,np.cumsum(counts)[:-1]]
    groups=np.repeat(np.arange(len(counts)),counts)
    # Centre within each field; all shared race constants cancel from the likelihood.
    means=np.add.reduceat(x,starts,axis=0)/counts[:,None]
    x=x-means[groups]
    if scales is None:scales=np.maximum(np.sqrt(np.mean(x*x,axis=0)),0.05)
    return x/np.asarray(scales),y,starts,groups,scales


def probabilities(x, weights, starts, groups):
    utility=x@weights
    maximum=np.maximum.reduceat(utility,starts)
    exponent=np.exp(utility-maximum[groups])
    return exponent/np.add.reduceat(exponent,starts)[groups]


def objective(weights,x,y,starts,groups,l2):
    utility=x@weights
    maximum=np.maximum.reduceat(utility,starts)
    log_denom=maximum+np.log(np.add.reduceat(np.exp(utility-maximum[groups]),starts))
    n=len(starts)
    loss=float((log_denom.sum()-utility@y)/n+l2/2*(weights@weights))
    p=probabilities(x,weights,starts,groups)
    grad=x.T@(p-y)/n+l2*weights
    return loss,grad


def fit(records,spec=None):
    spec=dict(SPEC if spec is None else spec)
    x,y,starts,groups,scales=matrix(records)
    weights=np.zeros(x.shape[1]);trace=[]
    for iteration in range(spec['iterations']):
        loss,grad=objective(weights,x,y,starts,groups,spec['l2'])
        norm=float(np.linalg.norm(grad))
        trace.append(loss)
        if norm<spec['gradient_tolerance']:break
        step=spec['learning_rate']
        for _ in range(30):
            candidate=weights-step*grad
            new_loss,_=objective(candidate,x,y,starts,groups,spec['l2'])
            if new_loss<=loss-1e-4*step*float(grad@grad):break
            step/=2
        else:raise ValueError('Optimizer failed to find a decreasing step')
        weights=candidate
    final_loss,final_grad=objective(weights,x,y,starts,groups,spec['l2'])
    if not np.isfinite(final_loss):raise ValueError('Non-finite fitted model')
    return dict(version=VERSION,spec=spec,weights=weights.tolist(),scales=np.asarray(scales).tolist(),
        training_races=len(records),training_loss=final_loss,iterations=iteration+1,
        gradient_norm=float(np.linalg.norm(final_grad)),converged=float(np.linalg.norm(final_grad))<spec['gradient_tolerance'],
        initial_loss=trace[0],features=FEATURE_NAMES)


def predict(model, records):
    if model['features']!=FEATURE_NAMES:raise ValueError('Unknown feature schema')
    x,_,starts,groups,_=matrix(records,model['scales'])
    return probabilities(x,np.asarray(model['weights']),starts,groups)


def evaluate(model,records):
    probs=predict(model,records)
    nll=brier=top_wins=v2_wins=model_only=v2_only=0
    calibration=[dict(lower=i/10,upper=(i+1)/10,n=0,prob_sum=0.0,wins=0) for i in range(10)]
    picks=[];cursor=0
    for race in records:
        field=race['runners'];n=len(field);p=probs[cursor:cursor+n];cursor+=n
        y=np.array([r['win'] for r in field])
        nll-=math.log(max(float(p@y),1e-300))
        # Multiclass Brier score: sum over runners, then mean across races.
        brier+=float(np.sum((p-y)**2))
        top=min(range(n),key=lambda i:(-p[i],field[i]['number']))
        baseline=min(field,key=lambda r:r['v2_rank'])
        top_wins+=field[top]['win'];v2_wins+=baseline['win']
        model_only+=int(bool(field[top]['win']) and not baseline['win'])
        v2_only+=int(bool(baseline['win']) and not field[top]['win'])
        ordered=sorted(range(n),key=lambda i:(-p[i],field[i]['number']))
        for rank,i in enumerate(ordered,1):
            runner=field[i];prob=float(p[i]);cell=calibration[min(int(prob*10),9)]
            cell['n']+=1;cell['prob_sum']+=prob;cell['wins']+=runner['win']
            picks.append(dict(date=race['date'],race_id=race['id'],number=runner['number'],name=runner['name'],
                model_rank=rank,probability=prob,v2_rank=runner['v2_rank'],win=runner['win']))
    count=len(records)
    for c in calibration:
        c['mean_probability']=c['prob_sum']/c['n'] if c['n'] else None
        c['observed_win_rate']=c['wins']/c['n'] if c['n'] else None
    return dict(races=count,runners=len(picks),model_top_wins=top_wins,model_top_win_rate=top_wins/count,
        v2_top_wins=v2_wins,v2_top_win_rate=v2_wins/count,
        paired_model_only_wins=model_only,paired_v2_only_wins=v2_only,
        uniform_expected_top_wins=sum(1/len(r['runners']) for r in records),
        race_log_loss=nll/count,uniform_log_loss=sum(math.log(len(r['runners'])) for r in records)/count,
        multiclass_brier=brier/count,uniform_multiclass_brier=sum(1-1/len(r['runners']) for r in records)/count,
        calibration=calibration),picks
