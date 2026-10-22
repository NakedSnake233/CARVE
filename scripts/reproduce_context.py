#!/usr/bin/env python3
"""Offline replay from exported features/labels, without cluster access or fitting."""
import json,math,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.context_transition import ContextTransitionModel,evaluate_rows

def main():
    folder=ROOT/'data/context_repair'
    expected=json.loads((folder/'EXPECTED_METRICS.json').read_text())
    model=ContextTransitionModel.load(folder/'model.json');results={}
    for stage in ('training','validation','test'):
        rows=[json.loads(line) for line in (folder/(stage+'.jsonl')).read_text().splitlines()]
        actual=evaluate_rows(model,rows)['overall'];target=expected[stage]
        assert actual['positions']==target['positions'] and actual['attempt_status']==target['attempt_status']
        results[stage]={}
        for head in ('context','coarse'):
            results[stage][head]={}
            for effect in ('success','safety'):
                metric=actual[head]['raw'][effect]
                for key,value in target['metrics'][head][effect].items():
                    if isinstance(value,float):assert math.isclose(metric[key],value,rel_tol=1e-10,abs_tol=1e-12),(stage,head,effect,key)
                    else:assert metric[key]==value,(stage,head,effect,key)
                results[stage][head][effect]={'brier':metric['brier'],'known_labels':metric['known_labels'],'missing_labels':metric['missing_labels']}
    online=json.loads((folder/'online_outcomes.json').read_text());assert len(online)==32
    for method,expected_success in [('goal_reuse',4),('goal_fresh',4),('expert_fresh',4),('blind_fresh',3)]:
        rows=[r for r in online if r['method']==method];assert len(rows)==8
        assert sum(r['business_recovered'] is True and r['non_target_safe'] is True for r in rows)==expected_success
    print(json.dumps({'status':'PASS','scope':'Numerical replay from exported derived features and recorded labels; raw labeling is not independently attested by this compact export.','results':results,'online_rows':len(online)},indent=2))
if __name__=='__main__':main()
