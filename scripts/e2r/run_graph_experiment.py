#!/usr/bin/env python3
"""Fixed-trained-weights E2R graph comparison on real, observation-only snapshots."""
from pathlib import Path
import sys,os
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
sys.dont_write_bytecode=True
os.environ.setdefault('OMP_NUM_THREADS','2');os.environ.setdefault('MKL_NUM_THREADS','2')
import argparse,copy,json,random,time
from collections import defaultdict
from methods.E2R.graph import use_upstream,graphs_for,tensors
from methods.E2R.paths import default_dyrca_root,observation_file


def split_cases(cases,seed=42):
    groups=defaultdict(list)
    for row in cases:groups[(row['app'],row['scenario'])].append(row)
    rng=random.Random(seed);splits={'train':[],'val':[],'test':[]}
    for key in sorted(groups):
        group=sorted(groups[key],key=lambda r:r['case_id']);rng.shuffle(group);n=len(group)
        if n<3:raise ValueError(f'Need >=3 real eligible incidents per app/scenario, got {key}:{n}')
        nval=max(1,int(n*.15));ntest=max(1,int(n*.15));ntrain=n-nval-ntest
        splits['train']+=group[:ntrain];splits['val']+=group[ntrain:ntrain+nval];splits['test']+=group[ntrain+nval:]
    ids={k:{r['case_id'] for r in rows} for k,rows in splits.items()}
    assert all(ids.values()) and not (ids['train']&ids['val'] or ids['train']&ids['test'] or ids['val']&ids['test'])
    return splits


def explicit_split(cases,path):
    declared=json.loads(Path(path).read_text())
    if set(declared)!={'train','val','test'} or any(not isinstance(declared[k],list) or not declared[k] for k in declared):
        raise ValueError('split file requires non-empty train/val/test ID lists')
    by_id={row['case_id']:row for row in cases};ids={k:set(v) for k,v in declared.items()}
    if any(len(ids[k])!=len(declared[k]) for k in declared) or ids['train']&ids['val'] or ids['train']&ids['test'] or ids['val']&ids['test']:
        raise ValueError('split file IDs duplicate or overlap')
    selected=set().union(*ids.values())
    if not selected.issubset(by_id):raise ValueError('split file contains unknown case ID')
    return {k:[by_id[case] for case in declared[k]] for k in ('train','val','test')},[by_id[case] for case in sorted(selected)]


def forward(model,item):
    return model(item['x'],item['edge_index'],edge_weight=item['edge_weight'])



def resolve_device(torch,name):
    if name=='auto':name='cuda:0' if torch.cuda.is_available() else 'cpu'
    device=torch.device(name)
    if device.type=='cuda' and not torch.cuda.is_available():
        raise RuntimeError('CUDA requested but unavailable; install CUDA PyTorch/check NVIDIA driver. No CPU fallback.')
    if device.type not in {'cpu','cuda'}:
        raise ValueError('Supported devices: cpu, cuda or cuda:N')
    if device.type=='cuda':
        index=device.index if device.index is not None else torch.cuda.current_device()
        if index>=torch.cuda.device_count():raise ValueError('Requested CUDA device index is unavailable')
    return device


def move_item(item,device):
    return {key:value.to(device) if key in {'x','edge_index','edge_weight'} else value
            for key,value in item.items()}


def synchronize(torch,device):
    if device.type=='cuda':torch.cuda.synchronize(device)


def supervised_loss(torch,loss_fn,model,item,true_service,device):
    logits=forward(model,item)
    target=item['node_ids'].index(true_service)
    mask=torch.zeros(len(item['node_ids']),dtype=torch.bool,device=device)
    mask[item['candidate_indices']]=True
    return loss_fn(logits,torch.tensor(target,device=device),mask)[0]


def run(args):
    use_upstream(args.dyrca_root)
    import torch
    from dyrca_rca.models.graphsage import WeightedGraphSAGE
    from dyrca_rca.models.config import FIXED_GNN_CONFIG,set_seed
    from dyrca_rca.models.losses import rca_node_loss
    torch.set_num_threads(args.threads)
    device=resolve_device(torch,args.device)
    print(f"device={device}; torch={torch.__version__}; cuda_runtime={torch.version.cuda}",flush=True)
    if device.type=='cuda':
        # Some CUDA builds require context initialization before allocator stats.
        torch.cuda.set_device(device)
        torch.cuda.reset_peak_memory_stats(device)
    out=Path(args.out_dir);out.mkdir(parents=True,exist_ok=True)
    if (out/'results.json').exists():raise ValueError('Refusing to overwrite completed results')
    manifest=json.loads(Path(args.manifest).read_text());cases=manifest['cases']
    if args.split_file and args.limit:
        raise ValueError('--split-file and --limit cannot be combined')
    if args.limit:
        from scripts.e2r.prepare_data import balanced
        cases=balanced(cases,args.limit,42)
    if args.split_file:
        splits,cases=explicit_split(cases,args.split_file)
    else:
        splits=split_cases(cases,seed=42)
    (out/'split.json').write_text(json.dumps({k:[r['case_id'] for r in v] for k,v in splits.items()},indent=2)+'\n')
    materialized={};stage_start=time.perf_counter();failures=[]
    for row in cases:
        obs=json.loads(observation_file(row,args.manifest).read_text())
        if obs.get('case_id')!=row['case_id']:raise ValueError('Manifest/snapshot case ID mismatch')
        try:
            variants,catalog,plan=graphs_for(obs)
            items={v:move_item(tensors(g,obs),device) for v,g in variants.items()}
            patch=items['patch']
            items['node_only']={**patch,
                'edge_index':torch.empty((2,0),dtype=torch.long,device=device),
                'edge_weight':torch.empty(0,dtype=torch.float32,device=device)}
            materialized[row['case_id']]=items
            (out/'plans').mkdir(exist_ok=True)
            (out/'plans'/f"{row['case_id']}.json").write_text(json.dumps({'catalog':catalog,'plan':plan},indent=2)+'\n')
        except Exception as exc:
            failures.append({'case_id':row['case_id'],'error_type':type(exc).__name__,'error':str(exc)})
    if failures:
        (out/'materialization_failures.json').write_text(json.dumps(failures,indent=2)+'\n')
        raise ValueError(f'{len(failures)} materialization errors; no post-hoc successful-only sample selection')
    synchronize(torch,device)
    materialization_seconds=time.perf_counter()-stage_start
    epochs=args.epochs or FIXED_GNN_CONFIG.epochs
    is_smoke=epochs!=FIXED_GNN_CONFIG.epochs or bool(args.limit)
    all_predictions=[];training=[]
    training_variant=args.training_variant
    for seed in args.seeds:
        set_seed(seed);sample=materialized[cases[0]['case_id']][training_variant]
        model=WeightedGraphSAGE(sample['x'].shape[1],hidden_dim=128,num_layers=2,dropout=.1).to(device)
        optimizer=torch.optim.Adam(model.parameters(),lr=.003,weight_decay=1e-5)
        rng=random.Random(seed);best=None;best_loss=float('inf');stale=0;history=[]
        synchronize(torch,device);started=time.perf_counter()
        for epoch in range(epochs):
            model.train();order=list(splits['train']);rng.shuffle(order);losses=[]
            for row in order:
                item=materialized[row['case_id']][training_variant];optimizer.zero_grad()
                loss=supervised_loss(torch,rca_node_loss,model,item,row['true_service'],device)
                if not torch.isfinite(loss):raise ValueError('non-finite training loss')
                loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),5.0);optimizer.step();losses.append(float(loss.detach()))
            model.eval();validation=[]
            with torch.no_grad():
                for row in splits['val']:
                    item=materialized[row['case_id']][training_variant]
                    loss=supervised_loss(torch,rca_node_loss,model,item,row['true_service'],device);validation.append(float(loss))
            val=sum(validation)/len(validation);history.append({'epoch':epoch+1,'train_loss':sum(losses)/len(losses),'val_loss':val})
            if val<best_loss:
                best_loss=val;best=copy.deepcopy(model.state_dict());stale=0
            else:stale+=1
            if (epoch+1)%10==0:print(f'seed={seed} epoch={epoch+1} train={history[-1]["train_loss"]:.4f} val={val:.4f}',flush=True)
            if stale>=30:break
        model.load_state_dict(best);model.eval();synchronize(torch,device);training.append({'seed':seed,'seconds':time.perf_counter()-started,'epochs':len(history),'history':history})
        torch.save({'state_dict':{k:v.detach().cpu() for k,v in best.items()},'in_dim':sample['x'].shape[1],'seed':seed,'config':{**FIXED_GNN_CONFIG.__dict__,'seed':seed,'epochs':epochs},
                    'training_variant':training_variant,'feature_policy':'shared_observation_v1','edge_weights':'uniform_fixed_1_structure_isolation'},out/f'model_seed{seed}.pt')
        # The very same frozen object/weights score every variant on the same test set.
        with torch.no_grad():
            for row in splits['test']:
                for variant in args.eval_variants:
                    item=materialized[row['case_id']][variant];synchronize(torch,device);t=time.perf_counter();logits=forward(model,item)
                    values=logits[item['candidate_indices']].detach().cpu().tolist()
                    synchronize(torch,device)
                    pairs=list(zip(item['candidate_services'],values))
                    ranking=[s for s,_ in sorted(pairs,key=lambda x:(-x[1],x[0]))];rank=ranking.index(row['true_service'])+1
                    all_predictions.append({'case_id':row['case_id'],'app':row['app'],'scenario':row['scenario'],'true_service':row['true_service'],
                                            'seed':seed,'variant':variant,'ranking':ranking,'rank':rank,
                                            'inference_seconds':time.perf_counter()-t,'model_file':f'model_seed{seed}.pt'})
    summary={}
    for seed in args.seeds:
        for variant in args.eval_variants:
            group=[r for r in all_predictions if r['seed']==seed and r['variant']==variant];n=len(group)
            summary[f'{seed}:{variant}']={'n':n,'AC@1':sum(r['rank']==1 for r in group)/n,
                'AC@3':sum(r['rank']<=3 for r in group)/n,'AC@5':sum(r['rank']<=5 for r in group)/n,
                'MRR':sum(1/r['rank'] for r in group)/n,'inference_seconds':sum(r['inference_seconds'] for r in group)}
    with (out/'predictions.jsonl').open('w') as f:
        for row in all_predictions:f.write(json.dumps(row)+'\n')
    result={'status':'real_data_smoke_not_paper_result' if is_smoke else 'completed_offline_structural_ablation',
            'implementation':'e2r_observation_patch_v1',
            'runtime':{'device':str(device),'torch':torch.__version__,'cuda_runtime':torch.version.cuda,
                       'gpu_name':torch.cuda.get_device_name(device) if device.type=='cuda' else None,
                       'peak_allocated_bytes':torch.cuda.max_memory_allocated(device) if device.type=='cuda' else None,
                       'deterministic_algorithms':torch.are_deterministic_algorithms_enabled()},'source_manifest':str(Path(args.manifest).resolve()),
            'split_counts':{k:len(v) for k,v in splits.items()},'training_variant':training_variant,
            'evaluation_variants':args.eval_variants,'fixed_weights_across_test_variants':True,
            'LLM_used':False,'recovery_executed':False,'epochs_requested':epochs,'seeds':args.seeds,
            'materialization_seconds':materialization_seconds,'summary':summary,'training':training,
            'limitations':['Structural rule-patch experiment only; fresh LLM and recovery experiments are separate.',
                           'All edges have fixed unit weights to isolate graph structure; not old oracle-conditioned edge fusion.',
                           'Same diagnostic-time observable node features are shared; topology nodes/relations differ.',
                           'node_only retains patch nodes/features/candidates but removes every message-passing edge.',
                           'Pre-event call graph can be sparse; saved observation coverage is authoritative.']}
    (out/'results.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(summary,indent=2))
    return result


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--manifest',required=True);ap.add_argument('--out-dir',required=True)
    ap.add_argument('--split-file',help='Optional explicit disjoint train/val/test case-ID JSON')
    ap.add_argument('--data-root',default=str(default_dyrca_root()/'data/experiments'))
    ap.add_argument('--dyrca-root',default=str(default_dyrca_root()));ap.add_argument('--planner',choices=['observation-rule'],default='observation-rule')
    ap.add_argument('--device',default='auto',help='auto | cpu | cuda | cuda:N; explicit CUDA never silently falls back')
    ap.add_argument('--training-variant',choices=['reference','runtime','patch','node_only'],default='patch')
    ap.add_argument('--eval-variants',choices=['reference','runtime','patch','node_only'],nargs='+',
                    default=['reference','runtime','patch','node_only'])
    ap.add_argument('--limit',type=int,default=0);ap.add_argument('--seed',type=int,default=42)
    ap.add_argument('--seeds',type=int,nargs='+');ap.add_argument('--epochs',type=int,default=0);ap.add_argument('--threads',type=int,default=2)
    args=ap.parse_args();args.seeds=args.seeds or [args.seed];run(args)

if __name__=='__main__':main()
