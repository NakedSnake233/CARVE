#!/usr/bin/env python3
"""Prospective online comparisons on a common corrected native platform."""
import argparse,copy,json,random,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from scripts.e2r.build_startup_study import runtime_template
from scripts.e2r.profile_native_intervention import db_binding
from methods.E2R.intervention.goal_policy import METHODS

def build(out,runtime,model,*,stage='pilot'):
    out,runtime,model=Path(out).resolve(),Path(runtime).resolve(),Path(model).resolve();out.mkdir(parents=True,exist_ok=False);(out/'configs').mkdir()
    source=ROOT/'enhancement/data-repair-stage/state_continuity_20261002_v1/configs'
    app_settings=json.loads(next((ROOT/'enhancement/data-repair-stage/recovery_fix_validation_20261002_v1/configs').glob('*.json')).read_text())['application_runtime_settings']
    if stage=='pilot':contexts=[('shared-db',8.,c,r) for c in ('shared_state','isolated_state') for r in range(4)]
    elif stage=='full':contexts=[(owner,rate,c,r) for owner in ('shared-db','private-db') for rate in (6.,10.) for c in ('shared_state','isolated_state') for r in range(3)]
    else:raise ValueError(stage)
    jobs=[];assignments=[];slot=0;first=[]
    for ci,(owner,rate,condition,repeat) in enumerate(contexts):
        lane=(repeat if stage=='pilot' else ci)%4
        for mi,method in enumerate(METHODS):
            config=json.loads((source/f'boutique-{condition}-r{lane}.json').read_text());case=config['cases'][0]
            alternative='private-db' if owner=='shared-db' else 'shared-db'
            bindings={'new':owner,'old':owner if condition=='shared_state' else alternative,'peer':alternative}
            for ch in case['baseline_changes']:
                if ch['entity'] in bindings and ch['path']=='/spec/template':ch['value']=db_binding(runtime_template(ch['value'],'r2r_no_tiering'),'boutique',bindings[ch['entity']])
            healthy=next(ch['value'] for ch in case['baseline_changes'] if ch['entity']=='new' and ch['path']=='/spec/template')
            fault=copy.deepcopy(healthy);fault['spec']['containers'][0]['readinessProbe'].update(tcpSocket={'port':1},failureThreshold=1)
            case['fault_changes']=[{'entity':'new','path':'/spec/template','value':fault}]
            config['policy']['rollback_options']['new']['known_good']['template']=copy.deepcopy(healthy)
            group=f'goal-{stage}-{owner}-L{rate:g}';case.update(group_id=group,comparison_group=group,offered_rps=rate,peer_rps=rate)
            identity=f'{stage}-{owner}-L{rate:g}-{condition}-r{repeat}-{method}';required=config['state_continuity']['required_cart'];required['user_id']='e2r-ack-'+identity
            config['state_continuity']['owner_database']=owner
            for rel in config['inventory']['relations']:
                if rel['source']=='ack_state':rel['target']='db' if owner=='shared-db' else 'db_private'
            config['workloads']['business']['continuity_check']=copy.deepcopy(required);config['workloads']['business']['canary']['continuity_check']=copy.deepcopy(required)
            config['workloads']['business']['offered_rps']=rate;config['workloads']['peer']['offered_rps']=rate
            config['baseline_latency_ceiling_ms']=config['verification']['latency_floor_ms']/config['verification']['latency_multiplier']
            config['application_runtime_settings']=copy.deepcopy(app_settings);config['startup_diagnostics']=False;config['observe_configuration_bindings']=True
            config.update(mode='goal_policy_comparison',stage='goal_policy_'+stage,initial_probes=[],arms=list(METHODS),goal_policy_authority='user_authorized_laboratory_only',
                goal_transition_model=str(model),goal_registration=str(out/'PREREGISTRATION.json'),goal_release=str(out/'RELEASE.json'),evidence_reuse_max_age_seconds=10.)
            config['goal_policy_protocol']={'schema':'goal_policy_v3','scope':'immutable_single_owner_state_laboratory','reuse_max_age_seconds':10.,
                'methods':list(METHODS),'mandatory_rule':'same operational checks, fresh required probes unless revision+age revalidation passes; still validate targetCAS and goal before write',
                'transition_point_estimates':'development learned probabilities, not production admission bounds'}
            config.pop('revision_design',None)
            config.pop('initial_probe_order',None);case.pop('initial_probe_order',None)
            config['collector_query_order_protocol']={'schema':'goal_policy_missing_evidence_v3','initial_probes':[],'selection':'minimum probe cover of action/goal requirements'}
            config['scheduled_jobs']=[{'group_id':group,'condition_id':condition,'repeat':repeat,'arm':method,'original_schedule_slot':slot}];slot+=1
            path=out/'configs'/f'{identity}.json';path.write_text(json.dumps(config,indent=2)+'\n');dest=out/'campaigns'/identity
            job={'id':identity,'command':[str(ROOT/'.venv-e2r/bin/python'),'-u',str(runtime/'scripts/e2r/run_native_campaign.py'),'--config',str(path),'--out',str(dest)],
                'resources':['namespace:'+config['experimental_namespace']],'requires':[str(out/'RELEASE.json'),str(runtime/'RUNTIME.json')],
                'outputs':[{'path':str(dest/'summary.json'),'json_equals':{'status':'complete'}}]}
            if stage=='full':job['requires'].append(str(out/'PILOT_APPROVED.json'))
            # Four included engineering trials, one per lane, cover all methods.
            if ci<4 and mi==ci:first.append(identity)
            else:job['depends_on']=['goal_sanity']
            jobs.append(job);assignments.append({'id':identity,'out':str(dest),'config':str(path),'method':method,'condition':condition,'repeat':repeat,'owner':owner,'rate':rate,'namespace':config['experimental_namespace']})
    random.Random(20261003).shuffle(jobs)
    for step,deps,name in [('sanity',first,'goal_sanity'),('final',[a['id'] for a in assignments],'goal_results')]:
        jobs.append({'id':name,'kind':'gate','depends_on':deps,'command':[str(ROOT/'.venv-e2r/bin/python'),'-u',str(runtime/'scripts/e2r/summarize_goal_policy.py'),'--queue',str(out),'--stage',step],
             'outputs':[{'path':str(out/('SANITY_GATE.json' if step=='sanity' else 'RESULTS.json')),'json_equals':{'status':'passed' if step=='sanity' else 'complete'}}]})
    (out/'manifest.json').write_text(json.dumps({'cwd':str(ROOT),'max_parallel':4,'jobs':jobs},indent=2)+'\n')
    (out/'PREREGISTRATION.json').write_text(json.dumps({'created_at':time.time(),'stage':stage,'planned_positions':slot,'assignments':assignments,'included_first_jobs':first,
        'runtime':str(runtime),'model':str(model),'scope':'Prospective developmental policy comparison; sameknown mechanism, controlledconfiguration transfer only; no production qualification.',
        'gate':{'joint_success_no_worse_than_each_baseline':True,'improved_safety_all':True,'cost_reduction_min':.30,'decision_wall_median_reduction_min':.20,'each_repeat_cost_lower':True,
                'interpretation':'Expansion screen, not statistical noninferiority or admission proof.'}},indent=2)+'\n')
    print(json.dumps({'stage':stage,'positions':slot,'queue':str(out)}))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);p.add_argument('--runtime',required=True);p.add_argument('--model',required=True);p.add_argument('--stage',choices=['pilot','full'],default='pilot');a=p.parse_args();build(a.out,a.runtime,a.model,stage=a.stage)
