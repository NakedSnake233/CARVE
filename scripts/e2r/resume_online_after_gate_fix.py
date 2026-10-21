#!/usr/bin/env python3
"""Resume never-started policy positions after the included32 verifier-only fix."""
import argparse
import copy
import json
from pathlib import Path
import time
ROOT=Path(__file__).resolve().parents[2]


def build(original,out,gate_runtime):
    original,out,gate_runtime=map(lambda p:Path(p).resolve(),(original,out,gate_runtime))
    manifest=json.loads((original/'manifest.json').read_text())
    state=json.loads((original/'queue_state.json').read_text())
    reg=json.loads((original/'PREREGISTRATION.json').read_text())
    if state['status']!='stopped':raise ValueError('original scheduler is not stopped')
    native=[j for j in manifest['jobs'] if j.get('kind')!='gate']
    complete=[j for j in native if state['jobs'][j['id']]['status']=='completed']
    pending=[j for j in native if state['jobs'][j['id']]['status']=='blocked_dependency']
    if len(complete)!=8 or len(pending)!=112 or {j['id'] for j in complete}!=set(reg['first_included_jobs']):
        raise ValueError('not the gate-only failure with32 completed and448 unstarted positions')
    by_id={a['id']:a for a in reg['assignments']}
    for job in pending:
        if (original/'jobs'/job['id']/'started.json').exists() or Path(by_id[job['id']]['out']).exists():
            raise ValueError('refuse to reschedule an already-started source')
    out.mkdir(parents=True,exist_ok=False);(out/'configs').mkdir()
    gate_id='verify_included_online_sanity_repaired';gate_path=out/'SANITY_GATE.json'
    jobs=[{'id':gate_id,'kind':'gate','requires':[str(out/'RELEASE.json'),str(gate_runtime/'RUNTIME.json')],
        'command':[str(ROOT/'.venv-e2r/bin/python'),'-u',str(gate_runtime/'scripts/e2r/verify_online_sanity.py'),
                   '--queue',str(original),'--out',str(gate_path)],
        'outputs':[{'path':str(gate_path),'json_equals':{'status':'passed'}}]}]
    assignments=[]
    for job in pending:
        assignment=copy.deepcopy(by_id[job['id']]);old_config=Path(assignment['config'])
        config=json.loads(old_config.read_text());config['laboratory_release']=str(out/'RELEASE.json')
        config['laboratory_registration']=str(out/'PREREGISTRATION.json')
        config_path=out/'configs'/old_config.name;destination=out/'campaigns'/job['id']
        config_path.write_text(json.dumps(config,indent=2)+'\n')
        assignment.update(config=str(config_path),out=str(destination),original_config=str(old_config),original_registered_out=assignment['out'])
        assignments.append(assignment)
        new=copy.deepcopy(job)
        new['command'][new['command'].index('--config')+1]=str(config_path)
        new['command'][new['command'].index('--out')+1]=str(destination)
        new.update(depends_on=[gate_id],requires=[str(out/'RELEASE.json'),str(Path(reg['runtime'])/'RUNTIME.json'),str(gate_path)],
            outputs=[{'path':str(destination/'summary.json'),'json_equals':{'status':'complete'}}])
        jobs.append(new)
    new_reg={**reg,'schema':'laboratory_online_gate_only_recovery_v1','frozen_at':time.time(),
        'original_queue':str(original),'original_registration':str(original/'PREREGISTRATION.json'),
        'planned_attempts':448,'native_jobs':112,'first_included_jobs':[], 'assignments':assignments,
        'original_completed_attempts':32,'gate_runtime':str(gate_runtime),
        'repair_scope':'Normalize plan container representation in verifier only. Native execution runtime, models, thresholds, schedules and all covered positions unchanged.'}
    (out/'PREREGISTRATION.json').write_text(json.dumps(new_reg,indent=2)+'\n')
    (out/'manifest.json').write_text(json.dumps({'cwd':str(ROOT),'max_parallel':8,'jobs':jobs},indent=2)+'\n')
    print(json.dumps({'queue':str(out),'new_native_jobs':112,'remaining_positions':448,'preserved_positions':32,'native_runtime':reg['runtime']}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--original',required=True);p.add_argument('--out',required=True);p.add_argument('--gate-runtime',required=True)
    a=p.parse_args();build(a.original,a.out,a.gate_runtime)
