#!/usr/bin/env python3
"""Freeze 480 native policy comparisons using existing owned replicas."""
import argparse
import copy
import json
from pathlib import Path
import random
import time
ROOT = Path(__file__).resolve().parents[2]


def build(out, runtime):
    out, runtime = Path(out).resolve(), Path(runtime).resolve()
    out.mkdir(parents=True, exist_ok=False)
    (out/'configs').mkdir()
    old = ROOT/'enhancement/queues/revision_exploratory_v1/configs'
    strategies = ['decision', 'random', 'fixed', 'none']
    jobs, assignments, first = [], [], []
    for ai, app in enumerate(('boutique', 'trainticket')):
        for si, stress in enumerate(('low', 'stressed')):
            for ci, condition in enumerate(('A', 'B')):
                templates = [json.loads((old/f'{app}-{stress}-{condition}-r{r}.json').read_text()) for r in range(5)]
                for seed_index, seed in enumerate((42, 43, 44)):
                    for repeat in range(5):
                        lane = (2*si+ci+repeat+seed_index) % 4
                        namespace = 'e2r-isg-'+app+('' if lane == 0 else f'-p{lane+1}')
                        config = copy.deepcopy(next(c for c in templates if c['experimental_namespace'] == namespace))
                        group = 'v2online-dependency-'+stress+'-01'
                        case = config['cases'][0]
                        case['group_id'] = group
                        order = list(strategies)
                        random.Random(20261011+ai*100000+si*10000+ci*1000+seed*10+repeat).shuffle(order)
                        config.update(mode='laboratory_policy_comparison', stage='prospective_online_strategy_comparison',
                            arms=strategies, seed=20261011+seed*100+repeat, initial_probes=[], initial_probe_order=[],
                            fixed_probe_order=[p['id'] for p in config['inventory']['probes']],
                            observation_budget={'max_calls':3, 'max_cost':6., 'max_seconds':40.},
                            total_probe_budget={'max_calls':16, 'max_cost':48., 'max_seconds':180.},
                            model=str(ROOT/f'enhancement/revision-stage/v2/exploratory_training_v1/models/graph_effect_distinguish_s{seed}'),
                            prediction_seeds=[seed], laboratory_release=str(out/'RELEASE.json'),
                            laboratory_registration=str(out/'PREREGISTRATION.json'),
                            experimental_evaluation={'authority':'user_authorized_laboratory_only', 'external_slo_required':False,
                                'external_benchmark_required':False, 'interpretation':'Known configuration online strategy measurements; not qualified admission or independent new configuration confirmation.'})
                        config['scheduled_jobs'] = [{'group_id':group, 'condition_id':case['condition_id'], 'repeat':repeat,
                            'arm':strategy, 'original_schedule_slot':ci*60+seed_index*20+repeat*4+strategies.index(strategy)} for strategy in order]
                        identity = f'{app}-{stress}-{condition}-s{seed}-r{repeat}'
                        path = out/'configs'/f'{identity}.json'
                        path.write_text(json.dumps(config, indent=2)+'\n')
                        destination = out/'campaigns'/identity
                        job = {'id':identity, 'command':[str(ROOT/'.venv-e2r/bin/python'), '-u',
                            str(runtime/'scripts/e2r/run_native_campaign.py'), '--config',str(path),'--out',str(destination)],
                            'resources':['namespace:'+namespace], 'requires':[str(out/'RELEASE.json'),str(runtime/'RUNTIME.json')],
                            'outputs':[{'path':str(destination/'summary.json'),'json_equals':{'status':'complete'}}]}
                        if seed == 42 and repeat == 0:
                            first.append(identity)
                            job['resources'].append('initial_online_lane:'+str((ai*4+si*2+ci)%4))
                        else:
                            job['depends_on']=['verify_included_online_sanity']
                        jobs.append(job)
                        assignments.append({'id':identity,'application':config['application'],'group':config['application']+'/'+group,
                            'comparison_group':config['application']+'/'+case['comparison_group'],'condition':condition,'stress':stress,
                            'model_seed':seed,'repeat':repeat,'namespace':namespace,'config':str(path),'out':str(destination),
                            'slots':[j['original_schedule_slot'] for j in config['scheduled_jobs']]})
    random.Random(20261011).shuffle(jobs)
    jobs.append({'id':'verify_included_online_sanity','kind':'gate','depends_on':first,
        'command':[str(ROOT/'.venv-e2r/bin/python'),'-u',str(runtime/'scripts/e2r/verify_online_sanity.py'),'--queue',str(out)],
        'outputs':[{'path':str(out/'SANITY_GATE.json'),'json_equals':{'status':'passed'}}]})
    (out/'manifest.json').write_text(json.dumps({'cwd':str(ROOT),'max_parallel':8,'jobs':jobs},indent=2)+'\n')
    registration={'schema':'laboratory_online_comparison_v1','frozen_at':time.time(),'runtime':str(runtime),
        'plan':str(ROOT/'enhancement/revision-stage/v2/ONLINE_COMPARISON_PLAN_20261001.md'),
        'planned_attempts':480,'native_jobs':120,'strategies':strategies,'model_seeds':[42,43,44],
        'repeats':5,'first_included_jobs':first,'assignments':assignments,
        'scope':'Known configuration online experiments with experimental authority; scientific admission remains separate.',
        'failure_policy':'Preserve all failed attempts, recover verified environments and resume only uncovered slots.'}
    (out/'PREREGISTRATION.json').write_text(json.dumps(registration,indent=2)+'\n')
    print(json.dumps({'queue':str(out),'attempts':480,'native_jobs':120,'included_sanity_attempts':32}))


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',required=True);p.add_argument('--runtime',required=True)
    a=p.parse_args();build(a.out,a.runtime)
