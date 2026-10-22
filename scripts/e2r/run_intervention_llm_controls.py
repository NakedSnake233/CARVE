#!/usr/bin/env python3
"""Run bounded same-model flat/graph catalog controls on identical pre-action anchors."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import random
import sys
import time
from collections import defaultdict
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.E2R.llm_pilot import load_model_config,call_once
from methods.E2R.intervention.campaign import append_json
from methods.E2R.intervention.contracts import Policy
from methods.E2R.intervention.data import read_records,group_key,validate_split
from methods.E2R.intervention.evaluation import evaluate,policy_for_record
from methods.E2R.intervention.llm_control import prompt,validate,RecordedLLMControl
from methods.E2R.intervention.schema import input_bundle
from rca_bench.llm_transport import request_settings


def run(data,split_file,config_file,alias,out,replicates,max_calls):
    rows=read_records(data);split=json.loads(split_file.read_text());validate_split(rows,split)
    config=load_model_config(config_file,alias)
    groups=defaultdict(list)
    for row in rows:
        if group_key(row) in split['validation']+split['test'] and 'input' in row:
            groups[(group_key(row),row['condition_id'])].append(row)
    anchors=[min(values,key=lambda r:r['episode_id']) for key,values in sorted(groups.items())]
    jobs=[(repeat,route,anchor) for repeat in range(replicates) for route in ('flat','graph') for anchor in anchors]
    if len(jobs)>max_calls:raise ValueError(f'{len(jobs)} calls exceed the frozen bound {max_calls}')
    random.Random(42).shuffle(jobs)
    out.mkdir(parents=True,exist_ok=False)
    (out/'protocol.json').write_text(json.dumps({'schema':'native_llm_control_v1','source':'live pre-action records',
        'requested_model':config['model'],'alias':alias,'request_settings':request_settings(config,max_tokens=2000),
        'planned_calls':len(jobs),'maximum_calls':max_calls,'replicates':replicates,
        'replicate_semantics':'repeated provider calls; no unsupported claim of a provider RNG seed',
        'routes':['flat','graph'],'scope':'local matched catalog controls; not an upstream ThinkRemed or E2E-REME reproduction',
        'split':split,'source_data':str(data)},indent=2)+'\n')
    cache=defaultdict(dict);results=[]
    for index,(repeat,route,anchor) in enumerate(jobs):
        graph,plans=input_bundle(anchor['input']);policy=Policy.from_dict(anchor['operation_policy'])
        messages=prompt(graph,plans,policy,route)
        call_dir=out/f'call-{index:04d}';call_dir.mkdir()
        (call_dir/'prompt.json').write_text(json.dumps(messages,indent=2)+'\n')
        identity={'index':index,'repeat':repeat,'route':route,'group':group_key(anchor),'condition':anchor['condition_id'],
                  'anchor_episode':anchor['episode_id'],'started_at':time.time()}
        append_json(out/'attempts.jsonl',{**identity,'status':'started'})
        print(f'[{index+1}/{len(jobs)}] {route} repeat={repeat}',flush=True)
        response=call_once(config,messages)
        forecasts=validate(response.get('response_text',''),plans) if response.get('provider_response_valid') else None
        result={**identity,'response':response,'forecast_valid':forecasts is not None,'completed_at':time.time()}
        (call_dir/'response.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
        append_json(out/'attempts.jsonl',{**identity,'status':'valid' if forecasts is not None else 'failed',
            'usage':response.get('usage'),'request_seconds':response.get('request_seconds')})
        results.append(result)
        if forecasts is not None:
            cache[(repeat,route)][json.dumps(graph.to_dict(),sort_keys=True,separators=(',',':'))]=forecasts
        if response.get('http_status') in (401,403,429):break
    base=Policy.from_dict(anchors[0]['operation_policy'])
    if len(results)==len(jobs):
        for repeat in range(replicates):
            for route in ('flat','graph'):
                report=evaluate(rows,split,base,RecordedLLMControl(cache[(repeat,route)]))
                report['effect_metric_scope']='only called pre-action anchors; do not compare this sparse Brier sample to all-repeat model Brier'
                report['route']=route;report['request_replicate']=repeat
                (out/f'evaluation_{route}_{repeat}.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    totals=defaultdict(float)
    for row in results:
        for key,value in (row['response'].get('usage') or {}).items():
            if type(value) in (int,float):totals[key]+=value
    served=sorted({str(r['response'].get('response_model')) for r in results if r['response'].get('response_model')})
    summary={'status':'complete' if len(results)==len(jobs) else 'stopped','planned_calls':len(jobs),'attempted_calls':len(results),
        'valid_forecasts':sum(r['forecast_valid'] for r in results),'requested_model':config['model'],'served_models':served,
        'same_served_model_observed':len(served)==1,'total_usage':dict(totals),
        'usage_missing_calls':sum(r['response'].get('usage') is None for r in results),
        'request_seconds':sum(r['response'].get('request_seconds',0) for r in results),
        'claim_status':'requires_independent_integrity_and_result_to_claim_review'}
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');return summary


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('data','split','config','out'):p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--alias',default='qwen3.8-flash');p.add_argument('--replicates',type=int,default=3);p.add_argument('--max-calls',type=int,default=216)
    a=p.parse_args();r=run(a.data,a.split,a.config,a.alias,a.out,a.replicates,a.max_calls);print(json.dumps(r));raise SystemExit(0 if r['status']=='complete' else 2)
