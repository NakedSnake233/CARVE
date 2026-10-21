"""Prospective v2 fitting on declared development groups; no test-based selection."""
from __future__ import annotations
from collections import Counter,defaultdict
import copy
import json
import math
from pathlib import Path
import random
import time
import torch
from torch.nn import functional as F
from .data import group_key,validate_record
from .effect_pairs import repeated_effect_pairs
from .learning import targets,effect_loss
from .model import plan_signature
from .relational_model import RelationalActionModel,ENTITY_FEATURES,entity_tensorize,fit_normalizer
from .schema import EFFECTS,number
from .learning_views import record_for_view
from .action_ranking import action_rank_pairs


def save(path,value):
    path=Path(path);temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n');temporary.replace(path)


def check_partitions(rows,split):
    if not split.get('train') or not split.get('development'):raise ValueError('training and development groups required')
    groups=[g for partition in split.values() for g in partition]
    observed={group_key(r) for r in rows}
    if (len(groups)!=len(set(groups)) or not observed<=set(groups)
            or not set(split['train']+split['development'])<=observed):raise ValueError('split overlaps or omits fitting/development groups')
    partitions={g:k for k,v in split.items() for g in v};pairs=defaultdict(set)
    for r in rows:pairs[(r['application'],r['comparison_group'])].add(partitions[group_key(r)])
    if any(len(v)>1 for v in pairs.values()):raise ValueError('comparison groups cross partitions')


def fit_v2(rows,split,out,*,seeds=(42,),epochs=120,hidden=64,layers=3,variant='graph',objective='effect_distinguish',
           learning_rate=.003,distinguish_weight=.2,minimum_repeats=5,allow_synthetic=False,run_metadata=None,input_view='full'):
    check_partitions(rows,split)
    if input_view not in ('full','initial'):raise ValueError('unknown learning input view')
    if variant not in ('graph','set','local') or objective not in ('effect','effect_distinguish','effect_action_rank','diagnosis','state'):raise ValueError('invalid model/objective')
    if epochs<2:raise ValueError('insufficient epoch budget')
    train=[r for r in rows if group_key(r) in split['train'] and 'input' in r]
    development=[r for r in rows if group_key(r) in split['development'] and 'input' in r]
    if not train or not development:raise ValueError('no fit/development observations')
    for r in train+development:validate_record(r,allow_synthetic=allow_synthetic)
    train=[record_for_view(r,input_view) for r in train]
    development=[record_for_view(r,input_view) for r in development]
    parsed=[validate_record(r,allow_synthetic=allow_synthetic) for r in train]
    normalizer=fit_normalizer(g for g,p in parsed)
    items=[entity_tensorize(g,p,normalizer) for g,p in parsed]
    dev_items=[entity_tensorize(*validate_record(r,allow_synthetic=allow_synthetic),normalizer) for r in development]
    labels=[targets(r) for r in train];dev_labels=[targets(r) for r in development]
    pairs=repeated_effect_pairs([r for r in rows if group_key(r) in split['train']],
        [r['episode_id'] for r in train],minimum_repeats) if objective=='effect_distinguish' else []
    ranking=action_rank_pairs([r for r in rows if group_key(r) in split['train']],
        [r['episode_id'] for r in train],minimum_repeats) if objective=='effect_action_rank' else []
    counts=Counter(group_key(r) for r,(y,m) in zip(train,labels) if m.any())
    weights=[1/counts[group_key(r)] if mask.any() else 0. for r,(y,mask) in zip(train,labels)]
    dev_counts=Counter(group_key(r) for r,(y,m) in zip(development,dev_labels) if m.any())
    dev_weights=[1/dev_counts[group_key(r)] if mask.any() else 0. for r,(y,mask) in zip(development,dev_labels)]
    if not sum(weights) or not sum(dev_weights):raise ValueError('no known target support')
    support=defaultdict(lambda:[0]*len(EFFECTS));ranges={}
    for row,(graph,plan) in zip(train,parsed):
        sig=plan_signature(graph,plan)
        for i,effect in enumerate(EFFECTS):support[sig][i]+=row['outcome']['effects'][effect] is not None
        for fact in graph.facts:
            if fact.usable(graph.at,graph.entity(fact.entity).uid) and number(fact.value):
                low,high=ranges.get(fact.variable,(fact.value,fact.value));ranges[fact.variable]=[min(low,fact.value),max(high,fact.value)]
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    config={'schema':'e2r_intervention_training_v2','architecture':'entity_relational_v2','source':train[0]['source'],
        'features':list(ENTITY_FEATURES),'normalizer':normalizer,'split':split,'seeds':list(seeds),'epochs':epochs,
        'hidden':hidden,'layers':layers,'variant':variant,'objective':objective,'learning_rate':learning_rate,
        'distinguish_weight':distinguish_weight,'minimum_repeats':minimum_repeats,'same_fine_diagnosis_pairs_only':True,
        'pair_count':len(pairs),'pair_kind_counts':dict(Counter(p['kind'] for p in pairs)),
        'action_rank_pair_count':len(ranking),'action_rank_scope':'Empirical contrasts between nonempty actions within training condition/protocol; not supported same-action C1 pairs.',
        'support':dict(support),'ranges':ranges,'train_input_attempts':len(train),'development_input_attempts':len(development),
        'train_attempts':sum(group_key(r) in split['train'] for r in rows),'test_evaluated':False,
        'calibration_status':'not_calibrated_for_admission','run_metadata':run_metadata,
        'input_view':input_view,
        'outcome_observation_scope':'Outcomes retain the original collection protocol, including all actually performed probes; initial-view prediction is not a no-query policy result.',
        'uncertainty_semantics':'Raw model probabilities and ensemble disagreement are not conditional probability confidence intervals. Separate validation empirical admission and policy validation are required.'}
    save(out/'training.json',config);save(out/'pairs.json',pairs);save(out/'action_rank_pairs.json',ranking)
    torch.set_num_threads(min(2,torch.get_num_threads()));summaries=[]
    for seed in seeds:
        random.seed(seed);torch.manual_seed(seed);model=RelationalActionModel(hidden=hidden,layers=layers,variant=variant)
        opt=torch.optim.AdamW(model.parameters(),lr=learning_rate,weight_decay=1e-4);best=float('inf');state=None;history=[]
        for epoch in range(epochs):
            began=time.perf_counter();pretrain=objective in ('diagnosis','state') and epoch<epochs//2
            if objective in ('diagnosis','state') and epoch==epochs//2:
                for name,p in model.named_parameters():p.requires_grad_(name.startswith('effect_head'))
            model.train();outputs=[model(x) for x in items]
            if pretrain and objective=='diagnosis':
                loss=torch.stack([F.cross_entropy(o['diagnosis'][None],torch.tensor([r['diagnosis_label']])) for o,r in zip(outputs,train)]).mean()
            elif pretrain:
                indices=[i for i,r in enumerate(train) if len(r.get('state_targets',[]))==len(EFFECTS) and all(number(v) for v in r['state_targets'])]
                if not indices:raise ValueError('no state pretraining targets')
                loss=torch.stack([F.mse_loss(outputs[i]['state'],torch.tensor(train[i]['state_targets'],dtype=torch.float32)) for i in indices]).mean()
            else:loss=sum(w*effect_loss(o['logits'],y,m) for o,(y,m),w in zip(outputs,labels,weights))/sum(weights)
            pair_loss=loss*0
            if pairs:
                def embedding(members):
                    return F.normalize(torch.stack([outputs[i]['embedding'] for i in members]).mean(0),dim=0)
                pair_loss=torch.stack([(torch.linalg.vector_norm(embedding(p['left_members'])-embedding(p['right_members']))/2-p['distance'])**2 for p in pairs]).mean()
            rank_loss=loss*0
            if ranking:
                def utility(side):
                    probabilities=torch.stack([outputs[i]['logits'].sigmoid() for i in side['members']]).mean(0)
                    return probabilities[EFFECTS.index('business_recovered')]+probabilities[EFFECTS.index('non_target_safe')]-1-side['action_cost']
                rank_loss=sum(p['weight']*F.softplus(-(utility(p['winner'])-utility(p['loser']))) for p in ranking)/sum(p['weight'] for p in ranking)
            total=loss+distinguish_weight*(pair_loss+rank_loss)
            if not torch.isfinite(total):raise ValueError('nonfinite v2 training loss')
            opt.zero_grad();total.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),5);opt.step();model.eval()
            with torch.no_grad():val=(sum(w*effect_loss(model(x)['logits'],y,m) for x,(y,m),w in zip(dev_items,dev_labels,dev_weights))/sum(dev_weights)).item()
            if not math.isfinite(val):raise ValueError('nonfinite development loss')
            history.append({'epoch':epoch+1,'loss':total.item(),'effect_or_aux_loss':loss.item(),'pair_loss':pair_loss.item(),'action_rank_loss':rank_loss.item(),
                'development_loss':val,'phase':'pretrain' if pretrain else 'effect','seconds':time.perf_counter()-began})
            save(out/'progress.json',{'status':'running','seed':seed,'epoch':epoch+1,'epochs':epochs,'updated_at':time.time(),'last_epoch':history[-1]})
            print(json.dumps({'seed':seed,**history[-1]}),flush=True)
            if not pretrain and val<best:best=val;state=copy.deepcopy(model.state_dict())
        torch.save({'model_config':model.config,'state_dict':state,'features':list(ENTITY_FEATURES)},out/f'seed{seed}.pt')
        save(out/f'seed{seed}_history.json',history);summaries.append({'seed':seed,'best_development_loss':best})
    save(out/'summary.json',summaries);save(out/'progress.json',{'status':'complete','updated_at':time.time(),'completed_seeds':list(seeds)})
    return {'training':config,'seeds':summaries}


class RelationalPredictor:
    def __init__(self,directory,seeds=None,admission_path=None):
        directory=Path(directory);self.directory=directory;self.metadata=json.loads((directory/'training.json').read_text())
        if self.metadata['schema']!='e2r_intervention_training_v2' or self.metadata['features']!=list(ENTITY_FEATURES):raise ValueError('v2 feature schema mismatch')
        self.selected_seeds=list(self.metadata['seeds'] if seeds is None else seeds)
        if not self.selected_seeds or not set(self.selected_seeds)<=set(self.metadata['seeds']):raise ValueError('seed not trained')
        self.metadata['prediction_seeds']=self.selected_seeds;self.models=[]
        for seed in self.selected_seeds:
            checkpoint=torch.load(directory/f'seed{seed}.pt',map_location='cpu',weights_only=True)
            model=RelationalActionModel(**checkpoint['model_config']);model.load_state_dict(checkpoint['state_dict']);self.models.append(model.eval())
        torch.set_num_threads(min(2,torch.get_num_threads()))
        self.admission=None
        if admission_path is not None:
            ledger=json.loads(Path(admission_path).read_text())
            if ledger['schema']!='validation_empirical_admission_v2' or ledger['training_configuration']!=self.metadata:
                raise ValueError('admission ledger model configuration differs')
            self.admission=ledger
            self.metadata['admission_calibration']={k:v for k,v in ledger.items() if k!='training_configuration'}

    def predict(self,graph,plan):
        item=entity_tensorize(graph,plan,self.metadata['normalizer'])
        with torch.no_grad():outputs=[m(item) for m in self.models];probs=torch.stack([o['logits'].sigmoid() for o in outputs])
        sig=plan_signature(graph,plan);support=self.metadata['support'].get(sig,[0]*len(EFFECTS));global_support=[sum(v[i] for v in self.metadata['support'].values()) for i in range(len(EFFECTS))]
        means=probs.mean(0).tolist();spread=probs.std(0,unbiased=False).tolist();outside=[]
        for fact in graph.facts:
            if fact.usable(graph.at,graph.entity(fact.entity).uid,hypothetical=True) and number(fact.value) and fact.variable in self.metadata['ranges']:
                low,high=self.metadata['ranges'][fact.variable];margin=max(1e-6,(high-low)*.2)
                if not low-margin<=fact.value<=high+margin:outside.append(fact.id)
        probabilities={k:v if n else None for k,v,n in zip(EFFECTS,means,global_support)}
        evidence={'schema':'validation_empirical_admission_v2','status':'uncalibrated'}
        if self.admission is not None:
            from .admission import lookup_admission
            evidence=lookup_admission(self.admission,sig,probabilities)
        return {'probabilities':probabilities,'raw_probabilities':dict(zip(EFFECTS,means)),
            'spread':dict(zip(EFFECTS,spread)),'support':dict(zip(EFFECTS,support)),'source':self.metadata['source'],
            'unseen_signature':sig not in self.metadata['support'],'outside_training_range':outside,'model_count':len(self.models),
            'selected_seeds':self.selected_seeds,'uncertainty_semantics':self.metadata['uncertainty_semantics'],
            'admission_evidence':evidence,
            'graph_sizes':[{'retained':o['kept_nodes'],'total':o['total_nodes'],'edges':o['kept_edges'],'raw_facts':o['raw_fact_count']} for o in outputs]}
