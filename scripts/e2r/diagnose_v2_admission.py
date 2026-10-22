#!/usr/bin/env python3
"""Inspect candidate gates on development data; never produce a live release."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.learning_v2 import RelationalPredictor
from methods.E2R.intervention.admission import fit_admission
from methods.E2R.intervention.data import group_key,validate_record
from methods.E2R.intervention.schema import input_bundle
from methods.E2R.intervention.contracts import Policy
from methods.E2R.intervention.runtime import rank_candidates
from methods.E2R.intervention.learning_views import record_for_view

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--episodes',required=True,type=Path)
    parser.add_argument('--model',required=True,type=Path)
    parser.add_argument('--out',required=True,type=Path)
    a=parser.parse_args()
    rows=[json.loads(x) for x in a.episodes.read_text().splitlines() if x.strip()]
    for row in rows:
        if 'input' in row:validate_record(row)
    predictor=RelationalPredictor(a.model)
    ledger=fit_admission(rows,predictor,calibration_groups=sorted({group_key(r) for r in rows}))
    predictor.admission=ledger
    reasons=Counter();variables=Counter();decisions=[]
    for row in rows:
        if 'input' not in row:
            decisions.append({'episode_id':row['episode_id'],'executable_candidates':[],'status':'no_input'})
            continue
        graph,plans=input_bundle(record_for_view(row,predictor.metadata.get('input_view','full'))['input'])
        candidates=rank_candidates(graph,plans,Policy.from_dict(row['operation_policy']),predictor)
        facts={f.id:f for f in graph.facts}
        for result in candidates:
            if result['plan_id']=='no_action':continue
            reasons.update(result['reasons'])
            variables.update(facts[i].variable for i in result['empirical_effects']['outside_training_range'])
        decisions.append({'episode_id':row['episode_id'],
            'executable_candidates':[r['plan_id'] for r in candidates if r['executable'] and r['plan_id']!='no_action'],
            'candidates':[{'plan_id':r['plan_id'],'reasons':r['reasons'],'probabilities':r['empirical_effects']['probabilities'],
                           'admission':r['empirical_effects']['admission_evidence']} for r in candidates]})
    result={'schema':'development_admission_diagnostic_v2','checked_at':time.time(),
        'scope':'Same development cohort fitted and inspected for gate diagnostics only; NOT independent policy validation or live release.',
        'live_authorized':False,'model':str(a.model),'attempts':len(rows),
        'calibration_cells':[{k:v for k,v in cell.items() if k!='arms'} for cell in ledger['cells'].values()],
        'refusal_reasons':dict(reasons),'outside_range_variables':dict(variables),
        'inputs_with_executable_candidate':sum(bool(d['executable_candidates']) for d in decisions),'decisions':decisions}
    with a.out.open('x') as stream:
        json.dump(result,stream,indent=2,allow_nan=False);stream.write('\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('decisions','calibration_cells')}))
