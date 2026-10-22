#!/usr/bin/env python3
"""Resume uncovered original slots with a prospectively declared dispatch tolerance."""
import argparse
import copy
import json
import math
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from scripts.e2r.build_parallel_native_resume import remaining


def build(previous,out,runtime,floor,namespaces=None,amendment_document='enhancement/execution-stage/DISPATCH_THRESHOLD_AMENDMENT_20260930.md',redis_client_budget=None):
    if type(floor) not in (int,float) or not math.isfinite(floor) or floor<=0:
        raise ValueError('finite positive dispatch delay floor required')
    if redis_client_budget is not None and (type(redis_client_budget) is not int or not 1<=redis_client_budget<=512):
        raise ValueError('Redis client budget must be a bounded positive integer')
    state=json.loads((previous/'queue_state.json').read_text())
    if state.get('status') not in ('stopped','complete') and namespaces is None:
        raise ValueError('previous queue must be terminal before amendment')
    source=json.loads((previous/'AMENDMENT.json').read_text())
    prior_jobs={j['id']:j for j in json.loads((previous/'manifest.json').read_text())['jobs']}
    selected=set(namespaces) if namespaces is not None else {a['namespace'] for a in source['assignments']}
    known={a['namespace'] for a in source['assignments']}
    if not selected or not selected<=known:raise ValueError('unknown or empty namespace selection')
    if namespaces is not None:
        quarantined=state.get('quarantined_resources',{})
        for namespace in selected:
            if 'namespace:'+namespace not in quarantined:
                raise ValueError('selected namespace is not quarantined: '+namespace)
    for entry in source['assignments']:
        if entry['namespace'] not in selected:continue
        job=prior_jobs[entry['id']]
        if job.get('resources')!=['namespace:'+entry['namespace']]:
            raise ValueError('job resources do not match its declared namespace')
        if state['jobs'][entry['id']]['status'] not in ('completed','stuck','blocked_resource'):
            raise ValueError('selected namespace still has nonterminal jobs: '+entry['namespace'])
    out.mkdir(parents=True,exist_ok=False);(out/'configs').mkdir()
    jobs=[];assignments=[];coverage=[]
    for entry in source['assignments']:
        if entry['namespace'] not in selected:continue
        config=json.loads(Path(entry['config']).read_text())
        original=Path(config['amendment']['original_campaign'])
        ancestors=[Path(s) for s in entry.get('attempt_sources',[str(original)])]
        if not ancestors or ancestors[0]!=original:raise ValueError('source lineage must begin with original campaign')
        prior=Path(entry['path'])
        sources=[*ancestors,prior]
        status=state['jobs'][entry['id']]['status']
        if status in ('stuck','completed') and not (prior/'summary.json').exists():
            raise ValueError('launched campaign has no cleanup receipt: '+entry['id'])
        if (prior/'summary.json').exists():
            cleanup=json.loads((prior/'summary.json').read_text())['campaign_baseline_restore']
            if cleanup['status'] not in ('restored','not_needed') or not all(cleanup.get('native_canaries',{}).values()):
                raise ValueError('prior campaign needs resource recovery: '+entry['id'])
        pending,planned,covered=remaining(config,original,sources[1:])
        coverage.append({'id':entry['original_id'],'planned_slots':planned,'covered_slots':covered,
                         'remaining_slots':len(pending),'attempt_sources':[str(s) for s in sources]})
        if not pending:continue
        config=copy.deepcopy(config)
        if redis_client_budget is not None:
            background=config.get('background_workloads',{}).get('background',{})
            if background.get('protocol')!='redis_kv':raise ValueError('selected campaign has no registered Redis background')
            old_budget=background.get('client_connection_budget',96)
            background['client_connection_budget']=redis_client_budget
            config['redis_client_protocol']={'version':'bounded_redis_deadline_v1','previous_client_budget':old_budget,
                'client_connection_budget':redis_client_budget,'key_slots':redis_client_budget,
                'maximum_payload_working_set_bytes':redis_client_budget*background['payload_bytes'],
                'deadline_seconds':background['timeout'],'deadline_includes_slot_wait':True,
                'server_configuration_unchanged':True,'amendment_document':amendment_document}
        previous_floor=config.get('dispatch_delay_floor_seconds',.25)
        config['scheduled_jobs']=pending
        config['dispatch_delay_floor_seconds']=float(floor)
        config['client_timing_protocol']={'version':f'dispatch_floor_{floor:g}s_20260930',
            'previous_floor_seconds':previous_floor,'floor_seconds':floor,
            'effective_tolerance':'max(floor_seconds, 1 / offered_rps)',
            'old_threshold_exceedances_retained':True,
            'authorization':'User requested relaxing dispatch delay tolerance and continuing experiments',
            'amendment_document':amendment_document}
        config['amendment']={**config['amendment'],'previous_queue':str(previous),
            'attempt_sources':[str(s) for s in sources],'dispatch_delay_floor_seconds':floor,
            'created_at':time.time()}
        identity=entry['id'];path=out/'configs'/(identity+'.json');result=out/'campaigns'/identity
        path.write_text(json.dumps(config,indent=2)+'\n')
        job=copy.deepcopy(prior_jobs[identity])
        job.update(command=[str(ROOT/'.venv-e2r/bin/python'),'-u',str(runtime/'scripts/e2r/run_native_campaign.py'),
                  '--config',str(path),'--out',str(result)],requires=[str(runtime/'RUNTIME.json')],
                  outputs=[{'path':str(result/'summary.json'),'json_equals':{'status':'complete'}}])
        jobs.append(job)
        assignments.append({**entry,'attempts':len(pending),'config':str(path),'path':str(result),
                            'attempt_sources':[str(s) for s in sources]})
    (out/'manifest.json').write_text(json.dumps({'schema':'local_experiment_queue_manifest_v1','cwd':str(ROOT),
        'max_parallel':len({a['namespace'] for a in assignments}),'jobs':jobs},indent=2)+'\n')
    amendment={**source,'previous_queue':str(previous),'created_at':time.time(),'assignments':assignments,
        'attempts_scheduled':sum(a['attempts'] for a in assignments),'source_coverage':coverage,
        'dispatch_delay_floor_seconds':floor,'original_attempts_preserved':True,
        'selected_namespaces':sorted(selected),'unselected_namespaces':sorted(known-selected),
        'amendment_document':amendment_document,'completed_positions_never_rerun':True}
    (out/'AMENDMENT.json').write_text(json.dumps(amendment,indent=2)+'\n')
    print(json.dumps({'queue':str(out),'campaigns':len(jobs),'attempts':amendment['attempts_scheduled'],
                      'dispatch_delay_floor_seconds':floor}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--previous-queue',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--runtime',type=Path,required=True);p.add_argument('--dispatch-delay-floor-seconds',type=float,required=True)
    p.add_argument('--namespaces',nargs='+')
    p.add_argument('--amendment-document',default='enhancement/execution-stage/DISPATCH_THRESHOLD_AMENDMENT_20260930.md')
    p.add_argument('--redis-client-budget',type=int)
    a=p.parse_args();build(a.previous_queue.resolve(),a.out.resolve(),a.runtime.resolve(),a.dispatch_delay_floor_seconds,a.namespaces,a.amendment_document,a.redis_client_budget)
