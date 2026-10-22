#!/usr/bin/env python3
"""Six preregistered native Station goal-adapter development positions."""
import argparse,copy,json,random,time,uuid,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from scripts.e2r.profile_native_intervention import db_binding
from methods.E2R.intervention.station_goal import goal_step
from methods.E2R.intervention.stateful_evidence import validate_state_goal

def build(root,runtime):
    root,runtime=Path(root).resolve(),Path(runtime).resolve();q=root/'preflight_v1';q.mkdir(exist_ok=False);(q/'configs').mkdir();jobs=[];assignments=[];slot=0
    for lane,condition in enumerate(('shared_state','isolated_state')):
        old=ROOT/f'enhancement/quick-stage/quick_cycle_20261002_v1/pilot/configs/pilot-trainticket-A-r{lane}.json';c=json.loads(old.read_text());case=c['cases'][0]
        states={x['entity']:copy.deepcopy(x['value']) for x in case['baseline_changes'] if x['path']=='/spec/template'}
        for role,store in [('new','shared-db'),('old','shared-db' if condition=='shared_state' else 'private-db'),('peer','private-db')]:states[role]=db_binding(states[role],'trainticket',store)
        group='station-goal-adapter-dev-v1';case.update(group_id=group,comparison_group=group,condition_id=condition,diagnosis_label=1,offered_rps=2.,peer_rps=2.)
        case['baseline_changes']=[{'entity':k,'path':'/spec/template','value':v} for k,v in states.items()]+[{'entity':k,'path':'/spec/replicas','value':1} for k in ('old','new','peer')]+[{'entity':'entry','path':'/spec/selector','value':{'app':'station-new'}}]
        fault=copy.deepcopy(states['new']);fault['spec']['containers'][0]['readinessProbe'].update(tcpSocket={'port':1},failureThreshold=1);case['fault_changes']=[{'entity':'new','path':'/spec/template','value':fault}];case['fault_wait_entities']=['new']
        c['policy']['rollback_options']['new']['known_good']['template']=copy.deepcopy(states['new'])
        c.update(stage='station_goal_adapter_development',background_workloads={},observe_configuration_bindings=True,repeats=1)
        c.pop('initial_probe_order',None);case.pop('initial_probe_order',None);c.pop('revision_design',None)
        c['plans']=[p for p in c['plans'] if p['id'] in ('no_action','switch_standby','rollback_active')];c['arms']=[p['id'] for p in c['plans']]
        c['initial_probes']=[p['id'] for p in c['inventory']['probes']];c['collector_query_order_protocol']={'schema':'assigned_all_registered_probes','initial':c['initial_probes']}
        c['baseline_latency_ceiling_ms']=c['verification']['latency_floor_ms']/c['verification']['latency_multiplier']
        c['inventory']['entities']=[e for e in c['inventory']['entities'] if e['id'] not in ('background','ack_state')]+[{'id':'ack_state','role':'goal','binding':'preincident-native-station-id-acknowledgment'}]
        c['inventory']['relations']=[rel for rel in c['inventory']['relations'] if rel['source'] not in ('background','ack_state') and rel['target']!='ack_state']
        c['inventory']['goals']=['entry','ack_state'];c['inventory']['relations'] +=[{'source':'entry','target':'ack_state','kind':'depends_on','observed_at':time.time(),'ttl':1209600,'origin':'contract'},{'source':'ack_state','target':'db','kind':'depends_on','observed_at':time.time(),'ttl':1209600,'origin':'configuration'}]
        goal={'id':str(uuid.uuid5(uuid.NAMESPACE_URL,'e2r-station-preflight-v1-'+condition)),'name':'e2r_ack_station_v1_'+condition,'stay_time':3}
        c['state_continuity']={'protocol':'station_owned_fixture_native_readback_v1','owner_database':'shared-db','required_station':goal,'fixture_scope':'one-row SQL seed then native API acknowledgement; not write-API validation','state_scope':'single owned unreplicated marker; no synthetic outcome labeling'}
        c['workloads']['business']['steps'].append(goal_step(goal));c['workloads']['business']['canary']['steps'].append(goal_step(goal))
        for name in ('business','peer'):c['workloads'][name]['offered_rps']=2.
        c['station_registration']=str(q/'PREREGISTRATION.json');c['station_release']=str(q/'RELEASE.json')
        arms=list(c['arms']);random.Random(20261003+lane).shuffle(arms);c['scheduled_jobs']=[]
        for arm in arms:c['scheduled_jobs'].append({'group_id':group,'condition_id':condition,'repeat':0,'arm':arm,'original_schedule_slot':slot});slot+=1
        validate_state_goal(c)
        identity='station-'+condition;config=q/'configs'/(identity+'.json');out=q/'campaigns'/identity;config.write_text(json.dumps(c,indent=2)+'\n')
        jobs.append({'id':identity,'command':[str(ROOT/'.venv-e2r/bin/python'),'-u',str(runtime/'scripts/e2r/run_native_campaign.py'),'--config',str(config),'--out',str(out)],'resources':['namespace:'+c['experimental_namespace']],'requires':[str(q/'RELEASE.json'),str(runtime/'RUNTIME.json')],'outputs':[{'path':str(out/'summary.json'),'json_equals':{'status':'complete'}}]})
        assignments.append({'id':identity,'out':str(out),'config':str(config),'condition':condition,'namespace':c['experimental_namespace']})
    jobs.append({'id':'station_preflight_report','kind':'gate','depends_on':[a['id'] for a in assignments],'command':[str(ROOT/'.venv-e2r/bin/python'),'-u',str(runtime/'scripts/e2r/summarize_station_goal_preflight.py'),'--queue',str(q)],'outputs':[{'path':str(q/'RESULTS.json'),'json_equals':{'status':'complete'}}]})
    (q/'manifest.json').write_text(json.dumps({'cwd':str(ROOT),'max_parallel':2,'jobs':jobs},indent=2)+'\n');(q/'PREREGISTRATION.json').write_text(json.dumps({'created_at':time.time(),'planned_positions':6,'assignments':assignments,'runtime':str(runtime),'scope':'Station/MySQL goal adapter development; not heldout transfer or model superiority','stop_rule':'No retries for positive outcomes; stop on unknownwrite/unverifiedcleanup. Onterminalfailure preserve raw and explicitly summarize after bothsourceworkersfinish.'},indent=2)+'\n');return q
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--runtime',required=True);a=p.parse_args();print(build(a.root,a.runtime))
