#!/usr/bin/env python3
"""Create one owned native experiment replica, including independent databases."""
from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.platform import KubectlClient
from scripts.e2r import prepare_native_formal_storage as storage
from scripts.e2r.prepare_native_formal_storage import (OWNER, apply_owned, mysql, private_write,
    ready_mysql, stored_rows, wait)

KUBE=['kubectl','--kubeconfig','/etc/rancher/k3s/k3s.yaml']


def inventory(namespace, resource):
    return json.loads(subprocess.check_output(KUBE+['get',resource,'-n',namespace,'-o','json']))['items']


def clean(obj, namespace):
    result={'apiVersion':obj['apiVersion'],'kind':obj['kind'],
            'metadata':{'name':obj['metadata']['name'],'namespace':namespace,'labels':{'e2r-owner':OWNER}}}
    if 'spec' in obj:
        result['spec']=copy.deepcopy(obj['spec'])
    if obj['kind']=='Service':
        for k in ('clusterIP','clusterIPs','ipFamilies','ipFamilyPolicy','healthCheckNodePort'):
            result['spec'].pop(k,None)
        for port in result['spec']['ports']:port.pop('nodePort',None)
    if obj['kind']=='PersistentVolumeClaim':
        result['spec'].pop('volumeName',None)
        result['spec'].pop('dataSource',None)
    return result


def clone(application, lane, out):
    source='e2r-isg-'+application;target=source+'-p'+str(lane)
    src,dst=KubectlClient(source),KubectlClient(target)
    labels=src._call(['get','namespace',source,'-o','json'])['metadata'].get('labels',{})
    if labels.get('e2r-owner')!=OWNER:raise ValueError('source is not owned by this study')
    node=src._call_cluster(['get','nodes','-o','json'])['items'][0]
    if int(node['status']['allocatable']['pods'])<180:raise RuntimeError('Pod capacity increase has not been verified')
    if subprocess.run(KUBE+['get','namespace',target],capture_output=True).returncode==0:
        raise RuntimeError('replica namespace already exists; inspect its preparation receipt')
    out.mkdir(parents=True,exist_ok=False);(out/'private').mkdir(mode=0o700)
    storage.ERROR_LOG=out/'private'/'command_errors.log'
    private_write(storage.ERROR_LOG,b'')
    state={'application':application,'namespace':target,'source':source,'status':'preparing','stages':[]}
    def checkpoint(stage):
        state['stages'].append({'stage':stage,'at':time.time()})
        (out/'state.json').write_text(json.dumps(state,indent=2)+'\n')
    checkpoint('before_namespace_creation')
    ns={'apiVersion':'v1','kind':'Namespace','metadata':{'name':target,'labels':{
        'e2r-owner':OWNER,'e2r-isolated-experiment':'true','e2r-cloned-from':source,'e2r-lane':str(lane)}}}
    subprocess.run(KUBE+['create','-f','-'],input=json.dumps(ns),text=True,check=True,capture_output=True)
    for kind in ('resourcequota','persistentvolumeclaim'):
        for obj in inventory(source,kind):apply_owned(dst,clean(obj,target))
    if application=='trainticket':
        secret=src._call(['get','secret','database-credentials','-o','json'])
        new=clean(secret,target);new.update(type=secret['type'],data=secret['data'])
        apply_owned(dst,new)  # Secret stays in memory/API; never printed or written to artifact logs.
    services=inventory(source,'service')
    for obj in services:apply_owned(dst,clean(obj,target))
    deployments=inventory(source,'deployment')
    # Copy all deployments, including Boutique frontends, to preserve the same quota commitment.
    databases=[x for x in deployments if x['metadata']['name'] in ('shared-db','private-db')]
    for obj in databases:apply_owned(dst,clean(obj,target))
    checkpoint('databases_created')
    for obj in databases:wait(dst,obj['metadata']['name'])
    verified=[]
    if application=='trainticket':
        for name in ('shared-db','private-db'):
            ready_mysql(dst,name)
            dump=mysql(src,name,'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysqldump -uroot --single-transaction --skip-lock-tables --databases ts')
            expected=stored_rows(src,name)
            if b'INSERT INTO' not in expected:raise RuntimeError('source database contains no Station records')
            private_write(out/'private'/(name+'.sql'),dump)
            mysql(dst,name,'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot',dump)
            if stored_rows(dst,name)!=expected:raise RuntimeError('replica data mismatch: '+name)
            verified.append(name)
    checkpoint('database_contents_verified')
    for obj in deployments:
        if obj not in databases:apply_owned(dst,clean(obj,target))
    for obj in deployments:wait(dst,obj['metadata']['name'])
    # The replica only becomes eligible after all native services are ready.
    mapping={obj['spec']['clusterIP']:dst.get('Service',obj['metadata']['name'])['spec']['clusterIP'] for obj in services}
    state.update(status='complete',service_ip_mapping=mapping,database_rows_verified=verified,
                 deployment_replicas={x['metadata']['name']:x['spec']['replicas'] for x in deployments},
                 completed_at=time.time())
    checkpoint('complete')
    (out/'summary.json').write_text(json.dumps(state,indent=2)+'\n')
    print(json.dumps({'namespace':target,'status':'complete','database_rows_verified':verified}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--application',choices=['boutique','trainticket'],required=True)
    p.add_argument('--lane',type=int,choices=[2,3,4],required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();clone(a.application,a.lane,a.out)
