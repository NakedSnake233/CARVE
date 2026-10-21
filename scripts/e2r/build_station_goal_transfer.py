#!/usr/bin/env python3
"""Fixed60+48 Station/MySQL cross-slice evaluation after reviewed adapter preflight."""
import argparse,copy,json,random,time,uuid,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from scripts.e2r.build_parallel_native_resume import remap
from scripts.e2r.profile_native_intervention import db_binding
from methods.E2R.intervention.station_goal import goal_step
from methods.E2R.intervention.stateful_evidence import validate_state_goal
from methods.E2R.intervention.goal_policy import METHODS

def save(p,v):Path(p).write_text(json.dumps(v,indent=2)+'\n')
def build(root,runtime):
    root,runtime=Path(root).resolve(),Path(runtime).resolve();endpoints=json.loads((root/'SERVICE_ENDPOINTS.json').read_text())['namespaces'];base=json.loads((root/'preflight_v1/configs/station-shared_state.json').read_text());base_ns=base['experimental_namespace'];model_root=ROOT/'enhancement/method-stage/goal_policy_v3_20261002';py=str(ROOT/'.venv-e2r/bin/python');queues={}
    for stage,repeats in [('assigned',5),('policy',3)]:
        q=root/(stage+'_v1');q.mkdir(exist_ok=False);(q/'configs').mkdir();jobs=[];assignments=[];first=[];slot=0
        for ci,(owner,condition) in enumerate([(o,c) for o in ('shared-db','private-db') for c in ('shared_state','isolated_state')]):
            for repeat in range(repeats):
                lane=(ci+repeat)%4;ns='e2r-isg-trainticket'+('' if lane==0 else f'-p{lane+1}')
                ips={v['clusterIP']:endpoints[ns][name]['clusterIP'] for name,v in endpoints[base_ns].items()}
                for mi,method in enumerate(('assigned',) if stage=='assigned' else METHODS):
                    c=remap(copy.deepcopy(base),base_ns,ns,ips);case=c['cases'][0];alt='private-db' if owner=='shared-db' else 'shared-db'
                    bindings={'new':owner,'old':owner if condition=='shared_state' else alt,'peer':alt}
                    for change in case['baseline_changes']:
                        if change['path']=='/spec/template' and change['entity'] in bindings:change['value']=db_binding(change['value'],'trainticket',bindings[change['entity']])
                    healthy=next(ch['value'] for ch in case['baseline_changes'] if ch['entity']=='new' and ch['path']=='/spec/template');bad=copy.deepcopy(healthy);bad['spec']['containers'][0]['readinessProbe'].update(tcpSocket={'port':1},failureThreshold=1)
                    case['fault_changes']=[{'entity':'new','path':'/spec/template','value':bad}];c['policy']['rollback_options']['new']['known_good']['template']=copy.deepcopy(healthy)
                    group=f'station-transfer-{stage}-{owner}';case.update(group_id=group,comparison_group=group,condition_id=condition)
                    identity=f'{stage}-{owner}-{condition}-r{repeat}'+('' if stage=='assigned' else '-'+method)
                    goal={'id':str(uuid.uuid5(uuid.NAMESPACE_URL,'e2r-station-transfer-v1-'+identity)),'name':'e2r_ack_'+identity.replace('-','_'),'stay_time':3};c['state_continuity'].update(owner_database=owner,required_station=goal)
                    for rel in c['inventory']['relations']:
                        if rel['source']=='ack_state':rel['target']='db' if owner=='shared-db' else 'db_private'
                    for spec in (c['workloads']['business'],c['workloads']['business']['canary']):spec['steps']=[s for s in spec['steps'] if not s.get('station_goal_check')]+[goal_step(goal)]
                    c.update(stage='station_goal_transfer_'+stage,repeats=repeats,station_registration=str(q/'PREREGISTRATION.json'),station_release=str(q/'RELEASE.json'))
                    arms=list(c['arms']) if stage=='assigned' else [method]
                    if stage=='assigned':random.Random(20261003+ci*10+repeat).shuffle(arms)
                    else:
                        c.update(mode='goal_policy_comparison',arms=list(METHODS),initial_probes=[],goal_policy_authority='user_authorized_laboratory_only',goal_transition_model=str(model_root/'transition_model.json'),goal_registration=str(q/'PREREGISTRATION.json'),goal_release=str(q/'RELEASE.json'),evidence_reuse_max_age_seconds=10.,retry_expired_reuse_once=True)
                        c['collector_query_order_protocol']={'schema':'goal_policy_missing_evidence_v3','initial_probes':[],'selection':'minimum probe cover of action/goal requirements'}
                        c['goal_policy_protocol']={'schema':'station_goal_transfer_v1','methods':list(METHODS),'reuse_max_age_seconds':10.,'expiry_retry_limit':1,'training_source':'closedCart18rows; frozen, no Station fitting','scope':'laboratory native Station/MySQL goal mapping; no production qualification'}
                    c['scheduled_jobs']=[]
                    for arm in arms:c['scheduled_jobs'].append({'group_id':group,'condition_id':condition,'repeat':repeat,'arm':arm,'original_schedule_slot':slot});slot+=1
                    validate_state_goal(c);config=q/'configs'/(identity+'.json');out=q/'campaigns'/identity;save(config,c)
                    j={'id':identity,'command':[py,'-u',str(runtime/'scripts/e2r/run_native_campaign.py'),'--config',str(config),'--out',str(out)],'resources':['namespace:'+ns],'requires':[str(root/'TRANSFER_RELEASE.json'),str(q/'RELEASE.json'),str(runtime/'RUNTIME.json')],'outputs':[{'path':str(out/'summary.json'),'json_equals':{'status':'complete'}}]}
                    if repeat==0 and (stage=='assigned' or mi==ci):first.append(identity)
                    else:j['depends_on']=[stage+'_sanity']
                    jobs.append(j);assignments.append({'id':identity,'config':str(config),'out':str(out),'owner':owner,'condition':condition,'repeat':repeat,'rate':2.,'namespace':ns,'method':method})
        random.Random(20261003).shuffle(jobs)
        for phase,deps in [('sanity',first),('final',[a['id'] for a in assignments])]:
            script='summarize_state_continuity.py' if stage=='assigned' else 'summarize_goal_policy.py'
            jobs.append({'id':stage+('_sanity' if phase=='sanity' else '_results'),'kind':'gate','depends_on':deps,'command':[py,'-u',str(runtime/'scripts/e2r'/script),'--queue',str(q),'--stage',phase],'outputs':[{'path':str(q/('SANITY_GATE.json' if phase=='sanity' else 'RESULTS.json')),'json_equals':{'status':'passed' if phase=='sanity' else 'complete'}}]})
        save(q/'manifest.json',{'cwd':str(ROOT),'max_parallel':4,'jobs':jobs});save(q/'PREREGISTRATION.json',{'created_at':time.time(),'cohort':'station_goal_transfer_20261003_'+stage+'_v1','planned_positions':slot,'planned_attempts':slot,'assignments':assignments,'included_first_jobs':first,'runtime':str(runtime),'scope':'Prospective frozen zero-target-fit Station/MySQL slice evaluation. Shared-owner settings exposed in adapter development, allfirst4configurations included infrastructure sanity; not pristine confirmatory or fullTrainTicket','gate':{'cost_reduction_min':.30,'decision_wall_median_reduction_min':.20,'interpretation':'descriptive screen, not noninferiority/production proof'}});queues[stage]=q
    master=root/'transfer_master_v1';master.mkdir(exist_ok=False);jobs=json.loads((queues['assigned']/'manifest.json').read_text())['jobs']
    jobs.append({'id':'score_transfer','kind':'gate','depends_on':['assigned_results'],'command':[py,'-u',str(runtime/'scripts/e2r/evaluate_goal_full.py'),'--data',str(queues['assigned']/'data/episodes.jsonl'),'--model',str(model_root/'transition_model.json'),'--neural',str(model_root/'neural_controls'),'--out',str(root/'evaluation_v1')],'requires':[str(root/'TRANSFER_RELEASE.json')],'outputs':[{'path':str(root/'evaluation_v1/RESULTS.json'),'json_equals':{'status':'complete'}}]})
    for j in json.loads((queues['policy']/'manifest.json').read_text())['jobs']:
        j=copy.deepcopy(j)
        if not j.get('depends_on'):j['depends_on']=['score_transfer']
        jobs.append(j)
    jobs.append({'id':'transfer_report','kind':'gate','depends_on':['policy_results'],'command':[py,'-u',str(runtime/'scripts/e2r/summarize_station_transfer.py'),'--root',str(root)],'outputs':[{'path':str(root/'TRANSFER_RESULTS.json'),'json_equals':{'status':'complete'}}]})
    save(master/'manifest.json',{'cwd':str(ROOT),'max_parallel':4,'max_dispatch_seconds':14400,'jobs':jobs});print(json.dumps({'assigned':60,'policy':48,'jobs':len(jobs),'master':str(master)}))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--runtime',required=True);a=p.parse_args();build(a.root,a.runtime)
