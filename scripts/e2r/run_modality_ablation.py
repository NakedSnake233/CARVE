#!/usr/bin/env python3
"""Score frozen patch-trained models under reviewer-requested observation deletions."""
from pathlib import Path
from collections import defaultdict
import argparse,copy,json,statistics,sys,time

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.E2R.graph import graphs_for,tensors,use_upstream
from methods.E2R.paths import default_dyrca_root,observation_file
from scripts.e2r.run_graph_experiment import move_item,resolve_device,synchronize

MODES=('full','no_log','no_trace_features','no_metric_features','topology_only')


def ablate(obs,mode):
    value=copy.deepcopy(obs)
    if mode=='full':return value
    if mode=='topology_only':value['observed_metrics']={};value['call_edges']={'pre':[],'during':[]};return value
    remove={
        'no_log':lambda key:'log_error' in key,
        'no_trace_features':lambda key:'trace_ms' in key or 'trace_error' in key,
        'no_metric_features':lambda key:not ('trace_' in key or 'log_error' in key),
    }[mode]
    for service,items in value['observed_metrics'].items():
        value['observed_metrics'][service]={key:stat for key,stat in items.items() if not remove(key)}
    # call_edges cannot be source-attributed in the saved compact schema; retain them
    # except in topology_only and disclose this in the result.
    return value


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--manifest',required=True)
    ap.add_argument('--split',required=True);ap.add_argument('--checkpoints',required=True);ap.add_argument('--out-dir',required=True)
    ap.add_argument('--app',default='boutique');ap.add_argument('--dyrca-root',default=str(default_dyrca_root()))
    ap.add_argument('--device',default='cuda:0');args=ap.parse_args();use_upstream(args.dyrca_root)
    import torch
    from dyrca_rca.models.graphsage import WeightedGraphSAGE
    device=resolve_device(torch,args.device)
    if device.type=='cuda':torch.cuda.set_device(device);torch.cuda.reset_peak_memory_stats(device)
    manifest_path=Path(args.manifest).resolve();manifest=json.loads(manifest_path.read_text());by={r['case_id']:r for r in manifest['cases']}
    split=json.loads(Path(args.split).read_text());cases=[c for c in split['test'] if by[c]['app']==args.app]
    models={};checkpoint_dir=Path(args.checkpoints)
    for seed in (42,43,44):
        state=torch.load(checkpoint_dir/f'model_seed{seed}.pt',map_location='cpu',weights_only=True)
        if state.get('training_variant')!='patch':raise ValueError('patch-trained checkpoint required')
        model=WeightedGraphSAGE(state['in_dim'],128,2,.1);model.load_state_dict(state['state_dict']);model.to(device);model.eval();models[seed]=model
    predictions=[];materialization=defaultdict(float)
    with torch.no_grad():
      for case in cases:
        original=json.loads(observation_file(by[case],manifest_path).read_text())
        for mode in MODES:
            started=time.perf_counter();obs=ablate(original,mode);graph=graphs_for(obs)[0]['patch'];item=move_item(tensors(graph,obs),device)
            synchronize(torch,device);materialization[mode]+=time.perf_counter()-started
            for seed,model in models.items():
                synchronize(torch,device);t=time.perf_counter();scores=model(item['x'],item['edge_index'],edge_weight=item['edge_weight'])
                values=scores[item['candidate_indices']].detach().cpu().tolist();synchronize(torch,device)
                ranking=[s for s,_ in sorted(zip(item['candidate_services'],values),key=lambda x:(-x[1],x[0]))]
                predictions.append({'case_id':case,'app':args.app,'scenario':by[case]['scenario'],'true_service':by[case]['true_service'],
                                    'mode':mode,'seed':seed,'ranking':ranking,'rank':ranking.index(by[case]['true_service'])+1,
                                    'inference_seconds':time.perf_counter()-t})
    summary={}
    for mode in MODES:
        group=[r for r in predictions if r['mode']==mode];n=len(group)
        summary[mode]={'rows':n,'incidents':len(cases),'AC@1':sum(r['rank']==1 for r in group)/n,
                       'AC@3':sum(r['rank']<=3 for r in group)/n,'AC@5':sum(r['rank']<=5 for r in group)/n,
                       'MRR':sum(1/r['rank'] for r in group)/n,'materialization_seconds':materialization[mode],
                       'inference_seconds':sum(r['inference_seconds'] for r in group)}
    out=Path(args.out_dir)
    if out.exists():raise SystemExit('Refusing overwrite')
    out.mkdir(parents=True)
    with (out/'predictions.jsonl').open('w') as f:
        for row in predictions:f.write(json.dumps(row)+'\n')
    result={'status':'completed_observation_deletion_ablation','app':args.app,'test_incidents':len(cases),'seeds':[42,43,44],
            'summary':summary,'runtime':{'device':str(device),'torch':torch.__version__,'cuda_runtime':torch.version.cuda},
            'limits':['Saved compact call_edges do not identify whether an edge came from metrics or traces; no_trace_features retains call_edges.',
                      'This is frozen-scorer input deletion, not retraining for each modality.']}
    (out/'results.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
