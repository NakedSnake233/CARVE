#!/usr/bin/env python3
"""CPU/native-study adapter for experiment-queue: durable receipts and dependency gates.

The bundled SSH scheduler requires screen/GPU telemetry. These jobs use the
validated local CPU environment and exclusive Kubernetes namespace resources.
Non-idempotent collection is never retried automatically. Failed dependencies
block their consumers. Every completion requires exit 0 and declared outputs.
"""
from __future__ import annotations
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import math


def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    with tmp.open('w') as stream:
        json.dump(value,stream,indent=2,allow_nan=False);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    tmp.replace(path)


def process_start(pid):
    try:return Path(f'/proc/{pid}/stat').read_text().split(') ',1)[1].split()[19]
    except FileNotFoundError:return None


def outputs_valid(job):
    for declaration in job.get('outputs',[]):
        path=Path(declaration['path'])
        if not path.exists():return False,'missing_output:'+str(path)
        if 'json_equals' in declaration:
            value=json.loads(path.read_text())
            if any(value.get(k)!=v for k,v in declaration['json_equals'].items()):return False,'invalid_output_status:'+str(path)
    return True,None


def worker(manifest,directory,identity):
    config=json.loads(Path(manifest).read_text());job=next(j for j in config['jobs'] if j['id']==identity)
    folder=Path(directory)/'jobs'/identity;folder.mkdir(parents=True,exist_ok=True)
    lock=(folder/'worker.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if (folder/'receipt.json').exists() or (folder/'started.json').exists():
        raise RuntimeError('refuse duplicate or unreviewed restart of experiment job')
    save(folder/'started.json',{'pid':os.getpid(),'start_ticks':process_start(os.getpid()),'started_at':time.time(),'command':job['command']})
    started=time.time()
    with (folder/'stdout.log').open('x') as stream:
        process=subprocess.Popen(job['command'],cwd=job.get('cwd',config['cwd']),stdout=stream,stderr=subprocess.STDOUT,
                                 env={**os.environ,'OMP_NUM_THREADS':'2','MKL_NUM_THREADS':'2'})
        code=process.wait()
    try:valid,reason=outputs_valid(job)
    except Exception as error:valid,reason=False,'output_check:'+str(error)
    save(folder/'receipt.json',{'exit_code':code,'outputs_valid':valid,'reason':reason,'started_at':started,
        'completed_at':time.time(),'status':'completed' if code==0 and valid else 'stuck'})


def run(manifest,directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    lock=(directory/'scheduler.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    frozen=directory/'manifest.json'
    incoming=json.loads(Path(manifest).read_text())
    limit=incoming.get('max_dispatch_seconds')
    if limit is not None and (type(limit) not in (int,float) or not math.isfinite(limit) or limit<=0):raise ValueError('finite positive dispatch envelope required')
    if frozen.exists() and json.loads(frozen.read_text())!=incoming:raise ValueError('existing queue manifest differs; create a new amended queue')
    if not frozen.exists():save(frozen,incoming)
    jobs=incoming['jobs'];ids={j['id'] for j in jobs}
    if len(ids)!=len(jobs) or any(set(j.get('depends_on',[]))-ids for j in jobs):raise ValueError('invalid dependency manifest')
    state_path=directory/'queue_state.json'
    state=json.loads(state_path.read_text()) if state_path.exists() else {'schema':'local_experiment_queue_v1','started_at':time.time(),
        'complete':False,'jobs':{j['id']:{'status':'pending'} for j in jobs}}
    save(state_path,state)
    children=[]
    while True:
        for identity,row in state['jobs'].items():
            folder=directory/'jobs'/identity;receipt=folder/'receipt.json';started=folder/'started.json'
            if row['status'] in ('launching','running'):
                if receipt.exists():row.update(json.loads(receipt.read_text()))
                elif started.exists():
                    info=json.loads(started.read_text());row.update(info)
                    if process_start(info['pid'])!=info['start_ticks']:row.update(status='stuck',reason='worker_lost_without_completion_receipt')
                    else:row['status']='running'
                elif time.time()-row['launch_at']>15:row.update(status='stuck',reason='worker_never_registered')
        for child in children:child.poll()
        active=[j for j in jobs if state['jobs'][j['id']]['status'] in ('launching','running')]
        resources={r for j in active for r in j.get('resources',[])}
        quarantined={resource:j['id'] for j in jobs if state['jobs'][j['id']]['status']=='stuck' for resource in j.get('resources',[])}
        state['quarantined_resources']=quarantined
        limit=incoming.get('max_dispatch_seconds')
        expired=limit is not None and time.time()-state['started_at']>=limit
        if limit is not None:state['dispatch_budget']={'limit_seconds':limit,'expired':expired,'active_jobs_drain_safely':True}
        for job in jobs:
            row=state['jobs'][job['id']]
            if row['status']!='pending':continue
            if expired and job.get('kind')!='gate':
                row.update(status='blocked_budget',reason='dispatch time envelope reached; active workers drain without interruption');continue
            dependencies=[state['jobs'][d]['status'] for d in job.get('depends_on',[])]
            if any(s in ('stuck','blocked_dependency','blocked_resource','blocked_budget') for s in dependencies):
                row.update(status='blocked_dependency',reason='required predecessor failed');continue
            if set(job.get('resources',[])) & set(quarantined):
                row.update(status='blocked_resource',reason='resource requires explicit recovery after failed job');continue
            if any(s!='completed' for s in dependencies):continue
            if len(active)>=incoming.get('max_parallel',2) or resources.intersection(job.get('resources',[])):continue
            missing=[p for p in job.get('requires',[]) if not Path(p).exists()]
            if missing:
                row.update(status='stuck',reason='missing_preconditions',missing=missing)
                quarantined.update({resource:job['id'] for resource in job.get('resources',[])})
                continue
            row.update(status='launching',launch_at=time.time());save(state_path,state)
            folder=directory/'jobs'/job['id'];folder.mkdir(parents=True,exist_ok=True)
            with (folder/'worker.log').open('a') as stream:
                child=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--manifest',str(frozen.resolve()),
                    '--directory',str(directory.resolve()),'--worker',job['id']],stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
            children.append(child);row['launcher_pid']=child.pid;active.append(job);resources.update(job.get('resources',[]))
        # Forward references to gates may become blocked later in this pass.
        # Propagate terminal failures before deciding that an idle queue is a cycle.
        changed=True
        while changed:
            changed=False
            for job in jobs:
                row=state['jobs'][job['id']]
                if row['status']!='pending':continue
                if any(state['jobs'][d]['status'] in ('stuck','blocked_dependency','blocked_resource','blocked_budget') for d in job.get('depends_on',[])):
                    row.update(status='blocked_dependency',reason='required predecessor failed');changed=True
                elif set(job.get('resources',[])) & set(quarantined):
                    row.update(status='blocked_resource',reason='resource requires explicit recovery after failed job');changed=True
        state['updated_at']=time.time()
        state['complete']=all(r['status']=='completed' for r in state['jobs'].values())
        terminal=all(r['status'] in ('completed','stuck','blocked_dependency','blocked_resource','blocked_budget') for r in state['jobs'].values())
        state['status']='complete' if state['complete'] else 'stopped' if terminal else 'running'
        save(state_path,state)
        if terminal:return 0 if state['complete'] else 2
        # A cycle would otherwise stay pending forever.
        if not any(r['status'] in ('running','launching') for r in state['jobs'].values()):
            raise RuntimeError('no runnable jobs: dependency cycle or invalid resource manifest')
        time.sleep(2)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--manifest',required=True,type=Path)
    parser.add_argument('--directory',required=True,type=Path);parser.add_argument('--worker')
    args=parser.parse_args()
    if args.worker:worker(args.manifest,args.directory,args.worker)
    else:raise SystemExit(run(args.manifest,args.directory))
