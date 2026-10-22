#!/usr/bin/env python3
"""Register at most four fixed-reference validity trajectories; stop on failure."""
import argparse
import copy
import json
from pathlib import Path
import random
import sys
import time
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from scripts.e2r.run_stability_diagnostic import REFERENCE_PROTOCOL


def build(source,out,runtime):
    source=Path(source).resolve();out=Path(out).resolve();runtime=Path(runtime).resolve()
    prior=json.loads((source/'PREREGISTRATION.json').read_text())
    lookup={(r['replica'],r['condition']):r for r in prior['assignments'] if r['mode']=='full8'}
    schedule=list(lookup);random.Random(20261004).shuffle(schedule)
    out.mkdir(parents=True,exist_ok=False);(out/'configs').mkdir();(out/'sanity').mkdir();jobs=[];assignments=[]
    for slot,key in enumerate(schedule):
        original=lookup[key];config=json.loads(Path(original['config']).read_text());replica,condition=key
        config['baseline_reference_protocol']=copy.deepcopy(REFERENCE_PROTOCOL)
        config['schema']='native_reference_validity_configuration_v1';config['stage']='no_fault_reference_validity'
        case=config['cases'][0];case['group_id']=case['comparison_group']='v2diag-fixed-reference-validity'
        for role in ('old','alternative','peer'):
            change=next(c for c in case['baseline_changes'] if c['entity']==role and c['path']=='/spec/template')
            change['value']['metadata']['annotations']['e2r.dev/diagnostic']='20261004-'+str(slot)
        identity=f'reference-{slot:02d}-r{replica}-{condition}'
        config['diagnostic']={'id':identity,'slot':slot,'mode':'full8','condition':condition,'replica':replica,
            'cohorts':34,'cohort_seconds':10,'startup_cohorts':6,'reference_cohorts':6,'passive_holdout_cohorts':6,
            'acquisition_after_seconds':180,'acquisition_deadline_seconds':300,'final_cohorts_start_seconds':310,
            'source_config':original['config'],'runtime':str(runtime)}
        file=out/'configs'/(identity+'.json');file.write_text(json.dumps(config,indent=2)+'\n');destination=out/'trajectories'/identity
        job={'id':identity,'command':[str(ROOT/'.venv-e2r/bin/python'),'-u',str(runtime/'scripts/e2r/run_stability_diagnostic.py'),
            '--config',str(file),'--out',str(destination)],'resources':['reference-validity-host','namespace:'+config['experimental_namespace']],
            'requires':[str(runtime/'RUNTIME.json'),str(out/('SANITY_RELEASE.json' if slot==0 else 'FULL_RELEASE.json'))],
            'outputs':[{'path':str(destination/'result.json'),'json_equals':{'status':'complete','cleanup_passed':True,'validity_gate_passed':True}}]}
        if slot:job['requires'].append(str(out/'SANITY_GATE.json'))
        jobs.append(job);assignments.append({**config['diagnostic'],'namespace':config['experimental_namespace'],'config':str(file),'out':str(destination)})
    assert len(jobs)==4
    for directory,subset in ((out/'sanity',jobs[:1]),(out,jobs[1:])):
        (directory/'manifest.json').write_text(json.dumps({'cwd':str(ROOT),'max_parallel':1,'jobs':subset},indent=2)+'\n')
    (out/'PREREGISTRATION.json').write_text(json.dumps({'schema':'fixed_reference_validity_gate_v1','frozen_at':time.time(),'maximum_trajectories':4,
        'seed':20261004,'assignments':assignments,'reference_protocol':REFERENCE_PROTOCOL,
        'scope':'Final bounded no-fault check of one proposed relative measurement reference. No repair or learned-policy qualification.',
        'stopping_rule':'First scientific validity failure, missing essential measurement or cleanup failure stops remaining slots; no replacements/retuning/repeated gate. Failure closes this measurement/repair branch.',
        'all_pass_implication':'Only permits proposing a new bounded development study for independent review; never an automatic repair release.'},indent=2)+'\n')
    print(json.dumps({'queue':str(out),'maximum_trajectories':len(jobs),'first':jobs[0]['id']}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',required=True,type=Path);p.add_argument('--out',required=True,type=Path);p.add_argument('--runtime',required=True,type=Path)
    a=p.parse_args();build(a.source,a.out,a.runtime)
