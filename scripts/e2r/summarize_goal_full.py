#!/usr/bin/env python3
"""Join the registered method suite without equating it to whole-project success."""
import argparse,json,time,statistics
from collections import defaultdict,Counter
from pathlib import Path

def run(root,policy_queue=None):
    root=Path(root);policy_queue=Path(policy_queue) if policy_queue else root/'full_online_v2';paths={'assigned':'full_assigned/RESULTS.json','policy':str(policy_queue/'RESULTS.json'),'predictions':'full_evaluation/RESULTS.json','training':'neural_controls/RESULTS.json'}
    results={k:json.loads((root/v).read_text()) for k,v in paths.items()}
    assert all(r['status']=='complete' for r in results.values())
    coverage={k:json.loads((root/p/'data/summary.json').read_text()) for k,p in [('assigned','full_assigned'),('policy',str(policy_queue))]}
    assert all(c['status']=='complete' and c['history_preserved'] for c in coverage.values())
    assert coverage['assigned']['expected_slots']==120 and coverage['policy']['expected_slots']==96
    policy_rows=json.loads((policy_queue/'ATTEMPTS.json').read_text())['records'];slices=defaultdict(list)
    for r in policy_rows:slices[(r['owner'],r['rate'],r['condition'],r['method'])].append(r)
    def average(rows,key):
        values=[r[key] for r in rows if r.get(key) is not None];return statistics.mean(values) if values else None
    policy_slices=[{'owner':k[0],'rate':k[1],'condition':k[2],'method':k[3],'n':len(v),'joint_success':sum(r['joint_success'] for r in v),'unknown':sum(r['business_recovered'] is None for r in v),'mean_registered_cost':average(v,'registered_cost'),'mean_decision_seconds':average(v,'decision_seconds_to_write'),'mean_api_reads':average(v,'api_reads'),'mean_api_seconds':average(v,'api_seconds')} for k,v in sorted(slices.items())]
    diagnostics=[]
    for source in ('full_assigned',str(policy_queue)):
        for line in (root/source/'data/episodes.jsonl').read_text().splitlines():
            r=json.loads(line);d=Path(r['artifact_directory']);durations=r.get('execution',{}).get('timings_seconds',{});errors=Counter()
            for name in ('post_business.json','post_peer.json'):
                if not (d/name).exists():continue
                for window in json.loads((d/name).read_text()).get('raw',[]):
                    for tx in window.get('transaction_results',[]):
                        if not tx.get('ok'):errors[name+':'+str(tx.get('diagnostics',{}).get('failure','unspecified'))]+=1
            diagnostics.append({'attempt_id':r['episode_id'],'status':r['status'],'restore_status':r.get('restore',{}).get('status'),'execution_status':r.get('execution',{}).get('status'),'timings_seconds':r.get('timings_seconds',{}),'controller_seconds':sum(v for k,v in durations.items() if k.startswith('controller_')),'execution_phase_seconds':durations,'rpc_failures':dict(errors),'startup_cpu_profile':'not_collected_in_this_policy_study','raw_episode':str(d/'episode.json')})
    result={'status':'complete','created_at':time.time(),'scope':'Registered Cart/Redis current-method suite only; independent integrity and result-to-claim reviews pending','project_complete':False,'C1':'not_yet_adjudicated','C2':'not_yet_adjudicated','sources':{k:str(root/v) for k,v in paths.items()},'assigned':results['assigned'],'policy':results['policy'],'prediction_metrics':results['predictions'],'training':{'models':len(results['training']['models']),'seconds':sum(m['seconds'] for m in results['training']['models'])},'remaining_obligations':['fresh integrity review','result-to-claim review','native TrainTicket state-goal adaptation and independent application transfer']}
    result.update(coverage=coverage,policy_by_configuration=policy_slices,attempt_diagnostics=diagnostics)
    (root/'FULL_RESULTS.json').write_text(json.dumps(result,indent=2)+'\n')
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--policy-queue');a=p.parse_args();run(a.root,a.policy_queue)
