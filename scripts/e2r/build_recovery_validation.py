#!/usr/bin/env python3
"""Freeze the selected compiled fix and a common preincident reference for 18 trials."""
import argparse,copy,json,random,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from scripts.e2r.build_startup_study import runtime_template

def build(out,runtime):
    out,runtime=Path(out).resolve(),Path(runtime).resolve();out.mkdir(parents=True,exist_ok=False);(out/'configs').mkdir()
    diagnostic=ROOT/'enhancement/data-repair-stage/rollback_r2r_20261002_v1';results=json.loads((diagnostic/'RESULTS.json').read_text())
    chosen=[r for r in results['attempts'] if r['variant']=='r2r_no_tiering'];assert len(chosen)==3 and all(r['effects']['business_recovered'] and r['effects']['non_target_safe'] for r in chosen)
    floor=17.888549715280533
    assert all(max(r['post_p95_ms'])<=floor and all(r['post_state_canaries']) for r in chosen)
    source=ROOT/'enhancement/data-repair-stage/state_continuity_20261002_v1/configs';jobs=[];assignments=[];first=[]
    artifact=json.loads(next((diagnostic/'configs').glob('r2r_no_tiering-*.json')).read_text())['application_runtime_settings']
    for ci,condition in enumerate(('shared_state','isolated_state')):
        for repeat in range(3):
            config=json.loads((source/f'boutique-{condition}-r{repeat}.json').read_text());case=config['cases'][0]
            for change in case['baseline_changes']:
                if change['entity'] in ('old','new','peer') and change['path']=='/spec/template':change['value']=runtime_template(change['value'],'r2r_no_tiering')
            for change in case['fault_changes']:
                if change['entity']=='new':change['value']=runtime_template(change['value'],'r2r_no_tiering')
            healthy=next(c['value'] for c in case['baseline_changes'] if c['entity']=='new' and c['path']=='/spec/template')
            config['policy']['rollback_options']['new']['known_good']['template']=copy.deepcopy(healthy)
            config['baseline_latency_ceiling_ms']=config['verification']['latency_floor_ms']/config['verification']['latency_multiplier']
            assert config['verification']['latency_floor_ms']==floor
            config['startup_diagnostics']=True;config['application_runtime_settings']=copy.deepcopy(artifact)
            identity=f'{condition}-r{repeat}';required=config['state_continuity']['required_cart'];required['user_id']='e2r-ack-r2r-validation-'+identity
            config['workloads']['business']['continuity_check']=copy.deepcopy(required);config['workloads']['business']['canary']['continuity_check']=copy.deepcopy(required)
            config.pop('revision_design',None);config['stage']='stateful_recovery_fix_validation';config['repeats']=3
            config['interpretation']='Development validation of fixed startup artifact and common baseline reference. Same known parent mechanism; not independent confirmation.'
            arms=['no_action','switch_standby','rollback_active'];random.Random(20261102+ci*10+repeat).shuffle(arms)
            config['scheduled_jobs']=[{'group_id':case['group_id'],'condition_id':condition,'repeat':repeat,'arm':arm,'original_schedule_slot':ci*9+repeat*3+['no_action','switch_standby','rollback_active'].index(arm)} for arm in arms]
            path=out/'configs'/f'{identity}.json';path.write_text(json.dumps(config,indent=2)+'\n');destination=out/'campaigns'/identity
            j={'id':identity,'command':[str(ROOT/'.venv-e2r/bin/python'),'-u',str(runtime/'scripts/e2r/run_native_campaign.py'),'--config',str(path),'--out',str(destination)],
                'resources':['namespace:'+config['experimental_namespace']],'requires':[str(out/'RELEASE.json'),str(runtime/'RUNTIME.json'),str(ROOT/'enhancement/builds/cart_r2r_20261002_v1/BUILD.json')],
                'outputs':[{'path':str(destination/'summary.json'),'json_equals':{'status':'complete'}}]}
            if (ci==0 and repeat in (0,2)) or (ci==1 and repeat==1):first.append(identity)
            else:j['depends_on']=['common_reference_sanity']
            jobs.append(j);assignments.append({'id':identity,'config':str(path),'out':str(destination),'condition':condition,'repeat':repeat,'namespace':config['experimental_namespace']})
    random.Random(20261102).shuffle(jobs)
    for stage,deps,name in [('sanity',first,'common_reference_sanity'),('final',[a['id'] for a in assignments],'validation_final')]:
        jobs.append({'id':name,'kind':'gate','depends_on':deps,'command':[str(ROOT/'.venv-e2r/bin/python'),'-u',str(runtime/'scripts/e2r/summarize_recovery_validation.py'),'--queue',str(out),'--stage',stage],
            'outputs':[{'path':str(out/('SANITY_GATE.json' if stage=='sanity' else 'RESULTS.json')),'json_equals':{'status':'passed' if stage=='sanity' else 'complete'}}]})
    (out/'manifest.json').write_text(json.dumps({'cwd':str(ROOT),'max_parallel':3,'jobs':jobs},indent=2)+'\n')
    (out/'PREREGISTRATION.json').write_text(json.dumps({'created_at':time.time(),'planned_attempts':18,'assignments':assignments,'included_first_jobs':first,'cohort':out.name,
        'selected_runtime_variant':'r2r_no_tiering','selection_evidence':str(diagnostic/'RESULTS.json'),
        'scope':'Selected startup fix, common preincident baseline, 18 native assigned-arm developmental trials; no independent heldout or model superiority claim.',
        'stopping':'Exactly18 positions, preserve failures/unknowns, do not add trials to obtain positive outcomes.',
        'baseline_guard':'All preincident measured streams must reach floor/multiplier for two consecutive cohorts within original240s; primary post-action formula/windows untouched; setup failures remain failures.'},indent=2)+'\n')
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);p.add_argument('--runtime',required=True);a=p.parse_args();build(a.out,a.runtime)
