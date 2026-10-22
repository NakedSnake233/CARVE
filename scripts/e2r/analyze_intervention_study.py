#!/usr/bin/env python3
"""Evaluate real intervention checkpoints, matched queries, and paired group differences."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import statistics
import sys
from collections import defaultdict
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.contracts import Policy
from methods.E2R.intervention.data import read_records,group_key,validate_split
from methods.E2R.intervention.evaluation import evaluate,ManualEffects,FixedPlanControl,summarize_decisions
from methods.E2R.intervention.learning import EffectPredictor
from methods.E2R.intervention.refinement_study import replay_refinement
from methods.E2R.intervention.runtime import ObservationBudget
from methods.E2R.intervention.study_analysis import prediction_metrics,paired_group_difference


def save(path,value):
    with path.open('x') as stream:json.dump(value,stream,indent=2,allow_nan=False);stream.write('\n')


def analyze(data,models,out):
    rows=read_records(data);spec=json.loads(models.read_text());out.mkdir(parents=True,exist_ok=False)
    base=Policy.from_dict(next(r['operation_policy'] for r in rows if 'operation_policy' in r))
    reports=[]
    for entry in spec['models']:
        name=entry['name'];directory=out/name;directory.mkdir()
        print('evaluating '+name,flush=True)
        trained=EffectPredictor(entry['path'],seeds=entry.get('seeds'))
        predictor=FixedPlanControl(entry['fixed_plan']) if entry.get('fixed_plan') else ManualEffects() if entry.get('manual') else trained
        split=trained.metadata['split'];validate_split(rows,split)
        test=[r for r in rows if group_key(r) in split['test']]
        evaluation=evaluate(rows,split,base,predictor)
        evaluation['prediction_metrics']=prediction_metrics(test,predictor)
        evaluation['model_name']=name;evaluation['model_spec']=entry
        evaluation['decision_conditions']=sorted((r['group'],r['condition']) for r in evaluation['decisions'])
        save(directory/'evaluation.json',evaluation)
        if entry.get('refinement'):
            validation=[r for r in rows if group_key(r) in split['validation']]
            for strategy in ('none','fixed','random','decision'):
                for calls in (1,2,3):
                    validation_result=replay_refinement(validation,base,predictor,strategy=strategy,
                        budget=ObservationBudget(max_calls=calls,max_cost=6,max_seconds=40),seed=entry.get('random_seed',42))
                    thresholds=sorted({r['score'] for r in validation_result if r.get('score') is not None}|{1e9})
                    admissible=[]
                    for threshold in thresholds:
                        summary=summarize_decisions(validation_result,threshold)
                        risk=summary.get('risk_upper')
                        if risk is not None and risk<=base.max_harm_probability:
                            admissible.append((summary.get('coverage') or 0,threshold,summary))
                    selected=max(admissible,key=lambda x:(x[0],x[1])) if admissible else (0,1e9,summarize_decisions(validation_result,1e9))
                    result=replay_refinement(test,base,predictor,strategy=strategy,
                        budget=ObservationBudget(max_calls=calls,max_cost=6,max_seconds=40),seed=entry.get('random_seed',42))
                    save(directory/f'refinement_{strategy}_{calls}.json',{
                        'scope':'saved genuine pre-action observation replay, not live policy execution',
                        'strategy':strategy,'budget_calls':calls,'decisions':result,
                        'validation_decisions':validation_result,'threshold_source':'validation_only',
                        'threshold':selected[1],'validation_summary':selected[2],
                        'summary':summarize_decisions(result,selected[1])})
        reports.append((entry,evaluation))
    by_variant=defaultdict(list)
    for entry,report in reports:
        point=report['risk_coverage'][-1]
        by_variant[entry.get('variant',entry['name'])].append({'name':entry['name'],
            'coverage':point['coverage'],'population_recovery_lower':point['population_recovery_lower'],
            'business_brier':report['prediction_metrics']['effects']['business_recovered']['brier']})
    seed_summary={}
    for variant,values in by_variant.items():
        seed_summary[variant]={'runs':values}
        for metric in ('coverage','population_recovery_lower','business_brier'):
            known=[v[metric] for v in values if v[metric] is not None]
            seed_summary[variant][metric]={'mean':statistics.mean(known) if known else None,
                'sd':statistics.stdev(known) if len(known)>1 else None,'n':len(known)}
    differences=[]
    for entry,reference in reports:
        if not entry.get('reference'):continue
        for other,candidate in reports:
            if other['name']==entry['name'] or other.get('seed') is not None and other.get('seed')!=entry.get('seed'):continue
            if reference.get('decision_conditions')!=candidate.get('decision_conditions'):
                continue
            try:
                comparison=paired_group_difference(reference['decisions'],candidate['decisions'],
                    reference['risk_coverage'][-1]['threshold'],candidate['risk_coverage'][-1]['threshold'])
            except ValueError:
                continue
            differences.append({'left':entry['name'],'right':other['name'],
                'group_conditions':reference['decision_conditions'],'comparison':comparison})
    save(out/'summary.json',{'schema':'intervention_complete_analysis_v1','status':'complete','attempts':len(rows),
        'independent_groups':len({group_key(r) for r in rows}),'per_seed':seed_summary,'paired_differences':differences,
        'claim_status':'requires_independent_integrity_and_result_to_claim_review',
        'scope':'native Cart/Redis and Station/MySQL dependency slices; matched-arm replay is not live policy execution'})
    lines=['# Native intervention experiment results','',f'{len(rows)} recorded attempts; {len({group_key(r) for r in rows})} configuration groups.',
           '', '| Model | Seeds | Coverage mean | Recovery lower mean | Business Brier mean |','|---|---:|---:|---:|---:|']
    for variant,row in seed_summary.items():
        def fmt(key):
            value=row[key]['mean'];return 'unknown' if value is None else f'{value:.4f}'
        lines.append(f'| {variant} | {len(row["runs"])} | {fmt("coverage")} | {fmt("population_recovery_lower")} | {fmt("business_brier")} |')
    lines+=['','These are measured results and descriptive paired estimates. They do not by themselves establish novelty or superiority.',
            'All failed attempts, unknown outcomes and missing alternative arms remain in the corresponding denominators. See JSON for paired intervals and per-seed details.']
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data',type=Path,required=True);p.add_argument('--models',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();analyze(a.data,a.models,a.out)
