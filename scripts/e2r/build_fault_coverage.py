#!/usr/bin/env python3
"""Frozen finite development matrix for distinct native fault mechanisms."""
import argparse
import copy
import json
import random
import sys
import time
import uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from scripts.e2r.build_parallel_native_resume import remap
from scripts.e2r.profile_native_intervention import db_binding
from methods.E2R.intervention.station_goal import goal_step
from methods.E2R.intervention.stateful_evidence import validate_state_goal
from methods.E2R.intervention.goal_policy import METHODS

FAULTS=('application_readiness','entry_route','active_replicas','owner_dependency')
CONDITIONS=('shared_state','isolated_state')
ARMS=('no_action','switch_standby','rollback_active')
PROBES={'old_health','old_capacity','new_capacity','old_cpu','peer_cpu'}

def save(path,value):
    Path(path).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')

def configure_fault(config,mechanism):
    """Exactly one owned field changes; all rollback/action definitions stay fixed."""
    case=config['cases'][0]
    if mechanism=='application_readiness':
        healthy=next(x['value'] for x in case['baseline_changes'] if x['entity']=='new' and x['path']=='/spec/template')
        bad=copy.deepcopy(healthy)
        bad['spec']['containers'][0]['readinessProbe'].update(tcpSocket={'port':1},failureThreshold=1)
        change={'entity':'new','path':'/spec/template','value':bad};wait=['new']
    elif mechanism=='entry_route':
        change={'entity':'entry','path':'/spec/selector','value':{'app':'e2r-unmatched-target'}};wait=[]
    elif mechanism=='active_replicas':
        change={'entity':'new','path':'/spec/replicas','value':0};wait=['new']
    elif mechanism=='owner_dependency':
        change={'entity':'db','path':'/spec/replicas','value':0};wait=['db']
    else:raise ValueError('unregistered fault mechanism')
    case.update(fault_changes=[change],fault_wait_entities=wait)

def build(root,runtime):
    root,runtime=Path(root).resolve(),Path(runtime).resolve()
    source=ROOT/'enhancement/method-stage/station_goal_transfer_20261003'
    endpoints=json.loads((root/'SERVICE_ENDPOINTS.json').read_text())['namespaces']
    base=json.loads((source/'assigned_v1/configs/assigned-shared-db-shared_state-r0.json').read_text())
    base_ns=base['experimental_namespace'];py=str(ROOT/'.venv-e2r/bin/python')
    model=ROOT/'enhancement/method-stage/goal_policy_v3_20261002/transition_model.json'
    all_jobs=[]
    for stage,repeats in [('assigned',2),('policy',1)]:
        q=root/(stage+'_v1');q.mkdir(exist_ok=False);(q/'configs').mkdir();jobs=[];assignments=[];first=[];slot=0
        for fi,fault in enumerate(FAULTS):
            for ci,condition in enumerate(CONDITIONS):
                for repeat in range(repeats):
                    lane=(fi+2*ci+repeat)%4;ns='e2r-isg-trainticket'+('' if lane==0 else f'-p{lane+1}')
                    ips={v['clusterIP']:endpoints[ns][name]['clusterIP'] for name,v in endpoints[base_ns].items()}
                    for mi,method in enumerate(('assigned',) if stage=='assigned' else METHODS):
                        c=remap(copy.deepcopy(base),base_ns,ns,ips);case=c['cases'][0]
                        identity=f'{stage}-{fault}-{condition}-r{repeat}'+('' if stage=='assigned' else '-'+method)
                        group='fault-coverage-'+fault;cond=fault+'__'+condition
                        case.update(group_id=group,comparison_group=group,condition_id=cond,diagnosis_label=fi+1)
                        for change in case['baseline_changes']:
                            if change['path']=='/spec/template' and change['entity'] in ('new','old','peer'):
                                store='private-db' if change['entity']=='peer' or (change['entity']=='old' and condition=='isolated_state') else 'shared-db'
                                change['value']=db_binding(change['value'],'trainticket',store)
                        case['baseline_changes']=[v for v in case['baseline_changes'] if not(v['path']=='/spec/replicas' and v['entity'] in ('db','db_private'))]+[{'entity':role,'path':'/spec/replicas','value':1} for role in ('db','db_private')]
                        healthy=next(x['value'] for x in case['baseline_changes'] if x['entity']=='new' and x['path']=='/spec/template')
                        c['policy']['rollback_options']['new']['known_good']['template']=copy.deepcopy(healthy)
                        configure_fault(c,fault)
                        c['family']='native_fault_mechanism_and_state_coverage'
                        c['diagnosis_label_mapping']={str(i+1):name for i,name in enumerate(FAULTS)}
                        goal={'id':str(uuid.uuid5(uuid.NAMESPACE_URL,'e2r-fault-coverage-v1-'+identity)),'name':'e2r_ack_fc_'+uuid.uuid5(uuid.NAMESPACE_URL,identity).hex,'stay_time':3}
                        c['state_continuity'].update(owner_database='shared-db',required_station=goal)
                        for rel in c['inventory']['relations']:
                            if rel['source']=='ack_state':rel['target']='db'
                        for spec in (c['workloads']['business'],c['workloads']['business']['canary']):
                            spec['steps']=[s for s in spec['steps'] if not s.get('station_goal_check')]+[goal_step(goal)]
                        c['inventory']['probes']=[p for p in c['inventory']['probes'] if p['id'] in PROBES]
                        c['initial_probes']=[p['id'] for p in c['inventory']['probes']]
                        c['collector_query_order_protocol']={'schema':'fault_coverage_five_application_probes_v1','initial':c['initial_probes'],'configuration_bindings':'observed Kubernetes configuration; does not establish availability','omitted_probe':'SQL shared_dependency stats; no scale actions in this catalog'}
                        c.update(stage='fault_coverage_development_'+stage,repeats=repeats,seed=20261003,station_registration=str(q/'PREREGISTRATION.json'),station_release=str(q/'RELEASE.json'),record_nondegrading_attempts=False)
                        if stage=='policy':
                            c.update(mode='goal_policy_comparison',arms=list(METHODS),initial_probes=[],goal_policy_authority='user_authorized_laboratory_only',goal_transition_model=str(model),goal_registration=str(q/'PREREGISTRATION.json'),goal_release=str(q/'RELEASE.json'),evidence_reuse_max_age_seconds=10.,retry_expired_reuse_once=True)
                            c['collector_query_order_protocol']['initial']=[]
                            c['goal_policy_protocol']={'schema':'fault_coverage_frozen_goal_v1','methods':list(METHODS),'source':'unchanged Cart18 empirical model','reuse_max_age_seconds':10.,'scope':'developmental mechanism coverage, not target fit or production qualification'}
                        arms=list(ARMS) if stage=='assigned' else [method]
                        random.Random(20261003+fi*100+ci*10+repeat).shuffle(arms)
                        c['scheduled_jobs']=[]
                        for arm in arms:
                            c['scheduled_jobs'].append({'group_id':group,'condition_id':cond,'repeat':repeat,'arm':arm,'original_schedule_slot':slot});slot+=1
                        validate_state_goal(c)
                        config=q/'configs'/(identity+'.json');out=q/'campaigns'/identity;save(config,c)
                        job={'id':identity,'command':[py,'-u',str(runtime/'scripts/e2r/run_native_campaign.py'),'--config',str(config),'--out',str(out)],'resources':['namespace:'+ns],'requires':[str(root/'RELEASE.json'),str(q/'RELEASE.json'),str(runtime/'RUNTIME.json')],'outputs':[{'path':str(out/'summary.json'),'json_equals':{'status':'complete'}}]}
                        included=(ci==fi%2 and repeat==0) if stage=='assigned' else (ci==fi%2 and mi==fi)
                        if included:
                            first.append(identity)
                            if stage=='policy':job['depends_on']=['assigned_results']
                        else:job['depends_on']=[stage+'_sanity']
                        jobs.append(job);assignments.append({'id':identity,'config':str(config),'out':str(out),'fault':fault,'condition':cond,'state_condition':condition,'repeat':repeat,'owner':'shared-db','rate':2.,'namespace':ns,'method':method})
        random.Random(20261003).shuffle(jobs)
        for phase,deps in [('sanity',first),('final',[a['id'] for a in assignments])]:
            jobs.append({'id':stage+('_sanity' if phase=='sanity' else '_results'),'kind':'gate','depends_on':deps,'command':[py,'-u',str(runtime/'scripts/e2r/summarize_fault_coverage.py'),'--root',str(root),'--stage',stage,'--phase',phase],'outputs':[{'path':str(q/('SANITY_GATE.json' if phase=='sanity' else 'RESULTS.json')),'json_equals':{'status':'passed' if phase=='sanity' else 'complete'}}]})
        save(q/'manifest.json',{'cwd':str(ROOT),'max_parallel':4,'jobs':jobs})
        save(q/'PREREGISTRATION.json',{'created_at':time.time(),'cohort':'fault_coverage_20261003_'+stage,'planned_positions':slot,'assignments':assignments,'included_first_jobs':first,'runtime':str(runtime),'scope':'Prospective developmental mechanism coverage; frozen old predictors, no fit on new results; not C1/C2 confirmation.'})
        all_jobs+=jobs
    master=root/'master_v1';master.mkdir(exist_ok=False)
    all_jobs.append({'id':'coverage_report','kind':'gate','depends_on':['policy_results'],'command':[py,'-u',str(runtime/'scripts/e2r/summarize_fault_coverage.py'),'--root',str(root)],'outputs':[{'path':str(root/'RESULTS.json'),'json_equals':{'status':'complete'}}]})
    save(master/'manifest.json',{'cwd':str(ROOT),'max_parallel':4,'max_dispatch_seconds':7200,'jobs':all_jobs})
    return {'assigned':48,'policy':32,'jobs':len(all_jobs),'master':str(master)}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--runtime',required=True);a=p.parse_args();print(json.dumps(build(a.root,a.runtime)))
