#!/usr/bin/env python3
"""Continue unobserved original slots after reviewed post-outcome cleanup repair."""
import argparse,copy,json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from scripts.e2r.terminal_recovery import validate_terminal_recovery
from scripts.e2r.build_goal_policy_study import build as build_policy

def read(p):return json.loads(Path(p).read_text())
def save(p,v):Path(p).write_text(json.dumps(v,indent=2)+'\n')

def build(root,runtime):
    root,runtime=Path(root).resolve(),Path(runtime).resolve();old=root/'full_master';original=root/'full_assigned';out=root/'full_assigned_resume_cleanup_v1';master=root/'full_master_cleanup_v1';policy=root/'full_online_cleanup_v1'
    old_jobs=read(old/'manifest.json')['jobs'];byid={j['id']:j for j in old_jobs}
    for folder in (old/'jobs').iterdir():
        if (folder/'started.json').exists() and not (folder/'receipt.json').exists():raise ValueError('original worker not terminal: '+folder.name)
    assert read(root/'neural_controls/RESULTS.json')['status']=='complete'
    if any((root/'full_online_v2/campaigns').glob('*')):raise ValueError('policy already started')
    plan=read(original/'PREREGISTRATION.json');prepared=[];coverage=[]
    for a in plan['assignments']:
        c=read(a['config']);source=Path(a['out']);rows=[];recovery=None
        if source.exists():
            summary=read(source/'summary.json');rows=[json.loads(l) for l in (source/'episodes.jsonl').read_text().splitlines()]
            if summary['status']!='complete' or any(r['restore']['status']!='restored' for r in rows):recovery=validate_terminal_recovery(source,c,summary,read(source/'TERMINAL_RECOVERY.json'))
            else:
                assert summary['campaign_baseline_restore']['status']=='restored' and all(summary['campaign_baseline_restore']['native_canaries'].values())
        covered=set()
        for row in rows:
            if row['status']=='observed' and (row['restore']['status']=='restored' or row['episode_id'] in (recovery or {}).get('recovered_episode_ids',[])):covered.add(row['original_schedule_slot'])
            else:raise ValueError('unexpected failed/unconfirmed observation needs distinct review')
        slots={s['original_schedule_slot'] for s in c['scheduled_jobs']};assert covered<=slots and len(covered)==len(rows)
        pending=[s for s in c['scheduled_jobs'] if s['original_schedule_slot'] not in covered]
        coverage.append({'source':str(source),'planned_slots':sorted(slots),'covered_slots':sorted(covered),'remaining_slots':[s['original_schedule_slot'] for s in pending]})
        if pending:c=copy.deepcopy(c);c['scheduled_jobs']=pending;prepared.append((a,c))
    out.mkdir(exist_ok=False);(out/'configs').mkdir();master.mkdir(exist_ok=False)
    first=[];lanes=set()
    for a,c in prepared:
        ns=c['experimental_namespace']
        if c['state_continuity']['owner_database']=='private-db' and ns not in lanes:first.append(a['id']);lanes.add(ns)
    assert len(lanes)==4
    jobs=[];assignments=[];py=str(ROOT/'.venv-e2r/bin/python');script=lambda n:str(runtime/'scripts/e2r'/n)
    for a,c in prepared:
        config=out/'configs'/(a['id']+'.json');dest=out/'campaigns'/a['id'];save(config,c)
        j={'id':a['id'],'command':[py,'-u',script('run_native_campaign.py'),'--config',str(config),'--out',str(dest)],'resources':['namespace:'+c['experimental_namespace']],'requires':[str(root/'CLEANUP_REPAIR_RELEASE.json'),str(runtime/'RUNTIME.json')],'outputs':[{'path':str(dest/'summary.json'),'json_equals':{'status':'complete'}}]}
        if a['id'] not in first:j['depends_on']=['cleanup_sanity']
        jobs.append(j);assignments.append({**a,'config':str(config),'out':str(dest)})
    jobs.append({'id':'cleanup_sanity','kind':'gate','depends_on':first,'command':[py,'-u',script('summarize_state_continuity.py'),'--queue',str(out),'--stage','sanity'],'outputs':[{'path':str(out/'SANITY_GATE.json'),'json_equals':{'status':'passed'}}]})
    save(out/'manifest.json',{'cwd':str(ROOT),'max_parallel':4,'jobs':jobs})
    save(out/'PREREGISTRATION.json',{**plan,'created_at':time.time(),'assignments':assignments,'included_first_jobs':first,'planned_attempts':sum(len(c['scheduled_jobs']) for _,c in prepared),'original_queue':str(original),'scope':'Only uncovered original assigned slots; outcome/threshold/model unchanged; cleanup-only runtime repair'})
    save(out/'COVERAGE.json',{'original_positions':120,'covered_positions':sum(len(x['covered_slots']) for x in coverage),'remaining_positions':sum(len(x['remaining_slots']) for x in coverage),'sources':coverage})
    save(original/'SOURCE_QUEUES.json',{'source_queues':[str(out)],'purpose':'retain originals and append only uncovered slots'})
    build_policy(policy,runtime,root/'transition_model.json',stage='full')
    combined=copy.deepcopy(jobs)
    combined.append({'id':'assigned_results','kind':'gate','depends_on':[a['id'] for a in assignments],'command':[py,'-u',script('summarize_state_continuity.py'),'--queue',str(original),'--stage','final'],'outputs':[{'path':str(original/'RESULTS.json'),'json_equals':{'status':'complete'}}]})
    score=copy.deepcopy(byid['score_controls']);score['depends_on']=['assigned_results'];score['command'][2]=script('evaluate_goal_full.py');combined.append(score)
    for j in read(policy/'manifest.json')['jobs']:
        j=copy.deepcopy(j);j['id']='policy:'+j['id'];j['depends_on']=['policy:'+x for x in j.get('depends_on',[])] or ['score_controls'];combined.append(j)
    combined.append({'id':'full_report','kind':'gate','depends_on':['policy:goal_results'],'command':[py,'-u',script('summarize_goal_full.py'),'--root',str(root),'--policy-queue',str(policy)],'outputs':[{'path':str(root/'FULL_RESULTS.json'),'json_equals':{'status':'complete'}}]})
    save(master/'manifest.json',{'cwd':str(ROOT),'max_parallel':4,'jobs':combined});save(master/'CONTINUATION.json',{'created_at':time.time(),'original_master':str(old),'assigned_original':str(original),'assigned_resume':str(out),'policy_queue':str(policy),'runtime':str(runtime),'scientific_protocol_unchanged':True,'old_attempts_and_checkpoints_preserved':True})
    print(json.dumps({'master':str(master),'remaining_assigned':sum(len(c['scheduled_jobs']) for _,c in prepared),'policy_positions':96}))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--runtime',required=True);a=p.parse_args();build(a.root,a.runtime)
