#!/usr/bin/env python3
"""Freeze eight sequential no-fault trajectories with one included sanity slot."""
import argparse
import copy
import json
from pathlib import Path
import random
import time
ROOT=Path(__file__).resolve().parents[2]


def build(source,out,runtime):
    source=Path(source).resolve();out=Path(out).resolve();runtime=Path(runtime).resolve()
    out.mkdir(parents=True,exist_ok=False);(out/'configs').mkdir();(out/'sanity').mkdir()
    schedule=[(repeat,condition,mode) for repeat in (0,1) for condition in ('affinity_A','affinity_B') for mode in ('passive','full8')]
    random.Random(20261003).shuffle(schedule);jobs=[];assignments=[]
    for slot,(repeat,condition,mode) in enumerate(schedule):
        original=source/'configs'/('boutique-symmetric-affinity-r'+str(repeat)+'.json')
        config=json.loads(original.read_text());case=copy.deepcopy(next(c for c in config['cases'] if c['condition_id']==condition))
        case['group_id']=case['comparison_group']='v2diag-no-fault-stability'
        case['fault_changes']=[];case.pop('fault_wait_entities',None)
        case.pop('fault_settle_seconds',None)
        for role in ('old','alternative','peer'):
            changes=[c for c in case['baseline_changes'] if c['entity']==role and c['path']=='/spec/template']
            if len(changes)!=1:raise ValueError('one baseline template per synchronized role required')
            changes[0]['value'].setdefault('metadata',{}).setdefault('annotations',{})['e2r.dev/diagnostic']='20261003-'+str(slot)
        blocks=[['old_health','old_capacity'],['alternative_health','alternative_capacity']]
        if repeat:blocks.reverse()
        order=sum(blocks,[])+['new_capacity','shared_dependency','old_cpu','peer_cpu']
        if sum(p['timeout'] for p in config['inventory']['probes'] if p['id'] in order)!=96:
            raise ValueError('registered full query timeout footprint changed')
        config.update(schema='native_stability_diagnostic_configuration_v1',stage='no_fault_protocol_diagnostic',cases=[case],
            arms=[],scheduled_jobs=[],initial_probes=order if mode=='full8' else [],
            initial_probe_order=order if mode=='full8' else [],synchronized_baseline_rollout_roles=['old','alternative','peer'])
        identity=f'trajectory-{slot:02d}-r{repeat}-{condition}-{mode}'
        config['diagnostic']={'id':identity,'slot':slot,'mode':mode,'condition':condition,'replica':repeat,
            'cohorts':20,'cohort_seconds':10,'baseline_cohorts':4,'acquisition_after_seconds':40,'acquisition_deadline_seconds':160,
            'final_cohorts_start_seconds':170,'source_config':str(original),'runtime':str(runtime)}
        file=out/'configs'/(identity+'.json');file.write_text(json.dumps(config,indent=2)+'\n');destination=out/'trajectories'/identity
        job={'id':identity,'command':[str(ROOT/'.venv-e2r/bin/python'),'-u',str(runtime/'scripts/e2r/run_stability_diagnostic.py'),
            '--config',str(file),'--out',str(destination)],'resources':['stability-diagnostic-host','namespace:'+config['experimental_namespace']],
            'requires':[str(runtime/'RUNTIME.json'),str(out/('SANITY_RELEASE.json' if slot==0 else 'FULL_RELEASE.json'))],
            'outputs':[{'path':str(destination/'result.json'),'json_equals':{'status':'complete','cleanup_passed':True}}]}
        if slot:job['requires'].append(str(out/'SANITY_GATE.json'))
        jobs.append(job);assignments.append({**config['diagnostic'],'namespace':config['experimental_namespace'],'config':str(file),'out':str(destination)})
    for folder,subset in ((out/'sanity',jobs[:1]),(out,jobs[1:])):
        (folder/'manifest.json').write_text(json.dumps({'cwd':str(ROOT),'max_parallel':1,'jobs':subset},indent=2)+'\n')
    (out/'PREREGISTRATION.json').write_text(json.dumps({'schema':'no_fault_observation_stability_diagnostic_v1','frozen_at':time.time(),
        'maximum_trajectories':8,'seed':20261003,'source_queue':str(source),'assignments':assignments,
        'scope':'Fixed development diagnostic only; stopped16 immutable; no injected fault, learned decision or repair',
        'stopping_rule':'No substitute or repeated trajectories. Stop remaining jobs on failed cleanup or infrastructure failure; preserve invalid baseline/query observations.',
        'analysis':'Absolute20-cohort trajectories and unchanged baseline formula; full-vs-passive, same workload; no retrospective old-label rewrite.'},indent=2)+'\n')
    print(json.dumps({'queue':str(out),'trajectories':len(jobs),'first_sanity':jobs[0]['id']}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--runtime',type=Path,required=True)
    a=p.parse_args();build(a.source,a.out,a.runtime)
