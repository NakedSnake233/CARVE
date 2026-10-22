#!/usr/bin/env python3
"""Native Station acknowledgement, response evidence and one-row cleanup checks."""
import argparse,json,time,sys
from collections import Counter,defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.data import validate_record
from scripts.e2r.collect_method_costs import native_record

def run(q):
    q=Path(q);plan=json.loads((q/'PREREGISTRATION.json').read_text());records=[];issues=[];costs=[];unattempted=[];goal_counts=defaultdict(Counter)
    for a in plan['assignments']:assert (q/'jobs'/a['id']/'receipt.json').exists(),'wait for both source workers to terminate'
    for a in plan['assignments']:
        folder=Path(a['out']);c=json.loads(Path(a['config']).read_text())
        if (folder/'protocol.json').exists():assert json.loads((folder/'protocol.json').read_text())==c
        else:issues.append(a['id']+':missing_protocol')
        summary=json.loads((folder/'summary.json').read_text()) if (folder/'summary.json').exists() else None
        rows=[json.loads(l) for l in (folder/'episodes.jsonl').read_text().splitlines()] if (folder/'episodes.jsonl').exists() else []
        if summary is None:issues.append(a['id']+':missing_final_summary')
        else:
            if summary['status']!='complete' or summary['attempted']!=len(rows) or len(rows)!=3:issues.append(a['id']+':source_incomplete')
            if summary['campaign_baseline_restore']['status']!='restored' or not all(summary['campaign_baseline_restore']['native_canaries'].values()):issues.append(a['id']+':source_restore_unverified')
        seen={r['original_schedule_slot'] for r in rows}
        assert len(seen)==len(rows) and seen<={j['original_schedule_slot'] for j in c['scheduled_jobs']}
        unattempted.extend({'source':a['id'],**j} for j in c['scheduled_jobs'] if j['original_schedule_slot'] not in seen)
        for index,row in enumerate(rows):
            validate_record(row);d=folder/row['episode_id']
            raw=d/'episode.json'
            if raw.exists():assert json.loads(raw.read_text())==row
            else:issues.append(a['id']+'/'+row['episode_id']+':missing_raw_episode')
            ack=d/'acknowledged_state_before_fault.json';clean=d/'station_goal_cleanup.json'
            receipt=json.loads(ack.read_text()) if ack.exists() else {};cleanup=json.loads(clean.read_text()) if clean.exists() else {}
            baseline=json.loads((d/'baseline_business.json').read_text()) if (d/'baseline_business.json').exists() else {}
            checks={'observed':row['status']=='observed','native_acknowledged':receipt.get('status')=='acknowledged' and receipt.get('required')==c['state_continuity']['required_station'],
                'chronology':receipt.get('completed_at',float('inf'))<baseline.get('window',{}).get('start_time',0),
                'one_row_cleanup':cleanup.get('status')=='restored' and cleanup.get('only_owned_marker_deleted') is True and cleanup.get('before_rows')==cleanup.get('after_rows'),
                'restored':row['restore']['status']=='restored','non_target_safe':row.get('outcome',{}).get('effects',{}).get('non_target_safe') is True}
            if row['status']=='observed':
                budget=row['total_probe_budget'];checks['budget']=budget['calls']<=16 and budget['cost']<=48 and not budget['actual_runtime_exceeded']
                assert row['outcome']['acknowledged_state_goal']['preincident_receipt']==receipt
            counts=Counter();post=d/'post_business.json'
            if post.exists():
                for window in json.loads(post.read_text())['raw']:
                    for tx in window['transaction_results']:
                        observations=tx.get('diagnostics',{}).get('station_goal_observations',[])
                        if not observations:counts['unobserved']+=1
                        for obs in observations:
                            assert obs['query']==[c['state_continuity']['required_station']['id']]
                            counts['confirmed' if obs['goal_confirmed'] else 'not_confirmed']+=1
                            if obs['goal_confirmed']:assert obs['observed_data']==[c['state_continuity']['required_station']['name']]
            goal_counts[(a['condition'],row['chosen_plan'])].update(counts)
            issues.extend(a['id']+'/'+row['episode_id']+':'+k for k,v in checks.items() if not v)
            records.append({'source':str(folder),'episode_id':row['episode_id'],'slot':row['original_schedule_slot'],'condition':a['condition'],'arm':row['chosen_plan'],'status':row['status'],'effects':row.get('outcome',{}).get('effects',{}),'checks':checks,'goal_queries':dict(counts)})
            costs.append(native_record(folder,row,index,c,'station_adapter_development'))
    if len(records)!=6:issues.append('not_all_six_positions_recorded')
    same=goal_counts[('shared_state','switch_standby')];other=goal_counts[('isolated_state','switch_standby')]
    if same['confirmed']<=0 or other['not_confirmed']<=0 or other['confirmed']!=0:issues.append('native_cross_store_goal_contrast_unverified')
    result={'status':'complete','adapter_gate':'passed' if not issues else 'not_passed','checked_at':time.time(),'planned_positions':6,'attempts_recorded':len(records),'issues':issues,'unattempted_positions':unattempted,'records':records,'costs':costs,'scope':plan['scope'],'native_write_API_validated':False,'SQL_fixture_then_native_read_acknowledgment':True,'no_efficacy_or_learning_superiority_claim':True}
    (q/'RESULTS.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:result[k] for k in ['status','adapter_gate','attempts_recorded','issues']}))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--queue',required=True);run(p.parse_args().queue)
