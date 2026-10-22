#!/usr/bin/env python3
"""Combine disjoint immutable pilot/formal M2 shards into one 116-case view."""
from __future__ import annotations

import argparse,json
from collections import Counter
from pathlib import Path


def read_json(path):return json.loads(Path(path).read_text())
def read_jsonl(path):return [json.loads(line) for line in Path(path).read_text().splitlines() if line]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaigns',nargs=2,required=True)
    parser.add_argument('--scores',nargs=2,required=True)
    parser.add_argument('--campaign-out',required=True);parser.add_argument('--score-out',required=True)
    args=parser.parse_args()
    campaign_out=Path(args.campaign_out);score_out=Path(args.score_out)
    if campaign_out.exists() or score_out.exists():raise SystemExit('Refusing overwrite')
    campaigns=[];statuses=[];inputs=[];labels=[];events=[];predictions=[];score_results=[]
    for campaign_path,score_path in zip(args.campaigns,args.scores):
        root=Path(campaign_path);score=Path(score_path)
        campaign=read_json(root/'campaign.json');status=read_json(root/'status.json')
        if status['status']!='complete' or status['actual_http_attempts_started']!=campaign['authorized_request_ceiling']:
            raise ValueError('source campaign is incomplete')
        score_result=read_json(score/'results.json')
        if score_result['status']!='completed_m2_formal_scoring' or score_result['cases']!=len(campaign['cases']):
            raise ValueError('source score scope mismatch')
        campaigns.append(campaign);statuses.append(status);score_results.append(score_result)
        inputs.append(read_json(root/campaign['frozen_model_inputs']))
        labels.append(read_json(root/campaign['separate_evaluator_labels']))
        events.append(read_jsonl(root/'requests.jsonl'));predictions.append(read_jsonl(score/'predictions.jsonl'))
    invariant=('schema','source_manifest','source_split','model_alias','request_model','api_style',
               'actual_request_settings','generations','routes','retry_policy','route_policy')
    for key in invariant:
        if campaigns[0].get(key)!=campaigns[1].get(key):raise ValueError(f'campaign invariant mismatch: {key}')
    case_sets=[{row['case_id'] for row in campaign['cases']} for campaign in campaigns]
    if case_sets[0]&case_sets[1]:raise ValueError('source campaign cases overlap')
    combined_cases=campaigns[0]['cases']+campaigns[1]['cases']
    combined_ids={row['case_id'] for row in combined_cases}
    if len(combined_ids)!=116:raise ValueError('combined campaign must cover exactly 116 test incidents')
    frozen={};evaluator={}
    for source in inputs:
        if set(frozen)&set(source):raise ValueError('frozen input overlap')
        frozen.update(source)
    for source in labels:
        if set(evaluator)&set(source):raise ValueError('evaluator label overlap')
        evaluator.update(source)
    if set(frozen)!=combined_ids or set(evaluator)!=combined_ids:raise ValueError('combined frozen coverage mismatch')
    all_events=events[0]+events[1];started={row['request_id'] for row in all_events if row['event']=='request_started'}
    finished={row['request_id'] for row in all_events if row['event']=='request_finished'}
    ceiling=sum(campaign['authorized_request_ceiling'] for campaign in campaigns)
    if len(started)!=ceiling or started!=finished:raise ValueError('combined request identity mismatch')
    all_predictions=predictions[0]+predictions[1]
    prediction_ids={(row['case_id'],row['arm'],row.get('generation'),row.get('seed')) for row in all_predictions}
    if len(prediction_ids)!=len(all_predictions):raise ValueError('combined prediction identities overlap')
    campaign={key:campaigns[0].get(key) for key in invariant}
    campaign.update({'cases':combined_cases,'excluded_case_ids':[],
        'frozen_model_inputs':'frozen_inputs.json','separate_evaluator_labels':'evaluator_labels.json',
        'selection_seed':campaigns[0].get('selection_seed'),'authorized_request_ceiling':ceiling,
        'combination_provenance':[str(Path(path).resolve()) for path in args.campaigns],
        'combination_policy':'exact concatenation of disjoint 8-case pilot and 108-case formal shards'})
    transport=sum(bool(row.get('transport_success')) for row in all_events if row['event']=='request_finished')
    accepted=sum(bool(row.get('validation',{}).get('accepted')) for row in all_events if row['event']=='request_finished')
    response_models=sorted({row.get('response_model') for row in all_events
                            if row['event']=='request_finished' and row.get('response_model')})
    usage_missing=sum(row.get('usage') is None for row in all_events if row['event']=='request_finished')
    status={'status':'complete','authorized_request_ceiling':ceiling,
        'actual_http_attempts_started':len(started),'finished_attempts':len(finished),
        'unknown_outcome_attempts':[],'transport_successes':transport,
        'strict_accepted':accepted,'response_models':response_models,
        'provider_usage_missing_attempts':usage_missing}
    campaign_out.mkdir(parents=True)
    (campaign_out/'campaign.json').write_text(json.dumps(campaign,indent=2)+'\n')
    (campaign_out/'frozen_inputs.json').write_text(json.dumps(frozen,sort_keys=True,separators=(',',':'))+'\n')
    (campaign_out/'evaluator_labels.json').write_text(json.dumps(evaluator,sort_keys=True,separators=(',',':'))+'\n')
    with (campaign_out/'requests.jsonl').open('w') as handle:
        for row in all_events:handle.write(json.dumps(row)+'\n')
    (campaign_out/'status.json').write_text(json.dumps(status,indent=2)+'\n')
    row_status=Counter(row['status'] for row in all_predictions)
    score_result={'status':'completed_m2_formal_scoring','evaluation_type':'real_gt',
        'campaign':str(campaign_out.resolve()),'manifest':score_results[1]['manifest'],
        'checkpoints':score_results[1]['checkpoints'],
        'checkpoint_provenance_by_shard':[
            {'source_score':str(Path(path).resolve()),
             'declared_checkpoint_sha256':result.get('checkpoint_sha256')}
            for path,result in zip(args.scores,score_results)],
        'cases':116,'generations':2,'seeds':[42,43,44],
        'prediction_rows':len(all_predictions),'row_status':dict(row_status),
        'runtime':{'shards':[result['runtime'] for result in score_results]},
        'combination_provenance':[str(Path(path).resolve()) for path in args.scores],
        'limits':['Exact concatenation of two disjoint, identically configured, frozen Qwen campaign shards.',
                  'No request was replayed and no prediction was recomputed during combination.',
                  'The pilot score shard predates checkpoint-hash recording; formal-shard hashes must not be treated as a historical pilot attestation.']}
    score_out.mkdir(parents=True)
    with (score_out/'predictions.jsonl').open('w') as handle:
        for row in all_predictions:handle.write(json.dumps(row)+'\n')
    (score_out/'results.json').write_text(json.dumps(score_result,indent=2)+'\n')
    print(json.dumps({'cases':116,'attempts':ceiling,'predictions':len(all_predictions),
                      'strict_accepted':accepted},indent=2))


if __name__=='__main__':main()
