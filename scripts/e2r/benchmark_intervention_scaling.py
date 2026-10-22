#!/usr/bin/env python3
"""Offline intervention-model compute scaling; no synthetic recovery ground truth."""
import argparse
import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import statistics
import sys
import time

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.contracts import Policy,check_plan
from methods.E2R.intervention.data import read_records,group_key
from methods.E2R.intervention.learning import EffectPredictor
from methods.E2R.intervention.model import tensorize
from methods.E2R.intervention.schema import Entity,Fact,Relation,EvidenceGraph,input_bundle


def grow(original,dimension,factor,plan=None):
    graph=copy.deepcopy(original)
    if factor==1:return graph
    if dimension=='services':
        base=sum(e.role=='deployment' and e.kind=='Deployment' for e in original.entities)
    elif dimension=='dependencies':
        base=sum(e.role=='shared_dependency' for e in original.entities)
    else:base=len(original.facts)
    count=(factor-1)*base
    for i in range(count):
        identity=f'scale-{dimension}-{i}'
        if dimension=='telemetry':
            source=original.facts[i%len(original.facts)]
            graph.facts.append(replace(source,id=identity))
        else:
            role='shared_dependency' if dimension=='dependencies' else 'deployment'
            graph.entities.append(Entity(identity,role,kind='Deployment' if dimension=='services' else '',binding=identity))
            if dimension=='dependencies':
                action_targets=[a.target for a in plan.actions if original.entity(a.target).role=='deployment'] if plan else []
                target=action_targets[0] if action_targets else next(e.id for e in original.entities if e.role=='deployment' and e.kind=='Deployment')
                graph.relations.append(Relation(target,identity,'competes_for',graph.at,120,'observed'))
                graph.facts.append(Fact(identity+'-headroom',identity,'connection_headroom',128,graph.at,120,'offline_compute_augmentation'))
            else:
                graph.facts.append(Fact(identity+'-cpu',identity,'cpu',.1,graph.at,120,'offline_compute_augmentation'))
    graph.__post_init__();return graph


def rename_bundle(bundle):
    result=copy.deepcopy(bundle);mapping={e['id']:f'anonymous-{i}' for i,e in enumerate(reversed(result['graph']['entities']))}
    for entity in result['graph']['entities']:
        entity['id']=mapping[entity['id']];entity['name']='renamed-'+entity['name']
    for fact in result['graph']['facts']:fact['entity']=mapping[fact['entity']]
    for edge in result['graph']['relations']:edge['source']=mapping[edge['source']];edge['target']=mapping[edge['target']]
    result['graph']['goals']=[mapping[g] for g in result['graph']['goals']]
    for plan in result['plans']:
        for action in plan['actions']:
            action['target']=mapping[action['target']]
            if action.get('destination'):action['destination']=mapping[action['destination']]
    return result


def benchmark(data,model,out,settings):
    out=Path(out);out.mkdir(parents=True,exist_ok=False);rows=read_records(data);predictor=EffectPredictor(model)
    validation=set(predictor.metadata['split']['validation']);anchors=[r for r in rows if group_key(r) in validation and 'input' in r]
    row=sorted(anchors,key=lambda r:r['episode_id'])[0]
    source_attempt=row['episode_id']
    graph,plans=input_bundle(row['input']);plan=sorted(plans,key=lambda p:(-len(p.actions),p.id))[0];policy=Policy.from_dict(row['operation_policy'])
    for _ in range(3):predictor.predict(graph,plan)
    records=[]
    def sample(g):
        start=time.perf_counter();item=tensorize(g,plan);encoded=time.perf_counter()-start
        start=time.perf_counter();answer=predictor.predict(g,plan);predicted=time.perf_counter()-start
        start=time.perf_counter();checks=check_plan(g,plan,policy);checked=time.perf_counter()-start
        return {'tensorize_seconds':encoded,'predictor_including_tensorize_seconds':predicted,'check_seconds':checked,
                'tensor_nodes':len(item.node_ids),'checks':len(checks),'graph_sizes':answer.get('graph_sizes',[])}
    for dimension in settings['dimensions']:
        for factor in settings['factors']:
            values=[]
            if dimension=='concurrent_replays':
                for _ in range(settings['repetitions']):
                    start=time.perf_counter()
                    with ThreadPoolExecutor(max_workers=factor) as pool:parts=list(pool.map(lambda _:sample(graph),range(factor)))
                    elapsed=time.perf_counter()-start;values.append({'batch_wall_seconds':elapsed,'replays_per_second':factor/elapsed,'per_replay':parts})
                record={'dimension':dimension,'factor':factor,'concurrent_calls':factor,'measurements':values}
            else:
                augmented=grow(graph,dimension,factor,plan)
                for _ in range(settings['repetitions']):values.append(sample(augmented))
                record={'dimension':dimension,'factor':factor,'entities':len(augmented.entities),'facts':len(augmented.facts),
                        'relations':len(augmented.relations),'measurements':values,
                        'deployment_service_nodes':sum(e.role=='deployment' and e.kind=='Deployment' for e in augmented.entities),
                        'shared_dependency_nodes':sum(e.role=='shared_dependency' for e in augmented.entities),
                        'mean_predictor_seconds':statistics.mean(v['predictor_including_tensorize_seconds'] for v in values)}
            records.append(record);(out/'progress.json').write_text(json.dumps({'status':'running','updated_at':time.time(),'dimension':dimension,'factor':factor})+'\n')
    invariance=[]
    for row in sorted(anchors,key=lambda r:r['episode_id'])[:20]:
        original,original_plans=input_bundle(row['input']);renamed,renamed_plans=input_bundle(rename_bundle(row['input']))
        p=next(p for p in original_plans if p.id==row['chosen_plan']);q=next(p for p in renamed_plans if p.id==row['chosen_plan'])
        a=predictor.predict(original,p)['raw_probabilities'];b=predictor.predict(renamed,q)['raw_probabilities']
        invariance.append({'attempt_id':row['episode_id'],'max_probability_difference':max(abs(a[k]-b[k]) for k in a)})
    result={'status':'complete','scope':'offline compute on validation-derived graph augmentation; no business effectiveness labels',
            'model':str(Path(model).resolve()),'source_partition':'validation','source_attempt':source_attempt,
            'benchmark_plan_id':plan.id,'plan_selection':'maximum registered action count then lexical ID; independent of outcomes',
            'records':records,'role_renaming':invariance,'timing_caveat':'tensorize and predictor timings overlap; do not add them. Threaded replay includes executor overhead.',
            'augmentation_scope':'Service growth adds isolated irrelevant deployment nodes and CPU facts; dependency growth attaches shared resources to an action target; telemetry growth repeats facts. These are compute scaling probes, not larger deployed applications.'}
    (out/'SCALING.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    (out/'SCALING_DONE.json').write_text(json.dumps({'status':'complete','records':len(records),'completed_at':time.time(),'test_evaluated':False},indent=2)+'\n')
    (out/'progress.json').write_text(json.dumps({'status':'complete','updated_at':time.time()})+'\n');print(json.dumps({'status':'complete','records':len(records)}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data',required=True,type=Path);p.add_argument('--model',required=True,type=Path);p.add_argument('--plan',required=True,type=Path);p.add_argument('--out',required=True,type=Path);a=p.parse_args()
    settings=json.loads(a.plan.read_text())['scaling'];benchmark(a.data,a.model,a.out,settings)
