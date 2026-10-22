#!/usr/bin/env python3
"""120 development replications with no external benchmark launch prerequisite."""
import argparse
import json
from pathlib import Path
import random
import sys
import time
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.platform import KubectlClient
from scripts.e2r.build_revision_native_pilot import SOURCES,paired_config
from scripts.e2r.build_parallel_native_resume import remap


def build(out,runtime):
    out=Path(out).resolve();runtime=Path(runtime).resolve();out.mkdir(parents=True,exist_ok=False);(out/'configs').mkdir()
    jobs=[];assignments=[];first=[];seed=20261005
    for app_index,(app,source_file) in enumerate(SOURCES.items()):
        base=json.loads((ROOT/source_file).read_text());origin=base['experimental_namespace'];services=KubectlClient(origin).list('Service')
        namespaces=['e2r-isg-'+app+suffix for suffix in ('','-p2','-p3','-p4')];maps={}
        for ns in namespaces:
            client=KubectlClient(ns);labels=client._call(['get','namespace',ns,'-o','json'])['metadata'].get('labels',{})
            if labels.get('e2r-owner')!='e2r-intervention-native-20260929':raise ValueError('unowned exploratory namespace')
            maps[ns]={s['spec']['clusterIP']:client.get('Service',s['metadata']['name'])['spec']['clusterIP'] for s in services}
        for stress_index,stress in enumerate(('low','stressed')):
            for condition_index,condition in enumerate(('A','B')):
                for repeat in range(5):
                    ns=namespaces[(2*stress_index+condition_index+repeat)%4]
                    group='v2explore-dependency-'+stress+'-01';parent='v2dev-dependency-'+stress+'-01'
                    config=paired_config(base,app,group,stress,condition,5,seed+100*app_index+stress_index)
                    config=remap(config,origin,ns,maps[ns]);config['cases'][0]['comparison_group']=parent
                    config['scheduled_jobs']=[j for j in config['scheduled_jobs'] if j['repeat']==repeat]
                    initial=[p['id'] for p in config['inventory']['probes'] if p['id'] in config['initial_probes']]
                    config.update(mode='fixed_observation_evidence',stage='user_authorized_exploratory_collection',
                        initial_probe_order=initial,synchronized_baseline_rollout_roles=['old','peer'],monitor_execution_safety=True,
                        observation_budget={'max_calls':0,'max_cost':0.,'max_seconds':30.},
                        total_probe_budget={'max_calls':16,'max_cost':48.,'max_seconds':180.},
                        experimental_evaluation={'external_slo_required':False,'external_benchmark_required':False,
                            'reference_validity_gate_required':False,'interpretation':'Exploratory measurements in known development configurations; raw/relative outcomes, not independent confirmation.'})
                    identity=f'{app}-{stress}-{condition}-r{repeat}';path=out/'configs'/(identity+'.json');path.write_text(json.dumps(config,indent=2)+'\n')
                    destination=out/'campaigns'/identity
                    job={'id':identity,'command':[str(ROOT/'.venv-e2r/bin/python'),'-u',str(runtime/'scripts/e2r/run_native_campaign.py'),
                        '--config',str(path),'--out',str(destination)],'resources':['namespace:'+ns],
                        'requires':[str(runtime/'RUNTIME.json'),str(out/'RELEASE.json')],
                        'outputs':[{'path':str(destination/'summary.json'),'json_equals':{'status':'complete'}}]}
                    jobs.append(job)
                    if repeat==0:first.append(identity)
                    assignments.append({'id':identity,'application':config['application'],'group':config['application']+'/'+group,
                        'comparison_group':config['application']+'/'+parent,'stress':stress,'condition':condition,'repeat':repeat,
                        'namespace':ns,'config':str(path),'out':str(destination),'slots':[j['original_schedule_slot'] for j in config['scheduled_jobs']]})
    random.Random(seed).shuffle(jobs)
    for job in jobs:
        if job['id'] not in first:job['depends_on']=['verify_included_sanity']
    gate={'id':'verify_included_sanity','kind':'gate','depends_on':first,
          'command':[str(ROOT/'.venv-e2r/bin/python'),'-u',str(runtime/'scripts/e2r/verify_exploratory_sanity.py'),'--queue',str(out)],
          'outputs':[{'path':str(out/'SANITY_GATE.json'),'json_equals':{'status':'passed'}}]}
    jobs.append(gate)
    (out/'manifest.json').write_text(json.dumps({'cwd':str(ROOT),'max_parallel':8,'jobs':jobs},indent=2)+'\n')
    (out/'PREREGISTRATION.json').write_text(json.dumps({'schema':'exploratory_continuation_without_external_benchmark_v1','frozen_at':time.time(),
        'authorization':'User requested experiments run without these validation benchmarks on2026-10-01; supersedes externalSLO/reference-validity launch prerequisite for this new exploratory phase.',
        'seed':seed,'planned_attempts':120,'native_jobs':40,'max_parallel':8,'first_included_jobs':first,'assignments':assignments,
        'external_benchmark_required':False,'scientific_pass_required_to_continue_collection':False,
        'data_scope':'Known development configuration families, all new attempts; old results/failed reference gate immutable.',
        'split_intent':{'additional_fit':'low groups','exploratory_transfer':'stressed groups','checkpoint_selection':'original historical validation','old_test':'excluded'},
        'unknown_policy':'Retain failures/rejections/unknowns and resume only uncovered slots after verified environment restoration.'},indent=2)+'\n')
    print(json.dumps({'queue':str(out),'native_jobs':40,'attempts':120,'max_parallel':8}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True,type=Path);p.add_argument('--runtime',required=True,type=Path)
    a=p.parse_args();build(a.out,a.runtime)
