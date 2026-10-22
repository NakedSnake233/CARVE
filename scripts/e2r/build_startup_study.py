#!/usr/bin/env python3
"""Freeze bounded, paired native rollback startup diagnosis: one runtime change."""
import argparse,copy,json,random,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))

def runtime_template(template,variant):
    value=copy.deepcopy(template)
    for container in value['spec']['containers']:
        container['env']=[e for e in container.get('env',[]) if e['name'] not in ('DOTNET_TieredCompilation','DOTNET_TieredPGO')]
        if variant=='optimized_jit':container['env'].append({'name':'DOTNET_TieredCompilation','value':'0'})
        elif variant=='pgo_disabled':container['env'].append({'name':'DOTNET_TieredPGO','value':'0'})
        elif variant not in ('default_jit','rebuilt_jit','r2r','r2r_no_tiering'):raise ValueError('unknown runtime')
        if variant=='r2r_no_tiering':container['env'].append({'name':'DOTNET_TieredCompilation','value':'0'})
    if variant in ('rebuilt_jit','r2r','r2r_no_tiering'):
        artifact=ROOT/'enhancement/builds/cart_r2r_20261002_v1'/('publish-jit' if variant=='rebuilt_jit' else 'publish-r2r')
        value['spec'].setdefault('volumes',[]).append({'name':'cart-published','hostPath':{'path':str(artifact),'type':'Directory'}})
        for container in value['spec']['containers']:
            container.setdefault('volumeMounts',[]).append({'name':'cart-published','mountPath':'/app','readOnly':True})
    return value

def build(out,runtime,candidate="optimized_jit"):
    out,runtime=Path(out).resolve(),Path(runtime).resolve();(out/'configs').mkdir(parents=True,exist_ok=False)
    source=ROOT/'enhancement/data-repair-stage/state_continuity_20261002_v1/configs';jobs=[];assignments=[];included=[]
    variants=('rebuilt_jit','r2r','r2r_no_tiering') if candidate=='precompiled' else ('default_jit',candidate)
    for vi,variant in enumerate(variants):
        for repeat in range(3):
            c=json.loads((source/f'boutique-isolated_state-r{repeat}.json').read_text());case=c['cases'][0]
            for change in case['baseline_changes']:
                if change['entity'] in ('old','new','peer') and change['path']=='/spec/template':change['value']=runtime_template(change['value'],variant)
            for change in case['fault_changes']:
                if change['entity']=='new':change['value']=runtime_template(change['value'],variant)
            healthy=next(ch['value'] for ch in case['baseline_changes'] if ch['entity']=='new' and ch['path']=='/spec/template')
            c['policy']['rollback_options']['new']['known_good']['template']=copy.deepcopy(healthy)
            case['group_id']='startup-runtime-diagnostic-'+variant;case['comparison_group']='startup-runtime-diagnostic';case['condition_id']=variant
            identity=f'{variant}-r{repeat}';required=c['state_continuity']['required_cart'];required['user_id']='e2r-ack-startup-'+identity
            c['workloads']['business']['continuity_check']=copy.deepcopy(required);c['workloads']['business']['canary']['continuity_check']=copy.deepcopy(required)
            c['startup_diagnostics']=True;c['application_runtime_settings']=({'DOTNET_TieredPGO':'0'} if variant=='pgo_disabled' else {'DOTNET_TieredCompilation':'0' if variant=='optimized_jit' else 'default'})
            if candidate=='precompiled':
                c['application_runtime_settings']={'artifact':str(ROOT/'enhancement/builds/cart_r2r_20261002_v1'/('publish-jit' if variant=='rebuilt_jit' else 'publish-r2r')),'PublishReadyToRun':variant!='rebuilt_jit','DOTNET_TieredCompilation':'0' if variant=='r2r_no_tiering' else 'default','source_tag':'v0.10.1','sdk':'8.0.303','runtime':'8.0.7'}
            if candidate=='precompiled':
                build_identity=json.loads((ROOT/'enhancement/builds/cart_r2r_20261002_v1/BUILD_IDENTITY.json').read_text())
                c['application_runtime_settings'].update(binary_sha256=build_identity['binaries']['jit' if variant=='rebuilt_jit' else 'r2r'],source_archive_sha256=build_identity['source_archive_sha256'])
            c['stage']='startup_runtime_development_diagnostic';c['interpretation']='New user-authorized runtime diagnosis; old36 negative outcomes unchanged. Single preregistered flag, resources/windows unchanged.'
            c['scheduled_jobs']=[{'group_id':case['group_id'],'condition_id':variant,'repeat':repeat,'arm':'rollback_active','original_schedule_slot':vi*3+repeat}]
            c.pop('revision_design',None)
            path=out/'configs'/f'{identity}.json';path.write_text(json.dumps(c,indent=2)+'\n');dest=out/'campaigns'/identity
            j={'id':identity,'command':[str(ROOT/'.venv-e2r/bin/python'),'-u',str(runtime/'scripts/e2r/run_native_campaign.py'),'--config',str(path),'--out',str(dest)],
               'resources':['namespace:'+c['experimental_namespace']],'requires':[str(out/'RELEASE.json'),str(runtime/'RUNTIME.json')],
               'outputs':[{'path':str(dest/'summary.json'),'json_equals':{'status':'complete'}}]}
            if vi==repeat:included.append(identity)
            else:j['depends_on']=['startup_sanity']
            if candidate=='precompiled':j['requires'].append(str(ROOT/'enhancement/builds/cart_r2r_20261002_v1/BUILD.json'))
            jobs.append(j);assignments.append({'id':identity,'out':str(dest),'config':str(path),'variant':variant,'repeat':repeat,'namespace':c['experimental_namespace']})
    random.Random(20261002).shuffle(jobs)
    for stage,deps in [('sanity',included),('final',[a['id'] for a in assignments])]:
        name='startup_sanity' if stage=='sanity' else 'startup_final'
        jobs.append({'id':name,'kind':'gate','depends_on':deps,'command':[str(ROOT/'.venv-e2r/bin/python'),'-u',str(runtime/'scripts/e2r/summarize_startup_study.py'),'--queue',str(out),'--stage',stage],
           'outputs':[{'path':str(out/('SANITY_GATE.json' if stage=='sanity' else 'RESULTS.json')),'json_equals':{'status':'passed' if stage=='sanity' else 'complete'}}]})
    (out/'manifest.json').write_text(json.dumps({'cwd':str(ROOT),'max_parallel':3,'jobs':jobs},indent=2)+'\n')
    (out/'PREREGISTRATION.json').write_text(json.dumps({'created_at':time.time(),'planned_attempts':3*len(variants),'variants':list(variants),'assignments':assignments,'included_first_jobs':included,
        'factor':f'{candidate} runtime setting for all3Cart roles; no resource/probe/traffic/window/threshold change',
        'scope':'Development root-cause diagnostic, not independent test or fullclaim evidence',
        'stopping':f'Exactly{3*len(variants)} registered attempts at most. Keep all failures/unknowns. No replacing scientific failures.',
        'followup':'If candidate lowers early latency without worsening correctness, prospectively validate it across both storage conditions and all3arms; retain default data separately.'},indent=2)+'\n')
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);p.add_argument('--runtime',required=True);p.add_argument('--candidate',choices=['optimized_jit','pgo_disabled','precompiled'],default='optimized_jit');a=p.parse_args();build(a.out,a.runtime,a.candidate)
