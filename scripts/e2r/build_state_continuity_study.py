#!/usr/bin/env python3
"""Prospective state-continuity contrast with identical readiness-drift mechanism."""
import argparse,copy,json,random,time,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from scripts.e2r.profile_native_intervention import db_binding


def build(out,runtime):
    out,runtime=Path(out).resolve(),Path(runtime).resolve();out.mkdir(parents=True,exist_ok=False);(out/'configs').mkdir()
    source=ROOT/'enhancement/queues/revision_exploratory_v1/configs';jobs=[];assignments=[];first=[]
    for ci,condition in enumerate(('shared_state','isolated_state')):
        for repeat in range(6):
            lane=repeat%4;namespace='e2r-isg-boutique'+('' if lane==0 else f'-p{lane+1}')
            templates=[json.loads((source/f'boutique-low-A-r{i}.json').read_text()) for i in range(5)]
            config=copy.deepcopy(next(c for c in templates if c['experimental_namespace']==namespace))
            states={x['entity']:copy.deepcopy(x['value']) for x in config['cases'][0]['baseline_changes'] if x['path']=='/spec/template'}
            states['new']=db_binding(states['new'],'boutique','shared-db')
            states['old']=db_binding(states['old'],'boutique','shared-db' if condition=='shared_state' else 'private-db')
            states['peer']=db_binding(states['peer'],'boutique','private-db')
            states['db']['spec']['containers'][0]['resources']=copy.deepcopy(states['db_private']['spec']['containers'][0]['resources'])
            group='state-continuity-readiness-20261002'
            config['cases']=[{'group_id':group,'comparison_group':group,'condition_id':condition,'diagnosis_label':1,
                'offered_rps':8.,'peer_rps':8.,'baseline_settle_seconds':3,'fault_settle_seconds':10,
                'baseline_changes':[{'entity':role,'path':'/spec/template','value':value} for role,value in states.items()]
                    +[{'entity':role,'path':'/spec/replicas','value':1} for role in ('old','new','peer')]
                    +[{'entity':'entry','path':'/spec/selector','value':{'app':'cart-new'}}],
                'fault_changes':[],'fault_wait_entities':['new']}]
            bad=copy.deepcopy(states['new']);bad['spec']['containers'][0]['readinessProbe'].update(tcpSocket={'port':1},failureThreshold=1)
            config['cases'][0]['fault_changes']=[{'entity':'new','path':'/spec/template','value':bad}]
            config['policy']['rollback_options']['new']['known_good']['template']=copy.deepcopy(states['new'])
            config.update(stage='state_continuity_mechanism',repeats=6,seed=20261030+repeat,background_workloads={},
                          interpretation='New stateful business requirement, same fine readiness diagnosis, known apps. Prior stateless outcomes unchanged.')
            config['inventory']['relations']=[r for r in config['inventory']['relations'] if r['source']!='background']
            config['inventory']['entities']=[e for e in config['inventory']['entities'] if e['id']!='background']
            config['inventory']['entities'].append({'id':'ack_state','role':'goal','binding':'preincident-acknowledged-cart-state'})
            config['inventory']['goals']=['entry','ack_state']
            config['inventory']['relations'] += [
                {'source':'entry','target':'ack_state','kind':'depends_on','observed_at':time.time(),'ttl':1209600,'origin':'contract'},
                {'source':'ack_state','target':'db','kind':'depends_on','observed_at':time.time(),'ttl':1209600,'origin':'configuration'}]
            identity=f'boutique-{condition}-r{repeat}'
            required={'user_id':'e2r-ack-'+identity,'product_id':'E2R_ACKNOWLEDGED_ITEM','quantity':3}
            config['state_continuity']={'owner_database':'shared-db','required_cart':required,
                'protocol':{'schema':'acknowledged_cart_continuity_v1','seed_phase':'after_baseline_setup_before_traffic_and_fault',
                    'business_check':'fresh_write_read_plus_read_preincident_acknowledged_cart',
                    'canary_check':'read_only_availability_plus_acknowledged_cart',
                    'required_cart_items':1,'direct_probe_scope':'fresh_request_availability_capacity_only_not_state_suitability',
                    'post_cleanup_check':'ordinary_endpoint_health_after_intentional_test_data_reset',
                    'background':'disabled_equally_to_isolate_state_continuity'}}
            config['workloads']['business']['continuity_check']=copy.deepcopy(required)
            config['workloads']['business']['canary']['continuity_check']=copy.deepcopy(required)
            arms=['no_action','switch_standby','rollback_active'];random.Random(20261030+ci*100+repeat).shuffle(arms)
            config['scheduled_jobs']=[{'group_id':group,'condition_id':condition,'repeat':repeat,'arm':arm,
                'original_schedule_slot':ci*18+repeat*3+['no_action','switch_standby','rollback_active'].index(arm)} for arm in arms]
            path=out/'configs'/f'{identity}.json';path.write_text(json.dumps(config,indent=2)+'\n');destination=out/'campaigns'/identity
            job={'id':identity,'command':[str(ROOT/'.venv-e2r/bin/python'),'-u',str(runtime/'scripts/e2r/run_native_campaign.py'),
                 '--config',str(path),'--out',str(destination)],'resources':['namespace:'+namespace],
                 'requires':[str(out/'RELEASE.json'),str(runtime/'RUNTIME.json')],
                 'outputs':[{'path':str(destination/'summary.json'),'json_equals':{'status':'complete'}}]}
            if (ci==0 and repeat==0) or (ci==1 and repeat==1):first.append(identity)
            else:job['depends_on']=['stateful_included_check']
            jobs.append(job);assignments.append({'id':identity,'config':str(path),'out':str(destination),'namespace':namespace,'condition':condition,'repeat':repeat})
    random.Random(20261030).shuffle(jobs)
    jobs.append({'id':'stateful_included_check','kind':'gate','depends_on':first,
        'command':[str(ROOT/'.venv-e2r/bin/python'),'-u',str(runtime/'scripts/e2r/summarize_state_continuity.py'),'--queue',str(out),'--stage','sanity'],
        'outputs':[{'path':str(out/'SANITY_GATE.json'),'json_equals':{'status':'passed'}}]})
    jobs.append({'id':'stateful_final_analysis','kind':'gate','depends_on':[a['id'] for a in assignments],
        'command':[str(ROOT/'.venv-e2r/bin/python'),'-u',str(runtime/'scripts/e2r/summarize_state_continuity.py'),'--queue',str(out),'--stage','final'],
        'outputs':[{'path':str(out/'RESULTS.json'),'json_equals':{'status':'complete'}}]})
    (out/'manifest.json').write_text(json.dumps({'cwd':str(ROOT),'max_parallel':4,'jobs':jobs},indent=2)+'\n')
    (out/'PREREGISTRATION.json').write_text(json.dumps({'created_at':time.time(),'runtime':str(runtime),'planned_attempts':36,
        'assignments':assignments,'included_first_jobs':first,'scope':'One same-fine-diagnosis state-continuity mechanism; not a learned model comparison or broad confirmation.',
        'hypotheses':['Switch can preserve acknowledged state with shared backing store.','Independent unreplicated standby may pass local health/capacity but lose acknowledged state after switch.','Rollback keeps active backing store; recovery/latency must still be measured.'],
        'stopping':'After36 registeredpositions stop regardless of outcomes. Keep all negatives/rejections/unknowns; no extra retries to obtain positive science.',
        'new_goal_not_old_label_patch':True,'balanced_replica_note':'Each repeat uses same replica in both conditions; first two included campaigns use separate namespaces.'},indent=2)+'\n')
    print(json.dumps({'queue':str(out),'attempts':36,'campaigns':12,'max_parallel':4,'included_sanity':6}))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True);p.add_argument('--runtime',required=True);a=p.parse_args();build(a.out,a.runtime)
