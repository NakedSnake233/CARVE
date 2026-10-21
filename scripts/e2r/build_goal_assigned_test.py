#!/usr/bin/env python3
"""Freeze 120 heldout assigned interventions; released only after the pilot gate."""
import argparse,copy,json,random,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from scripts.e2r.profile_native_intervention import db_binding

def build(policy_queue,out,runtime):
    policy_queue,out,runtime=Path(policy_queue),Path(out).resolve(),Path(runtime).resolve();out.mkdir(parents=True,exist_ok=False);(out/'configs').mkdir();sources=[json.loads(p.read_text()) for p in (policy_queue/'configs').glob('*.json')]
    jobs=[];assignments=[];first=[];slot=0
    contexts=[(o,rate,condition) for o in ('shared-db','private-db') for rate in (6.,10.) for condition in ('shared_state','isolated_state')]
    for ci,(owner,rate,condition) in enumerate(contexts):
        for repeat in range(5):
            namespace='e2r-isg-boutique'+('' if (ci+repeat)%4==0 else f'-p{(ci+repeat)%4+1}')
            c=copy.deepcopy(next(c for c in sources if c['experimental_namespace']==namespace and c['cases'][0]['condition_id']==condition))
            alternative='private-db' if owner=='shared-db' else 'shared-db'
            bindings={'new':owner,'old':owner if condition=='shared_state' else alternative,'peer':alternative}
            for change in c['cases'][0]['baseline_changes']:
                if change['entity'] in bindings and change['path']=='/spec/template':change['value']=db_binding(change['value'],'boutique',bindings[change['entity']])
            for change in c['cases'][0]['fault_changes']:
                if change['entity']=='new':change['value']=db_binding(change['value'],'boutique',owner)
            healthy=next(ch['value'] for ch in c['cases'][0]['baseline_changes'] if ch['entity']=='new' and ch['path']=='/spec/template')
            c['policy']['rollback_options']['new']['known_good']['template']=copy.deepcopy(healthy)
            c['state_continuity']['owner_database']=owner
            for relation in c['inventory']['relations']:
                if relation['source']=='ack_state':relation['target']='db' if owner=='shared-db' else 'db_private'
            c['cases'][0].update(offered_rps=rate,peer_rps=rate)
            c['workloads']['business']['offered_rps']=rate;c['workloads']['peer']['offered_rps']=rate
            for key in list(c):
                if key.startswith('goal_'):c.pop(key)
            c.pop('evidence_reuse_max_age_seconds',None);c.update(mode='fixed_observation_evidence',arms=['no_action','switch_standby','rollback_active'],initial_probes=[p['id'] for p in c['inventory']['probes']],stage='goal_full_assigned_holdout')
            c['collector_query_order_protocol']={'schema':'assigned_all_registered_probes','initial':c['initial_probes']}
            identity=f'{owner}-L{rate:g}-{condition}-r{repeat}';required=c['state_continuity']['required_cart'];required['user_id']='e2r-ack-assigned-'+identity
            c['workloads']['business']['continuity_check']=copy.deepcopy(required);c['workloads']['business']['canary']['continuity_check']=copy.deepcopy(required)
            case=c['cases'][0];case['group_id']=case['comparison_group']=f'heldout-{owner}-L{rate:g}'
            arms=list(c['arms']);random.Random(20261004+ci*10+repeat).shuffle(arms)
            c['scheduled_jobs']=[]
            for arm in arms:c['scheduled_jobs'].append({'group_id':case['group_id'],'condition_id':condition,'repeat':repeat,'arm':arm,'original_schedule_slot':slot});slot+=1
            path=out/'configs'/f'{identity}.json';path.write_text(json.dumps(c,indent=2)+'\n');dest=out/'campaigns'/identity
            job={'id':identity,'command':[str(ROOT/'.venv-e2r/bin/python'),'-u',str(runtime/'scripts/e2r/run_native_campaign.py'),'--config',str(path),'--out',str(dest)],
                'resources':['namespace:'+namespace],'requires':[str(out/'PILOT_APPROVED.json'),str(runtime/'RUNTIME.json')],
                'outputs':[{'path':str(dest/'summary.json'),'json_equals':{'status':'complete'}}]}
            if ci==0 and repeat<4:first.append(identity)
            else:job['depends_on']=['assigned_sanity']
            jobs.append(job);assignments.append({'id':identity,'out':str(dest),'config':str(path),'owner':owner,'rate':rate,'condition':condition,'repeat':repeat})
    random.Random(20261004).shuffle(jobs)
    jobs.append({'id':'assigned_sanity','kind':'gate','depends_on':first,'command':[str(ROOT/'.venv-e2r/bin/python'),'-u',str(runtime/'scripts/e2r/summarize_state_continuity.py'),'--queue',str(out),'--stage','sanity'],
        'outputs':[{'path':str(out/'SANITY_GATE.json'),'json_equals':{'status':'passed'}}]})
    jobs.append({'id':'assigned_results','kind':'gate','depends_on':[a['id'] for a in assignments],'command':[str(ROOT/'.venv-e2r/bin/python'),'-u',str(runtime/'scripts/e2r/summarize_state_continuity.py'),'--queue',str(out),'--stage','final'],
        'outputs':[{'path':str(out/'RESULTS.json'),'json_equals':{'status':'complete'}}]})
    (out/'manifest.json').write_text(json.dumps({'cwd':str(ROOT),'max_parallel':4,'jobs':jobs},indent=2)+'\n')
    (out/'PREREGISTRATION.json').write_text(json.dumps({'created_at':time.time(),'planned_attempts':120,'assignments':assignments,'included_first_jobs':first,'cohort':out.name,
       'scope':'Frozen unfit owner/load configuration evaluation in known Cart mechanism; no independent application claim; not used for tuning'},indent=2)+'\n')
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--policy-queue',required=True);p.add_argument('--out',required=True);p.add_argument('--runtime',required=True);a=p.parse_args();build(a.policy_queue,a.out,a.runtime)
