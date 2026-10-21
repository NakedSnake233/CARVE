"""Observation-supported diagnostic graph patches with a fixed candidate set.

Only the pure upstream executor/op schema is reused. No directory-reading upstream
planner, cached plan, target prior, or oracle-derived edge weighting is called.
"""
from __future__ import annotations

import copy
import json
import math
import sys
from dataclasses import asdict
from pathlib import Path


def use_upstream(root):
    source=Path(root)/'src'
    if not (source/'dyrca_rca/graph/executor.py').is_file():
        raise FileNotFoundError('DY-RCA pure component source not found')
    sys.dont_write_bytecode=True
    if str(source) not in sys.path:sys.path.insert(0,str(source))


def owner(service, candidates):
    text=str(service or '')
    if text.endswith('-v2') and text[:-3] in candidates:return text[:-3]
    if ':' in text and text.split(':')[0] in candidates:return text.split(':')[0]
    return text


def observation_graph(obs, phase):
    topology=obs[phase];candidates=set(obs['candidates']);nodes={};edges=[]
    for service in sorted(candidates):nodes[service]={'id':service,'attrs':{'type':'service','service':service}}
    pods={p.get('ip'):p for p in topology['pods'] if p.get('ip')}
    for pod in topology['pods']:
        if not pod.get('name'):continue
        service=owner(pod.get('service'),candidates)
        nodes['pod:'+pod['name']]={'id':'pod:'+pod['name'],'attrs':{'type':'pod','service':service,'version':pod.get('version','unknown')}}
    for svc in topology['services']:
        source=owner(svc.get('name'),candidates)
        if source not in candidates:continue
        for ip in svc.get('endpoint_ips',[]):
            pod=pods.get(ip)
            if pod and 'pod:'+pod['name'] in nodes:
                edges.append({'source':source,'target':'pod:'+pod['name'],'edge_type':'endpoint','weight':1.0,'attrs':{}})
    for edge in obs['call_edges'][phase]:
        if edge['source'] in candidates and edge['target'] in candidates:
            edges.append({'source':edge['source'],'target':edge['target'],'edge_type':'dependency','weight':1.0,'attrs':{}})
    return {'attrs':{'phase':phase},'nodes':list(nodes.values()),'edges':edges}


def operation_catalog(obs):
    pre=observation_graph(obs,'pre');during=observation_graph(obs,'during')
    before={n['id']:n for n in pre['nodes']};after={n['id']:n for n in during['nodes']}
    pre_edges={(e['source'],e['target'],e['edge_type']) for e in pre['edges']}
    now_edges={(e['source'],e['target'],e['edge_type']) for e in during['edges']}
    candidates=set(obs['candidates']);catalog=[]
    def add(group,operation,source):
        operation=dict(operation);operation['evidence']=[source]
        catalog.append({'id':f'op{len(catalog):04d}','group':group,'operation':operation,
                        'evidence_id':source})
    for nid in sorted(set(after)-set(before)):
        add('node_ops',{'op':'ADD_NODE','node_id':nid,'attrs':after[nid]['attrs']},'during:'+nid)
    for a,b,kind in sorted(pre_edges-now_edges):
        add('edge_ops',{'op':'REMOVE_EDGE','source':a,'target':b},f'topology_diff:{a}->{b}')
    for a,b,kind in sorted(now_edges-pre_edges):
        add('edge_ops',{'op':'ADD_EDGE','source':a,'target':b,'edge_type':kind,'weight':1.0},f'during:{a}->{b}')
    pre_svcs={s['name']:s for s in obs['pre']['services']}
    during_svcs={s['name']:s for s in obs['during']['services']}
    during_ip={p.get('ip'):p for p in obs['during']['pods'] if p.get('ip')}
    pre_ip={p.get('ip'):p for p in obs['pre']['pods'] if p.get('ip')}
    pre_owner_relations={(service['name'],owner(pre_ip[ip].get('service'),candidates))
                         for service in obs['pre']['services'] for ip in service.get('endpoint_ips',[])
                         if ip in pre_ip}
    for svc in sorted(candidates):
        a=pre_svcs.get(svc);b=during_svcs.get(svc)
        if a and b and a.get('endpoint_ips') and not b.get('endpoint_ips'):
            add('node_ops',{'op':'ADD_BLACKHOLE_NODE','service':svc,'attrs':{'type':'blackhole','service':svc}},f'empty_endpoint:{svc}')
        if b:
            for ip in b.get('endpoint_ips',[]):
                pod=during_ip.get(ip,{})
                actual=owner(pod.get('service'),candidates)
                if actual in candidates and actual!=svc and (svc,actual) not in pre_owner_relations:
                    add('edge_ops',{'op':'ADD_TRACE_MISMATCH_EDGE','source':svc,'target':actual,
                                    'edge_type':'endpoint_mismatch','weight':1.0},f'endpoint_owner:{svc}:{ip}')
        removed=[n for n in set(before)-set(after) if before[n]['attrs'].get('service')==svc]
        added=[n for n in set(after)-set(before) if after[n]['attrs'].get('service')==svc]
        if len(removed)==len(added)==1:
            old,new=removed[0],added[0]
            if before[old]['attrs'].get('version')==after[new]['attrs'].get('version'):
                add('node_ops',{'op':'ALIGN_NODE','old_node':old,'new_node':new,'service':svc,
                                'attrs':after[new]['attrs']},f'identity_change:{svc}')
        versions={str(p.get('version') or 'unversioned') for p in obs['during']['pods']
                  if owner(p.get('service'),candidates)==svc}
        versions={v if v not in {'unknown','latest',''} else 'unversioned' for v in versions}
        if len(versions)>1:
            children=[svc+':'+v for v in sorted(versions)]
            add('node_ops',{'op':'SPLIT_NODE','parent':svc,'children':children,
                            'attrs':{'type':'version','service':svc}},f'version_coexistence:{svc}')
    creators={}
    for entry in catalog:
        op=entry['operation']
        if op['op']=='ADD_NODE':creators.setdefault(op['node_id'],entry['id'])
        if op['op']=='ALIGN_NODE':creators.setdefault(op['new_node'],entry['id'])
        if op['op']=='ADD_BLACKHOLE_NODE':creators.setdefault('blackhole:'+op['service'],entry['id'])
        if op['op']=='SPLIT_NODE':
            for child in op['children']:creators.setdefault(child,entry['id'])
    for entry in catalog:
        op=entry['operation'];required=[]
        if entry['group']=='edge_ops':
            for node in [op.get('source'),op.get('target')]:
                if node and node not in before and node in creators:required.append(creators[node])
        entry['requires']=sorted(set(required))
    return catalog


def select_plan(obs,catalog,selected_ids=None):
    from dyrca_rca.graph.update_ops import GraphUpdatePlan
    selected_ids=[c['id'] for c in catalog] if selected_ids is None else selected_ids
    if not isinstance(selected_ids,list) or any(not isinstance(k,str) for k in selected_ids):
        raise ValueError('operation IDs must be a string list')
    allowed={c['id']:c for c in catalog}
    if len(set(selected_ids))!=len(selected_ids) or any(k not in allowed for k in selected_ids):
        raise ValueError('unsupported or duplicate operation ID')
    for key in selected_ids:
        if not set(allowed[key].get('requires',[])).issubset(selected_ids):
            raise ValueError('missing prerequisite operation')
    raw={'experiment_id':obs['case_id'],'change_type':'observation_supported_updates','confidence':0.0,
         'causal_role':'not_inferred','node_ops':[],'edge_ops':[],'weight_ops':[],'state_transfer':{},'evidence':[]}
    # Follow canonical operation order regardless of model output ordering.
    for entry in catalog:
        if entry['id'] in selected_ids:
            raw[entry['group']].append(copy.deepcopy(entry['operation']))
            raw['evidence'].append(entry['evidence_id'])
    plan=GraphUpdatePlan.from_dict(raw)
    errors=plan.validate_basic()
    if errors:raise ValueError('; '.join(errors))
    return plan


def execute(obs,plan):
    from dyrca_rca.graph.executor import execute_plan
    result=execute_plan(observation_graph(obs,'pre'),plan)
    if result.warnings:
        # No silent partial graph accepted for missing entities or invalid operations.
        raise ValueError('graph execution rejected: '+'; '.join(result.warnings))
    return result.graph


def feature_vector(attrs,obs):
    candidates=set(obs['candidates']);service=owner(attrs.get('service') or attrs.get('parent'),candidates)
    kind=attrs.get('type','service')
    if attrs.get('blackhole'):kind='blackhole'
    if attrs.get('split_child'):kind='version'
    stats=obs['observed_metrics'].get(service,{})
    feats=[float(kind==k) for k in ('service','pod','version','blackhole')]
    for metric,field in [('cpu_usage','mean'),('cpu_usage','max'),('latency_p99','mean'),
                         ('request_rate','mean'),('service_up','mean'),('trace_ms','p95'),
                         ('trace_error','mean'),('log_error','mean')]:
        value=stats.get('during:'+metric)
        x=float(value.get(field,0)) if value else 0.0
        feats.extend([math.log1p(max(x,0)),float(value is not None)])
    dep=next((d for d in obs['during']['deployments'] if d.get('name')==service),{})
    svc=next((s for s in obs['during']['services'] if s.get('name')==service),{})
    feats += [math.log1p(max(float(dep.get('replicas',0) or 0),0)),
              math.log1p(max(float(dep.get('readyReplicas',0) or 0),0)),
              math.log1p(len(svc.get('endpoint_ips',[]))),float(bool(dep)),float(bool(svc))]
    return feats


def tensors(graph,obs):
    import torch
    nodes=[n for n in graph['nodes'] if not n.get('attrs',{}).get('removed')]
    nodes.sort(key=lambda n:n['id'])
    ids=[n['id'] for n in nodes];lookup={n:i for i,n in enumerate(ids)}
    x=[feature_vector(n.get('attrs',{}),obs) for n in nodes]
    pairs=[];weights=[]
    for edge in graph['edges']:
        if edge.get('attrs',{}).get('active') is False:continue
        a,b=edge['source'],edge['target']
        if a not in lookup or b not in lookup:continue
        # Shared edge transform: keep structural experiment free of oracle-weight priors.
        weight=float(edge.get('weight',1.0))
        if not math.isfinite(weight) or weight<0:raise ValueError('invalid edge weight')
        pairs += [(lookup[a],lookup[b]),(lookup[b],lookup[a])];weights += [weight,weight]
    return {'x':torch.tensor(x,dtype=torch.float32),
            'edge_index':torch.tensor(pairs,dtype=torch.long).t().contiguous() if pairs else torch.empty((2,0),dtype=torch.long),
            'edge_weight':torch.tensor(weights,dtype=torch.float32),'node_ids':ids,
            'candidate_indices':[lookup[c] for c in obs['candidates']],
            'candidate_services':list(obs['candidates'])}


def graphs_for(obs,selected_ids=None):
    catalog=operation_catalog(obs)
    plan=select_plan(obs,catalog,selected_ids)
    return {'reference':observation_graph(obs,'pre'),'runtime':observation_graph(obs,'during'),
            'patch':execute(obs,plan)},catalog,asdict(plan)


def model_input(obs,catalog):
    # Same factual representation for graph proposal and direct ranking.
    # IDs/labels from original experiment directories never enter this payload.
    return {'case_id':obs['case_id'],'candidates':obs['candidates'],
            'pre_topology':obs['pre'],'observed_topology':obs['during'],
            'telemetry':obs['observed_metrics'],'call_edges':obs['call_edges'],
            'supported_operations':catalog,'window':obs['window'],'coverage':obs['coverage']}
