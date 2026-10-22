#!/usr/bin/env python3
"""Descriptive final Station contrasts; no refitting or gate modification."""
import argparse,json,statistics,random,time
from pathlib import Path
from collections import defaultdict,Counter

def analyze(root):
    root=Path(root);result=json.loads((root/'TRANSFER_RESULTS.json').read_text());rows=json.loads((root/'policy_v1/ATTEMPTS.json').read_text())['records'];costs=json.loads((root/'TRANSFER_COSTS.json').read_text())['records'];methods={};comparisons={}
    for method in ('goal_reuse','goal_fresh','expert_fresh','blind_fresh'):
        rs=[r for r in rows if r['method']==method];cs=[r for r in costs if r['method']==method];assert len(rs)==len(cs)==12
        end_control=[]
        for c in cs:
            t=c['time_seconds'];end_control.append(sum(t[k] for k in ('pre_action_observation','refinement_and_decision','execution_and_controller')))
        methods[method]={'n':len(rs),'joint_success':sum(r['joint_success'] for r in rs),'known_safe':sum(r['non_target_safe'] is True for r in rs),'unknown':sum(r['business_recovered'] is None for r in rs),'mean_probe_cost':statistics.mean(r['registered_cost'] for r in rs),'median_decision_to_write_seconds':statistics.median(r['decision_seconds_to_write'] for r in rs),'mean_api_reads':statistics.mean(r['api_reads'] for r in rs),'mean_api_seconds':statistics.mean(r['api_seconds'] for r in rs),'median_observation_through_controller_seconds':statistics.median(end_control),'median_total_attempt_seconds':statistics.median(c['time_seconds']['total_attempt'] for c in cs),'mean_total_attempt_seconds':statistics.mean(c['time_seconds']['total_attempt'] for c in cs),'llm_total_tokens':sum(c['tokens']['total_tokens'] for c in cs)}
    for baseline in ('goal_fresh','expert_fresh','blind_fresh'):
        groups=defaultdict(dict)
        for r in rows:
            if r['method'] in ('goal_reuse',baseline):groups[(r['owner'],r['rate'],r['condition'])].setdefault(r['method'],[]).append(r)
        delta=[statistics.mean(r['joint_success'] for r in g['goal_reuse'])-statistics.mean(r['joint_success'] for r in g[baseline]) for g in groups.values()];rng=random.Random(20261003);boot=sorted(statistics.mean(rng.choices(delta,k=len(delta))) for _ in range(10000));a=methods['goal_reuse'];b=methods[baseline]
        comparisons[baseline]={'joint_success_difference':statistics.mean(delta),'configuration_blocks':len(delta),'descriptive_block_bootstrap95':[boot[249],boot[9749]],'probe_cost_reduction':1-a['mean_probe_cost']/b['mean_probe_cost'],'decision_to_write_median_reduction':1-a['median_decision_to_write_seconds']/b['median_decision_to_write_seconds'],'full_attempt_median_reduction':1-a['median_total_attempt_seconds']/b['median_total_attempt_seconds'],'scope':'4 controlled configurations with shared infrastructure; descriptive interval only, no equivalence/noninferiority proof'}
    neural={}
    for variant in ('graph','local'):
        values=[r['brier'] for r in result['evaluation']['metrics'] if r['model'].startswith(variant+'-s') and r['effect']=='business_recovered'];neural[variant]={'seeds':3,'mean_brier':statistics.mean(values),'sd_across_seeds':statistics.stdev(values),'per_seed':values}
    negatives=[]
    for r in rows:
        if r['joint_success']:continue
        d=Path(r['raw_episode']).parent;failure={}
        for label in ('business','peer'):
            post=json.loads((d/f'post_{label}.json').read_text());reasons=Counter(tx.get('diagnostics',{}).get('failure','unspecified') for w in post['raw'] for tx in w['transaction_results'] if not tx['ok']);failure[label]={'windows':post['windows'],'transaction_failures':dict(reasons)}
        negatives.append({'id':r['id'],'method':r['method'],'action':r['chosen_plan'],'business_recovered':r['business_recovered'],'non_target_safe':r['non_target_safe'],'raw_episode':r['raw_episode'],'diagnostics':failure})
    out={'status':'complete_descriptive_analysis','created_at':time.time(),'methods':methods,'comparisons':comparisons,'neural_business_brier':neural,'retained_policy_negatives':negatives,'fixed_gate':json.loads((root/'policy_v1/PILOT_GATE.json').read_text()),'scope':'No original outputs changed; native acquisition/deployment/verification/cleanup time distinct from prediction timing; no comparative causal harm or learned superiority inference from rare events.'}
    path=root/'TRANSFER_ANALYSIS.json';assert not path.exists();path.write_text(json.dumps(out,indent=2)+'\n');print(json.dumps({'methods':methods,'comparisons':comparisons,'neural':neural}))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);analyze(p.parse_args().root)
