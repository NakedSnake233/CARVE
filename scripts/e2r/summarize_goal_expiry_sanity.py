#!/usr/bin/env python3
"""Engineering branch coverage and native safety, with explicit injected delay costs."""
import argparse,json,time,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.data import validate_record
from scripts.e2r.collect_method_costs import native_record

def run(q):
    q=Path(q);plan=json.loads((q/'PREREGISTRATION.json').read_text());records=[];costs=[];issues=[]
    for a in plan['assignments']:
        assert (q/'jobs'/a['id']/'receipt.json').exists(), 'all source workers must terminate before finalization'
    for a in plan['assignments']:
        folder=Path(a['out'])
        if not all((folder/f).exists() for f in ('protocol.json','summary.json','episodes.jsonl')):
            issues.append(a['id']+':missing_artifacts');records.append({'id':a['id'],'status':'missing_artifacts','worker_receipt':json.loads((q/'jobs'/a['id']/'receipt.json').read_text())});continue
        c=json.loads((folder/'protocol.json').read_text());summary=json.loads((folder/'summary.json').read_text());rows=[json.loads(l) for l in (folder/'episodes.jsonl').read_text().splitlines()]
        assert c==json.loads(Path(a['config']).read_text()) and len(rows)==summary['attempted']==1
        row=rows[0];validate_record(row);assert json.loads((folder/row['episode_id']/'episode.json').read_text())==row
        exe=row.get('execution',{});retry=exe.get('expired_reuse_retry',{});initial=retry.get('initial_execution',{});budget=row.get('total_probe_budget',{})
        journal=folder/row['episode_id']/'execution_private.jsonl'
        events=[json.loads(l) for l in journal.read_text().splitlines()] if journal.exists() else []
        prior_expiry_events=[e for e in events if e.get('event')=='empirical_recheck' and e.get('approval',{}).get('reason')=='reuse_expired_before_write']
        delay=folder/row['episode_id']/'engineering_delay.jsonl';injection=[json.loads(l) for l in delay.read_text().splitlines()] if delay.exists() else []
        checks={'observed':row['status']=='observed','initial_expiry_without_write':initial.get('status')=='empirical_recheck_rejected' and initial.get('steps')==[] and initial.get('refreshes',[{}])[-1].get('empirical_recheck',{}).get('reason')=='reuse_expired_before_write','exactly_one_retry':retry.get('maximum_retries')==1 and retry.get('same_accountant_and_deadline') is True,'one_prepared_write':sum(e['event']=='prepared' for e in events)==1,'executed':exe.get('status')=='executed','budget':budget.get('calls',100)<=16 and budget.get('cost',100)<=48 and budget.get('actual_runtime_exceeded') is False,'peer_safe':row.get('outcome',{}).get('effects',{}).get('non_target_safe') is True,'restored':row['restore']['status']=='restored' and summary['campaign_baseline_restore']['status']=='restored' and all(summary['campaign_baseline_restore']['native_canaries'].values()),'one_injected_delay':len(injection)==1}
        issues += [a['id']+':'+k for k,v in checks.items() if not v]
        records.append({'id':a['id'],'checks':checks,'status':row['status'],'effects':row.get('outcome',{}).get('effects',{}),'registered_budget':budget,'injected_delay':injection,'expired_authorization_events_preserved':prior_expiry_events,'raw':str(folder/row['episode_id']/'episode.json')})
        costs.append(native_record(folder,row,0,c,'development_expiry_engineering'))
    result={'status':'complete','engineering_gate':'passed' if not issues else 'not_passed','checked_at':time.time(),'planned_positions':len(records),'attempts_recorded':sum('raw' in r for r in records),'issues':issues,'records':records,'costs':costs,'scope':plan['scope'],'closed_V3_results_unchanged':True,'C1_C2_confirmation':False}
    (q/'RESULTS.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'status':result['status'],'engineering_gate':result['engineering_gate'],'issues':issues}))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--queue',required=True);run(p.parse_args().queue)
