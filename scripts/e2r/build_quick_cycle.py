#!/usr/bin/env python3
"""Small fixed sequential cycle:24 native fit trials,4models,32 online trials."""
import argparse,copy,json,random,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]

def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,indent=2)+'\n')

def build(out,runtime):
    out,runtime=Path(out).resolve(),Path(runtime).resolve();out.mkdir(parents=True,exist_ok=False)
    old=ROOT/'enhancement/queues/revision_exploratory_v1/configs';python=str(ROOT/'.venv-e2r/bin/python')
    pipeline=[];pilot_jobs=[];online_jobs=[];pilot_assign=[];online_assign=[]
    def template(app,stress,condition,lane):
        ns='e2r-isg-'+app+('' if lane==0 else f'-p{lane+1}')
        return copy.deepcopy(next(c for c in [json.loads((old/f'{app}-{stress}-{condition}-r{r}.json').read_text()) for r in range(5)] if c['experimental_namespace']==ns))
    def native_job(identity,config,stage,dependencies):
        directory=out/stage;path=directory/'configs'/f'{identity}.json';destination=directory/'campaigns'/identity
        write(path,config)
        return {'id':identity,'command':[python,'-u',str(runtime/'scripts/e2r/run_native_campaign.py'),'--config',str(path),'--out',str(destination)],
                'resources':['namespace:'+config['experimental_namespace']], 'depends_on':dependencies,
                'requires':[str(out/'RELEASE.json')], 'outputs':[{'path':str(destination/'summary.json'),'json_equals':{'status':'complete'}}]},path,destination
    for ai,app in enumerate(('boutique','trainticket')):
        for ci,condition in enumerate(('A','B')):
            for repeat in range(2):
                c=template(app,'low',condition,ci*2+repeat);group='quickfit-low-20261002'
                c['cases'][0]['group_id']=group;c.update(repeats=2,seed=20261012+ai*100+repeat,stage='quick_development_replication')
                arms=['no_action','switch_standby','rollback_active'];random.Random(c['seed']+ci).shuffle(arms)
                c['scheduled_jobs']=[{'group_id':group,'condition_id':c['cases'][0]['condition_id'],'repeat':repeat,'arm':a,
                     'original_schedule_slot':ci*6+repeat*3+['no_action','switch_standby','rollback_active'].index(a)} for a in arms]
                identity=f'pilot-{app}-{condition}-r{repeat}';job,path,dest=native_job(identity,c,'pilot',[])
                pilot_jobs.append(job);pipeline.append(job);pilot_assign.append({'id':identity,'config':str(path),'out':str(dest),'namespace':c['experimental_namespace']})
    prepare={'id':'pack_and_profile','kind':'preparation','depends_on':[j['id'] for j in pilot_jobs],
             'command':[python,'-u',str(runtime/'scripts/e2r/run_quick_cycle.py'),'--plan',str(out/'PLAN.json'),'--stage','prepare'],
             'outputs':[{'path':str(out/'PREPARED.json'),'json_equals':{'status':'passed'}}]};pipeline.append(prepare)
    models=[]
    for i,(variant,objective) in enumerate([('graph','effect'),('local','effect'),('graph','effect_action_rank'),('local','effect_action_rank')]):
        identity=variant+'_'+objective
        models.append({'id':identity,'out':str(out/'models'/identity),'parameters':{'variant':variant,'objective':objective,'epochs':30,'hidden':64,'layers':3,'seeds':[42],'learning_rate':.003,'distinguish_weight':.2,'minimum_repeats':5}})
        pipeline.append({'id':'train-'+identity,'kind':'training','depends_on':['pack_and_profile'],'resources':['training_slot:'+str(i%2)],
             'command':[python,'-u',str(runtime/'scripts/e2r/run_quick_cycle.py'),'--plan',str(out/'PLAN.json'),'--stage','train','--id',identity],
             'outputs':[{'path':str(out/'models'/identity/'QUICK_MODEL_DONE.json'),'json_equals':{'status':'complete'}}]})
    policies=[('graph_rank_guided','graph_effect_action_rank','decision'),('local_rank_guided','local_effect_action_rank','decision'),
              ('graph_effect_guided','graph_effect','decision'),('graph_rank_fixed2','graph_effect_action_rank','fixed')]
    train_ids=['train-'+m['id'] for m in models]
    for ai,app in enumerate(('boutique','trainticket')):
        for ci,condition in enumerate(('A','B')):
            for repeat in range(2):
                for pi,(label,model,strategy) in enumerate(policies):
                    c=template(app,'stressed',condition,(ci*2+repeat+pi)%4);group='quickeval-stressed-20261002';c['cases'][0]['group_id']=group
                    c.update(mode='laboratory_policy_comparison',stage='quick_known_configuration_online',repeats=2,seed=20261020+repeat,
                        arms=[strategy],initial_probes=[],initial_probe_order=[],fixed_probe_order=['shared_dependency','old_capacity'],
                        observation_budget={'max_calls':3,'max_cost':6.,'max_seconds':40.},total_probe_budget={'max_calls':16,'max_cost':48.,'max_seconds':180.},
                        model=str(out/'models'/model),prediction_seeds=[42],quick_policy_id=label,
                        laboratory_release=str(out/'online/RELEASE.json'),laboratory_registration=str(out/'online/PREREGISTRATION.json'),
                        experimental_evaluation={'authority':'user_authorized_laboratory_only','external_benchmark_required':False,
                             'interpretation':'Small single-seed exploratory known-config comparison; not confirmation or qualified admission.'})
                    if strategy=='fixed':c['fixed_adaptive_probe_ids']=['shared_dependency','old_capacity']
                    c['scheduled_jobs']=[{'group_id':group,'condition_id':c['cases'][0]['condition_id'],'repeat':repeat,'arm':strategy,'original_schedule_slot':ci*8+repeat*4+pi}]
                    identity=f'online-{app}-{condition}-r{repeat}-{label}';job,path,dest=native_job(identity,c,'online',train_ids)
                    online_jobs.append(job);online_assign.append({'id':identity,'config':str(path),'out':str(dest),'namespace':c['experimental_namespace'],'policy':label})
    first_online='online-boutique-A-r0-graph_rank_fixed2'
    for job in online_jobs:
        if job['id']!=first_online:job['depends_on']=['online_first_check']
    random.Random(20261020).shuffle(online_jobs);pipeline+=online_jobs
    pipeline.append({'id':'online_first_check','kind':'gate','depends_on':[first_online],
        'command':[python,'-u',str(runtime/'scripts/e2r/run_quick_cycle.py'),'--plan',str(out/'PLAN.json'),'--stage','check_online'],
        'outputs':[{'path':str(out/'ONLINE_SANITY.json'),'json_equals':{'status':'passed'}}]})
    pipeline.append({'id':'summarize','kind':'analysis','depends_on':[j['id'] for j in online_jobs],
        'command':[python,'-u',str(runtime/'scripts/e2r/run_quick_cycle.py'),'--plan',str(out/'PLAN.json'),'--stage','summarize'],
        'outputs':[{'path':str(out/'results/FINAL.json'),'json_equals':{'status':'complete','online_attempts':32}}]})
    write(out/'pilot/manifest.json',{'cwd':str(ROOT),'jobs':pilot_jobs,'max_parallel':8})
    write(out/'online/manifest.json',{'cwd':str(ROOT),'jobs':online_jobs,'max_parallel':8})
    write(out/'pilot/PREREGISTRATION.json',{'created_at':time.time(),'planned_attempts':24,'assignments':pilot_assign,'scope':'Known low-condition developmental replications, not new independent groups'})
    write(out/'online/PREREGISTRATION.json',{'created_at':time.time(),'planned_attempts':32,'assignments':online_assign,'runtime':str(runtime),'scope':'Known stressed configurations, one seed, two repeats. Informed fixed2 chosen from old480 development findings before these measurements.'})
    write(out/'PLAN.json',{'created_at':time.time(),'runtime':str(runtime),'project':str(ROOT),'out':str(out),'pilot_attempts':24,'online_attempts':32,'models':models,
        'base_training_plan':str(ROOT/'enhancement/revision-stage/v2/exploratory_training_v1/PLAN.json'),
        'scope':'Time-limited complete sequential exploratory cycle. Oldtest and existing stressed/online outcome labels excluded from fitting/checkpoint selection. Newpilot low fit; currentonline stressed postfit only. No claim of statistical C1 pair support from empirical action-ranking pairs.',
        'worker_queue':str(out/'queue'),'first_online_id':first_online,'ranking_weight':.2,'ranking_minimum_gap':.1,'ranking_excludes_no_action':True,
        'stage_order':['24 native low trials','pack+2epoch engineering profile','4models one seed30epochs','32 online trials','summary+fresh review']})
    write(out/'queue/manifest.json',{'cwd':str(ROOT),'max_parallel':8,'jobs':pipeline})
    print(json.dumps({'out':str(out),'queue_jobs':len(pipeline),'native_trials':56,'models':4}))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True);p.add_argument('--runtime',required=True);a=p.parse_args();build(a.out,a.runtime)
