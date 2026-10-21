#!/usr/bin/env python3
"""Four prospective development-only native expiry stress positions; no efficacy claim."""
import argparse,copy,json,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]

def build(root,runtime):
    root,runtime=Path(root).resolve(),Path(runtime).resolve();q=root/'expiry_engineering_v1';q.mkdir(exist_ok=False);(q/'configs').mkdir();jobs=[];assignments=[]
    for slot,(condition,lane) in enumerate([('shared_state',0),('shared_state',1),('isolated_state',2),('isolated_state',3)]):
        old=root/'pilot_v2/configs'/f'pilot-shared-db-L8-{condition}-r{lane}-goal_reuse.json';c=json.loads(old.read_text());identity=f'{condition}-lane{lane}'
        c.update(stage='goal_expiry_engineering_sanity',retry_expired_reuse_once=True,engineering_prewrite_delay_seconds=10.25,goal_registration=str(q/'PREREGISTRATION.json'),goal_release=str(q/'RELEASE.json'))
        c['goal_policy_protocol'].update(expiry_retry_limit=1,engineering_first_prewrite_get_delay_seconds=10.25,scope='new developmental engineering stress; not efficacy or final confirmatory evidence')
        c['cases'][0].update(group_id='goal-expiry-engineering-v1',comparison_group='goal-expiry-engineering-v1')
        required=copy.deepcopy(c['state_continuity']['required_cart']);required['user_id']='e2r-ack-expiry-'+identity;c['state_continuity']['required_cart']=required
        c['workloads']['business']['continuity_check']=copy.deepcopy(required);c['workloads']['business']['canary']['continuity_check']=copy.deepcopy(required)
        c['scheduled_jobs']=[{'group_id':'goal-expiry-engineering-v1','condition_id':condition,'repeat':lane,'arm':'goal_reuse','original_schedule_slot':slot}]
        config=q/'configs'/(identity+'.json');out=q/'campaigns'/identity;config.write_text(json.dumps(c,indent=2)+'\n')
        jobs.append({'id':identity,'command':[str(ROOT/'.venv-e2r/bin/python'),'-u',str(runtime/'scripts/e2r/run_native_campaign.py'),'--config',str(config),'--out',str(out)],'resources':['namespace:'+c['experimental_namespace']],'requires':[str(q/'RELEASE.json'),str(runtime/'RUNTIME.json')],'outputs':[{'path':str(out/'summary.json'),'json_equals':{'status':'complete'}}]})
        assignments.append({'id':identity,'config':str(config),'out':str(out),'method':'goal_reuse','condition':condition,'repeat':lane,'owner':'shared-db','rate':8.,'namespace':c['experimental_namespace']})
    jobs.append({'id':'engineering_report','kind':'gate','depends_on':[a['id'] for a in assignments],'command':[str(ROOT/'.venv-e2r/bin/python'),'-u',str(runtime/'scripts/e2r/summarize_goal_expiry_sanity.py'),'--queue',str(q)],'outputs':[{'path':str(q/'RESULTS.json'),'json_equals':{'status':'complete'}}]})
    (q/'manifest.json').write_text(json.dumps({'cwd':str(ROOT),'max_parallel':4,'jobs':jobs},indent=2)+'\n')
    (q/'PREREGISTRATION.json').write_text(json.dumps({'created_at':time.time(),'stage':'engineering_sanity','planned_positions':4,'assignments':assignments,'included_first_jobs':[a['id'] for a in assignments],'scope':'New development stress positions after V3 closure, injected10.25sGET delay; tests bounded fresh fallback safety, not performance superiority','model':str(root/'transition_model.json'),'runtime':str(runtime),'maximum_retry':1,'stop_rule':'No outcome-seeking retries; preserve any failure/unknown, investigate before additional work.'},indent=2)+'\n');return q
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--runtime',required=True);a=p.parse_args();print(build(a.root,a.runtime))
