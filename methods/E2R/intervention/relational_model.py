"""Entity-bound measurements and relation-conditioned action contexts (v2)."""
from __future__ import annotations
from dataclasses import dataclass
import math
import statistics
import torch
from torch import nn
from torch.nn import functional as F
from .model import scalar
from .schema import EFFECTS,OPERATIONS,ORIGINS,RELATIONS,ROLES,VARIABLES,number

# Entity names, role bindings, namespace, diagnosis and group identity are excluded.
ENTITY_FEATURES=tuple('role:'+x for x in ROLES)+('goal','target','destination')+tuple(
    f'{v}:{attribute}' for v in VARIABLES for attribute in ('value','known','fresh','age_fraction',*('origin:'+o for o in ORIGINS)))


def fit_normalizer(graphs):
    values={v:[] for v in VARIABLES}
    for graph in graphs:
        for entity in graph.entities:
            for variable in VARIABLES:
                fact=graph.latest(entity.id,variable)
                if fact and fact.usable(graph.at,entity.uid) and number(fact.value):
                    values[variable].append(scalar(fact.value))
    return {k:{'mean':statistics.mean(v),'scale':max(.05,statistics.pstdev(v)),'n':len(v)}
            for k,v in values.items() if v}


@dataclass
class EntityInput:
    x:torch.Tensor
    edges:torch.Tensor
    edge_types:torch.Tensor
    targets:torch.Tensor
    destinations:torch.Tensor
    operations:torch.Tensor
    parameters:torch.Tensor
    goals:torch.Tensor
    entity_ids:list[str]
    original_fact_count:int


def entity_tensorize(graph,plan,normalizer):
    entities=sorted(graph.entities,key=lambda e:e.id);lookup={e.id:i for i,e in enumerate(entities)}
    target={a.target for a in plan.actions};destination={a.destination for a in plan.actions if a.destination}
    rows=[]
    for entity in entities:
        row=[float(entity.role==role) for role in ROLES]+[float(entity.id in graph.goals),float(entity.id in target),float(entity.id in destination)]
        for variable in VARIABLES:
            fact=graph.latest(entity.id,variable)
            known=bool(fact and fact.value is not None and fact.validity=='valid')
            fresh=bool(fact and fact.usable(graph.at,entity.uid,hypothetical=True))
            value=scalar(fact.value) if fresh else 0.
            if fresh and number(fact.value) and variable in normalizer:
                spec=normalizer[variable];value=max(-5.,min(5.,(value-spec['mean'])/spec['scale']))
            row += [value,float(known),float(fresh),min(5.,max(0.,(graph.at-fact.observed_at)/fact.ttl)) if fact else 0.]
            row += [float(fact is not None and fact.origin==origin) for origin in ORIGINS]
        rows.append(row)
    edges=[];types=[]
    for relation in graph.active_relations():
        a,b=lookup[relation.source],lookup[relation.target];kind=RELATIONS.index(relation.kind)
        edges.extend([(a,b),(b,a)]);types.extend([kind,kind+len(RELATIONS)])
    return EntityInput(torch.tensor(rows,dtype=torch.float32),
        torch.tensor(edges,dtype=torch.long).T.contiguous() if edges else torch.empty(2,0,dtype=torch.long),
        torch.tensor(types,dtype=torch.long),torch.tensor([lookup[a.target] for a in plan.actions],dtype=torch.long),
        torch.tensor([lookup[a.destination or a.target] for a in plan.actions],dtype=torch.long),
        torch.tensor([OPERATIONS.index(a.operation) for a in plan.actions],dtype=torch.long),
        torch.tensor([[scalar(a.parameters.get('replicas',0))] for a in plan.actions],dtype=torch.float32).reshape(-1,1),
        torch.tensor([lookup[g] for g in graph.goals],dtype=torch.long),[e.id for e in entities],len(graph.facts))


class RelationalActionModel(nn.Module):
    """The set control retains entity/variable binding and removes only inter-entity edges."""
    def __init__(self,hidden=64,layers=3,variant='graph',diagnosis_classes=8):
        super().__init__()
        if variant not in ('graph','set','local'):raise ValueError('v2 variant must be graph, set or local')
        self.config={'hidden':hidden,'layers':layers,'variant':variant,'diagnosis_classes':diagnosis_classes}
        self.variant=variant
        self.input=nn.Sequential(nn.Linear(len(ENTITY_FEATURES),hidden),nn.LayerNorm(hidden),nn.SiLU())
        self.relation=nn.Embedding(2*len(RELATIONS),hidden)
        self.source=nn.ModuleList(nn.Linear(hidden,hidden) for _ in range(layers))
        self.update=nn.ModuleList(nn.Linear(hidden*2,hidden) for _ in range(layers))
        self.norm=nn.ModuleList(nn.LayerNorm(hidden) for _ in range(layers))
        self.operation=nn.Embedding(len(OPERATIONS),hidden)
        self.action_input=nn.Sequential(nn.Linear(hidden*3+1,hidden),nn.SiLU())
        self.sequence=nn.GRU(hidden,hidden,batch_first=True)
        self.no_action=nn.Parameter(torch.zeros(hidden))
        self.pool=nn.Linear(hidden,1)
        self.representation=nn.Sequential(nn.Linear(hidden*3,hidden),nn.SiLU(),nn.LayerNorm(hidden))
        self.effect_head=nn.Linear(hidden,len(EFFECTS))
        self.state_head=nn.Linear(hidden,len(EFFECTS));self.diagnosis_head=nn.Linear(hidden,diagnosis_classes)

    def forward(self,item):
        h=self.input(item.x)
        # Same representation path in training and inference: no discontinuous test-only pruning.
        for source,update,norm in zip(self.source,self.update,self.norm):
            aggregate=torch.zeros_like(h);degree=torch.zeros(len(h),1)
            if self.variant=='local':
                aggregate=F.silu(source(h));degree.fill_(1)
            elif self.variant=='graph' and item.edges.numel():
                left,right=item.edges
                messages=F.silu(source(h[left])*self.relation(item.edge_types))
                aggregate.index_add_(0,right,messages)
                degree.index_add_(0,right,torch.ones(len(right),1))
            h=F.silu(norm(h+update(torch.cat([h,aggregate/degree.clamp_min(1)],-1))))
        if len(item.operations):
            tokens=self.action_input(torch.cat([h[item.targets],h[item.destinations],self.operation(item.operations),item.parameters],-1))
            _,query=self.sequence(tokens.unsqueeze(0));query=query[0,0]
        else:query=self.no_action
        pooled=(h*self.pool(h).softmax(0)).sum(0);goals=h[item.goals].mean(0)
        z=self.representation(torch.cat([query,goals,pooled]))
        return {'embedding':z,'logits':self.effect_head(z),'state':self.state_head(z),'diagnosis':self.diagnosis_head(z),
            'kept_nodes':len(h),'total_nodes':len(h),'kept_edges':item.edges.shape[1] if self.variant=='graph' else 0,
            'raw_fact_count':item.original_fact_count,'representation_scope':'entities with bound latest evidence; no learned graph sparsity claim'}
