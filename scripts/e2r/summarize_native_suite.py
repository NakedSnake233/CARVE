#!/usr/bin/env python3
"""Check all registered artifacts and report the native suite without selecting test winners."""
from __future__ import annotations
import argparse
from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.study_analysis import paired_group_difference,paired_prediction_difference
from methods.E2R.intervention.schema import EFFECTS


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def read(path):return json.loads(Path(path).read_text())


def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n');temporary.replace(path)


def frozen(path):
    if digest(path)!=Path(path).with_suffix('.sha256').read_text().strip():raise ValueError('plan changed')
    return read(path)


def training_gate(plan_path):
    plan=frozen(plan_path);identity=digest(plan_path);records=[]
    for item in plan['models']:
        folder=Path(item['out']);done=read(folder/'TRAINING_DONE.json');progress=read(folder/'progress.json')
        expected={'status':'complete','model_id':item['id'],'view':item['view'],'seed':item['seed'],
                  'name':item['name'],'plan_sha256':identity,'data_sha256':item['data_sha256'],
                  'split_sha256':item['split_sha256'],'runtime':plan['training_runtime'],'test_evaluated':False}
        if any(done.get(k)!=v for k,v in expected.items()) or progress['status']!='complete':
            raise ValueError('training receipt mismatch: '+item['id'])
        if digest(done['checkpoint'])!=done['checkpoint_sha256']:raise ValueError('checkpoint mismatch')
        # Input hashes are checked once per unique path below.
        records.append(done)
    for kind in ('data','split'):
        paths={item[kind]:item[kind+'_sha256'] for item in plan['models']}
        if any(digest(path)!=sha for path,sha in paths.items()):raise ValueError('frozen '+kind+' differs')
    return {'status':'passed','plan_sha256':identity,'models':len(records),'checked_at':time.time(),
            'test_evaluated':False,'total_worker_seconds':sum(r['wall_seconds'] for r in records),
            'peak_single_worker_rss_mib':max(r['peak_rss_mib'] for r in records),'receipts':records}


def mean_std(values):
    known=[x for x in values if x is not None]
    return {'n':len(known),'mean':statistics.mean(known) if known else None,
            'sample_std':statistics.stdev(known) if len(known)>1 else None}


def summarize(plan_path,analysis_path,out):
    plan=frozen(plan_path);analysis=frozen(analysis_path);gate=training_gate(plan_path)
    if analysis['suite_plan_sha256']!=digest(plan_path):raise ValueError('analysis and training differ')
    entries=[{**m,'is_control':False} for m in plan['models']]
    entries += [{'id':view+'__'+name,'view':view,'name':name,'seed':None,'is_control':True,
                 'evaluation':str(Path(analysis['control_output_root'])/(view+'__'+name))}
                for view in plan['control_views'] for name in plan['controls']]
    results={};flat=[];artifacts=[];refinements=[];compositions=[];refinement_comparisons=[]
    views=read(Path(plan['corpus'])/'views.json')
    for entry in entries:
        folder=Path(entry['evaluation']);done=read(folder/'EVALUATION_DONE.json');primary=read(folder/'primary.json')
        if any(done.get(k)!=v for k,v in {'status':'complete','name':entry['name'],'view':entry['view'],
            'seed':entry['seed'],'is_control':entry['is_control'],'suite_plan_sha256':digest(plan_path),
            'analysis_plan_sha256':digest(analysis_path),'partition':'test','test_evaluated':True,
            'engineering_sanity':False,'runtime':analysis['runtime']}.items()):
            raise ValueError('evaluation receipt mismatch: '+entry['id'])
        for key in ('data_sha256','split_sha256'):
            if done[key]!=views[entry['view']][key]:raise ValueError('evaluation data/split mismatch')
        if primary['partition']!='test' or not primary['protocol_matching']:raise ValueError('primary scope differs')
        curve=primary['risk_coverage']
        if [p['requested_validation_coverage'] for p in curve]!=analysis['coverage_targets']:
            raise ValueError('coverage targets differ')
        if any(p['threshold_source']!='validation_only' for p in curve):raise ValueError('non-validation threshold')
        pooled=read(folder/'heterogeneous_protocol_sensitivity.json')
        if pooled['protocol_matching']:raise ValueError('pooling sensitivity mislabeled')
        primary_point=curve[-1];metrics=primary['prediction_metrics']
        row={'id':entry['id'],'view':entry['view'],'name':entry['name'],'seed':entry['seed'],
             'attempts':primary['episodes'],'groups':primary['groups'],'conditions':primary_point['conditions'],
             'protocol_cells':primary_point['protocol_cells'],'threshold':primary_point['threshold'],
             **{k:primary_point[k] for k in ('coverage','population_recovery_lower','population_recovery_upper',
                'policy_recovery_lower','policy_recovery_upper','risk_lower','risk_upper')},
             'business_brier':metrics['effects']['business_recovered']['brier'],
             'distinction_accuracy':metrics['distinguishability']['accuracy'],
             'distinction_pairs':metrics['distinguishability']['scored_pairs'],
             'distinction_balanced_accuracy':metrics['distinguishability']['kind_balanced_accuracy'],
             'mean_retained_nodes':metrics['mean_retained_nodes'],'mean_total_nodes':metrics['mean_total_nodes'],
             'mean_inference_seconds':metrics['mean_inference_seconds'],
             'pooled_sensitivity_coverage':pooled['risk_coverage'][-1]['coverage']}
        flat.append(row);results[entry['id']]=(entry,primary);artifacts.append(str(folder/'EVALUATION_DONE.json'))
        exposed=set(plan['known_test_exposure']);split=read(Path(plan['corpus'])/views[entry['view']]['split_file'])
        if exposed & set(split['test']) and not exposed & set(split['train']+split['validation']):
            read(folder/'known_test_exposure_sensitivity.json')
        if entry['view']=='unseen_composition':
            composition=read(folder/'withheld_composition_subset.json')
            compositions.append({'id':entry['id'],'effects':composition['effects'],'decision_counts':composition['decision_counts']})
        if entry.get('refinement'):
            expected=[f'refinement_{strategy}_{limit}.json' for strategy in plan['refinement']['strategies']
                      for limit in ([0] if strategy=='none' else plan['refinement']['query_limits'])]
            if sorted(done['refinement_files'])!=sorted(expected):raise ValueError('refinement matrix incomplete')
            for file in expected:
                data=read(folder/file)
                if data['threshold_source']!='validation_only':raise ValueError('refinement threshold source differs')
                refinements.append({'id':entry['id'],'seed':entry['seed'],'strategy':data['strategy'],
                                    'limits':data['limits'],'summary':data['summary'],'actual_resources':data['actual_resources']})
            paired=read(folder/'refinement_paired.json')
            if len(paired)!=3*len(plan['refinement']['query_limits']):
                raise ValueError('refinement comparisons incomplete')
            refinement_comparisons.append({'id':entry['id'],'seed':entry['seed'],
                'path':str(folder/'refinement_paired.json'),'comparisons':paired})
    comparisons=[]
    for entry,left in results.values():
        if entry['is_control'] or entry['name']!='main':continue
        for other,right in results.values():
            if other['view']!=entry['view'] or other['name']=='main':continue
            if not other['is_control'] and other['seed']!=entry['seed']:continue
            comparisons.append({'left':entry['id'],'right':other['id'],
                'policy':paired_group_difference(left['decisions'],right['decisions'],left['risk_coverage'][-1]['threshold'],right['risk_coverage'][-1]['threshold'],
                    repetitions=plan['replay']['paired_group_bootstrap_repetitions']),
                'business_brier':paired_prediction_difference(left['prediction_metrics']['predictions'],right['prediction_metrics']['predictions'],
                    repetitions=plan['replay']['paired_group_bootstrap_repetitions'])})
    grouped=defaultdict(list)
    for row in flat:grouped[(row['view'],row['name'])].append(row)
    aggregate=[{'view':view,'name':name,'seeds':[r['seed'] for r in rows],
                'metrics':{metric:mean_std([r[metric] for r in rows]) for metric in ('coverage','policy_recovery_lower','policy_recovery_upper',
                    'population_recovery_lower','risk_upper','business_brier','distinction_accuracy','mean_retained_nodes','mean_inference_seconds')}}
               for (view,name),rows in grouped.items()]
    b5=read(Path(analysis['cost_output'])/'B5_COST_FAILURE.json');b5done=read(Path(analysis['cost_output'])/'B5_DONE.json')
    scaling=read(Path(analysis['scaling_output'])/'SCALING.json');scalingdone=read(Path(analysis['scaling_output'])/'SCALING_DONE.json')
    if b5done['status']!='complete' or b5['attempts']!=1812 or b5['original_slots']!=1786:raise ValueError('B5 scope incomplete')
    if b5done['data_sha256']!=views['base']['data_sha256']:raise ValueError('B5 corpus mismatch')
    expected={(d,f) for d in plan['scaling']['dimensions'] for f in plan['scaling']['factors']}
    actual=[(r['dimension'],r['factor']) for r in scaling['records']]
    if scalingdone['status']!='complete' or set(actual)!=expected or len(actual)!=len(expected):raise ValueError('scaling incomplete')
    if any(len(r['measurements'])!=plan['scaling']['repetitions'] for r in scaling['records']):raise ValueError('scaling repetitions incomplete')
    report={'schema':'native_offline_suite_report_v1','status':'registered_offline_artifacts_complete_pending_claim_review',
            'created_at':time.time(),'suite_plan_sha256':digest(plan_path),'analysis_plan_sha256':digest(analysis_path),
            'training_jobs':gate['models'],'evaluation_jobs':len(entries),'refinement_cells':len(refinements),
            'artifact_receipts':artifacts,'per_run':flat,'three_seed_summaries':aggregate,'paired_comparisons':comparisons,
            'refinement':refinements,'refinement_paired':refinement_comparisons,
            'composition_subsets':compositions,'cost_failure':b5,'scaling':scaling,
            'remaining_review':['fresh experiment integrity audit','result-to-claim for C1 and C2'],
            'scope_limits':['Native Cart/Redis and Station/MySQL dependency slices, not whole applications.',
                'Existing test groups include disclosed infrastructure exposure; not pristine confirmatory holdout.',
                'Protocol-matched replay; absent alternative arms are unknown, not model ground truth.',
                'B3 is saved pre-action observation replay, not a live adaptive rollout.',
                'Sparse distinction pairs and absent equivalent pairs limit representational-equivalence claims.',
                'Scaling is offline compute; standalone wait/compensation/reset timers were not consistently recorded.',
                'Paired effects use common known targets and macro-group Brier; main per-run Brier is micro over known attempts.',
                'Rule controls are measured; unavailable external LLM adapters remain unsupported.']}
    out=Path(out);out.mkdir(parents=True,exist_ok=False);save(out/'RESULTS.json',report)
    with (out/'PER_RUN.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(flat[0]));writer.writeheader();writer.writerows(flat)
    lines=['# Native offline experiment results','',f"Training {gate['models']}/42; evaluations {len(entries)}/62; refinement cells {len(refinements)}/30.",'',
           'All thresholds use validation only. Claims remain pending independent result review.','',
           '| View | Model | Seeds | Coverage | Policy recovery lower–upper | Business Brier |','|---|---|---:|---:|---:|---:|']
    def fmt(x):return 'NA' if x is None else f'{x:.4f}'
    for row in aggregate:
        m=row['metrics'];lines.append(f"| {row['view']} | {row['name']} | {len(row['seeds'])} | {fmt(m['coverage']['mean'])} | {fmt(m['policy_recovery_lower']['mean'])}–{fmt(m['policy_recovery_upper']['mean'])} | {fmt(m['business_brier']['mean'])} |")
    lines += ['','## Interpretation limits','']+['- '+s for s in report['scope_limits']]
    (out/'RESULTS.md').write_text('\n'.join(lines)+'\n')
    save(out/'OFFLINE_SUITE_GATE.json',{'status':'passed','scope':'registered offline artifacts only; not full research claim acceptance',
        'training_jobs':gate['models'],'evaluation_jobs':len(entries),'refinement_cells':len(refinements),'at':time.time()})
    print(json.dumps({k:report[k] for k in ('status','training_jobs','evaluation_jobs','refinement_cells')}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--plan',type=Path,required=True);p.add_argument('--analysis-plan',type=Path)
    p.add_argument('--training-gate',type=Path);p.add_argument('--out',type=Path);a=p.parse_args()
    if a.training_gate:save(a.training_gate,training_gate(a.plan))
    elif a.analysis_plan and a.out:summarize(a.plan,a.analysis_plan,a.out)
    else:p.error('provide --training-gate or --analysis-plan and --out')
