#!/usr/bin/env python3
"""Post-collection descriptive comparisons, preserving original frozen reports."""
import argparse,json,statistics,random,time,sys
from collections import defaultdict,Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from scripts.e2r.summarize_goal_policy import collect

def describe(v):return {'n':len(v),'mean':statistics.mean(v),'sd_across_seeds':statistics.stdev(v) if len(v)>1 else None}

def analyze(root):
    root=Path(root).resolve();q=root/'full_online_cleanup_v1';rows,missing,terminal=collect(q,json.loads((q/'PREREGISTRATION.json').read_text()),'final');assert not missing and terminal
    methods={}
    for method in ('goal_reuse','goal_fresh','expert_fresh','blind_fresh'):
        rs=[r for r in rows if r['method']==method];written=[r['decision_seconds_to_write'] for r in rs if r['write_prepared']];outcomes=Counter(str(r['business_recovered']) for r in rs)
        methods[method]={'n':len(rs),'observed':sum(r['status']=='observed' for r in rs),'joint_verified_success':sum(r['joint_success'] for r in rs),'business_outcomes':dict(outcomes),'safe_known':sum(r['non_target_safe'] is True for r in rs),'safety_unknown':sum(r['non_target_safe'] is None for r in rs),'registered_cost_mean':statistics.mean(r['registered_cost'] for r in rs),'write_prepared_n':len(written),'decision_to_write_median_seconds':statistics.median(written),'decision_or_terminal_median_seconds':statistics.median(r['decision_or_terminal_recheck_seconds'] for r in rs),'mean_api_reads':statistics.mean(r['api_reads'] for r in rs),'mean_api_seconds':statistics.mean(r['api_seconds'] for r in rs)}
    comparisons={};ours=methods['goal_reuse']
    for baseline in ('goal_fresh','expert_fresh','blind_fresh'):
        cells=defaultdict(dict)
        for r in rows:
            if r['method'] in ('goal_reuse',baseline):cells[(r['owner'],r['rate'],r['condition'])].setdefault(r['method'],[]).append(r)
        deltas=[statistics.mean(x['joint_success'] for x in c['goal_reuse'])-statistics.mean(x['joint_success'] for x in c[baseline]) for c in cells.values()]
        rng=random.Random(20261003);samples=sorted(statistics.mean(rng.choices(deltas,k=len(deltas))) for _ in range(10000));b=methods[baseline]
        comparisons[baseline]={'cost_reduction':1-ours['registered_cost_mean']/b['registered_cost_mean'],'write_only_median_reduction':1-ours['decision_to_write_median_seconds']/b['decision_to_write_median_seconds'],'terminal_inclusive_median_reduction':1-ours['decision_or_terminal_median_seconds']/b['decision_or_terminal_median_seconds'],'verified_joint_difference':statistics.mean(deltas),'configuration_block_bootstrap_95_interval':[samples[249],samples[9749]],'configuration_blocks':len(deltas),'scope':'Descriptive bootstrap of 8 controlled configuration blocks, unknown is not verified success; shared lab/namespace dependence remains; no equivalence or noninferiority proof.'}
    evaluation=json.loads((root/'full_evaluation/RESULTS.json').read_text());neural={}
    for variant in ('graph','local'):
        neural[variant]={effect:describe([x['brier'] for x in evaluation['metrics'] if x['model'].startswith(variant+'-s') and x['effect']==effect]) for effect in ('business_recovered','non_target_safe')}
    launch=json.loads((root/'full_master/LAUNCH.json').read_text());end=json.loads((root/'FULL_RESULTS.json').read_text())['created_at']
    result={'status':'complete_descriptive_analysis','created_at':time.time(),'raw_results_preserved':True,'scope':'Cart configuration study, all original claim judgments pending independent review','positions':{'assigned':120,'policy':96},'methods':methods,'comparisons':comparisons,'neural_brier':neural,'all_prediction_metrics':evaluation['metrics'],'corrected_timing':{'original_fields_unchanged':True,'affected_attempt':'full-private-db-L10-shared_state-r2-goal_reuse','reason':'rejected attempt has no prepared write; original decision_seconds_to_write was terminal duration','write_only_known_n':23,'no_write_n':1,'original_median_seconds':9.606489076744765,'write_only_median_seconds':ours['decision_to_write_median_seconds'],'terminal_inclusive_median_seconds':ours['decision_or_terminal_median_seconds']},'elapsed_full_pipeline_seconds_including_repair':end-launch['started_at'],'cost_report':str(root/'METHOD_COSTS.json'),'records':rows}
    path=root/'FULL_ANALYSIS.json';assert not path.exists();path.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:result[k] for k in ('methods','comparisons','neural_brier','elapsed_full_pipeline_seconds_including_repair')}))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);analyze(p.parse_args().root)
