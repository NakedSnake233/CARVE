#!/usr/bin/env python3
"""Freeze sixteen developmental trials with symmetric standbys and a reference DB."""
import argparse
import copy
from dataclasses import asdict
import json
from pathlib import Path
import random
import sys
import time
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.platform import KubectlClient
from methods.E2R.intervention.schema import Action,Plan
from scripts.e2r.build_native_campaign import transaction
from scripts.e2r.build_parallel_native_resume import remap
from scripts.e2r.profile_native_intervention import db_binding,cpu_template
from scripts.e2r.prepare_identifiability_native import NAMESPACES,OWNER
SOURCE=ROOT/'enhancement/queues/parallel2_formal_v4/campaigns/boutique-F3-config-01/protocol.json'


def configure(base,namespace,templates,alternative_workload):
    c=copy.deepcopy(base)
    templates=copy.deepcopy(templates)
    if c['experimental_namespace']!=namespace:raise ValueError('localize namespace and Service addresses before configuring the trial')
    for key in ('scheduled_jobs','amendment','resume','recovery','background_amendment'):c.pop(key,None)
    c.update(schema='native_identifiability_development_v1',stage='identifiability_development',repeats=2,seed=20261002,
        family='same_readiness_symmetric_standby_dependency_assignment',native_roles=['old','new','peer','alternative'],
        observe_configuration_bindings=True,traffic_process=True,dispatch_delay_floor_seconds=2.,
        synchronized_baseline_rollout_roles=['peer'],
        resource_lock_directory=str(ROOT/'enhancement/namespace_locks'),convergence_timeout=300,
        baseline_health_timeout=240,reset_health_timeout=300,measurement_timeout=150,
        collection_observation_scope='Full registered development observations; initial masked_graph is a separate factual prefix, not a live C2 efficacy result.')
    c['direct_workloads']['alternative']=copy.deepcopy(alternative_workload)
    c['inventory']['entities'] += [
        {'id':'alternative','role':'deployment','kind':'Deployment','namespace':namespace,'name':'cart-alternative','binding':'alternate-standby-service'},
        {'id':'db_reference','role':'shared_dependency','kind':'Deployment','namespace':namespace,'name':'reference-db','binding':'reference-storage'}]
    c['inventory']['relations']=[r for r in c['inventory']['relations'] if r['kind']!='competes_for']
    c['inventory']['relations'].append({'source':'entry','target':'alternative','kind':'depends_on','observed_at':time.time(),'ttl':1209600,'origin':'contract'})
    for name in ('healthy','capacity'):
        original=next(p for p in c['inventory']['probes'] if p['id']=='old_'+('health' if name=='healthy' else 'capacity'))
        p=copy.deepcopy(original);p['id']='alternative_'+('health' if name=='healthy' else 'capacity');p['entity']='alternative'
        if p['kind']=='native_graph':
            for value in p['possible_values']:
                for f in value['facts']:f['entity']='alternative'
        c['inventory']['probes'].append(p)
    shared=next(p for p in c['inventory']['probes'] if p['id']=='shared_dependency');shared['timeout']=30
    shared['possible_values']=[]
    for host in ('db','db_private'):
        for headroom in (8,96):
            shared['possible_values'].append({'facts':[
                *[{'entity':db,'variable':'connection_headroom','value':headroom} for db in ('db','db_private','db_reference')],
                *[{'entity':role,'variable':'connections_per_replica','value':2} for role in c['native_roles']],
                *[{'entity':role,'variable':'dependencies_known','value':True} for role in c['native_roles']]],
                'relations':[{'source':role,'target':db,'kind':'competes_for','validity':'present' if db==destination else 'absent'}
                    for role,destination in [('old','db'),('alternative','db_private'),('new','db_reference'),('peer',host)]
                    for db in ('db','db_private','db_reference')]})
    c['initial_probes']=['old_health','old_capacity','alternative_health','alternative_capacity','new_capacity','shared_dependency','old_cpu','peer_cpu']
    c['collector_query_order_protocol']={'schema':'counterbalanced_standby_probe_order_v1','rule':'(repeat+arm_index)%2 reverses the two standby health/capacity blocks; remaining probes have fixed order'}
    c['policy']['allowed_targets']=sorted(set(c['policy']['allowed_targets'])|{'alternative','db_reference','db_private'})
    c['policy']['max_replicas']['alternative']=3
    c['policy']['selector_options']['entry']['alternative']={'selector':{'app':'cart-alternative'},'destination':'alternative'}
    plans=[Plan('no_action',()),Plan('switch_standby',(Action('restore_selector','entry',{'option':'standby'},'old'),)),
           Plan('switch_alternative',(Action('restore_selector','entry',{'option':'alternative'},'alternative'),)),
           Plan('rollback_active',(Action('rollback_version','new',{'option':'known_good'},'approved_new'),))]
    c['plans']=[p.to_dict() for p in plans];c['arms']=[p.id for p in plans]
    # Scalar allocations are identical for both treatment assignments.
    for role in ('old','new','peer','alternative'):
        templates[role]['spec']['containers'][0]['resources']=copy.deepcopy(templates['old']['spec']['containers'][0]['resources'])
    for role in ('db','db_private'):
        container=templates[role]['spec']['containers'][0];container['resources']['limits']['cpu']='25m';container['resources']['requests']['cpu']='25m'
    templates['db_reference']['spec']['containers'][0]['resources']['limits']['cpu']='200m'
    templates['old']=db_binding(templates['old'],'boutique','shared-db')
    templates['alternative']=db_binding(templates['alternative'],'boutique','private-db')
    templates['new']=db_binding(templates['new'],'boutique','reference-db')
    c['policy']['rollback_options']['new']['known_good']['template']=copy.deepcopy(templates['new'])
    c['workloads']['business']['offered_rps']=10.;c['workloads']['peer']['offered_rps']=20.
    c['background_workloads']['background'].update(offered_rps=32,payload_bytes=262144,client_connection_budget=192)
    group='v2dev-symmetric-affinity-01';cases=[]
    for condition,peer_database in [('affinity_A','shared-db'),('affinity_B','private-db')]:
        states=copy.deepcopy(templates);states['peer']=db_binding(states['peer'],'boutique',peer_database)
        baseline=[{'entity':key,'path':'/spec/template','value':value} for key,value in states.items()]
        baseline += [{'entity':role,'path':'/spec/replicas','value':1} for role in c['native_roles']]
        baseline += [{'entity':'entry','path':'/spec/selector','value':{'app':'cart-new'}}]
        bad=copy.deepcopy(states['new']);bad['spec']['containers'][0]['readinessProbe'].update(tcpSocket={'port':1},failureThreshold=1)
        cases.append({'group_id':group,'comparison_group':group,'condition_id':condition,'diagnosis_label':1,
            'offered_rps':10.,'peer_rps':20.,'background_bindings':{'background':peer_database},
            'baseline_changes':baseline,'fault_changes':[{'entity':'new','path':'/spec/template','value':bad}],
            'fault_wait_entities':['new'],'baseline_settle_seconds':3,'fault_settle_seconds':10})
    c['cases']=cases
    jobs=[]
    for case in cases:
        for repeat in range(2):
            for index,arm in enumerate(c['arms']):
                blocks=[['old_health','old_capacity'],['alternative_health','alternative_capacity']]
                if (repeat+index)%2:blocks.reverse()
                order=sum(blocks,[])+['new_capacity','shared_dependency','old_cpu','peer_cpu']
                jobs.append({'group_id':group,'condition_id':case['condition_id'],'repeat':repeat,'arm':arm,
                    'original_schedule_slot':len(jobs),'initial_probe_order':order})
    random.Random(c['seed']).shuffle(jobs);c['scheduled_jobs']=jobs
    return c


def build(setup,out,runtime):
    setup=Path(setup).resolve();out=Path(out).resolve();runtime=Path(runtime).resolve()
    if json.loads((setup/'READY.json').read_text())['status']!='passed':raise ValueError('new owned resources not ready')
    base=json.loads(SOURCE.read_text());origin=base['experimental_namespace'];source=KubectlClient(origin);services=source.list('Service')
    out.mkdir(parents=True,exist_ok=False);(out/'configs').mkdir();jobs=[];assignments=[]
    for repeat,ns in enumerate(NAMESPACES):
        c=KubectlClient(ns);labels=c._call(['get','namespace',ns,'-o','json'])['metadata'].get('labels',{})
        if labels.get('e2r-owner')!=OWNER:raise ValueError('namespace ownership differs')
        mapping={s['spec']['clusterIP']:c.get('Service',s['metadata']['name'])['spec']['clusterIP'] for s in services}
        local=remap(base,origin,ns,mapping)
        names={'old':'cart-old','new':'cart-new','peer':'cart-peer','alternative':'cart-alternative','db':'shared-db','db_private':'private-db','db_reference':'reference-db'}
        templates={role:c.get('Deployment',name)['spec']['template'] for role,name in names.items()}
        service=c.get('Service','cart-alternative');workload=transaction('boutique','http://'+service['spec']['clusterIP']+':7070','boutique-direct-alternative')
        config=configure(local,ns,templates,workload);config['scheduled_jobs']=[j for j in config['scheduled_jobs'] if j['repeat']==repeat]
        identity='boutique-symmetric-affinity-r'+str(repeat);file=out/'configs'/(identity+'.json');file.write_text(json.dumps(config,indent=2)+'\n');destination=out/'campaigns'/identity
        jobs.append({'id':identity,'command':[str(ROOT/'.venv-e2r/bin/python'),'-u',str(runtime/'scripts/e2r/run_native_campaign.py'),'--config',str(file),'--out',str(destination)],
            'resources':['namespace:'+ns],'requires':[str(out/'RELEASE.json'),str(setup/'READY.json'),str(runtime/'RUNTIME.json')],
            'outputs':[{'path':str(destination/'summary.json'),'json_equals':{'status':'complete'}}]})
        assignments.append({'id':identity,'namespace':ns,'repeat':repeat,'config':str(file),'slots':len(config['scheduled_jobs'])})
    (out/'manifest.json').write_text(json.dumps({'cwd':str(ROOT),'max_parallel':2,'jobs':jobs},indent=2)+'\n')
    (out/'PREREGISTRATION.json').write_text(json.dumps({'schema':'native_symmetric_standby_development_v1','frozen_at':time.time(),'planned_attempts':16,
        'purpose':'mechanism feasibility only, not C1/C2 confirmation','design':'Joint peer and registered background affinity across equally resourced standby DBs; common reference baseline.',
        'group':'boutique-native-dependency-slice/v2dev-symmetric-affinity-01','assignments':assignments,
        'load_parameters_source':'Prior training F3 profile, not new confirmation outcomes','stopping_rule':['No crossover or one fixed switch always best: stop this design.','Both alternatives unsafe/unavailable: stop.','Inconsistent two repeats: inconclusive.','Coherent crossover: only proceed to new preregistered replication.'],
        'strong_rule_control':'Avoid peer/background-shared dependency; tie with this rule does not prove learning necessary.',
        'limits':['Capacity probes are interventions; ordering is counterbalanced and raw histories retained.','All telemetry retained; no artificial equality.','Initial/full views are separately scoped; full collector is not a budget-capped live policy.']},indent=2)+'\n')
    print(json.dumps({'queue':str(out),'jobs':2,'attempts':16}))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--setup',required=True,type=Path);p.add_argument('--out',required=True,type=Path);p.add_argument('--runtime',required=True,type=Path);a=p.parse_args();build(a.setup,a.out,a.runtime)
