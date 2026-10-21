#!/usr/bin/env python3
"""Freeze prospective development-only paired dependency assignments in native slices."""
import argparse
import copy
import json
from pathlib import Path
import random
import sys
import time
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.platform import KubectlClient
from scripts.e2r.build_parallel_native_resume import remap
from scripts.e2r.profile_native_intervention import db_binding,cpu_template

SOURCES={
'boutique':'enhancement/queues/parallel2_formal_v4/campaigns/boutique-F3-config-01/protocol.json',
'trainticket':'enhancement/queues/parallel8_formal_v2/campaigns/trainticket-F3-config-01/protocol.json'}


def paired_config(base,application,group,stress,assignment,repeats,seed):
    if stress not in ('low','stressed') or assignment not in ('A','B'):raise ValueError('unregistered pilot condition')
    config=copy.deepcopy(base)
    for key in ('scheduled_jobs','amendment','resume','recovery','background_amendment'):config.pop(key,None)
    templates={r['entity']:copy.deepcopy(r['value']) for r in base['cases'][0]['baseline_changes'] if r['path']=='/spec/template'}
    for role in ('old','new','peer'):
        # Identical scalar resource allocation across A/B, and old/new allocations matched.
        templates[role]['spec']['containers'][0]['resources']=copy.deepcopy(templates['old']['spec']['containers'][0]['resources'])
    if stress=='low':
        for container,reference in zip(templates['db']['spec']['containers'],templates['db_private']['spec']['containers']):
            container['resources']=copy.deepcopy(reference['resources'])
    else:templates['db']=cpu_template(templates['db'],2.)
    for role,database in {'old':'private-db' if assignment=='A' else 'shared-db',
                          'new':'shared-db' if assignment=='A' else 'private-db','peer':'shared-db'}.items():
        templates[role]=db_binding(templates[role],application,database)
    prefix='cart' if application=='boutique' else 'station'
    load=8. if stress=='low' else 12.;peer=8. if stress=='low' else 24.
    baseline=[{'entity':role,'path':'/spec/template','value':template} for role,template in templates.items()]
    baseline += [{'entity':role,'path':'/spec/replicas','value':1} for role in ('old','new','peer')]
    baseline += [{'entity':'entry','path':'/spec/selector','value':{'app':prefix+'-new'}}]
    broken=copy.deepcopy(templates['new']);broken['spec']['containers'][0]['readinessProbe'].update(tcpSocket={'port':1},failureThreshold=1)
    condition='standby_independent_active_shared' if assignment=='A' else 'standby_shared_active_independent'
    config.update(schema='native_revision_development_pilot_v2',stage='revision_development_pilot',family='same_readiness_diagnosis_dependency_assignment',
        repeats=repeats,seed=seed,traffic_process=True,dispatch_delay_floor_seconds=2.,resource_lock_directory=str(ROOT/'enhancement/namespace_locks'),
        continuous_workload=True,record_nondegrading_attempts=True,direct_baseline_restore=True,retain_baseline_context=True,
        baseline_consecutive_windows=2,baseline_health_timeout=240,convergence_timeout=300,reset_health_timeout=300,measurement_timeout=150,
        interpretation='Prospective DEVELOPMENT pilot, not confirmatory test. Physical dependency assignments are changed; all effects are measured, never assumed.')
    config['policy']['rollback_options']['new']['known_good']['template']=copy.deepcopy(templates['new'])
    config['plans']=[p for p in config['plans'] if p['id'] in ('no_action','switch_standby','rollback_active')]
    config['arms']=['no_action','switch_standby','rollback_active']
    config['cases']=[{'group_id':group,'comparison_group':group,'condition_id':condition,'diagnosis_label':1,
        'offered_rps':load,'peer_rps':peer,'baseline_changes':baseline,
        'fault_changes':[{'entity':'new','path':'/spec/template','value':broken}],
        'fault_wait_entities':['new'],'baseline_settle_seconds':3,'fault_settle_seconds':10}]
    if application=='boutique':
        background=config['background_workloads']['background'];background.update(offered_rps=4 if stress=='low' else 16,client_connection_budget=192)
    config['workloads']['business']['offered_rps']=load;config['workloads']['peer']['offered_rps']=peer
    # The paired assignment has a common slot space; separate catalogs preserve
    # explicit approval of each baseline template without changing policy mid-run.
    offset=0 if assignment=='A' else 3*repeats
    schedule=[{'group_id':group,'condition_id':condition,'repeat':repeat,'arm':arm,'original_schedule_slot':offset+3*repeat+i}
              for repeat in range(repeats) for i,arm in enumerate(config['arms'])]
    random.Random(seed).shuffle(schedule);config['scheduled_jobs']=schedule
    config['revision_design']={'assignment':assignment,'stress':stress,'same_fine_diagnosis':1,'development_only':True,
        'paired_scalar_configurations_matched':True,'telemetry_equality_assumed':False,
        'catalog_semantics':'same role-aligned restore-to-approved-template operation; both distinct templates and their actual bindings are recorded'}
    return config


def build(out,runtime,repeats):
    out=Path(out).resolve();runtime=Path(runtime).resolve();out.mkdir(parents=True,exist_ok=False);(out/'configs').mkdir()
    jobs=[];assignments=[];groups=[];seed=20261001
    for application,source in SOURCES.items():
        base=json.loads((ROOT/source).read_text());origin=base['experimental_namespace'];client=KubectlClient(origin)
        services=client.list('Service')
        for i,stress in enumerate(('low','stressed')):
            namespace='e2r-isg-'+application+('' if i==0 else '-p2');target=KubectlClient(namespace)
            labels=target._call(['get','namespace',namespace,'-o','json'])['metadata'].get('labels',{})
            if labels.get('e2r-owner')!='e2r-intervention-native-20260929':raise ValueError('pilot namespace not owned')
            ips={s['spec']['clusterIP']:target.get('Service',s['metadata']['name'])['spec']['clusterIP'] for s in services}
            group=f'v2dev-dependency-{stress}-01';groups.append(application+'-native-dependency-slice/'+group)
            order=['A','B'] if (i+(0 if application=='boutique' else 1))%2==0 else ['B','A']
            predecessor=None
            for assignment in order:
                c=paired_config(base,application,group,stress,assignment,repeats,seed+i)
                c=remap(c,origin,namespace,ips);c['revision_design']['source_config']=str((ROOT/source).resolve())
                identity=application+'-'+group+'-'+assignment;path=out/'configs'/(identity+'.json');path.write_text(json.dumps(c,indent=2)+'\n')
                destination=out/'campaigns'/identity
                job={'id':identity,'command':[str(ROOT/'.venv-e2r/bin/python'),'-u',str(runtime/'scripts/e2r/run_native_campaign.py'),'--config',str(path),'--out',str(destination)],
                    'resources':['namespace:'+namespace],'requires':[str(runtime/'RUNTIME.json'),str(out/'RELEASE.json')],
                    'outputs':[{'path':str(destination/'summary.json'),'json_equals':{'status':'complete'}}]}
                if predecessor:job['depends_on']=[predecessor]
                jobs.append(job);predecessor=identity
                assignments.append({'id':identity,'group':application+'-native-dependency-slice/'+group,'condition':assignment,'namespace':namespace,
                    'config':str(path),'path':str(destination),'slots':len(c['scheduled_jobs']),'source_train_config':str((ROOT/source).resolve())})
    manifest={'schema':'native_revision_pilot_queue_v2','cwd':str(ROOT),'max_parallel':4,'jobs':jobs}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (out/'PREREGISTRATION.json').write_text(json.dumps({'schema':'native_revision_pilot_registration_v2','frozen_at':time.time(),
        'purpose':'native developmental mechanism feasibility only; not independent confirmation','groups':groups,'assignments':assignments,
        'planned_attempts':len(assignments)*3*repeats,'repeats_per_arm_condition':repeats,'seeds':{'schedule':seed},
        'design':'same readiness-drift mechanism, complementary actual DB bindings, no-action/switch/rollback arms; low and stressed resource contexts',
        'predeclared_interpretation':'Two repeats establish operational feasibility only, not statistical distinction/equivalence or C1/C2 support.',
        'limitations':['A/B catalogs run in counterbalanced blocks within each namespace; a time-block confound remains at pilot stage.',
        'All measured scalar covariates are kept; no input is overwritten to force feature equality.',
        'Baseline-relative SLO and transient rollout effects are retained; contrast is a hypothesis, not a designed label.'],
        'on_failure':'Preserve failed attempts; repair and resume only uncovered slots after restored-environment checks.',
        'next_gate':'Inspect measurements, freeze a new qualified data/validation/confirmation protocol before further efficacy claims.'},indent=2)+'\n')
    print(json.dumps({'queue':str(out),'jobs':len(jobs),'attempts':len(assignments)*3*repeats,'parallel':4}))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True,type=Path);p.add_argument('--runtime',required=True,type=Path);p.add_argument('--repeats',type=int,default=2);a=p.parse_args();build(a.out,a.runtime,a.repeats)
