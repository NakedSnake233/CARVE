#!/usr/bin/env python3
"""Fixed-epoch, equal-data neural controls; no holdout-driven checkpoint choice."""
import argparse,json,random,sys,time,hashlib,resource
from collections import defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
import torch
from methods.E2R.intervention.relational_model import RelationalActionModel,entity_tensorize,fit_normalizer
from methods.E2R.intervention.data import validate_record
from methods.E2R.intervention.learning import targets,effect_loss
from methods.E2R.intervention.goal_transition import fit_goal_model

def run(data,out,epochs=40):
    started_all=time.time()
    data,out=Path(data).resolve(),Path(out).resolve();out.mkdir(parents=True,exist_ok=False)
    rows=[json.loads(l) for l in data.read_text().splitlines()];parsed=[validate_record(r) for r in rows];normalizer=fit_normalizer(g for g,p in parsed)
    items=[entity_tensorize(g,p,normalizer) for g,p in parsed];labels=[targets(r) for r in rows];torch.set_num_threads(2)
    meta={'schema':'fixed_epoch_neural_controls','source':'live','training_data':str(data),'data_sha256':hashlib.sha256(data.read_bytes()).hexdigest(),
       'fit_attempt_ids':[r['episode_id'] for r in rows],'epochs':epochs,'seeds':[42,43,44],'variants':['graph','local'],'hidden':32,'layers':2,'normalizer':normalizer,
       'checkpoint_selection':'last fixed epoch, no development/test scoring','scope':'whole closed18-row development family, not independently validated training performance'}
    (out/'TRAINING_PLAN.json').write_text(json.dumps(meta,indent=2)+'\n')
    models=[];transition_models=[];strata=defaultdict(list)
    for row in rows:strata[(row['group_id'],row['condition_id'],row['chosen_plan'])].append(row)
    for seed in meta['seeds']:
        rng=random.Random(seed);sample=[rng.choice(cell) for cell in strata.values() for _ in range(len(cell))]
        path=out/f'transition-bootstrap-s{seed}.json';fit_goal_model(sample,path)
        transition_models.append({'seed':seed,'path':str(path),'scope':'development within-cell bootstrap sensitivity; no new independent evidence'})
    for variant in meta['variants']:
        for seed in meta['seeds']:
            random.seed(seed);torch.manual_seed(seed);model=RelationalActionModel(hidden=32,layers=2,variant=variant);opt=torch.optim.AdamW(model.parameters(),lr=.003,weight_decay=1e-4)
            history=[];started=time.time()
            for epoch in range(epochs):
                model.train();loss=torch.stack([effect_loss(model(x)['logits'],y,m) for x,(y,m) in zip(items,labels)]).mean()
                if not torch.isfinite(loss):raise ValueError('nonfinite fit')
                opt.zero_grad();loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),5.);opt.step();history.append(loss.item())
                (out/'progress.json').write_text(json.dumps({'status':'running','variant':variant,'seed':seed,'epoch':epoch+1,'epochs':epochs,'updated_at':time.time()})+'\n')
            identity=f'{variant}-s{seed}';torch.save({'config':model.config,'state_dict':model.state_dict(),'normalizer':normalizer},out/(identity+'.pt'))
            models.append({'id':identity,'variant':variant,'seed':seed,'checkpoint':str(out/(identity+'.pt')),'training_losses':history,'seconds':time.time()-started})
    (out/'RESULTS.json').write_text(json.dumps({'status':'complete','metadata':meta,'models':models,'transition_sensitivity':transition_models,'resources':{'wall_seconds':time.time()-started_all,'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'torch_threads':torch.get_num_threads()}},indent=2)+'\n')
    (out/'progress.json').write_text(json.dumps({'status':'complete','models':len(models),'updated_at':time.time(),'results':str(out/'RESULTS.json')})+'\n')
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',required=True);p.add_argument('--out',required=True);p.add_argument('--epochs',type=int,default=40);a=p.parse_args();run(a.data,a.out,a.epochs)
