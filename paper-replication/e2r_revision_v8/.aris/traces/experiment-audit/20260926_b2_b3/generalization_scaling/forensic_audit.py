#!/usr/bin/env python3
"""Reviewer-authored read-only CPU forensic replay; writes only alongside this script.

No experiment launch, retraining, GPU use, remote access, or production writes.
Metrics/splits/timing arithmetic independently implemented; graph construction and
checkpoint forward use inspected local production modules, whose hashes are pinned.
"""
import os
os.environ['CUDA_VISIBLE_DEVICES']=''
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ['OMP_NUM_THREADS']='2'
os.environ['MKL_NUM_THREADS']='2'
import sys,json,hashlib,statistics,math,random,itertools
from pathlib import Path
from collections import Counter,defaultdict
sys.dont_write_bytecode=True
ROOT=Path('${PROJECT_ROOT}'); OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT)); sys.path.insert(0,str(ROOT.parent/'dyrca/src'))
import torch
from methods.E2R.graph import graphs_for,tensors,operation_catalog,model_input
from dyrca_rca.models.graphsage import WeightedGraphSAGE
from dyrca_rca.models.losses import rca_node_loss
from scripts.e2r.benchmark_scaling import replicate
torch.set_num_threads(2)
RUNS=ROOT/'experiments/e2r/runs'
hashes={}; failures=[]
def sha(p):
    p=Path(p); h=hashlib.sha256(p.read_bytes()).hexdigest(); hashes[str(p)]=h; return h
def load(p):
    sha(p); return json.loads(Path(p).read_text())
def lines(p):
    sha(p); return [json.loads(x) for x in Path(p).read_text().splitlines() if x]
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def check(value,message):
    if not value:failures.append(message)
def close(a,b):return math.isclose(a,b,rel_tol=1e-10,abs_tol=1e-12)
def metric(rows):
    ranks=[x['ranking'].index(x['true_service'])+1 for x in rows];n=len(rows)
    return {'n':n,**{f'AC@{k}':sum(r<=k for r in ranks)/n for k in (1,3,5)},'MRR':sum(1/r for r in ranks)/n,'inference_seconds':sum(x['inference_seconds'] for x in rows)}
def signature(item):
    return digest({k:(v.tolist() if isinstance(v,torch.Tensor) else v) for k,v in item.items() if k!='node_ids'})
def overlap(ids,signatures):
    a,b=ids; shared=set(signatures[c] for c in a)&set(signatures[c] for c in b)
    return {'shared_input_signatures':len(shared),'left_cases':sum(signatures[c] in shared for c in a),'right_cases':sum(signatures[c] in shared for c in b)}

manifest=load(RUNS/'full_data_v2/manifest.json'); by={r['case_id']:r for r in manifest['cases']}
check(len(by)==len(manifest['cases']),'duplicate manifest case IDs')
base=load(RUNS/'gpu_full_4090d/split.json')
groups=defaultdict(list)
for r in manifest['cases']:groups[(r['app'],r['scenario'])].append(r['case_id'])
rng=random.Random(42);derived={k:[] for k in ('train','val','test')}
for key in sorted(groups):
    ids=sorted(groups[key]);rng.shuffle(ids);n=len(ids);nv=max(1,int(n*.15));nt=max(1,int(n*.15))
    derived['train']+=ids[:n-nv-nt];derived['val']+=ids[n-nv-nt:n-nt];derived['test']+=ids[n-nt:]
check(base==derived,'base split differs from deterministic app/scenario-stratified split')
check(len(set(r['directory'] for r in by.values()))==len(by),'repeated original source directory')
observations={};items={};plans={};signatures={};obshashes={};gtchecked=0
for cid,row in by.items():
    p=RUNS/'full_data_v2/observations'/f'{cid}.json';obs=load(p);observations[cid]=obs;obshashes[cid]=hashes[str(p)]
    check(obs['case_id']==cid,f'observation case ID {cid}')
    gt=Path(row['directory'])/'ground_truth.json'
    if gt.exists():
        truth=load(gt);check(truth['root_cause_service']==row['true_service'],f'ground truth service mismatch {cid}');gtchecked+=1
    gs,cat,plan=graphs_for(obs);plans[cid]={'catalog':cat,'plan':plan}
    its={v:tensors(g,obs) for v,g in gs.items()};its['node_only']={**its['patch'],'edge_index':torch.empty((2,0),dtype=torch.long),'edge_weight':torch.empty(0,dtype=torch.float32)}
    items[cid]=its;signatures[cid]=signature(its['patch'])
    check(row['true_service'] in obs['candidates'],f'ground truth excluded from candidates {cid}')
    check(all(torch.isfinite(x['x']).all().item() for x in its.values()),f'nonfinite features {cid}')

names=[f'lofo_{s}' for s in ('T1','T2','T3','T4')]+['boutique_to_trainticket','trainticket_to_boutique']
analysis=load(RUNS/'generalization_analysis_v1/analysis.json');generalization={};replay_differences=[]
for name in names:
    d=RUNS/f'generalization_{name}_v1';result=load(d/'results.json');split=load(d/'split.json');pred=lines(d/'predictions.jsonl')
    declared=load(ROOT/f'experiments/e2r/splits/generalization_v1/{name}.json')
    if name.startswith('lofo_'):
        scenario=name[-2:];expected={k:[c for c in base[k] if (by[c]['scenario']==scenario)==(k=='test')] for k in base}
    else:
        source,target=name.split('_to_');expected={k:[c for c in base[k] if by[c]['app']==(target if k=='test' else source)] for k in base}
    check(split==expected==declared,f'exact split mismatch {name}')
    for a,b in itertools.combinations(split,2):check(not set(split[a])&set(split[b]),f'ID split overlap {name} {a} {b}')
    check(all(len(x)==len(set(x)) for x in split.values()),f'duplicate split IDs {name}')
    check(result['split_counts']=={k:len(v) for k,v in split.items()},f'split count mismatch {name}')
    check(result['training_variant']=='patch' and result['seeds']==[42,43,44],f'protocol mismatch {name}')
    selected=set().union(*map(set,split.values()));planfiles=list((d/'plans').glob('*.json'))
    check({x.stem for x in planfiles}==selected,f'plan coverage mismatch {name}')
    for p in planfiles:check(load(p)==plans[p.stem],f'plan regeneration differs {p}')
    keys=Counter((r['case_id'],r['seed'],r['variant']) for r in pred)
    required=set(itertools.product(split['test'],[42,43,44],['reference','runtime','patch','node_only']))
    check(set(keys)==required and all(n==1 for n in keys.values()),f'prediction identity/coverage mismatch {name}')
    for i,r in enumerate(pred,1):
        row=by[r['case_id']];obs=observations[r['case_id']]
        check(all(r[k]==row[k] for k in ['app','scenario','true_service']),f'prediction metadata mismatch {name}:{i}')
        check(len(r['ranking'])==len(set(r['ranking'])) and set(r['ranking'])==set(obs['candidates']),f'candidate mismatch {name}:{i}')
        check(r['rank']==r['ranking'].index(r['true_service'])+1,f'rank field mismatch {name}:{i}')
        check(r['model_file']==f"model_seed{r['seed']}.pt",f'model pointer mismatch {name}:{i}')
    summary={}
    for seed,var in itertools.product([42,43,44],['reference','runtime','patch','node_only']):
        m=metric([r for r in pred if r['seed']==seed and r['variant']==var]);summary[f'{seed}:{var}']=m
        check(all(close(v,result['summary'][f'{seed}:{var}'][k]) for k,v in m.items()),f'summary arithmetic mismatch {name} {seed}:{var}')
    am=analysis['runs'][name]
    for k in ['AC@1','AC@3','AC@5','MRR']:
        vals=[summary[f'{s}:patch'][k] for s in (42,43,44)]
        check(close(statistics.mean(vals),am['mean'][k]) and close(statistics.stdev(vals),am['sample_std'][k]),f'analysis metric mismatch {name}:{k}')
        check(all(close(summary[f'{s}:patch'][k],am['by_seed'][str(s)][k]) for s in (42,43,44)),f'analysis seed metric mismatch {name}:{k}')
    check(close(sum(t['seconds'] for t in result['training']),am['training_seconds']),f'train timing arithmetic {name}')
    cpreplays=[]
    with torch.no_grad():
      for seed in (42,43,44):
        cp=d/f'model_seed{seed}.pt';h=sha(cp);state=torch.load(cp,map_location='cpu',weights_only=True)
        check(state['seed']==seed and state['training_variant']=='patch' and state['in_dim']==25,f'checkpoint metadata {name}/{seed}')
        expected_config={'hidden_dim':128,'num_layers':2,'dropout':.1,'lr':.003,'weight_decay':1e-5,'epochs':200,'batch_size':1,'seed':seed,'early_stop_patience':30,'pairwise_ranking_coef':1.}
        check(state['config']==expected_config,f'checkpoint config mismatch {name}/{seed}')
        check(all(torch.isfinite(t).all().item() for t in state['state_dict'].values()),f'nonfinite checkpoint {name}/{seed}')
        model=WeightedGraphSAGE(state['in_dim'],128,2,.1);model.load_state_dict(state['state_dict']);model.eval()
        rankdiff=0;fulldiff=0;top1diff=0;max_violation=0.;meaningful=0
        for line,r in enumerate(pred,1):
          if r['seed']!=seed:continue
          item=items[r['case_id']][r['variant']];scores=model(item['x'],item['edge_index'],edge_weight=item['edge_weight']);vals=scores[item['candidate_indices']].tolist();sv=dict(zip(item['candidate_services'],vals))
          ranking=sorted(sv,key=lambda s:(-sv[s],s));rank=ranking.index(r['true_service'])+1
          diff=ranking!=r['ranking'];fulldiff+=diff;rankdiff+=rank!=r['rank'];top1diff+=ranking[0]!=r['ranking'][0]
          violation=max([0.]+[sv[b]-sv[a] for a,b in zip(r['ranking'],r['ranking'][1:])]);max_violation=max(max_violation,violation)
          bad=violation>1e-5*max(1.,max(abs(v) for v in vals));meaningful+=bad
          if diff:replay_differences.append({'run':name,'seed':seed,'line':line,'case_id':r['case_id'],'variant':r['variant'],'saved_rank':r['rank'],'cpu_rank':rank,'max_adjacent_score_violation':violation,'outside_relative_tolerance':bad})
        history=next(t for t in result['training'] if t['seed']==seed)['history'];best=min(history,key=lambda x:x['val_loss']);epochs=len(history)
        check(history==sorted(history,key=lambda x:x['epoch']) and [x['epoch'] for x in history]==list(range(1,epochs+1)),f'training history epoch mismatch {name}/{seed}')
        check(epochs==200 or epochs-best['epoch']==30,f'early stop mismatch {name}/{seed}')
        losses=[]
        for cid in split['val']:
            item=items[cid]['patch'];scores=model(item['x'],item['edge_index'],edge_weight=item['edge_weight']);mask=torch.zeros(len(item['node_ids']),dtype=torch.bool);mask[item['candidate_indices']]=True
            losses.append(float(rca_node_loss(scores,torch.tensor(item['node_ids'].index(by[cid]['true_service'])),mask)[0]))
        val=sum(losses)/len(losses)
        check(abs(val-best['val_loss'])<1e-4,f'checkpoint validation loss mismatch {name}/{seed}: {val} versus {best["val_loss"]}')
        check(meaningful==0,f'checkpoint prediction score-order mismatch {name}/{seed}: {meaningful}')
        cpreplays.append({'seed':seed,'sha256':h,'config':state['config'],'epochs':epochs,'best_epoch':best['epoch'],'saved_best_validation_loss':best['val_loss'],'cpu_checkpoint_validation_loss':val,'full_ranking_differences':fulldiff,'true_rank_differences':rankdiff,'top1_service_differences':top1diff,'max_adjacent_score_violation':max_violation,'order_violations_outside_1e-5_relative_tolerance':meaningful})
    candidates={part:set().union(*(set(observations[c]['candidates']) for c in ids)) for part,ids in split.items()}
    generalization[name]={'split_counts':{k:len(v) for k,v in split.items()},'predictions':len(pred),'plans':len(planfiles),'patch_mean':am['mean'],'sample_std':am['sample_std'],'candidate_train_test_overlap':len(candidates['train']&candidates['test']),'exact_observation_sha_overlap':overlap((split['train'],split['test']),obshashes),'patch_tensor_train_test_overlap':overlap((split['train'],split['test']),signatures),'patch_tensor_train_validation_overlap':overlap((split['train'],split['val']),signatures),'checkpoints':cpreplays,'runtime':result['runtime']}

parts=('catalog_seconds','serialization_seconds','execution_seconds','tensor_seconds','inference_seconds')
def p95(v):return sorted(v)[math.floor((len(v)-1)*.95)]
scaling={};comparison=load(RUNS/'scaling_analysis_v1/analysis.json');cg={};fixture={}
for n in (1,5,10,20,40):
    for vol in (1,2,4,8):
        obs=replicate(observations['case00001'],n,vol);cat=operation_catalog(obs);payload=json.dumps(model_input(obs,cat),sort_keys=True,separators=(',',':'));item=tensors(graphs_for(obs)[0]['patch'],obs)
        fixture[(13*n,vol)]={'candidate_services':len(obs['candidates']),'nodes':len(item['node_ids']),'directed_edges':len(item['edge_weight']),'serialized_bytes':len(payload.encode()),'catalog_operations':len(cat),'patch_tensor_sha256':signature(item),'serialized_sha256':hashlib.sha256(payload.encode()).hexdigest()}
for label in ('cpu','gpu'):
    d=RUNS/f'scaling_{label}_v1';result=load(d/'results.json');rows=lines(d/'raw.jsonl');groups=defaultdict(list)
    for r in rows:groups[(r['candidate_services'],r['telemetry_factor'])].append(r)
    check(set(groups)==set(fixture),f'scaling condition coverage {label}')
    for key,group in groups.items():
        check(sorted(r['repetition'] for r in group)==list(range(1,11)),f'scaling repetition coverage {label}:{key}')
        for r in group:
            check(all(math.isfinite(r[p]) and r[p]>0 for p in parts),f'nonpositive/nonfinite timer {label}:{key}')
            check(all(r[k]==fixture[key][k] for k in ['candidate_services','nodes','directed_edges','serialized_bytes','catalog_operations']),f'fixture reconstruction mismatch {label}:{key}')
        saved=next(s for s in result['summary'] if (s['candidate_services'],s['telemetry_factor'])==key)
        for p in parts:
            vals=[r[p] for r in group]
            check(close(statistics.median(vals),saved[p+'_median']) and close(p95(vals),saved[p+'_p95']),f'scaling summary arithmetic {label}:{key}:{p}')
        check(saved['repetitions']==10,f'scaling summary repetitions {label}:{key}')
    cg[label]=groups;scaling[label]={'raw_rows':len(rows),'conditions':len(groups),'device':result['device'],'torch':result['torch'],'peak_allocated_bytes':result['peak_allocated_bytes']}
for row in comparison['conditions']:
    key=(row['candidate_services'],row['telemetry_factor'])
    for label in ('cpu','gpu'):
        g=cg[label][key];pipeline=[sum(x[p] for p in parts) for x in g];infer=[x['inference_seconds'] for x in g]
        for metric,values in [('pipeline',pipeline),('inference',infer)]:
            for stat,fn in [('median',statistics.median),('p95',p95)]:check(close(row[f'{label}_{metric}_{stat}_seconds'],fn(values)),f'scaling comparison {label}:{key}:{metric}:{stat}')
    for metric in ('inference','pipeline'):check(close(row[f'{metric}_speedup_cpu_over_gpu'],row[f'cpu_{metric}_median_seconds']/row[f'gpu_{metric}_median_seconds']),f'speedup arithmetic {key}:{metric}')
for candidates in (13,65,130,260,520):
    check(len(set(fixture[(candidates,v)]['patch_tensor_sha256'] for v in (1,2,4,8)))==1,f'telemetry changes fixed model inputs {candidates}')
for p in list(ROOT.joinpath('scripts/e2r').glob('*.py'))+list(ROOT.joinpath('methods/E2R').glob('*.py'))+[ROOT/'scripts/remote/generalization_worker.py']+list((ROOT.parent/'dyrca/src/dyrca_rca/models').glob('*.py'))+list((ROOT.parent/'dyrca/src/dyrca_rca/graph').glob('*.py')):
    sha(p)
receipt=load(ROOT/'experiments/e2r/checkpoint_hashes_gpu_full_4090d.json')
for fname,expected in receipt.items():check(sha(RUNS/'gpu_full_4090d'/fname)==expected,f'M1 checkpoint differs from existing receipt: {fname}')
sha(__file__)
for name in names:
    pred=lines(RUNS/f'generalization_{name}_v1/predictions.jsonl')
    changed={(d['seed'],d['case_id'],d['variant']):d['cpu_rank'] for d in replay_differences if d['run']==name}
    patch=[r for r in pred if r['variant']=='patch']; ranks=[changed.get((r['seed'],r['case_id'],r['variant']),r['rank']) for r in patch]
    generalization[name]['cpu_replayed_patch_mean']={**{f'AC@{k}':sum(r<=k for r in ranks)/len(ranks) for k in (1,3,5)},'MRR':statistics.mean(1/r for r in ranks)}
    generalization[name]['cpu_replayed_patch_rank_differences']=sum(r!=saved['rank'] for r,saved in zip(ranks,patch))
output={'review':'same-family provisional independent reviewer; CPU replay only, no retraining','torch_replay':torch.__version__,'checks_failed':failures,'warnings':['CPU replay includes near-tie ranking differences; see checkpoint_replay_differences and cpu_replayed_patch_mean.','No historical run-specific content hash receipt for generalization/scaling.','Scaling environments differ; hardware/thread identities are not persisted by benchmark.','Reported p95 uses floor((n-1)*.95), i.e. ninth of ten samples.'],'manifest_cases':len(by),'raw_gt_services_checked':gtchecked,'base_split_counts':{k:len(v) for k,v in base.items()},'generalization':generalization,'scaling':scaling,'scaling_fixtures':[{**v,'telemetry_factor':k[1]} for k,v in fixture.items()],'scaling_comparison':comparison['conditions'],'checkpoint_replay_differences':replay_differences,'hash_count':len(hashes),'hash_limit':'Computed at audit time. No run-time per-generalization/scaling input/checkpoint hash receipt exists in these run directories; these hashes alone cannot establish historical remote byte identity. Existing M1 checkpoint receipt matches all three current M1 checkpoint files but does not prove scaling loaded them.'}
(OUT/'forensic_output.json').write_text(json.dumps(output,indent=2)+'\n')
(OUT/'sha256_inventory.json').write_text(json.dumps(hashes,indent=2,sort_keys=True)+'\n')
(OUT/'input_fingerprints.json').write_text(json.dumps({c:{'observation_sha256':obshashes[c],'patch_tensor_sha256':signatures[c]} for c in by},indent=2,sort_keys=True)+'\n')
print(json.dumps({'checks_failed':failures,'generalization':{k:{x:v[x] for x in ['split_counts','predictions','patch_mean','patch_tensor_train_test_overlap','patch_tensor_train_validation_overlap']} for k,v in generalization.items()},'replay_differences':len(replay_differences),'scaling':scaling,'hash_count':len(hashes)},indent=2))
