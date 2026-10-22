#!/usr/bin/env python3
"""Run/resume reviewer-aligned three-route E2R LLM campaigns with an explicit budget."""
from pathlib import Path
import argparse,json,sys,time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from methods.E2R.graph import use_upstream
from methods.E2R.llm_pilot import (FORMAL_ROUTES,acquire_campaign_lock,append_event,call_once,
    choose_cases,journal_state,load_model_config,messages_for,task_list,validate_output)
from methods.E2R.paths import default_dyrca_root,observation_file
from rca_bench.llm_transport import request_settings


def write_status(out,campaign,started,finished):
    unknown=sorted(set(started)-set(finished))
    status={'status':'complete' if len(started)==campaign['authorized_request_ceiling'] and not unknown else
            'interrupted_requests_present' if unknown else 'in_progress',
            'authorized_request_ceiling':campaign['authorized_request_ceiling'],
            'actual_http_attempts_started':len(started),'finished_attempts':len(finished),
            'unknown_outcome_attempts':unknown,
            'transport_successes':sum(bool(x.get('transport_success')) for x in finished.values()),
            'strict_accepted':sum(bool(x.get('validation',{}).get('accepted')) for x in finished.values()),
            'response_models':sorted({x.get('response_model') for x in finished.values() if x.get('response_model')}),
            'provider_usage_missing_attempts':sum(x.get('usage') is None for x in finished.values())}
    (out/'status.json').write_text(json.dumps(status,indent=2)+'\n');return status


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--manifest',default=str(ROOT/'experiments/e2r/runs/full_data_v2/manifest.json'))
    ap.add_argument('--split',default=str(ROOT/'experiments/e2r/runs/gpu_full_4090d/split.json'))
    ap.add_argument('--out-dir',required=True);ap.add_argument('--config',default=str(ROOT/'llm_rca_config.json'))
    ap.add_argument('--model-alias',default='qwen3.8-flash')
    ap.add_argument('--cases',type=int,default=8,help='0 means every eligible fixed-test incident')
    ap.add_argument('--generations',type=int,default=2);ap.add_argument('--selection-seed',type=int,default=20260926)
    ap.add_argument('--exclude-campaign',help='campaign.json whose case IDs are excluded from this campaign')
    ap.add_argument('--authorized-request-ceiling',type=int,required=True)
    ap.add_argument('--phase',choices=['sanity','run'],required=True)
    args=ap.parse_args()
    use_upstream(default_dyrca_root())
    manifest_path=Path(args.manifest).resolve();manifest=json.loads(manifest_path.read_text())
    by_id={row['case_id']:row for row in manifest['cases']};split=json.loads(Path(args.split).read_text())
    test_rows=[by_id[case] for case in split['test']]
    excluded=[]
    if args.exclude_campaign:
        excluded=[row['case_id'] for row in json.loads(Path(args.exclude_campaign).read_text())['cases']]
        test_rows=[row for row in test_rows if row['case_id'] not in set(excluded)]
    observations={row['case_id']:json.loads(observation_file(row,manifest_path).read_text()) for row in test_rows}
    count=len(test_rows) if args.cases==0 else args.cases
    selected=choose_cases(test_rows,observations,count,args.selection_seed)
    expected=len(selected)*args.generations*len(FORMAL_ROUTES)
    if expected!=args.authorized_request_ceiling:
        raise SystemExit(f'Explicit ceiling must equal scheduled attempts: {expected}')
    cfg=load_model_config(args.config,args.model_alias)
    out=Path(args.out_dir);out.mkdir(parents=True,exist_ok=True)
    try:campaign_lock=acquire_campaign_lock(out)
    except ValueError as exc:raise SystemExit(str(exc))
    frozen_inputs={row['case_id']:observations[row['case_id']] for row in selected}
    evaluator_labels={row['case_id']:{'app':row['app'],'scenario':row['scenario'],'true_service':row['true_service']} for row in selected}
    for filename,value in [('frozen_inputs.json',frozen_inputs),('evaluator_labels.json',evaluator_labels)]:
        target=out/filename
        if target.exists() and json.loads(target.read_text())!=value:
            raise SystemExit(f'Frozen campaign input mismatch: {filename}')
        if not target.exists():target.write_text(json.dumps(value,sort_keys=True,separators=(',',':'))+'\n')
    observations=frozen_inputs
    campaign={'schema':'e2r_m2_formal_v1','source_manifest':str(manifest_path),
              'source_split':str(Path(args.split).resolve()),'model_alias':args.model_alias,
              'request_model':cfg['model'],'api_style':cfg.get('api_style','openai'),
              'actual_request_settings':request_settings(cfg,max_tokens=2000),
              'cases':[{'case_id':row['case_id'],'app':row['app'],'scenario':row['scenario'],
                        'catalog_operations':len(messages_for(observations[row['case_id']],'proposal')[1])}
                       for row in selected],
              'excluded_case_ids':excluded,'generations':args.generations,'routes':list(FORMAL_ROUTES),
              'frozen_model_inputs':'frozen_inputs.json','separate_evaluator_labels':'evaluator_labels.json',
              'selection_seed':args.selection_seed,'authorized_request_ceiling':expected,
              'retry_policy':'none; failed/unknown attempts consume the fixed ceiling',
              'route_policy':{'raw_direct':'cutoff facts only','catalog_direct':'same facts and catalog as proposal; ranking only',
                              'proposal':'same facts/catalog; complete ranking plus selected operation IDs'}}
    path=out/'campaign.json'
    if path.exists() and json.loads(path.read_text())!=campaign:raise SystemExit('Immutable campaign mismatch')
    if not path.exists():path.write_text(json.dumps(campaign,indent=2)+'\n')
    journal=out/'requests.jsonl';started,finished=journal_state(journal);tasks=task_list(selected,args.generations,FORMAL_ROUTES)
    if len({t['request_id'] for t in tasks})!=len(tasks):raise AssertionError('duplicate task IDs')
    if set(started)-set(finished):raise SystemExit('Unknown-outcome attempt exists; refusing further spend')
    if args.phase=='sanity':
        if started:raise SystemExit('Sanity requires a fresh campaign')
        stop=len(FORMAL_ROUTES)
    else:
        if len(started)<len(FORMAL_ROUTES):raise SystemExit('Run requires the completed three-route sanity')
        sanity=[finished.get(t['request_id']) for t in tasks[:len(FORMAL_ROUTES)]]
        if any(x is None or not x.get('transport_success') or not x.get('validation',{}).get('accepted') for x in sanity):
            raise SystemExit('Strict three-route sanity failed; refusing the remaining campaign')
        stop=expected
    for task in tasks:
        if len(started)>=stop:break
        request_id=task['request_id']
        if request_id in started:continue
        obs=observations[task['case_id']];messages,catalog=messages_for(obs,task['route'])
        start={'event':'request_started',**task,'attempt_number':len(started)+1,
               'time_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
               'model_alias':args.model_alias,'request_model':cfg['model'],'api_style':cfg.get('api_style','openai'),
               'actual_request_settings':request_settings(cfg,max_tokens=2000),
               'messages':messages}
        append_event(journal,start);started[request_id]=start
        outcome=call_once({**cfg,'session_id':f'e2r-m2-{request_id}'},messages)
        end={'event':'request_finished',**task,'time_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),**outcome}
        if outcome.get('transport_success'):
            end['validation']=validate_output(outcome['response_text'],task['route'],obs,catalog)
        append_event(journal,end);finished[request_id]=end
        print(json.dumps({'started':len(started),'finished':len(finished),'request_id':request_id,
                          'transport_success':outcome.get('transport_success')}),flush=True)
    print(json.dumps(write_status(out,campaign,started,finished),indent=2));campaign_lock.close()


if __name__=='__main__':main()
