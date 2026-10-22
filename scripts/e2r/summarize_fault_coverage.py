#!/usr/bin/env python3
"""Coverage, unknown denominators and costs for the finite native fault study."""
import argparse
import json
import sys
import time
from collections import Counter,defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.data import validate_record
from scripts.e2r.collect_method_costs import native_record,stats,TOKEN_KEYS
from scripts.e2r.pack_revision_native import pack


def read(path):return json.loads(Path(path).read_text())
def save(path,value):
    path=Path(path);tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n');tmp.replace(path)

def raw_evidence_issues(episode,row,config):
    """Engineering gate: verify native records exist even for rejected actions."""
    required=['fault_window.json','baseline_business.json','baseline_peer.json',
              'native_probe_measurements.json','configuration_binding_observations.jsonl',
              'setup_private.jsonl','version_reference.json','station_goal_lifecycle.jsonl']
    if row.get('total_probe_budget',{}).get('events'):required.append('registered_probe_events.jsonl')
    if row['status']=='observed':required += ['post_business.json','post_peer.json']
    missing=[name for name in required if not (episode/name).exists()]
    if missing:return ['missing_raw:'+name for name in missing]
    try:
        for name in required:
            value=([json.loads(l) for l in (episode/name).read_text().splitlines() if l.strip()]
                   if name.endswith('jsonl') else read(episode/name))
            if not value:raise ValueError('empty_raw:'+name)
        base=read(episode/'baseline_business.json')['window'];fault=read(episode/'fault_window.json');w=fault['window'];v=config['verification']
        assert fault['raw'] and w['start_time']>=base['end_time'] and w['samples']>=v['min_samples']
        assert (w['error_rate']>base['error_rate']+v['error_margin']
                or w['p95_ms']>max(base['p95_ms']*v['latency_multiplier'],v['latency_floor_ms'])
                or w['throughput_rps']<base['throughput_rps']*v['throughput_fraction']
                or w['state_canary_ok'] is False), 'fault_not_degraded'
        journal=[json.loads(l) for l in (episode/'setup_private.jsonl').read_text().splitlines()]
        for change in config['cases'][0]['fault_changes']:
            assert any(e.get('event')=='api_accepted' and all(e.get('mutation',{}).get(k)==value for k,value in [('entity',change['entity']),('path',change['path']),('after',change['value'])]) for e in journal), 'missing_injection_acceptance'
        budget=row['total_probe_budget']
        if budget.get('events'):
            events=[json.loads(l) for l in (episode/'registered_probe_events.jsonl').read_text().splitlines()]
            for event in budget['events']:
                assert event in events, 'probe_event_mismatch'
        if row['status']=='observed':
            for name in ('business','peer'):
                post=read(episode/('post_'+name+'.json'))
                assert len(post['windows'])==len(post['raw'])==v['required_windows']
                assert all(post['raw'])
                for w in post['windows']:assert w['start_time']>=row['outcome']['execution_completed_at']
    except (KeyError,ValueError,AssertionError,TypeError) as error:
        return ['raw_evidence_invalid:'+str(error)]
    return []


def missing_slots(folder,source,scheduled,seen):
    started=set();ledger=folder/'attempts.jsonl'
    if ledger.exists():
        for line in ledger.read_text().splitlines():
            try:event=json.loads(line)
            except json.JSONDecodeError:continue
            if event.get('status')=='started':started.add(event.get('episode_id'))
    return [{'source':source,'slot':job['original_schedule_slot'],
             'status':'started_without_terminal' if f'episode-{i:06d}' in started else 'unstarted'}
            for i,job in enumerate(scheduled) if job['original_schedule_slot'] not in seen]


def collect(root,stage,phase='final'):
    q=Path(root)/(stage+'_v1');registration=read(q/'PREREGISTRATION.json')
    rows=[];costs=[];missing=[];issues=[]
    for a in registration['assignments']:
        if phase=='sanity' and a['id'] not in registration['included_first_jobs']:continue
        config=read(a['config']);folder=Path(a['out']);scheduled={j['original_schedule_slot']:j for j in config['scheduled_jobs']}
        if not(folder/'episodes.jsonl').exists():
            missing.extend(missing_slots(folder,a['id'],config['scheduled_jobs'],set()))
            continue
        assert read(folder/'protocol.json')==config
        raw=[json.loads(line) for line in (folder/'episodes.jsonl').read_text().splitlines() if line.strip()]
        seen=set()
        for index,row in enumerate(raw):
            validate_record(row);slot=row['original_schedule_slot'];assert slot in scheduled and slot not in seen;seen.add(slot)
            assert read(folder/row['episode_id']/'episode.json')==row
            assert row.get('assigned_method')==a['method'] if stage=='policy' else row['chosen_plan']==scheduled[slot]['arm']
            if stage=='policy':assert read(folder/'goal_model_snapshot.json')==read(config['goal_transition_model'])
            effects=row.get('outcome',{}).get('effects',{})
            record={'attempt_id':str(folder/row['episode_id']),'slot':slot,'fault':a['fault'],'state_condition':a['state_condition'],'repeat':a['repeat'],'method':a['method'] if stage=='policy' else scheduled[slot]['arm'],'selected_plan':row.get('chosen_plan'),'status':row['status'],'restored':row.get('restore',{}).get('status')=='restored','business_recovered':effects.get('business_recovered'),'non_target_safe':effects.get('non_target_safe'),'joint_success':effects.get('business_recovered') is True and effects.get('non_target_safe') is True}
            rows.append(record);costs.append(native_record(folder,row,index,config,'fault_coverage_'+stage))
            episode=folder/row['episode_id'];budget=row.get('total_probe_budget',{})
            if not record['restored']:issues.append(a['id']+':restore_failed')
            if row['status'] in ('failed','ineligible') or row.get('execution',{}).get('status') in ('execution_unknown','write_deviation'):issues.append(a['id']+':'+row['status'])
            if budget and (budget.get('actual_runtime_exceeded') or budget.get('calls',0)>config['total_probe_budget']['max_calls'] or budget.get('cost',0)>config['total_probe_budget']['max_cost']):issues.append(a['id']+':budget_exceeded')
            if row['status'] not in ('failed','ineligible'):
                issues += [a['id']+':'+issue for issue in raw_evidence_issues(episode,row,config)]
                receipt=read(episode/'acknowledged_state_before_fault.json');baseline=read(episode/'baseline_business.json')['window']
                assert receipt['status']=='acknowledged' and receipt['required']==config['state_continuity']['required_station'] and receipt['completed_at']<baseline['start_time']
                assert baseline['state_canary_ok'] is True
                assert baseline['p95_ms']<=config['baseline_latency_ceiling_ms']
                cleanup=read(episode/'station_goal_cleanup.json');assert cleanup['status']=='restored'
        missing.extend(missing_slots(folder,a['id'],config['scheduled_jobs'],seen))
        if (folder/'summary.json').exists():
            summary=read(folder/'summary.json');assert summary['attempted']==len(raw)
            if summary['status']!='complete' or summary['campaign_baseline_restore']['status']!='restored' or not all(summary['campaign_baseline_restore']['native_canaries'].values()):issues.append(a['id']+':source_not_clean_complete')
        else:issues.append(a['id']+':source_not_terminal')
    return rows,costs,missing,issues

def cells(records):
    groups=defaultdict(list)
    for row in records:groups[(row['fault'],row['state_condition'],row['method'])].append(row)
    return [{'fault':key[0],'state_condition':key[1],'method':key[2],'n':len(rs),'joint_success':sum(r['joint_success'] for r in rs),'business_recovered':sum(r['business_recovered'] is True for r in rs),'non_target_safe':sum(r['non_target_safe'] is True for r in rs),'unknown':sum(r['business_recovered'] is None for r in rs),'statuses':dict(Counter(r['status'] for r in rs)),'actions':dict(Counter(r['selected_plan'] for r in rs))} for key,rs in sorted(groups.items())]

def cost_report(root):
    records=[]
    for stage in ('assigned','policy'):
        q=Path(root)/(stage+'_v1')
        if not (q/'PREREGISTRATION.json').exists():continue
        for a in read(q/'PREREGISTRATION.json')['assignments']:
            folder=Path(a['out']);ledger=folder/'episodes.jsonl'
            if not ledger.exists():continue
            config=read(folder/'protocol.json')
            for i,line in enumerate(ledger.read_text().splitlines()):
                try:row=json.loads(line)
                except json.JSONDecodeError:continue
                record=native_record(folder,row,i,config,'fault_coverage_'+stage)
                record.update(fault=a['fault'],state_condition=a['state_condition'])
                records.append(record)
    groups=defaultdict(list)
    for row in records:groups[(row['cohort'],row['method'])].append(row)
    result={'checked_at':time.time(),'scope':'New developmental fault matrix only; token0 for audited local algorithms, excludes Codex; overlapping time fields not additive.','closed_attempts':len(records),'records':records,'methods':[]}
    for (cohort,method),rs in sorted(groups.items()):
        result['methods'].append({'cohort':cohort,'method':method,'n':len(rs),'tokens':{key:stats([r['tokens'][key] for r in rs]) for key in TOKEN_KEYS},'time_seconds':{key:stats([r['time_seconds'].get(key) for r in rs]) for key in sorted({k for r in rs for k in r['time_seconds']})},'probe_cost':stats([r['registered_probe_cost'] for r in rs])})
    by_cell=defaultdict(list)
    for r in records:by_cell[(r['cohort'],r['fault'],r['state_condition'],r['method'])].append(r)
    result['per_configuration']=[{'cohort':k[0],'fault':k[1],'state_condition':k[2],'method':k[3],'n':len(rs),'probe_cost':stats([r['registered_probe_cost'] for r in rs]),'decision_to_write':stats([r['time_seconds'].get('decision_to_write') for r in rs]),'total_attempt':stats([r['time_seconds'].get('total_attempt') for r in rs])} for k,rs in sorted(by_cell.items())]
    save(Path(root)/'METHOD_COSTS.json',result)
    return {'status':'updated','closed_attempts':len(records),'report':str(Path(root)/'METHOD_COSTS.json')}

def run(root,stage=None,phase='final'):
    root=Path(root);cost_report(root)
    if stage:
        records,costs,missing,issues=collect(root,stage,phase);q=root/(stage+'_v1');complete=not missing and not issues
        result={'status':('passed' if phase=='sanity' else 'complete') if complete else 'partial','checked_at':time.time(),'attempts':len(records),'uncovered':missing,'integrity_issues':issues,'records':records,'cells':cells(records),'scope':'Contract refusals remain unknown; adverse recovery outcomes do not fail engineering gate; no C1/C2 superiority claim.'}
        save(q/('SANITY_GATE.json' if phase=='sanity' else 'RESULTS.json'),result)
        if complete and phase=='final':pack(q,[],q/'data','fault_coverage_'+stage)
    else:
        results={}
        for s in ('assigned','policy'):
            records,_,missing,issues=collect(root,s)
            results[s]={'status':'complete' if not missing and not issues else 'partial','attempts':len(records),'uncovered':missing,'integrity_issues':issues,'cells':cells(records),'records':records}
        result={'status':'complete' if all(r['status']=='complete' for r in results.values()) else 'partial','checked_at':time.time(),'positions':sum(r['attempts'] for r in results.values()),'stages':results,'scope':'Frozen exploratory coverage expansion; no retraining, no new C1/C2 conclusion, all failed/unknown kept.'}
        queue=root/'master_v1/queue_state.json'
        if queue.exists():
            state=read(queue);started=state.get('started_at')
            result['pipeline_timing']={'started_at':started,'reported_at':time.time(),'elapsed_seconds':time.time()-started if started else None,'queue_status_at_report':state.get('status'),'scope':'Wall-clock snapshot including pauses; gate may still be running; not sum of parallel attempts.'}
        save(root/'RESULTS.json',result)
        lines=['# 故障覆盖扩展结果','','开发实验，原判据未改；必须按机制分别解释，不能用基线下降替代学习优势证据。','','| 阶段 | 故障 | 状态 | 方法 | n | 恢复且安全 | unknown |','|---|---|---|---|---:|---:|---:|']
        for stage,r in results.items():
            for c in r['cells']:lines.append(f"| {stage} | {c['fault']} | {c['state_condition']} | {c['method']} | {c['n']} | {c['joint_success']} | {c['unknown']} |")
        (root/'RESULTS.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'status':result['status'],'attempts':result.get('attempts',result.get('positions'))}))
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--stage',choices=['assigned','policy']);p.add_argument('--phase',choices=['sanity','final'],default='final');p.add_argument('--cost-only',action='store_true');a=p.parse_args()
    if a.cost_only:print(json.dumps(cost_report(a.root)))
    else:run(a.root,a.stage,a.phase)
