#!/usr/bin/env python3
"""Score a completed three-route M2 campaign with frozen audited M1 checkpoints."""
from pathlib import Path
from collections import Counter
import argparse,hashlib,json,sys,time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from methods.E2R.graph import execute,graphs_for,operation_catalog,select_plan,tensors,use_upstream
from methods.E2R.llm_pilot import FORMAL_ROUTES,journal_state,validate_output
from methods.E2R.paths import default_dyrca_root,observation_file
from scripts.e2r.run_graph_experiment import move_item,resolve_device,synchronize


def graph_rank(torch,model,item,truth,device):
    moved=move_item(item,device);synchronize(torch,device);started=time.perf_counter()
    scores=model(moved['x'],moved['edge_index'],edge_weight=moved['edge_weight'])
    values=scores[moved['candidate_indices']].detach().cpu().tolist();synchronize(torch,device)
    ranking=[s for s,_ in sorted(zip(moved['candidate_services'],values),key=lambda x:(-x[1],x[0]))]
    return ranking,ranking.index(truth)+1,time.perf_counter()-started


def direct_row(arm,base,validation,truth):
    if validation and validation.get('accepted'):
        ranking=validation['ranking'];return {**base,'arm':arm,'status':'ranked','ranking':ranking,'rank':ranking.index(truth)+1}
    return {**base,'arm':arm,'status':'failed','rank':None,
            'errors':validation.get('errors',[]) if validation else ['transport_failure']}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--campaign-dir',required=True);ap.add_argument('--manifest',required=True)
    ap.add_argument('--checkpoints',required=True);ap.add_argument('--out-dir',required=True)
    ap.add_argument('--checkpoint-hashes',required=True,
                    help='JSON mapping model filename to audited SHA-256')
    ap.add_argument('--dyrca-root',default=str(default_dyrca_root()));ap.add_argument('--device',default='cuda:0')
    ap.add_argument('--threads',type=int,default=2);args=ap.parse_args()
    use_upstream(args.dyrca_root)
    import torch
    from dyrca_rca.models.graphsage import WeightedGraphSAGE
    torch.set_num_threads(args.threads);device=resolve_device(torch,args.device)
    if device.type=='cuda':torch.cuda.set_device(device);torch.cuda.reset_peak_memory_stats(device)
    campaign_dir=Path(args.campaign_dir);campaign=json.loads((campaign_dir/'campaign.json').read_text())
    status=json.loads((campaign_dir/'status.json').read_text())
    if campaign.get('schema')!='e2r_m2_formal_v1' or tuple(campaign['routes'])!=FORMAL_ROUTES:
        raise ValueError('not a reviewer-aligned formal campaign')
    if status['status']!='complete' or status['actual_http_attempts_started']!=campaign['authorized_request_ceiling']:
        raise SystemExit('Refusing incomplete campaign')
    started,finished=journal_state(campaign_dir/'requests.jsonl')
    if set(started)!=set(finished) or len(finished)!=campaign['authorized_request_ceiling']:
        raise ValueError('request journal coverage mismatch')
    manifest_path=Path(args.manifest).resolve();manifest=json.loads(manifest_path.read_text());source_rows={r['case_id']:r for r in manifest['cases']}
    selected=[row['case_id'] for row in campaign['cases']]
    obs=json.loads((campaign_dir/campaign['frozen_model_inputs']).read_text())
    labels=json.loads((campaign_dir/campaign['separate_evaluator_labels']).read_text())
    if set(obs)!=set(selected) or set(labels)!=set(selected):raise ValueError('frozen campaign input/label coverage mismatch')
    for case in selected:
        current=json.loads(observation_file(source_rows[case],manifest_path).read_text())
        current_label={key:source_rows[case][key] for key in ('app','scenario','true_service')}
        if current!=obs[case] or current_label!=labels[case]:raise ValueError(f'source changed after campaign freeze: {case}')
    checkpoint_dir=Path(args.checkpoints);models={}
    expected_hashes=json.loads(Path(args.checkpoint_hashes).read_text())
    expected_names={f'model_seed{seed}.pt' for seed in (42,43,44)}
    if set(expected_hashes)!=expected_names or any(not isinstance(value,str) or len(value)!=64 for value in expected_hashes.values()):
        raise ValueError('checkpoint hash manifest must contain exactly three SHA-256 values')
    actual_hashes={}
    for seed in (42,43,44):
        name=f'model_seed{seed}.pt';checkpoint=checkpoint_dir/name
        actual_hashes[name]=hashlib.sha256(checkpoint.read_bytes()).hexdigest()
        if actual_hashes[name]!=expected_hashes[name]:raise ValueError('checkpoint content hash mismatch')
        state=torch.load(checkpoint,map_location='cpu',weights_only=True)
        if (state.get('seed')!=seed or state.get('training_variant')!='patch'
                or state.get('feature_policy')!='shared_observation_v1'):
            raise ValueError('checkpoint identity mismatch')
        model=WeightedGraphSAGE(state['in_dim'],hidden_dim=128,num_layers=2,dropout=.1)
        model.load_state_dict(state['state_dict']);model.to(device);model.eval();models[seed]=model
    predictions=[]
    with torch.no_grad():
      for case in selected:
        observation=obs[case];truth=labels[case]['true_service'];catalog=operation_catalog(observation)
        graphs,_,_=graphs_for(observation);base_items={name:tensors(graph,observation) for name,graph in graphs.items()}
        common={'case_id':case,'app':labels[case]['app'],'scenario':labels[case]['scenario'],'true_service':truth}
        for seed,model in models.items():
            for arm,variant in [('reference','reference'),('runtime','runtime'),('rule_patch','patch')]:
                ranking,rank,seconds=graph_rank(torch,model,base_items[variant],truth,device)
                predictions.append({**common,'generation':None,'seed':seed,'arm':arm,'status':'ranked',
                                    'ranking':ranking,'rank':rank,'inference_seconds':seconds,'fallback_used':False})
        for generation in range(campaign['generations']):
            events={route:finished[f'{case}-g{generation}-{route}'] for route in FORMAL_ROUTES}
            validations={}
            for route,event in events.items():
                validation=None
                if event.get('transport_success'):
                    validation=validate_output(event['response_text'],route,observation,catalog)
                    if validation!=event.get('validation'):raise ValueError(f'validation mismatch: {event["request_id"]}')
                validations[route]=validation
            for route,arm in [('raw_direct','raw_direct_rank'),('catalog_direct','catalog_direct_rank')]:
                base={**common,'generation':generation,'seed':None,'request_id':events[route]['request_id'],'fallback_used':False}
                predictions.append(direct_row(arm,base,validations[route],truth))
            proposal=validations['proposal'];proposal_event=events['proposal']
            base={**common,'generation':generation,'seed':None,'request_id':proposal_event['request_id'],'fallback_used':False}
            predictions.append(direct_row('proposal_rank_no_gnn',base,proposal,truth))
            accepted=bool(proposal and proposal.get('accepted'));llm_item=None
            if accepted:
                plan=select_plan(observation,catalog,proposal['selected_operation_ids']);llm_item=tensors(execute(observation,plan),observation)
            for seed,model in models.items():
                b={**common,'generation':generation,'seed':seed,'request_id':proposal_event['request_id']}
                if accepted:
                    ranking,rank,seconds=graph_rank(torch,model,llm_item,truth,device)
                    row={**b,'status':'ranked','ranking':ranking,'rank':rank,'inference_seconds':seconds,'fallback_used':False}
                    predictions += [{**row,'arm':'llm_patch_no_fallback'},{**row,'arm':'llm_patch_with_fallback'}]
                else:
                    errors=proposal.get('errors',[]) if proposal else ['transport_failure']
                    predictions.append({**b,'arm':'llm_patch_no_fallback','status':'failed','rank':None,'errors':errors,'fallback_used':False})
                    ranking,rank,seconds=graph_rank(torch,model,base_items['patch'],truth,device)
                    predictions.append({**b,'arm':'llm_patch_with_fallback','status':'ranked','ranking':ranking,'rank':rank,
                                        'inference_seconds':seconds,'fallback_used':True,'fallback_reason':errors})
    out=Path(args.out_dir)
    if out.exists():raise SystemExit('Refusing overwrite')
    out.mkdir(parents=True)
    with (out/'predictions.jsonl').open('w') as f:
        for row in predictions:f.write(json.dumps(row)+'\n')
    result={'status':'completed_m2_formal_scoring','evaluation_type':'real_gt',
            'campaign':str(campaign_dir.resolve()),'manifest':str(manifest_path),'checkpoints':str(checkpoint_dir.resolve()),
            'checkpoint_sha256':actual_hashes,
            'cases':len(selected),'generations':campaign['generations'],'seeds':[42,43,44],
            'prediction_rows':len(predictions),'row_status':dict(Counter(r['status'] for r in predictions)),
            'runtime':{'device':str(device),'torch':torch.__version__,'cuda_runtime':torch.version.cuda,
                       'gpu_name':torch.cuda.get_device_name(device) if device.type=='cuda' else None,
                       'peak_allocated_bytes':torch.cuda.max_memory_allocated(device) if device.type=='cuda' else None},
            'limits':['Frozen patch-trained M1 scorers.','No recovery action or external baseline is evaluated.']}
    (out/'results.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':main()
