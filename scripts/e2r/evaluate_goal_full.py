#!/usr/bin/env python3
"""Evaluate frozen goal / relational / blind / rule predictors on unfit native rows."""
import argparse,json,sys,time,statistics,math
from collections import defaultdict,Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
import torch
from methods.E2R.intervention.goal_transition import GoalTransitionModel,goal_state
from methods.E2R.intervention.relational_model import RelationalActionModel,entity_tensorize
from methods.E2R.intervention.data import validate_record
from methods.E2R.intervention.contracts import Policy
from methods.E2R.intervention.schema import EFFECTS

def run(data,model,neural,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=False);rows=[json.loads(l) for l in Path(data).read_text().splitlines()]
    metadata=json.loads((Path(neural)/'RESULTS.json').read_text());assert not set(metadata['metadata']['fit_attempt_ids'])&{r['episode_id'] for r in rows}
    prediction_times=defaultdict(list);load_times={};predictor=GoalTransitionModel.load(model);nets={};torch.set_num_threads(2)
    def timed(identity,fn):
        started=time.perf_counter();result=fn();prediction_times[identity].append(time.perf_counter()-started);return result
    assert not set(predictor.metadata['training_attempt_ids'])&{r['episode_id'] for r in rows}
    sensitivity={f"transition_bootstrap_s{i['seed']}":GoalTransitionModel.load(i['path']) for i in metadata.get('transition_sensitivity',[])}
    for info in metadata['models']:
        loaded_at=time.perf_counter();ckpt=torch.load(info['checkpoint'],map_location='cpu',weights_only=True);net=RelationalActionModel(**ckpt['config']);net.load_state_dict(ckpt['state_dict']);net.eval();nets[info['id']]=(net,ckpt['normalizer']);load_times[info['id']]=time.perf_counter()-loaded_at
    records=[];errors=defaultdict(list);group_errors=defaultdict(list);denominators=defaultdict(Counter)
    for row in rows:
        effects=row.get('outcome',{}).get('effects',{})
        for effect in ('business_recovered','non_target_safe'):
            denominators[effect]['total']+=1;denominators[effect]['known']+=effects.get(effect) is not None;denominators[effect]['unknown']+=effects.get(effect) is None
        graph,plan=validate_record(row)
        if graph is None:continue
        policy=Policy.from_dict(row['operation_policy']);forecast=timed('goal_transition',lambda:predictor.predict(graph,plan,policy));blind=timed('blind_transition',lambda:predictor.predict(graph,plan,policy,blind=True))
        rule=timed('goal_rule',lambda:float(bool(plan.actions) and goal_state(graph,plan,policy)['status']=='pass'))
        predictions={'goal_transition':{'business_recovered':forecast['success'],'non_target_safe':forecast['safety']},
          'blind_transition':{'business_recovered':blind['success'],'non_target_safe':blind['safety']},'goal_rule':{'business_recovered':rule,'non_target_safe':1.}}
        for identity,fitted in sensitivity.items():
            estimate=timed(identity,lambda:fitted.predict(graph,plan,policy));predictions[identity]={'business_recovered':estimate['success'],'non_target_safe':estimate['safety']}
        with torch.no_grad():
            for identity,(net,normalizer) in nets.items():
                probabilities=timed(identity,lambda:net(entity_tensorize(graph,plan,normalizer))['logits'].sigmoid().tolist());predictions[identity]={k:probabilities[EFFECTS.index(k)] for k in ('business_recovered','non_target_safe')}
        for identity,values in predictions.items():
            for effect,value in values.items():
                actual=row.get('outcome',{}).get('effects',{}).get(effect)
                if actual is not None and value is not None:
                    error=(float(actual)-value)**2;errors[(identity,effect)].append(error);group_errors[(row['group_id'],row['condition_id'],identity,effect)].append(error)
        records.append({'attempt_id':row['episode_id'],'group':row['group_id'],'condition':row['condition_id'],'arm':row['chosen_plan'],'predictions':predictions,'actual':effects})
    costs={identity:{'n':len(values),'sum_seconds':sum(values),'mean_seconds':statistics.mean(values),'median_seconds':statistics.median(values),'p95_seconds':sorted(values)[math.ceil(.95*len(values))-1],'model_load_seconds':load_times.get(identity),'llm_tokens':0,'token_status':'not_applicable_no_llm','scope':'single CPU prediction stream,2torchthreads,noexcludedwarmup; tensorization+forward included for neuralmodels,commonJSON/validation outside timing; not native end-to-end decision latency'} for identity,values in prediction_times.items()}
    (out/'INFERENCE_COSTS.json').write_text(json.dumps(costs,indent=2)+'\n')
    result={'status':'complete','created_at':time.time(),'total_attempts':len(rows),'input_attempts':len(records),'inference_costs':str(out/'INFERENCE_COSTS.json'),
      'metrics':[{'model':k[0],'effect':k[1],'known_n':len(v),'brier':sum(v)/len(v)} for k,v in sorted(errors.items())],
      'effect_denominators':dict(denominators),'status_denominators':dict(Counter(r['status'] for r in rows)),
      'by_configuration':[{'group':k[0],'condition':k[1],'model':k[2],'effect':k[3],'known_n':len(v),'brier':sum(v)/len(v)} for k,v in sorted(group_errors.items())],
      'scope':'Frozen predictors on registered native evaluation configurations; development/infrastructure exposure follows the cohort preregistration; known-outcome Brier with missing counts explicit, no target fitting or test-based tuning; not pristine confirmation or full-application recovery.'}
    (out/'RESULTS.json').write_text(json.dumps(result,indent=2)+'\n');(out/'PREDICTIONS.json').write_text(json.dumps(records,indent=2)+'\n')
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',required=True);p.add_argument('--model',required=True);p.add_argument('--neural',required=True);p.add_argument('--out',required=True);a=p.parse_args();run(a.data,a.model,a.neural,a.out)
