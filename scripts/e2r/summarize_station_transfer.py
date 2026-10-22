#!/usr/bin/env python3
"""Close the fixed Station slice matrix without inflating original C1/C2 scope."""
import argparse,json,time,statistics,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from scripts.e2r.collect_method_costs import native_record
from methods.E2R.intervention.data import validate_record
from collections import Counter

def pipeline_timing(root,report_at=None):
    root=Path(root);master=root/'transfer_master_v1';manifest=json.loads((master/'manifest.json').read_text());state=json.loads((master/'queue_state.json').read_text()) if (master/'queue_state.json').exists() else {}
    all_receipts=[json.loads(p.read_text()) for p in (master/'jobs').glob('*/receipt.json')]
    completed_times=[r['completed_at'] for r in all_receipts if r.get('completed_at') is not None]
    ends=completed_times+([state['updated_at']] if state.get('status') in ('complete','stopped') and state.get('updated_at') else [])
    end=max(ends) if ends else None;start=state.get('started_at');elapsed=end-start if end is not None and start is not None else None
    limit=manifest.get('max_dispatch_seconds')
    timing={'queue_status':state.get('status'),'started_at':start,'last_terminal_or_queue_at':end,'elapsed_seconds':elapsed,'max_receipt_completed_at':max(completed_times) if completed_times else None,'dispatch_budget':state.get('dispatch_budget'),'dispatch_limit_seconds':limit,'overrun_seconds':max(0,elapsed-limit) if elapsed is not None and limit is not None else None,'scope':'actual master walltime, including safely drained in-flight work; missing clock data staysnull'}
    if report_at is not None and start is not None:
        timing.update(last_terminal_or_queue_at=report_at,elapsed_seconds=report_at-start,overrun_seconds=max(0,report_at-start-limit) if limit is not None else None)
    return timing

def partial(root):
    root=Path(root);master=root/'transfer_master_v1';manifest=json.loads((master/'manifest.json').read_text());state=json.loads((master/'queue_state.json').read_text()) if (master/'queue_state.json').exists() else {}
    if state.get('status')=='running' and not (master/'DISPATCH_STOP_FOR_REPAIR.json').exists():raise RuntimeError('stop dispatcher and wait for workers before partial finalization')
    native=[j for j in manifest['jobs'] if j.get('kind')!='gate']
    for job in manifest['jobs']:
        folder=master/'jobs'/job['id']
        if (folder/'started.json').exists() and not (folder/'receipt.json').exists():raise RuntimeError('live or lost worker needs terminal inspection before partial finalization: '+job['id'])
    records=[];costs=[];issues=[]
    for job in native:
        cmd=job['command'];config=Path(cmd[cmd.index('--config')+1]);out=Path(cmd[cmd.index('--out')+1]);c=json.loads(config.read_text());worker=master/'jobs'/job['id']/'receipt.json';receipt=json.loads(worker.read_text()) if worker.exists() else None
        if (out/'protocol.json').exists() and json.loads((out/'protocol.json').read_text())!=c:raise ValueError('source config mismatch')
        rows={};started=set()
        for name in ('attempts.jsonl','episodes.jsonl'):
            if not (out/name).exists():continue
            for lineno,line in enumerate((out/name).read_text().splitlines(),1):
                try:value=json.loads(line)
                except json.JSONDecodeError:issues.append(str(out/name)+':invalid_line:'+str(lineno));continue
                if name=='attempts.jsonl' and value.get('status')=='started':started.add(value['episode_id'])
                if name=='episodes.jsonl':
                    if value['episode_id'] in rows:raise ValueError('duplicate terminal episode')
                    validate_record(value);rows[value['episode_id']]=value
        for index,assignment in enumerate(c['scheduled_jobs']):
            identity=f'episode-{index:06d}';row=rows.get(identity);cohort='policy' if c.get('mode')=='goal_policy_comparison' else 'assigned'
            status=row['status'] if row else 'started_without_terminal_record' if identity in started else 'not_started'
            if row:
                assert row['original_schedule_slot']==assignment['original_schedule_slot']
                raw=out/identity/'episode.json'
                if raw.exists():
                    try:actual=json.loads(raw.read_text())
                    except json.JSONDecodeError:issues.append(str(raw)+':invalid_json')
                    else:
                        if actual!=row:raise ValueError('raw/ledger mismatch')
                else:issues.append(str(raw)+':missing')
                costs.append(native_record(out,row,index,c,'station_transfer_'+cohort))
            effects=(row or {}).get('outcome',{}).get('effects',{})
            records.append({'cohort':cohort,'source':str(out),'episode_id':identity,'slot':assignment['original_schedule_slot'],'assigned_arm_or_method':assignment['arm'],'status':status,'business_recovered':effects.get('business_recovered'),'non_target_safe':effects.get('non_target_safe'),'restore_status':(row or {}).get('restore',{}).get('status'),'raw':str(out/identity/'episode.json') if row else None,'worker_receipt':receipt})
    counts={cohort:dict(Counter(r['status'] for r in records if r['cohort']==cohort)) for cohort in ('assigned','policy')}
    timing=pipeline_timing(root);inference_path=root/'evaluation_v1/INFERENCE_COSTS.json'
    inference=json.loads(inference_path.read_text()) if inference_path.exists() else None
    report={'status':'partial','checked_at':time.time(),'planned_positions':len(records),'counts':counts,'issues':issues,'records':records,'costs':costs,'cost_attempts_recorded':len(costs),'unmeasured_started_costs':[r['source']+'/'+r['episode_id'] for r in records if r['status']=='started_without_terminal_record'],'pipeline_timing':timing,'inference_costs':inference,'inference_costs_source':str(inference_path) if inference is not None else None,'scope':'All registered positions shown, missing/stopped/unknown retained; no fabricated recovery or complete matrix claim; no original raw overwritten.'}
    path=root/'PARTIAL_TRANSFER_RESULTS.json';tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(report,indent=2)+'\n');tmp.replace(path);return report

def run(root):
    root=Path(root);assigned=json.loads((root/'assigned_v1/RESULTS.json').read_text());policy=json.loads((root/'policy_v1/RESULTS.json').read_text());evaluation=json.loads((root/'evaluation_v1/RESULTS.json').read_text());assert all(x['status']=='complete' for x in (assigned,policy,evaluation))
    coverage={name:json.loads((root/name/'data/summary.json').read_text()) for name in ('assigned_v1','policy_v1')};assert coverage['assigned_v1']['expected_slots']==60 and coverage['policy_v1']['expected_slots']==48
    costs=[];cleanup=[]
    for name in ('assigned_v1','policy_v1'):
        for source in json.loads((root/name/'data/sources.json').read_text()):
            d=Path(source['source_directory']);c=json.loads((d/'protocol.json').read_text());rows=[json.loads(l) for l in (d/'episodes.jsonl').read_text().splitlines()]
            for index,row in enumerate(rows):
                cost=native_record(d,row,index,c,'station_transfer_'+name);costs.append(cost)
                p=d/row['episode_id']/'station_goal_cleanup.json';receipt=json.loads(p.read_text()) if p.exists() else None
                cleanup.append({'raw':str(d/row['episode_id']/'episode.json'),'verified':bool(receipt and receipt['status']=='restored' and receipt['before_rows']==receipt['after_rows']),'receipt':str(p)})
    created_at=time.time()
    result={'status':'complete','created_at':created_at,'pipeline_timing':pipeline_timing(root,created_at),'scope':'Station/MySQL second application slice, no target fitting; developed goal semantics/included infrastructure sanity, not pristine final test or fullTrainTicket','project_complete':False,'C1':'pending_independent_review','C2':'pending_independent_review','coverage':coverage,'assigned':assigned,'policy':policy,'evaluation':evaluation,'costs':costs,'station_table_cleanup':cleanup,'remaining':['independent integrity/claim review','original C1/C2 missing controls are not supplied merely by another app']}
    (root/'TRANSFER_RESULTS.json').write_text(json.dumps(result,indent=2)+'\n');(root/'TRANSFER_COSTS.json').write_text(json.dumps({'records':costs,'inference':json.loads((root/'evaluation_v1/INFERENCE_COSTS.json').read_text()),'scope':'local methods use0LLMtokens; unknown measurements remainnull; phase/API totals overlap'},indent=2)+'\n')
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--partial',action='store_true');a=p.parse_args();partial(a.root) if a.partial else run(a.root)
