#!/usr/bin/env python3
"""Score a frozen native model/control under predeclared protocol and split scopes."""
from __future__ import annotations
import argparse
import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import resource
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.contracts import Policy
from methods.E2R.intervention.data import read_records, group_key
from methods.E2R.intervention.evaluation import ManualEffects, FixedPlanControl, evaluate, summarize_decisions
from methods.E2R.intervention.learning import EffectPredictor
from methods.E2R.intervention.refinement_study import replay_refinement
from methods.E2R.intervention.runtime import ObservationBudget
from methods.E2R.intervention.schema import input_bundle
from methods.E2R.intervention.study_analysis import prediction_metrics, paired_group_difference
from methods.E2R.intervention.experiment_reports import (
    summaries_by_protocol, prediction_subsets, reweight_conditions, choose_replay_threshold, refinement_resources, rejection_summary)


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def save(path, value):
    path=Path(path);tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n');tmp.replace(path)


def check_plan_file(path):
    path=Path(path).resolve()
    if digest(path)!=path.with_suffix('.sha256').read_text().strip():raise ValueError('frozen suite plan differs')
    return json.loads(path.read_text())


def run(plan_path, analysis_path, identity, control_view=None, validation_sanity_out=None):
    plan=check_plan_file(plan_path);analysis=check_plan_file(analysis_path)
    if analysis['suite_plan_sha256']!=digest(plan_path) or analysis['runtime']!=str(ROOT):
        raise ValueError('analysis runtime or training plan not registered')
    # Test opens only after all predeclared training jobs complete.
    gate=json.loads(Path(analysis['training_complete_gate']).read_text())
    if gate['status']!='passed' or gate['plan_sha256']!=digest(plan_path) or gate['models']!=len(plan['models']):
        raise ValueError('complete training barrier not established')
    if validation_sanity_out is None:
        sanity=json.loads(Path(analysis['sanity_gate']).read_text())
        if sanity['status']!='passed' or sanity['analysis_plan_sha256']!=digest(analysis_path) or sanity['test_evaluated'] is not False:
            raise ValueError('validation-only analysis sanity has not passed')
    views=json.loads((Path(plan['corpus'])/'views.json').read_text())
    model_entry=None
    if control_view is None:
        model_entry=next(x for x in plan['models'] if x['id']==identity)
        view=model_entry['view'];out=Path(model_entry['evaluation']);seed=model_entry['seed']
        training=Path(model_entry['out']);done=json.loads((training/'TRAINING_DONE.json').read_text())
        if (done['status']!='complete' or done['model_id']!=identity or done['plan_sha256']!=digest(plan_path)
                or digest(done['checkpoint'])!=done['checkpoint_sha256']):
            raise ValueError('model completion/provenance mismatch')
        predictor=EffectPredictor(training,seeds=[seed]);name=model_entry['name']
    else:
        if identity not in plan['controls'] or control_view not in plan['control_views']:
            raise ValueError('unregistered control/view')
        view=control_view;name=identity;seed=None
        out=Path(analysis['control_output_root'])/(view+'__'+name)
        predictor=ManualEffects() if identity=='manual' else FixedPlanControl(identity.removeprefix('fixed_'))
    data=Path(plan['corpus'])/views[view]['data'];split_path=Path(plan['corpus'])/views[view]['split_file']
    if digest(data)!=views[view]['data_sha256'] or digest(split_path)!=views[view]['split_sha256']:
        raise ValueError('analysis data/split differs')
    partition='validation' if validation_sanity_out is not None else 'test'
    if validation_sanity_out is not None:out=Path(validation_sanity_out)
    out.mkdir(parents=True,exist_ok=False);started=time.time()
    def progress(phase, **details):
        save(out/'progress.json',{'status':'running','phase':phase,'updated_at':time.time(),'id':identity,**details})
        print(json.dumps({'phase':phase,'id':identity,**details}),flush=True)
    progress('loading')
    rows=read_records(data);split=json.loads(split_path.read_text())
    policy=replace(Policy.from_dict(next(r['operation_policy'] for r in rows)),**plan['risk_policy'])
    test=[r for r in rows if group_key(r) in split[partition]]
    validation=[r for r in rows if group_key(r) in split['validation']]
    warm=next(r for r in validation if 'input' in r)
    graph,plans=input_bundle(warm['input']);chosen=next(p for p in plans if p.id==warm['chosen_plan'])
    for _ in range(3):predictor.predict(graph,chosen)
    progress('primary_protocol_matched_test')
    primary=evaluate(rows,split,policy,predictor,partition=partition,coverages=analysis['coverage_targets'],protocol_matching=True)
    metric=prediction_metrics(test,predictor)
    primary['prediction_metrics']=metric
    primary['protocol_prediction_subsets']=prediction_subsets(metric['predictions'])
    threshold=primary['risk_coverage'][-1]['threshold']
    primary['protocol_decision_subsets']=summaries_by_protocol(primary['decisions'],threshold)
    primary['candidate_rejections']=rejection_summary(primary['decisions'],threshold)
    primary.update(model_id=identity,name=name,seed=seed,view=view,
        test_interpretation='validation-only engineering sanity' if validation_sanity_out is not None else 'Previously collected held-out groups, with disclosed infrastructure exposure; not a pristine confirmatory holdout',
        data_sha256=views[view]['data_sha256'],split_sha256=views[view]['split_sha256'],
        inference_timing_scope='tensorization plus predictor call; three validation warmup calls excluded')
    save(out/'primary.json',primary)
    progress('protocol_pooling_sensitivity')
    pooled=evaluate(rows,split,policy,predictor,partition=partition,coverages=analysis['coverage_targets'],protocol_matching=False)
    pooled['interpretation']='Heterogeneous-protocol pooling sensitivity only; never replace the matched primary with a more favorable pooled score.'
    save(out/'heterogeneous_protocol_sensitivity.json',pooled)
    exposed=set(plan['known_test_exposure']);kept=[r for r in test if group_key(r) not in exposed]
    if partition=='test' and len(kept)!=len(test):
        filtered=[r for r in rows if group_key(r) not in exposed];filtered_split=copy.deepcopy(split)
        for subset_name in filtered_split:filtered_split[subset_name]=[g for g in split[subset_name] if g not in exposed]
        # Only meaningful when exposure was in this run's test split, not its source train/validation.
        if not exposed & set(split['train']+split['validation']) and all(filtered_split.values()):
            sensitivity=evaluate(filtered,filtered_split,policy,predictor,coverages=analysis['coverage_targets'])
            sensitivity['interpretation']='Prespecified exclusion of the known infrastructure-exposed group; does not certify all other groups pristine.'
            save(out/'known_test_exposure_sensitivity.json',sensitivity)
    if view=='unseen_composition':
        composite=[r for r in test if r['source_provenance']['action_count']>1]
        subset=prediction_metrics(composite,predictor)
        subset['decision_counts']=rejection_summary(primary['decisions'],threshold,composites_only=True)
        save(out/'withheld_composition_subset.json',subset)
    refinement_files=[];refinement_results={}
    if model_entry and model_entry['refinement']:
        for strategy in plan['refinement']['strategies']:
            limits=[0] if strategy=='none' else plan['refinement']['query_limits']
            for calls in limits:
                progress('refinement_replay',strategy=strategy,call_limit=calls)
                budget=ObservationBudget(max_calls=calls,max_cost=plan['refinement']['max_cost'],max_seconds=plan['refinement']['max_seconds'])
                val=replay_refinement(validation,replace(policy,min_utility=-1e9),predictor,strategy=strategy,budget=budget,seed=seed)
                _,cutoff,val_summary=choose_replay_threshold(val,policy.max_harm_probability)
                result=replay_refinement(test,replace(policy,min_utility=-1e9),predictor,strategy=strategy,budget=budget,seed=seed)
                name_key=f'{strategy}_{calls}'
                report={'status':'complete','model_id':identity,'seed':seed,'strategy':strategy,'limits':{'max_calls':calls,'max_cost':budget.max_cost,'max_seconds':budget.max_seconds},
                    'threshold_source':'validation_only','threshold':cutoff,'validation_summary':val_summary,
                    'validation_decisions':val,'decisions':result,'summary':summarize_decisions(result,cutoff),
                    'actual_resources':refinement_resources(result),'validation_resources':refinement_resources(val),
                    'scope':'saved genuine pre-action observation replay; no live adaptive policy execution'}
                filename='refinement_'+name_key+'.json';save(out/filename,report);refinement_files.append(filename);refinement_results[name_key]=report
        comparisons=[]
        for calls in plan['refinement']['query_limits']:
            left=refinement_results[f'decision_{calls}']
            for alternative in (f'fixed_{calls}',f'random_{calls}','none_0'):
                right=refinement_results[alternative]
                comparisons.append({'left':f'decision_{calls}','right':alternative,
                    'comparison':paired_group_difference(left['decisions'],right['decisions'],left['threshold'],right['threshold'],
                        seed=42,repetitions=plan['replay']['paired_group_bootstrap_repetitions']),
                    'left_resources':left['actual_resources'],'right_resources':right['actual_resources']})
        save(out/'refinement_paired.json',comparisons)
    done={'status':'complete','id':identity,'name':name,'seed':seed,'view':view,'is_control':control_view is not None,
          'suite_plan_sha256':digest(plan_path),'analysis_plan_sha256':digest(analysis_path),
          'data_sha256':views[view]['data_sha256'],'split_sha256':views[view]['split_sha256'],'runtime':str(ROOT),
          'primary':str(out/'primary.json'),'refinement_files':refinement_files,'completed_at':time.time(),
          'wall_seconds':time.time()-started,'peak_rss_mib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
          'partition':partition,'test_evaluated':partition=='test','engineering_sanity':validation_sanity_out is not None,
          'scope':'one registered offline evaluation; not a B1–B5 completion certificate'}
    save(out/'EVALUATION_DONE.json',done);save(out/'progress.json',{'status':'complete','phase':'evaluation_complete','updated_at':time.time(),'id':identity});print(json.dumps(done),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--plan',type=Path,required=True);p.add_argument('--analysis-plan',type=Path,required=True);p.add_argument('--id',required=True);p.add_argument('--control-view');p.add_argument('--validation-sanity-out',type=Path)
    args=p.parse_args();run(args.plan,args.analysis_plan,args.id,args.control_view,args.validation_sanity_out)
