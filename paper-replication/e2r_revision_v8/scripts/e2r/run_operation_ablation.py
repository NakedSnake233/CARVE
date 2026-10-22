#!/usr/bin/env python3
"""Score dependency-consistent GraphPatch operation-group deletions."""
from pathlib import Path
from collections import defaultdict
import argparse,json,sys,time

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.E2R.graph import execute,operation_catalog,select_plan,tensors,use_upstream
from methods.E2R.paths import default_dyrca_root,observation_file
from scripts.e2r.run_graph_experiment import move_item,resolve_device,synchronize

MODES={
 'full':set(),
 'drop_identity':{'ALIGN_NODE'},
 'drop_blackhole_mismatch':{'ADD_BLACKHOLE_NODE','ADD_TRACE_MISMATCH_EDGE'},
 'drop_version_split':{'SPLIT_NODE'},
}


def selected_ids(catalog,dropped):
    selected={entry['id'] for entry in catalog if entry['operation']['op'] not in dropped}
    changed=True
    while changed:
        changed=False
        for entry in catalog:
            if entry['id'] in selected and not set(entry.get('requires',[])).issubset(selected):
                selected.remove(entry['id']);changed=True
    return [entry['id'] for entry in catalog if entry['id'] in selected]


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--manifest',required=True);ap.add_argument('--split',required=True)
    ap.add_argument('--checkpoints',required=True);ap.add_argument('--out-dir',required=True);ap.add_argument('--dyrca-root',default=str(default_dyrca_root()))
    ap.add_argument('--device',default='cuda:0');args=ap.parse_args();use_upstream(args.dyrca_root)
    import torch
    from dyrca_rca.models.graphsage import WeightedGraphSAGE
    device=resolve_device(torch,args.device)
    if device.type=='cuda':torch.cuda.set_device(device);torch.cuda.reset_peak_memory_stats(device)
    manifest_path=Path(args.manifest).resolve();manifest=json.loads(manifest_path.read_text());by={r['case_id']:r for r in manifest['cases']}
    cases=json.loads(Path(args.split).read_text())['test'];models={};checkpoints=Path(args.checkpoints)
    for seed in (42,43,44):
        state=torch.load(checkpoints/f'model_seed{seed}.pt',map_location='cpu',weights_only=True)
        if state.get('training_variant')!='patch':raise ValueError('patch checkpoint required')
        model=WeightedGraphSAGE(state['in_dim'],128,2,.1);model.load_state_dict(state['state_dict']);model.to(device);model.eval();models[seed]=model
    predictions=[];selection_counts=defaultdict(list);failures=[]
    with torch.no_grad():
      for case in cases:
        obs=json.loads(observation_file(by[case],manifest_path).read_text());catalog=operation_catalog(obs)
        for mode,dropped in MODES.items():
            ids=selected_ids(catalog,dropped);selection_counts[mode].append(len(ids))
            try:item=move_item(tensors(execute(obs,select_plan(obs,catalog,ids)),obs),device)
            except Exception as exc:failures.append({'case_id':case,'mode':mode,'error_type':type(exc).__name__,'error':str(exc)});continue
            for seed,model in models.items():
                synchronize(torch,device);started=time.perf_counter();score=model(item['x'],item['edge_index'],edge_weight=item['edge_weight'])
                values=score[item['candidate_indices']].detach().cpu().tolist();synchronize(torch,device)
                ranking=[s for s,_ in sorted(zip(item['candidate_services'],values),key=lambda x:(-x[1],x[0]))]
                predictions.append({'case_id':case,'app':by[case]['app'],'scenario':by[case]['scenario'],'true_service':by[case]['true_service'],
                                    'mode':mode,'seed':seed,'selected_operations':len(ids),'ranking':ranking,
                                    'rank':ranking.index(by[case]['true_service'])+1,'inference_seconds':time.perf_counter()-started})
    if failures:raise ValueError(f'operation ablation produced {len(failures)} execution failures: {failures[:3]}')
    summary={}
    for mode in MODES:
        group=[r for r in predictions if r['mode']==mode];n=len(group)
        summary[mode]={'rows':n,'incidents':len(cases),'AC@1':sum(r['rank']==1 for r in group)/n,'AC@3':sum(r['rank']<=3 for r in group)/n,
                       'AC@5':sum(r['rank']<=5 for r in group)/n,'MRR':sum(1/r['rank'] for r in group)/n,
                       'selected_operations_mean':sum(selection_counts[mode])/len(selection_counts[mode]),
                       'cases_changed_from_full_selection':sum(a!=b for a,b in zip(selection_counts[mode],selection_counts['full']))}
    out=Path(args.out_dir)
    if out.exists():raise SystemExit('Refusing overwrite')
    out.mkdir(parents=True)
    with (out/'predictions.jsonl').open('w') as f:
        for row in predictions:f.write(json.dumps(row)+'\n')
    (out/'results.json').write_text(json.dumps({'status':'completed_operation_group_deletion','seeds':[42,43,44],
        'test_incidents':len(cases),'summary':summary,'failure_count':0,
        'limits':['Frozen full-patch-trained scorer; operation groups are deleted without retraining.','Dependencies are removed recursively when their creator operation is deleted.']},indent=2)+'\n')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
