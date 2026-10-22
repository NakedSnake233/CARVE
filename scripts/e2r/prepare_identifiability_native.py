#!/usr/bin/env python3
"""Render/apply two bounded, study-owned additions per idle Boutique namespace."""
import argparse
import copy
import fcntl
import json
from pathlib import Path
import subprocess
import sys
import time
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.platform import KubectlClient
from methods.E2R.intervention.campaign import http_request
from scripts.e2r.build_native_campaign import transaction
from scripts.e2r.profile_native_intervention import db_binding
OWNER='e2r-intervention-native-20260929'
REVISION='identifiability-20261001'
NAMESPACES=('e2r-isg-boutique-p3','e2r-isg-boutique-p4')


def objects_for(namespace,old,private):
    if any('persistentVolumeClaim' in v or 'hostPath' in v for v in private['spec']['template']['spec'].get('volumes',[])):
        raise ValueError('reference database must not share a persistent/host data volume')
    result=[]
    for original,name,port in [(old,'cart-alternative',7070),(private,'reference-db',6379)]:
        spec=copy.deepcopy(original['spec']);spec['replicas']=1;spec['strategy']={'type':'Recreate'}
        spec['selector']={'matchLabels':{'app':name}}
        spec['template']['metadata']={'labels':{'app':name,'e2r-owner':OWNER,'e2r-revision':REVISION}}
        if name=='cart-alternative':spec['template']=db_binding(spec['template'],'boutique','private-db')
        labels={'e2r-owner':OWNER,'e2r-revision':REVISION}
        result.append({'apiVersion':'apps/v1','kind':'Deployment','metadata':{'name':name,'namespace':namespace,'labels':labels},'spec':spec})
        result.append({'apiVersion':'v1','kind':'Service','metadata':{'name':name,'namespace':namespace,'labels':labels},
            'spec':{'type':'ClusterIP','selector':{'app':name},'ports':[{'name':'api','port':port,'targetPort':port}]}})
    return result


def render(out):
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False);all_objects=[];sources=[]
    for ns in NAMESPACES:
        c=KubectlClient(ns);labels=c._call(['get','namespace',ns,'-o','json'])['metadata'].get('labels',{})
        if labels.get('e2r-owner')!=OWNER or labels.get('e2r-cloned-from')!='e2r-isg-boutique':raise ValueError('namespace is not the registered replica')
        existing={d['metadata']['name'] for d in c.list('Deployment')}
        if existing&{'cart-alternative','reference-db'}:raise ValueError('new revision resources already exist; inspect prior preparation receipt')
        old=c.get('Deployment','cart-old');private=c.get('Deployment','private-db')
        all_objects.extend(objects_for(ns,old,private));sources.append({'namespace':ns,'old_uid':old['metadata']['uid'],'private_db_uid':private['metadata']['uid']})
    (out/'objects.json').write_text(json.dumps({'apiVersion':'v1','kind':'List','items':all_objects},indent=2)+'\n')
    (out/'PREPARATION_PLAN.json').write_text(json.dumps({'schema':'bounded_native_identifiability_setup_v1','created_at':time.time(),
        'namespaces':list(NAMESPACES),'additional_pods':4,'additional_deployments':['cart-alternative','reference-db'],
        'source_objects':sources,'scope':'Only new objects in existing owned replicas; no existing deployment/namespace/quota changed.',
        'next_gate':'Independent code/resource review, server dry-run, actual availability and native canary before any16-attempt campaign.'},indent=2)+'\n')
    print(out)


def apply(out):
    out=Path(out).resolve();release=json.loads((out/'RELEASE.json').read_text())
    if release['status']!='passed':raise ValueError('preparation release missing')
    plan=json.loads((out/'PREPARATION_PLAN.json').read_text());objects=json.loads((out/'objects.json').read_text())['items']
    if plan['namespaces']!=list(NAMESPACES) or len(objects)!=8:raise ValueError('preparation scope differs')
    expected={(ns,kind,name) for ns in NAMESPACES for kind in ('Deployment','Service') for name in ('cart-alternative','reference-db')}
    if {(o['metadata'].get('namespace'),o['kind'],o['metadata'].get('name')) for o in objects}!=expected:
        raise ValueError('object names or namespaces differ from the bounded preparation')
    if any(o['metadata'].get('labels',{}).get('e2r-owner')!=OWNER or o['metadata']['labels'].get('e2r-revision')!=REVISION for o in objects):
        raise ValueError('new object ownership labels absent')
    handles=[]
    try:
        for ns in NAMESPACES:
            h=(ROOT/'enhancement/namespace_locks'/(ns+'.lock')).open('a');fcntl.flock(h,fcntl.LOCK_EX|fcntl.LOCK_NB);handles.append(h)
            c=KubectlClient(ns);labels=c._call(['get','namespace',ns,'-o','json'])['metadata'].get('labels',{})
            if labels.get('e2r-owner')!=OWNER:raise ValueError('ownership changed')
            for kind in ('Deployment','Service'):
                for existing in c.list(kind):
                    if existing['metadata']['name'] in ('cart-alternative','reference-db') and existing['metadata'].get('labels',{}).get('e2r-revision')!=REVISION:
                        raise ValueError('refuse overwriting a preexisting unrelated resource')
        environment=KubectlClient(NAMESPACES[0]).context.environment()
        nodes=json.loads(subprocess.check_output(['kubectl','get','nodes','-o','json','--request-timeout=20s'],env=environment))['items']
        pods=json.loads(subprocess.check_output(['kubectl','get','pods','-A','-o','json','--request-timeout=20s'],env=environment))['items']
        capacity=sum(int(n['status']['allocatable']['pods']) for n in nodes)
        active=sum(p.get('status',{}).get('phase') not in ('Succeeded','Failed') for p in pods)
        memory={l.split(':')[0]:int(l.split()[1]) for l in Path('/proc/meminfo').read_text().splitlines()};available=memory['MemAvailable']/1024**2
        if capacity-active<12 or available<10:raise RuntimeError('insufficient conservative preparation headroom')
        for mode in ('dry-run','apply'):
            command=['kubectl','apply','--server-side','--field-manager=e2r-identifiability-20261001','-f',str(out/'objects.json')]
            if mode=='dry-run':command.append('--dry-run=server')
            p=subprocess.run(command,env=environment,capture_output=True,text=True,timeout=60);(out/(mode+'.log')).write_text(p.stdout+p.stderr)
            if p.returncode:raise RuntimeError(mode+' did not complete; preserve preparation artifacts')
        receipts=[]
        for ns in NAMESPACES:
            c=KubectlClient(ns)
            for name in ('cart-alternative','reference-db'):
                p=subprocess.run(['kubectl','-n',ns,'rollout','status','deployment/'+name,'--timeout=180s'],env=environment,capture_output=True,text=True,timeout=190)
                (out/(ns+'_'+name+'_rollout.log')).write_text(p.stdout+p.stderr)
                if p.returncode:raise RuntimeError('new replica did not become available')
            service=c.get('Service','cart-alternative');spec=transaction('boutique','http://'+service['spec']['clusterIP']+':7070',ns+'-new-standby-canary')
            ok,latency=http_request(spec['canary'])
            if not ok:raise RuntimeError('new standby native canary failed')
            receipts.append({'namespace':ns,'native_canary':True,'canary_ms':latency,
                'objects':[{k:d['metadata'][k] for k in ('name','uid','resourceVersion')} for d in c.list('Deployment') if d['metadata']['name'] in ('cart-alternative','reference-db')]})
        (out/'READY.json').write_text(json.dumps({'status':'passed','created_at':time.time(),'receipts':receipts,'preflight_available_memory_gib':available,
            'preflight_pods':active,'pod_capacity':capacity,'existing_resources_unchanged':True},indent=2)+'\n');print(json.dumps({'status':'passed','namespaces':list(NAMESPACES)}))
    finally:
        for h in handles:h.close()

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=['render','apply']);p.add_argument('--out',required=True,type=Path);a=p.parse_args();(render if a.mode=='render' else apply)(a.out)
