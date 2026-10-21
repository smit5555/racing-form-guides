"""Settle both published locks against one shared official-results snapshot."""
import argparse
import json
from pathlib import Path
import shutil
from rules_v2.results import load_lock, settle, SavedResults


def compare(locked_folder, output):
    root=Path(locked_folder)
    a,b=load_lock(root/'v2'),load_lock(root/'v3')
    expected=dict(a['config'],version=b['version'],minimum_known_weight=100)
    if b['config']!=expected or a['config']['minimum_known_weight']!=80:
        raise ValueError('Unexpected comparison configuration')
    if a['races']!=b['races'] or a['sources']!=b['sources']:
        raise ValueError('Models must use exactly the same race fields and sources')
    fields=('id','number','score','factors','rank','race_eligible')
    if [[r[k] for k in fields] for r in a['runners']] != [[r[k] for k in fields] for r in b['runners']]:
        raise ValueError('Scoring or runner identities differ')
    out=Path(output);out.mkdir(parents=True,exist_ok=False)
    first=settle(root/'v2',out/'v2')
    if first['status']=='stopped':
        raise ValueError('Official retrieval stopped; V3 not started. See v2/summary.json')
    archive=shutil.make_archive(str(out/'shared-official-results'),'zip',out/'v2')
    reader=SavedResults(archive)
    try: second=settle(root/'v3',out/'v3',reader)
    finally: reader.close()
    if second['status']=='stopped':raise ValueError('Offline V3 settlement stopped: '+str(second['stopped_reason']))
    (out/'comparison.json').write_text(json.dumps({'v2':first,'v3':second},indent=2))
    lines=['# V2 / V3-A paper comparison','','Same guides, unchanged scoring; V3 requires all six factors for its selected runner.',
           'Official results fetched once, then replayed offline. Unknown and void outcomes are not losses.','',
           '| Model | Settled bets | Wins | Staked | Return | Profit/loss | Pending |',
           '|---|---:|---:|---:|---:|---:|---:|']
    for label,s in [('V2',first),('V3-A',second)]:
        lines.append(f"| {label} | {s['selected_settled']} | {s['wins']} | {s['staked']} | {s['gross_return']} | {s['paper_profit_loss']} | {s['selected_unresolved']} |")
    lines+=['','Historical paper comparison, not evidence of future profitability. September informed V3; it is development data.',
            'Daily stake caps apply separately. V3 may admit a later complete-evidence selection when V2 used its cap earlier.']
    (out/'comparison.md').write_text('\n'.join(lines)+'\n')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--locked-folder',required=True);p.add_argument('--output',required=True)
    a=p.parse_args()
    try:compare(a.locked_folder,a.output)
    except (ValueError,OSError) as exc:p.exit(1,str(exc)+'\n')

if __name__=='__main__':main()

