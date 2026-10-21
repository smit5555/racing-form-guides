"""Train on August; report fixed September development diagnostic. No site or AI calls."""
import argparse
import csv
import datetime as dt
import hashlib
import json
import os
from pathlib import Path

from .core import SPEC, FEATURE_NAMES, canonical, digest, validate, fit, evaluate


def run(data,output):
    folder=Path(data);out=Path(output)
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    body=(folder/'development.jsonl').read_bytes()
    if hashlib.sha256(body).hexdigest()!=manifest['dataset_sha256']:raise ValueError('Development dataset hash mismatch')
    if manifest['features']!=FEATURE_NAMES:raise ValueError('Dataset feature schema mismatch')
    encoder_hash=hashlib.sha256(Path(__file__).with_name('core.py').read_bytes()).hexdigest()
    if manifest['feature_encoder_sha256']!=encoder_hash:raise ValueError('Feature encoder changed: dataset must be rebuilt and audited')
    records=[json.loads(line) for line in body.splitlines() if line]
    validate(records)
    if len(records)!=manifest['races'] or sum(len(r['runners']) for r in records)!=manifest['runners']:raise ValueError('Manifest count mismatch')
    training=[r for r in records if r['date'][:7]=='2026-08' and r['date']<=SPEC['train_end']]
    validation=[r for r in records if SPEC['validation_start']<=r['date']<=SPEC['validation_end']]
    if len(training)<30 or len(validation)<10:raise ValueError('Insufficient chronological development fields')
    if max(r['date'] for r in training)>=min(r['date'] for r in validation):raise ValueError('Chronological split overlaps')
    if len(training)+len(validation)!=len(records):raise ValueError('Unexpected dates outside the frozen split')
    out.mkdir(parents=True,exist_ok=False)
    model=fit(training)
    model.update(created_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
        training_start=min(r['date'] for r in training),training_end=max(r['date'] for r in training),
        dataset_sha256=manifest['dataset_sha256'],source_code_sha256=encoder_hash,
        github_run_id=os.environ.get('GITHUB_RUN_ID','local'),git_commit=os.environ.get('GITHUB_SHA','local'),
        purpose='Development model only. Both months were previously examined; September is not an untouched test.',
        no_automatic_bets=True)
    # Model is frozen before September probabilities/labels are scored by the evaluator.
    (out/'model.json').write_bytes(canonical(model))
    (out/'model.sha256').write_text(digest(model)+'\n',encoding='ascii')
    result={};picks={}
    for name,fields in (('august_training',training),('september_development',validation)):
        result[name],picks[name]=evaluate(model,fields)
    report=dict(version=model['version'],model_sha256=digest(model),split=SPEC,
        optimizer_converged=model['converged'],metrics=result,
        limitations=['No untouched test period: both months previously reviewed.',
          'Only verified full eligible fields; original data safeguards remain.',
          'No live pre-race odds; no betting recommendations, stakes, value margin or profitability claim.',
          'Estimates are uncalibrated until reliability has been assessed on separate data.',
          'Historical guides may contain late changes unavailable at a genuine pre-race decision time.'])
    (out/'diagnostics.json').write_bytes(canonical(report))
    (out/'data-manifest.json').write_bytes(canonical(manifest))
    for name,rows in picks.items():
        with (out/(name+'-runners.csv')).open('w',newline='',encoding='utf-8-sig') as f:
            writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader()
            for row in rows:
                safe={k:("'"+v if isinstance(v,str) and v and v[0] in '=+-@' else v) for k,v in row.items()}
                writer.writerow(safe)
    with (out/'coefficients.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.writer(f);w.writerow(['feature','standardised_coefficient'])
        w.writerows(zip(model['features'],model['weights']))
    lines=['# P1 race probability development check','',
      'Trained on August; September is a later development diagnostic. Both months have already been reviewed.',
      'This is not a blind backtest or a live betting system. No AI calls or racing-site requests.','',
      '| Period | Races | Model top-pick win rate | V2 top-pick win rate | Model log loss | Equal-chance log loss |',
      '|---|---:|---:|---:|---:|---:|']
    for name,m in result.items():
        lines.append(f"| {name} | {m['races']} | {m['model_top_win_rate']:.1%} | {m['v2_top_win_rate']:.1%} | {m['race_log_loss']:.4f} | {m['uniform_log_loss']:.4f} |")
    lines+=['','Lower log loss and Brier scores are better. Compare top-pick accuracy on exactly the same races.',
      'The V2 comparator is its top-ranked runner in each eligible field, not its filtered betting shortlist.',
      'The equal-chance baseline assigns each runner 1 / field size; it is not an odds-market baseline.',
      f"Optimizer converged: {model['converged']}; gradient norm {model['gradient_norm']:.3g}.",
      'Calibration bins and multiclass Brier scores are in diagnostics.json. A fitted probability is not automatically reliable.',
      'No coefficient selection, regularisation search or threshold tuning used September.',
      'Model/schema/config hashes and the source manifest are included for reproducibility.',
      'If performance is promising, freeze this exact model before a later unreviewed period. Do not repeatedly tune against September.',
      'No Github prediction or results workflow has been changed by this training job.']
    (out/'START_HERE.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data',default='race_model/data');p.add_argument('--output',default='probability-development')
    a=p.parse_args()
    try:run(a.data,a.output)
    except (ValueError,OSError,KeyError) as exc:p.exit(1,str(exc)+'\n')


if __name__=='__main__':main()
