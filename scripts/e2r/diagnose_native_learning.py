#!/usr/bin/env python3
"""Development-only diagnosis of the registered v1 model and its decision gate."""
import argparse
from collections import Counter,defaultdict
import json
from pathlib import Path
import statistics
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.data import group_key,validate_record
from methods.E2R.intervention.learning import EffectPredictor
from methods.E2R.intervention.evaluation import policy_for_record
from methods.E2R.intervention.contracts import Policy
from methods.E2R.intervention.runtime import rank_candidates
from methods.E2R.intervention.schema import EFFECTS,input_bundle


def diagnose(data,split_path,model,out):
 split=json.loads(Path(split_path).read_text());allowed=set(split['train']+split['validation'])
 rows=[]
 with Path(data).open() as stream:
  for line in stream:
   row=json.loads(line)
   if group_key(row) in allowed:rows.append(row)
 predictor=EffectPredictor(model);buckets=defaultdict(list);gates=Counter();condition_gates={};forecast_rows=[]
 for row in rows:
  if 'input' not in row:continue
  graph,plan=validate_record(row);forecast=predictor.predict(graph,plan)
  partition='train' if group_key(row) in split['train'] else 'validation'
  buckets[(partition,row['application'],row['condition_id'],row['chosen_plan'])].append((row,forecast))
  forecast_rows.append({'attempt_id':row['episode_id'],'group':group_key(row),'partition':partition,
    'condition':row['condition_id'],'plan':row['chosen_plan'],'truth':row['outcome']['effects'],
    'p':forecast['probabilities'],'calibration':forecast['calibration']})
  key=(partition,group_key(row),row['condition_id'],row.get('protocol_stratum'))
  if key not in condition_gates:
   _,plans=input_bundle(row['input']);policy=Policy.from_dict(row['operation_policy']);candidates=rank_candidates(graph,plans,policy,predictor)
   condition_gates[key]=candidates
   gates.update(reason for c in candidates if len(next(p.actions for p in plans if p.id==c['plan_id'])) for reason in c['reasons'])
 reports=[]
 for key,values in buckets.items():
  effects={}
  for effect in EFFECTS:
   known=[(float(r['outcome']['effects'][effect]),p['probabilities'][effect]) for r,p in values if r['outcome']['effects'][effect] is not None and p['probabilities'][effect] is not None]
   probs=[p for _,p in known]
   effects[effect]={'n':len(known),'true':sum(y for y,p in known),'observed_mean':statistics.mean(y for y,p in known) if known else None,
    'predicted_mean':statistics.mean(probs) if probs else None,'predicted_sd':statistics.pstdev(probs) if probs else None,
    'brier':statistics.mean((y-p)**2 for y,p in known) if known else None}
  reports.append({'partition':key[0],'application':key[1],'condition':key[2],'plan':key[3],'attempts':len(values),'effects':effects})
 calibration=predictor.metadata['calibration']
 result={'status':'complete','scope':'historical train+validation development diagnosis; old test not scored',
  'groups':sorted(allowed),'attempts':len(rows),'input_attempts':len(forecast_rows),'calibration':calibration,
  'bounds_feasibility':{'success_max_possible_lower':1-calibration['business_recovered'],'harm_min_possible_upper':calibration['non_target_safe'],
    'explanation':'With global additive residual, even perfect p=1 cannot pass if these conflict with the fixed .8 success/.05 harm limits.'},
  'candidate_rejection_counts':dict(gates),'stratified_outcomes':reports,'forecasts':forecast_rows}
 out=Path(out);out.parent.mkdir(parents=True,exist_ok=True)
 with out.open('x') as f:json.dump(result,f,indent=2,allow_nan=False);f.write('\n')
 print(json.dumps({k:result[k] for k in ('scope','attempts','calibration','bounds_feasibility','candidate_rejection_counts')}))

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data',required=True);p.add_argument('--split',required=True);p.add_argument('--model',required=True);p.add_argument('--out',required=True);a=p.parse_args();diagnose(a.data,a.split,a.model,a.out)
