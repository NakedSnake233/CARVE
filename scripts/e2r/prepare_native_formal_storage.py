#!/usr/bin/env python3
"""Add a private DB and preserve Station data before native CPU-template trials."""
from __future__ import annotations
import argparse
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.platform import KubectlClient
from methods.E2R.intervention.pilot_completion import verified_pilot
OWNER='e2r-intervention-native-20260929'
ERROR_LOG=None


def private_write(path,data):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(data)


def run(client,args, *, payload=None, timeout=45):
    result=subprocess.run(['kubectl','--kubeconfig',client.context.kubeconfig,'--namespace',client.namespace,*args],
                          input=payload,capture_output=True,timeout=timeout)
    if result.returncode:
        if ERROR_LOG:
            with ERROR_LOG.open('ab') as stream:stream.write(result.stderr+b'\n')
        raise RuntimeError('native storage command failed: '+args[0]+'; see private command_errors.log')
    return result.stdout


def wait(client,name,timeout=300):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        obj=client.get('Deployment',name);status=obj.get('status',{});n=obj['spec']['replicas']
        if status.get('observedGeneration',0)>=obj['metadata']['generation'] and status.get('readyReplicas',0)==n and status.get('updatedReplicas',0)==n and status.get('replicas',0)==n:
            if not any(p['metadata'].get('deletionTimestamp') for p in client.list('Pod','app='+name)):
                return
        time.sleep(1)
    raise TimeoutError(name+' did not converge')


def scale(client,name,replicas):
    old=client.get('Deployment',name)
    client.patch('Deployment',name,[{'op':'test','path':'/metadata/uid','value':old['metadata']['uid']},
       {'op':'test','path':'/spec/replicas','value':old['spec']['replicas']},{'op':'replace','path':'/spec/replicas','value':replicas}])
    wait(client,name)


def apply_owned(client,obj):
    previous=subprocess.run(['kubectl','--kubeconfig',client.context.kubeconfig,'-n',client.namespace,'get',obj['kind'],obj['metadata']['name'],'-o','json'],capture_output=True)
    if previous.returncode==0:
        if json.loads(previous.stdout)['metadata'].get('labels',{}).get('e2r-owner')!=OWNER:
            raise RuntimeError('resource exists without study ownership')
        raise RuntimeError('formal-storage resource already exists; inspect prior state before resuming')
    run(client,['create','-f','-'],payload=json.dumps(obj).encode())


def pvc(client,name):
    apply_owned(client,{'apiVersion':'v1','kind':'PersistentVolumeClaim','metadata':{'name':name,'namespace':client.namespace,'labels':{'e2r-owner':OWNER}},
       'spec':{'accessModes':['ReadWriteOnce'],'storageClassName':'local-path','resources':{'requests':{'storage':'1Gi'}}}})


def mysql(client,deployment,command,body=None):
    return run(client,['exec','-i','deployment/'+deployment,'--','sh','-c',command],payload=body,timeout=90)


def ready_mysql(client,deployment):
    deadline=time.monotonic()+180
    while time.monotonic()<deadline:
        try:
            mysql(client,deployment,'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot -N -e "SELECT 1"');return
        except RuntimeError:time.sleep(2)
    raise TimeoutError('private MySQL did not accept authenticated query')


def stored_rows(client,deployment):
    return mysql(client,deployment,'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysqldump -uroot --no-create-info --skip-triggers --skip-comments --compact --skip-extended-insert --order-by-primary ts')


def create_second(client,application,out):
    if application=='trainticket':pvc(client,'private-db-data')
    template=copy.deepcopy(client.get('Deployment','shared-db')['spec'])
    template['replicas']=1;template['selector']={'matchLabels':{'app':'private-db'}}
    template['template']['metadata']['labels']['app']='private-db'
    if application=='trainticket':template['template']['spec']['volumes']=[{'name':'database','persistentVolumeClaim':{'claimName':'private-db-data'}}]
    db={'apiVersion':'apps/v1','kind':'Deployment','metadata':{'name':'private-db','namespace':client.namespace,'labels':{'e2r-owner':OWNER}},'spec':template}
    port=3306 if application=='trainticket' else 6379
    service={'apiVersion':'v1','kind':'Service','metadata':{'name':'private-db','namespace':client.namespace,'labels':{'e2r-owner':OWNER}},
             'spec':{'selector':{'app':'private-db'},'ports':[{'name':'api','port':port,'targetPort':port}]}}
    (out/'private_db_manifest.json').write_text(json.dumps({'items':[db,service]},indent=2)+'\n')
    apply_owned(client,db);apply_owned(client,service);wait(client,'private-db')


def prepare(application,out,after):
    global ERROR_LOG
    verified_pilot(after,application)
    out.mkdir(parents=True,exist_ok=False);private=out/'private';private.mkdir(mode=0o700)
    ERROR_LOG=private/'command_errors.log';private_write(ERROR_LOG,b'')
    client=KubectlClient('e2r-isg-'+application)
    if client._call(['get','namespace',client.namespace,'-o','json'])['metadata'].get('labels',{}).get('e2r-owner')!=OWNER:
        raise RuntimeError('not a study-owned namespace')
    source=client.get('Deployment','shared-db')
    (out/'shared_db_before.json').write_text(json.dumps(source,indent=2)+'\n')
    original={name:client.get('Deployment',name)['spec']['replicas'] for name in ['station-old','station-new','station-peer']} if application=='trainticket' else {}
    state={'status':'running','application':application,'namespace':client.namespace,'original_station_replicas':original,
           'source_database_verified':True,'stages':[],'restore_errors':[]}
    def checkpoint(stage):
        state['stages'].append({'stage':stage,'at':time.time()})
        temp=out/'state.tmp';temp.write_text(json.dumps(state,indent=2)+'\n');temp.replace(out/'state.json')
    checkpoint('original_state_saved_before_writes')
    error=None
    try:
        if application=='trainticket':
            for name in original:scale(client,name,0)
            checkpoint('station_clients_stopped')
            dump=mysql(client,'shared-db','MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysqldump -uroot --single-transaction --skip-lock-tables --databases ts')
            expected=stored_rows(client,'shared-db')
            if b'INSERT INTO' not in expected:raise RuntimeError('native Station source has no stored demo records')
            private_write(private/'station_snapshot.sql',dump);private_write(private/'expected_rows.sql',expected)
            checkpoint('database_and_rows_saved')
            create_second(client,application,out);ready_mysql(client,'private-db')
            mysql(client,'private-db','MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot',dump)
            if stored_rows(client,'private-db')!=expected:raise RuntimeError('second database differs from the original stored rows')
            checkpoint('second_persistent_copy_restored_and_verified')
            pvc(client,'shared-db-data')
            obj=client.get('Deployment','shared-db');volumes=copy.deepcopy(obj['spec']['template']['spec']['volumes'])
            if volumes!=[{'name':'database','emptyDir':{}}]:raise RuntimeError('unexpected existing database volume; do not overwrite')
            state['source_database_verified']=False;checkpoint('before_primary_volume_change')
            client.patch('Deployment','shared-db',[{'op':'test','path':'/metadata/uid','value':obj['metadata']['uid']},
                {'op':'test','path':'/spec/template/spec/volumes','value':volumes},
                {'op':'replace','path':'/spec/template/spec/volumes','value':[{'name':'database','persistentVolumeClaim':{'claimName':'shared-db-data'}}]}])
            wait(client,'shared-db');ready_mysql(client,'shared-db')
            mysql(client,'shared-db','MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot',dump)
            if stored_rows(client,'shared-db')!=expected:raise RuntimeError('primary persistent database differs from original rows')
            state['source_database_verified']=True;checkpoint('both_persistent_databases_verified')
        else:
            create_second(client,application,out)
            if run(client,['exec','deployment/private-db','--','redis-cli','PING']).decode().strip()!='PONG':
                raise RuntimeError('second Redis not responsive')
            checkpoint('second_redis_ready')
    except Exception as exc:
        error=exc;state.update(status='failed',error=type(exc).__name__,detail=str(exc));checkpoint('preparation_failed')
    finally:
        if state['source_database_verified']:
            for name,replicas in original.items():
                try:scale(client,name,replicas)
                except Exception as exc:state['restore_errors'].append({'name':name,'replicas':replicas,'error':type(exc).__name__})
            checkpoint('station_replica_restoration_attempted')
        else:
            state['recovery_required']='Keep Station stopped. Restore primary from verified private-db or saved SQL and validate rows before resuming.'
            checkpoint('clients_kept_stopped_to_protect_unverified_database')
    if error or state['restore_errors']:
        state['status']='failed';checkpoint('requires_recovery');raise error or RuntimeError('Station restoration incomplete')
    state['status']='complete';checkpoint('complete')
    (out/'summary.json').write_text(json.dumps({'status':'complete','application':application,'scope':'study-owned databases only',
        'database_names':['shared-db','private-db'],'stored_rows_verified':application=='trainticket',
        'preserved_station_replicas':original,'completed_at':time.time()},indent=2)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--application',choices=['boutique','trainticket'],required=True)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--after',type=Path,required=True)
    args=p.parse_args();prepare(args.application,args.out,args.after)
