import copy
import contextlib
import hashlib
import json
import io
from pathlib import Path
import tempfile
import unittest

import numpy as np

from .core import (FEATURE_NAMES,SPEC,canonical,digest,encode,evaluate,fit,matrix,
                   objective,predict,probabilities,validate)
from .train import run
from rules_v2.core import config_at,score_races
from rules_v2.test_rules import race


def toy(identity='2026-08-01|WA|Test|1',first_wins=True):
    return dict(id=identity,date=identity[:10],runners=[dict(number=i+1,name=name,
        v2_rank=i+1,x=[float(i==0)]+[0.0]*(len(FEATURE_NAMES)-1),
        win=int((i==0)==first_wins)) for i,name in enumerate(('ALPHA','BETA'))])


class ProbabilityTests(unittest.TestCase):
    def test_probabilities_sum_to_one(self):
        x,y,s,g,scale=matrix([toy(),toy('2026-08-02|WA|Test|1')])
        p=probabilities(x,np.ones(x.shape[1])*100,s,g)
        for group in range(2):self.assertAlmostEqual(float(p[g==group].sum()),1)
        self.assertTrue(np.isfinite(p).all())

    def test_gradient_matches_finite_difference(self):
        x,y,s,g,_=matrix([toy(),toy('2026-08-02|WA|Test|1',False)])
        weights=np.linspace(-0.2,0.2,x.shape[1]);_,grad=objective(weights,x,y,s,g,.1)
        for k in range(len(weights)):
            shift=np.zeros(len(weights));shift[k]=1e-6
            fd=(objective(weights+shift,x,y,s,g,.1)[0]-objective(weights-shift,x,y,s,g,.1)[0])/2e-6
            self.assertAlmostEqual(grad[k],fd,places=6)

    def test_training_learns_and_is_deterministic(self):
        records=[toy(),toy('2026-08-02|WA|Test|1')]
        a=fit(records);b=fit(records)
        self.assertEqual(a,b);self.assertLess(a['training_loss'],a['initial_loss'])
        self.assertGreater(predict(a,records)[0],.5)

    def test_invalid_or_duplicate_fields_fail(self):
        with self.assertRaises(ValueError):validate([toy(),toy()])
        t=toy();t['runners'][1]['win']=1
        with self.assertRaises(ValueError):validate([t])
        t=toy();t['runners'][0]['x'][0]=float('nan')
        with self.assertRaises(ValueError):validate([t])

    def test_encoder_ignores_outcomes_and_odds(self):
        r=race();row=score_races([r],config_at())[0]
        a=encode(row,r)
        for k in ('win','finish','sp','result','paper_pl','market_favourite'):
            row[k]='HINDSIGHT'
        self.assertEqual(a,encode(row,r))
        row['factors']['going']=None
        x=encode(row,r)
        self.assertEqual(x[3],0);self.assertEqual(x[len(FEATURE_NAMES)//2+3],1)

    def test_v2_rank_not_training_feature(self):
        a=[toy(),toy('2026-08-02|WA|Test|1')];b=copy.deepcopy(a)
        for r in b:
            for runner in r['runners']:runner['v2_rank']=3-runner['v2_rank']
        self.assertEqual(fit(a)['weights'],fit(b)['weights'])

    def test_validation_does_not_change_model(self):
        model=fit([toy(),toy('2026-08-02|WA|Test|1')]);before=copy.deepcopy(model)
        records=[toy('2026-09-01|WA|Test|1')]
        a,_=evaluate(model,records)
        records[0]['runners'][0]['x'][0]=100
        b,_=evaluate(model,records)
        self.assertEqual(before,model);self.assertNotEqual(a['race_log_loss'],b['race_log_loss'])

    def test_validation_labels_do_not_change_probabilities(self):
        model=fit([toy()]);a=[toy('2026-09-01|WA|Test|1')]
        b=copy.deepcopy(a)
        for r in b[0]['runners']:r['win']=1-r['win']
        np.testing.assert_array_equal(predict(model,a),predict(model,b))

    def test_runner_order_does_not_change_chances(self):
        model=fit([toy()]);a=[toy('2026-09-01|WA|Test|1')]
        b=copy.deepcopy(a);b[0]['runners'].reverse()
        np.testing.assert_allclose(predict(model,a),predict(model,b)[::-1])

    def test_uniform_and_metrics(self):
        model=fit([toy()]);model['weights']=[0.0]*len(FEATURE_NAMES)
        report,_=evaluate(model,[toy()])
        self.assertAlmostEqual(report['race_log_loss'],report['uniform_log_loss'])
        self.assertAlmostEqual(report['multiclass_brier'],.5)

    def test_pipeline_hash_tamper_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);data=p/'data';data.mkdir()
            records=[toy(f'2026-08-{n:02d}|WA|Test|1') for n in range(1,32)]
            records += [toy(f'2026-09-{n:02d}|WA|Test|1') for n in range(1,11)]
            body=b''.join(canonical(r)+b'\n' for r in records)
            (data/'development.jsonl').write_bytes(body)
            manifest=dict(dataset_sha256=hashlib.sha256(body).hexdigest(),features=FEATURE_NAMES,
                races=len(records),runners=2*len(records),
                feature_encoder_sha256=hashlib.sha256(Path(__file__).with_name('core.py').read_bytes()).hexdigest())
            (data/'manifest.json').write_bytes(canonical(manifest))
            with contextlib.redirect_stdout(io.StringIO()):report=run(data,p/'output')
            model=json.loads((p/'output/model.json').read_text())
            self.assertEqual(digest(model),(p/'output/model.sha256').read_text().strip())
            self.assertEqual(report['metrics']['september_development']['races'],10)
            with self.assertRaises(FileExistsError):run(data,p/'output')
            (data/'development.jsonl').write_bytes(body+b' ')
            with self.assertRaisesRegex(ValueError,'hash mismatch'):run(data,p/'tampered')


if __name__=='__main__':unittest.main()
