"""Explicit features and fixed baseline rules. A score is NOT a probability."""
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import re

from .guides import dated_form


def canonical(obj):
    return json.dumps(obj,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')


def digest(obj): return hashlib.sha256(canonical(obj)).hexdigest()


def config_at(path=None):
    c=json.loads(Path(path or Path(__file__).with_name('config.json')).read_text(encoding='utf-8'))
    if set(c['weights'])!={'recent_finish','recent_margin','distance','going','recency','weight_change'} or sum(c['weights'].values())!=100:
        raise ValueError('Feature weights must total 100')
    if any(type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in c['weights'].values()):
        raise ValueError('Invalid feature weights')
    if c['probabilities_enabled'] or c['minimum_prices_enabled']:
        raise ValueError('This baseline does not estimate probabilities or minimum prices')
    if not 0<=c['minimum_score']<=100 or not 0<c['minimum_score_lead']<=100:
        raise ValueError('Invalid selection thresholds')
    if c['stake']!=10 or c['daily_cap']!=275: raise ValueError('Baseline uses $10 stakes and a $275 daily cap')
    if c['recent_starts'] not in (2,3,4,5) or c['minimum_prior_completed_starts']<2:
        raise ValueError('Need at least two completed prior starts')
    return c


def history_row(text,target):
    dates=list(dated_form(text))
    if len(dates)!=1 or dates[0]>=target: return None
    # Trials/jump-outs must never be treated as completed race starts.
    if re.search(r'\b(?:TRL|TRIAL|JUMP.?OUT)\b',text,re.I) or re.match(r'^\s*[TJ]\s*\d',text): return None
    position=re.match(r'^\s*(\d+)\s+of\s+(\d+)\b',text)
    distance=re.search(r'\b(\d{3,4})m\b',text)
    if not position or not distance: return None
    finish,size=map(int,position.groups())
    if not 1<=finish<=size or size<2: return None
    going=re.search(r'\b(Firm|Good|Soft|Heavy|Synthetic)\s*\d?\b',text,re.I)
    # The runner's own weight is before Barrier and before opponent placings.
    own=re.search(r'(\d+(?:\.\d+)?)kg\s+(?:\(cd\s*(\d+(?:\.\d+)?)kg\)\s*)?Barrier',text)
    weight=float(own[2] or own[1]) if own else None
    # Explicit comma-delimited beaten margin only; never an opponent's weight/time.
    margin=re.search(r',\s*(\d+(?:\.\d+)?)L\s*[,;]',text)
    margin=0.0 if finish==1 else float(margin[1]) if margin else None
    if margin is not None and margin<0: return None
    return {'date':dates[0].isoformat(),'finish':finish,'field_size':size,
            'finish_percentile':(size-finish)/(size-1),'distance':int(distance[1]),
            'going':going[1].lower() if going else None,'weight':weight,'margin':margin,
            'evidence':text}


def weighted(values):
    available=[(v,w) for v,w in zip(values,(.5,.3,.2,.1,.05)) if v is not None]
    return sum(v*w for v,w in available)/sum(w for _,w in available) if available else None


def recency_score(days):
    if days<7:return .5
    if days<14:return .8
    if days<=35:return 1.0
    if days<=60:return .7
    if days<=90:return .4
    return .2


def features(race,runner,c):
    target=dt.date.fromisoformat(race['date'])
    history=[r for text in runner['history_text'] if (r:=history_row(text,target)) is not None]
    history.sort(key=lambda r:r['date'],reverse=True)
    # Duplicate starts can be an extraction problem, not extra supporting evidence.
    if len({(r['date'],r['distance']) for r in history})!=len(history):
        raise ValueError('Duplicate dated starts')
    distance=re.search(r'\((\d+)\s+METRES\)',race['title'],re.I)
    distance=int(distance[1]) if distance else None
    ground=re.search(r'Track Condition:\s*(Firm|Good|Soft|Heavy|Synthetic)\b',race['conditions'],re.I)
    ground=ground[1].lower() if ground else None
    near=[r for r in history if distance and abs(r['distance']-distance)/distance<=c['distance_tolerance']]
    same=[r for r in history if ground and r['going']==ground]
    last=history[:c['recent_starts']]
    days=(target-dt.date.fromisoformat(history[0]['date'])).days if history else None
    weight_text=runner['field_values'].get('weight','')
    claim_text=runner['field_values'].get('jockey','')
    kg=re.fullmatch(r'\s*(\d+(?:\.\d+)?)kg\s*',weight_text)
    # Do not guess an apprentice's net weight: leave this feature unknown.
    claim=bool(re.search(r'\ba\s*\d|\(\s*a|\bclaim|\bcd\b',claim_text,re.I))
    current_weight=float(kg[1]) if kg and not claim else None
    delta=history[0]['weight']-current_weight if history and history[0]['weight'] is not None and current_weight is not None else None
    factors={'recent_finish':weighted([r['finish_percentile'] for r in last]),
        'recent_margin':weighted([max(0,1-r['margin']/c['margin_cap_lengths']) if r['margin'] is not None else None for r in last]),
        'distance':weighted([r['finish_percentile'] for r in near[:3]]),
        'going':weighted([r['finish_percentile'] for r in same[:3]]),
        'recency':recency_score(days) if days is not None else None,
        'weight_change':max(0,min(1,.5+.1*delta)) if delta is not None else None}
    score=sum(c['weights'][k]*(v if v is not None else c['unknown_factor_score']) for k,v in factors.items())
    known=sum(c['weights'][k] for k,v in factors.items() if v is not None)
    return {'factors':factors,'score':round(score,6),'known_weight':known,
        'prior_completed_starts':len(history),'distance_starts':len(near),'going_starts':len(same),
        'days_since_start':days,'distance_metres':distance,'going':ground,'declared_weight':current_weight,
        'weight_change_benefit_kg':delta,'barrier':runner['field_values'].get('barrier',''),
        'trainer':runner['field_values'].get('trainer',''),'jockey':claim_text,
        'history':history,'missing_factors':[k for k,v in factors.items() if v is None]}


def score_races(races,c):
    rows=[]
    for race in races:
        if c['exclude_jumps'] and re.search(r'\bhurdle\b|\bsteeple\w*\b',race['title'],re.I):
            race['exclusions'].append('Jumps race excluded from baseline')
        field=[]
        for runner in race['runners']:
            if runner['scratched']: continue
            try: f=features(race,runner,c)
            except ValueError as exc:
                race['exclusions'].append(f'#{runner["number"]}: {exc}')
                continue
            if f['prior_completed_starts']<c['minimum_prior_completed_starts']:
                race['exclusions'].append(f'#{runner["number"]}: fewer than two completed prior starts')
            field.append({k:race[k] for k in ('id','date','state','meeting','race','guide','guide_sha256','title')} |
                {'number':runner['number'],'name':runner['name'],**f,'selected':False,'stake':0,
                 'candidate':False,'reasons':[],'probability':'','min_price':'','sp':'','result':'','bet':'',
                 'pre_race_qualification':'Unknown','citation':f'{race["guide"]}#Race{race["race"]}; field and detailed form #{runner["number"]}'})
        race['exclusions']=list(dict.fromkeys(race['exclusions']))
        ranked=sorted(field,key=lambda r:(-r['score'],r['number']))
        for n,row in enumerate(ranked):
            row['rank']=n+1
            row['score_lead']=row['score']-ranked[1]['score'] if n==0 and len(ranked)>1 else ''
            row['race_eligible']=not race['exclusions']
            row['race_exclusions']=list(race['exclusions'])
            reasons=list(race['exclusions'])
            if n!=0:reasons.append('Not top ranked')
            if row['score']<c['minimum_score']:reasons.append('Score below threshold')
            if n==0 and (len(ranked)<2 or row['score_lead']<c['minimum_score_lead']):reasons.append('Score lead below threshold')
            if row['known_weight']<c['minimum_known_weight']:reasons.append('Insufficient known factor weight')
            if row['distance_starts']<c['minimum_distance_starts_for_selection']:reasons.append('No qualifying distance evidence')
            if row['days_since_start'] is None or row['days_since_start']>c['maximum_days_since_start_for_selection']:
                reasons.append('Outside recency selection limit')
            row['reasons']=reasons;row['candidate']=not reasons
            rows.append(row)
    for date in sorted({r['date'] for r in rows}):
        pool=sorted([r for r in rows if r['date']==date and r['candidate']],key=lambda r:(-r['score'],-r['score_lead'],r['id']))
        spent=0
        for row in pool:
            if spent+c['stake']<=c['daily_cap']:
                row['selected']=True;row['stake']=c['stake'];spent+=c['stake']
            else:row['reasons'].append('Daily stake cap')
    return rows
