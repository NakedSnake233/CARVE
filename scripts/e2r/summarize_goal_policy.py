#!/usr/bin/env python3
"""Prospective pilot accounting and frozen expansion screen, including unknowns."""
import argparse,json,statistics,sys,time
from collections import Counter,defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.data import validate_record
from scripts.e2r.pack_revision_native import pack
from scripts.e2r.terminal_recovery import validate_terminal_recovery
from methods.E2R.intervention.goal_policy import METHODS

def read(p):return json.loads(Path(p).read_text())
def save(p,v):Path(p).write_text(json.dumps(v,indent=2,allow_nan=False)+'\n')

def collect(q,plan,stage):
    records=[];unstarted=[];terminal=True
    for a in plan['assignments']:
        if stage=='sanity' and a['id'] not in plan['included_first_jobs']:continue
        folder=Path(a['out']);config=read(a['config'])
        if not folder.exists():unstarted.append(a);continue
        if not (folder/'summary.json').exists():
            receipt=q/'jobs'/a['id']/'receipt.json'
            if not receipt.exists():raise RuntimeError('live source still running')
            unstarted.append({**a,'status':'terminal_source_without_native_summary','worker_receipt':str(receipt),'requires_inspection':True});terminal=False;continue
        summary=read(folder/'summary.json');assert read(folder/'protocol.json')==config
        rows=[json.loads(l) for l in (folder/'episodes.jsonl').read_text().splitlines()];assert len(rows)==summary['attempted']==1
        row=rows[0];validate_record(row);assert read(folder/row['episode_id']/'episode.json')==row
        assert row['assigned_method']==a['method'] and row['original_schedule_slot']==config['scheduled_jobs'][0]['original_schedule_slot']
        assert read(folder/'goal_model_snapshot.json')==read(config['goal_transition_model'])
        good=summary['status']=='complete' and row['restore']['status']=='restored' and summary['campaign_baseline_restore']['status']=='restored' and all(summary['campaign_baseline_restore']['native_canaries'].values())
        terminal_recovery=None
        if not good and (folder/'TERMINAL_RECOVERY.json').exists():
            terminal_recovery=validate_terminal_recovery(folder,config,summary,read(folder/'TERMINAL_RECOVERY.json'))
            good=row['restore']['status']=='restored'
        terminal &= good
        effects=row.get('outcome',{}).get('effects',{});budget=row.get('total_probe_budget',{})
        timings=row['timings_seconds'];event_file=folder/row['episode_id']/'execution_private.jsonl'
        events=[json.loads(l) for l in event_file.read_text().splitlines()] if event_file.exists() else []
        prepared=next((x['time'] for x in events if x['event']=='prepared'),None)
        prewrite=prepared-row['action_started_at'] if prepared is not None else timings.get('execution_and_controller',0.)
        decision_seconds=timings.get('pre_action_observation',0.)+timings.get('refinement_and_decision',0.)+prewrite
        if row['status']=='failed' and 'refinement_and_decision' not in timings:decision_seconds=None
        decision_or_terminal_seconds=decision_seconds
        if prepared is None:decision_seconds=None
        diag=row.get('execution',{}).get('goal_policy_diagnostics',{})
        api_path=folder/row['episode_id']/'goal_policy_api_events.json'
        if not diag and api_path.exists():
            api=read(api_path);diag={'api_reads':sum(e['verb']=='get' for e in api),'api_seconds':sum(e['seconds'] for e in api)}
        if stage=='sanity':
            assert good and row['status']=='observed'
            assert not budget['actual_runtime_exceeded'] and budget['calls']<=16 and budget['cost']<=48
            for w in row['outcome']['baseline_windows'].values():assert w['p95_ms']<=config['baseline_latency_ceiling_ms']
        records.append({'id':a['id'],'attempt_id':q.name+'/'+a['id']+'/'+row['episode_id'],'method':a['method'],'condition':a['condition'],'repeat':a['repeat'],'owner':a['owner'],'rate':a['rate'],
            'status':row['status'],'restored':good,'chosen_plan':row['chosen_plan'],'business_recovered':effects.get('business_recovered'),'non_target_safe':effects.get('non_target_safe'),
            'original_campaign_status':summary['status'],'subsequent_terminal_recovery':str(folder/'TERMINAL_RECOVERY.json') if terminal_recovery else None,
            'joint_success':effects.get('business_recovered') is True and effects.get('non_target_safe') is True,
            'failed_decision_elapsed_seconds':budget.get('elapsed_through_close_seconds') if decision_seconds is None else None,'registered_calls':budget.get('calls'),'registered_cost':budget.get('cost'),'decision_seconds_to_write':decision_seconds,'decision_or_terminal_recheck_seconds':decision_or_terminal_seconds,'write_prepared':prepared is not None,'api_reads':diag.get('api_reads'),
            'api_seconds':diag.get('api_seconds'),'reuse_valid':(diag.get('reuse_certificate') or {}).get('valid'),
            'raw_episode':str(folder/row['episode_id']/'episode.json')})
    return records,unstarted,terminal

def run(q,stage):
    q=Path(q);plan=read(q/'PREREGISTRATION.json');rows,unstarted,terminal=collect(q,plan,stage)
    if stage=='sanity':
        assert not unstarted;save(q/'SANITY_GATE.json',{'status':'passed','attempts':len(rows),'scope':'Engineering integrity/restore only; no favorable outcome requirement'});return
    complete=not unstarted and terminal
    save(q/'ATTEMPTS.json',{'planned_positions':plan['planned_positions'],'started':len(rows),'unstarted':unstarted,'records':rows})
    cells={}
    for method in METHODS:
        rs=[r for r in rows if r['method']==method]
        cells[method]={'n':len(rs),'observed':sum(r['status']=='observed' for r in rs),'joint_success':sum(r['joint_success'] for r in rs),
            'business_recovered':sum(r['business_recovered'] is True for r in rs),'non_target_safe':sum(r['non_target_safe'] is True for r in rs),
            'unknown':sum(r['business_recovered'] is None for r in rs),
            'mean_registered_cost':statistics.mean(r['registered_cost'] for r in rs if r['registered_cost'] is not None) if any(r['registered_cost'] is not None for r in rs) else None,
            'mean_registered_calls':statistics.mean(r['registered_calls'] for r in rs if r['registered_calls'] is not None) if any(r['registered_calls'] is not None for r in rs) else None,
            'median_decision_seconds':statistics.median(r['decision_seconds_to_write'] for r in rs if r['decision_seconds_to_write'] is not None) if any(r['decision_seconds_to_write'] is not None for r in rs) else None,
            'write_timing_known_n':sum(r['decision_seconds_to_write'] is not None for r in rs),
            'write_timing_missing_n':sum(r['decision_seconds_to_write'] is None for r in rs),
            'median_decision_or_terminal_recheck_seconds':statistics.median(r.get('decision_or_terminal_recheck_seconds') for r in rs if r.get('decision_or_terminal_recheck_seconds') is not None) if any(r.get('decision_or_terminal_recheck_seconds') is not None for r in rs) else None,
            'mean_api_reads':statistics.mean(r['api_reads'] for r in rs if r['api_reads'] is not None) if rs and any(r['api_reads'] is not None for r in rs) else None,
            'actions':dict(Counter(r['chosen_plan'] for r in rs))}
    gates={};ours=cells['goal_reuse'];reasons=[]
    if not complete or ours['observed']!=ours['n'] or ours['n']==0 or ours['non_target_safe']!=ours['n']:reasons.append('integrity_or_safety_or_observation')
    for baseline in ('goal_fresh','expert_fresh','blind_fresh'):
        b=cells[baseline]
        if ours['joint_success']<b['joint_success'] or ours['n']!=b['n']:reasons.append('joint_success_not_noninferior_to_'+baseline)
    for baseline in ('goal_fresh','expert_fresh'):
        b=cells[baseline]
        cost_gain=1-ours['mean_registered_cost']/b['mean_registered_cost'] if b['mean_registered_cost'] and ours['mean_registered_cost'] is not None else None
        time_gain=1-ours['median_decision_seconds']/b['median_decision_seconds'] if b['median_decision_seconds'] and ours['median_decision_seconds'] is not None else None
        repeats=sorted({r['repeat'] for r in rows});block_pass=[]
        for repeat in repeats:
            left=[r['registered_cost'] for r in rows if r['method']=='goal_reuse' and r['repeat']==repeat];right=[r['registered_cost'] for r in rows if r['method']==baseline and r['repeat']==repeat]
            block_pass.append(bool(left and right) and None not in left+right and statistics.mean(left)<statistics.mean(right))
        gates[baseline]={'cost_reduction':cost_gain,'decision_time_reduction':time_gain,'every_repeat_cost_lower':all(block_pass)}
        if cost_gain is None or cost_gain<plan['gate']['cost_reduction_min']:reasons.append('cost_gain_'+baseline)
        if time_gain is None or time_gain<plan['gate']['decision_wall_median_reduction_min']:reasons.append('decision_time_gain_'+baseline)
        if not all(block_pass):reasons.append('inconsistent_cost_gain_'+baseline)
    result={'status':'complete' if complete else 'partial','created_at':time.time(),'cells':cells,'comparisons':gates,'scope':plan['scope']}
    save(q/'RESULTS.json',result)
    save(q/'PILOT_GATE.json',{'status':'passed' if not reasons else 'not_passed','reasons':reasons,'comparisons':gates,'criteria':plan['gate'],
        'scope':'Development expansion screen only; no statistical noninferiority or production qualification; fresh final audit required before launch'})
    if complete:
        repair=q/'ENGINEERING_REPAIR.json'
        if repair.exists():
            history=read(repair);pack(Path(history['prior_queue']),[q],q/'data',q.name)
            result['engineering_history']={**history,'reporting':'Current comparison uses corrected policy positions; historical pre-policy unknown attempts are preserved in the full data package and reported separately, not asserted safe.'}
            save(q/'RESULTS.json',result)
        else:pack(q,[],q/'data',plan.get('cohort',q.name))
    print(json.dumps(result))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--queue',required=True);p.add_argument('--stage',choices=['sanity','final'],required=True);a=p.parse_args();run(a.queue,a.stage)
