#!/usr/bin/env python3
"""Measure native CPU/load/DB response ranges before freezing formal test groups."""
from __future__ import annotations
import argparse
import copy
import json
import math
from pathlib import Path
import sys
import time
import traceback
from dataclasses import asdict
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.native import NativeBackend, cpu_cores
from methods.E2R.intervention.campaign import KubernetesBackend, append_json, collector, http_request
from methods.E2R.intervention.traffic import ConcurrentTraffic


def cpu_template(template,factor):
    result=copy.deepcopy(template)
    for container in result['spec']['containers']:
        limits=container['resources']['limits'];cores=max(.01,cpu_cores(limits['cpu'])*factor)
        millicores=round(cores*1000)
        limits['cpu']=str(millicores//1000) if millicores%1000==0 else str(millicores)+'m'
        request=min(cpu_cores(container['resources']['requests']['cpu']),cores)
        request_millicores=round(request*1000)
        container['resources']['requests']['cpu']=str(request_millicores//1000) if request_millicores%1000==0 else str(request_millicores)+'m'
    return result


def db_binding(template,application,name):
    result=copy.deepcopy(template);key='REDIS_ADDR' if application=='boutique' else 'STATION_MYSQL_HOST'
    for container in result['spec']['containers']:
        for value in container.get('env',[]):
            if value['name']==key:value['value']=name+(':6379' if application=='boutique' else '')
    return result


def configure(config):
    config=copy.deepcopy(config)
    config['inventory']['entities'].append({'id':'db_private','role':'shared_dependency','kind':'Deployment',
        'namespace':config['experimental_namespace'],'name':'private-db','binding':'independent-storage'})
    config['inventory']['relations']=[r for r in config['inventory']['relations'] if r['kind']!='competes_for']
    config['discover_dependencies']=True
    config['policy']['allowed_targets']=['entry','old','new','peer','db','db_private']
    config['convergence_timeout']=600
    for workload in config['workloads'].values():workload['workers']=128
    return config


def run(config,out,storage,loads,windows,custom_points=None,warmup_windows=1):
    config=configure(config)
    if custom_points and any(p.get('background') for p in custom_points):
        config['inventory']['entities'].append({'id':'background','role':'factor','binding':'registered-cache-background'})
        config['inventory']['relations'].append({'source':'background','target':'db','kind':'competes_for',
            'observed_at':time.time(),'ttl':1209600,'origin':'configuration'})
    if json.loads(storage.read_text()).get('status')!='complete':raise RuntimeError('formal private storage is not prepared')
    if json.loads(storage.read_text()).get('application')!=config['native_application']:raise ValueError('storage receipt application mismatch')
    out.mkdir(parents=True,exist_ok=False)
    backend=NativeBackend(config,out)
    original={e['id']:backend.client.get('Deployment',e['name'])['spec']['template'] for e in config['inventory']['entities'] if e.get('kind')=='Deployment'}
    (out/'base_templates.json').write_text(json.dumps(original,indent=2)+'\n')
    points=[]
    for load in loads:
        points.append({'id':f'baseline-L{load}','kind':'baseline','load':load,'peer':load,'factor':1,'route':'new','changes':[]})
        for kind,factors in [('old_cpu',[.5,.25]),('db_cpu',[.25,.125])]:
            for factor in factors:
                entity='old' if kind=='old_cpu' else 'db'
                points.append({'id':f'{kind}-L{load}-f{factor}','kind':kind,'load':load,'peer':load,'factor':factor,
                    'route':'old' if kind=='old_cpu' else 'new',
                    'changes':[{'entity':entity,'path':'/spec/template','value':cpu_template(original[entity],factor)}]})
    load=loads[-1]
    for factor in (.25,.125):
        for binding in ('shared-db','private-db'):
            points.append({'id':f'topology-L{load}-f{factor}-{binding}','kind':'topology','load':load,'peer':load*2,'factor':factor,'binding':binding,'route':'old',
                'changes':[{'entity':'db','path':'/spec/template','value':cpu_template(original['db'],factor)},
                           {'entity':'old','path':'/spec/template','value':db_binding(original['old'],config['native_application'],binding)},
                           {'entity':'new','path':'/spec/template','value':db_binding(original['new'],config['native_application'],'private-db')}]})
    if custom_points is not None:
        points=copy.deepcopy(custom_points)
    (out/'profile_protocol.json').write_text(json.dumps({'source':'live','purpose':'exploratory_native_load_profile_not_formal_test',
        'config':config,'points':points,'windows':windows,'loads':loads,'warmup_windows':warmup_windows},indent=2)+'\n')
    results=[]
    prefix='cart' if config['native_application']=='boutique' else 'station'
    for index,point in enumerate(points):
        directory=out/point['id'];directory.mkdir();backend.current_dir=directory
        backend.setup_mutations=[];backend.executors=[];backend.baselines={};backend.provider.references={};backend.provider.raw_probes=[]
        row={'point':point,'source':'live','started_at':time.time(),'status':'started'}
        print(f'[{index+1}/{len(points)}] {point["id"]} preparing',flush=True)
        try:
            backend.reset_application_state(directory,'before')
            specs=copy.deepcopy(config['workloads'])
            if point.get('background'):
                if config['native_application']=='boutique':
                    expected=backend.client.get('Service','shared-db')['spec']['clusterIP']
                    if point['background']['url']!='redis://'+expected+':6379':raise ValueError('background must target the owned shared Redis Service')
                specs['background']=point['background']
            backend.workloads={name:collector({**spec,'connection_pool_size':point.get('peer_connection_pool_size',0) if name=='peer' else 0})
                               for name,spec in specs.items()}
            changes=point['changes']+[{'entity':'entry','path':'/spec/selector','value':{'app':prefix+'-'+point['route']}}]
            backend._setup(changes)
            if not backend.wait_controllers():raise TimeoutError('profile controllers did not settle')
            backend.offered_rps=point['load']
            backend.workloads['business'].offered_rps=point['load'];backend.workloads['peer'].offered_rps=point['peer']
            for workload in backend.workloads.values():
                deadline=3 if workload.spec.get('protocol') in ('native_cart_grpc','redis_kv') else 9
                workload.workers=max(128,math.ceil(deadline*workload.offered_rps)+8)
            row['client_workers']={name:workload.workers for name,workload in backend.workloads.items()}
            backend.traffic=ConcurrentTraffic(backend.workloads).start()
            began=time.time();print(f'[{index+1}/{len(points)}] {point["id"]} measuring',flush=True)
            cycles=backend.traffic.take(backend.traffic.origin_wall+10*warmup_windows,windows,10*(windows+warmup_windows)+60)
            row['windows']=[{n:asdict(w) for n,(w,raw) in cycle.items()} for cycle in cycles]
            graph=backend.provider.collect()
            for identity in (point['route']+'_capacity','shared_dependency'):
                probe=next(p for p in backend.provider.probes if p.id==identity)
                try:row[identity]={'status':'observed','observation':asdict(backend.provider.probe(probe,graph))}
                except Exception as exc:row[identity]={'status':'unknown','error':type(exc).__name__}
            row.update(status='measured',measurement_seconds=time.time()-began)
        except Exception as exc:
            row.update(status='invalid',error=type(exc).__name__,detail=str(exc)[:300])
            (directory/'traceback.txt').write_text(traceback.format_exc())
        finally:
            # Stop all load before restoring configuration, then verify original native transactions.
            try:KubernetesBackend.close_traffic(backend)
            except Exception as exc:row['traffic_stop_error']=type(exc).__name__
            try:
                row['restore']=backend.restore()
                if point.get('quiesce_cart_after') and row['restore']['status']=='restored':
                    if config['native_application']!='boutique':raise ValueError('Cart quiescence is application-specific')
                    backend.setup_mutations=[]
                    backend._setup([{'entity':role,'path':'/spec/replicas','value':0} for role in ('old','new','peer')])
                    if not backend.wait_controllers():raise TimeoutError('Cart workers did not quiesce')
                    backend.reset_application_state(directory,'quiescent')
                    row['cart_quiescence_restore']=backend.restore()
                    if row['cart_quiescence_restore']['status']!='restored':row['restore']['status']='failed'
                backend.reset_application_state(directory,'after')
                row['restore_canaries']={name:http_request(spec['canary'])[0] for name,spec in config['workloads'].items()}
                if not all(row['restore_canaries'].values()):row['restore']['status']='failed'
            except Exception as exc:row['restore']={'status':'failed','error':type(exc).__name__}
            (directory/'native_probe_measurements.json').write_text(json.dumps(backend.provider.raw_probes,indent=2,allow_nan=False)+'\n')
            row['completed_at']=time.time();(directory/'result.json').write_text(json.dumps(row,indent=2)+'\n');append_json(out/'profiles.jsonl',row)
            results.append(row)
        print(f'[{index+1}/{len(points)}] {point["id"]} {row["status"]} restore={row["restore"]["status"]}',flush=True)
        if row['restore']['status']!='restored' or 'traffic_stop_error' in row:break
    summary={'status':'complete' if len(results)==len(points) and all(r['restore']['status']=='restored' and r['status']=='measured'
             and 'traffic_stop_error' not in r for r in results) else 'stopped',
        'planned':len(points),'attempted':len(results),'measured':sum(r['status']=='measured' for r in results),
        'purpose':'exploration only; not training/test intervention episodes','application':config['native_application']}
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--storage-summary',type=Path,required=True);p.add_argument('--loads',type=float,nargs='+',default=[2,4,8]);p.add_argument('--windows',type=int,default=6)
    p.add_argument('--points',type=Path);p.add_argument('--warmup-windows',type=int,default=1)
    a=p.parse_args();result=run(json.loads(a.config.read_text()),a.out,a.storage_summary,a.loads,a.windows,
                              json.loads(a.points.read_text()) if a.points else None,a.warmup_windows)
    print(json.dumps(result));raise SystemExit(0 if result['status']=='complete' else 2)
