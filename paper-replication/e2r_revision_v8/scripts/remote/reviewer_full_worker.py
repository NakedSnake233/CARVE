#!/usr/bin/env python3
"""Idle-gated M1 matched-training queue for reviewer-requested controls."""
from pathlib import Path
import argparse,fcntl,json,os,signal,subprocess,sys,time

ROOT=Path(__file__).resolve().parents[2];STATE=ROOT/'experiments/e2r/remote/reviewer_full'
CHILD=None


def probe(gpu):
    out=subprocess.check_output(['nvidia-smi','-i',str(gpu),'--query-gpu=name,memory.used,memory.total,utilization.gpu','--format=csv,noheader,nounits'],text=True,timeout=15).strip()
    name,used,total,util=[x.strip() for x in out.split(',')]
    return {'gpu':name,'memory_used_mib':int(used),'memory_total_mib':int(total),'utilization_percent':int(util),
            'idle':int(used)<500 and int(util)<5}


def record(status,**fields):
    value={'status':status,'pid':os.getpid(),'time_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),**fields}
    temp=STATE/'worker_status.json.tmp';temp.write_text(json.dumps(value,indent=2)+'\n');temp.replace(STATE/'worker_status.json');print(json.dumps(value),flush=True)


def stop(signum,frame):
    global CHILD
    if CHILD is not None and CHILD.poll() is None:os.killpg(CHILD.pid,signal.SIGTERM);CHILD.wait()
    record('STOPPED',signal=signum);raise SystemExit(128+signum)


def wait_idle(gpu,stage,interval):
    consecutive=0
    while consecutive<2:
        sample=probe(gpu);consecutive=consecutive+1 if sample['idle'] else 0
        record('WAIT_GPU_IDLE',next_stage=stage,consecutive_idle_samples=consecutive,**sample)
        if consecutive<2:time.sleep(interval)


def run_stage(name,command,env):
    global CHILD
    with (STATE/f'{name}.log').open('w') as log:
        CHILD=subprocess.Popen(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        record('RUNNING',stage=name,child_pid=CHILD.pid,command=command);code=CHILD.wait()
    CHILD=None
    if code:raise RuntimeError(f'{name} exited {code}')
    record('STAGE_DONE',stage=name)


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--run',action='store_true');ap.add_argument('--check',action='store_true')
    ap.add_argument('--gpu',type=int,default=0);ap.add_argument('--poll-seconds',type=float,default=30);args=ap.parse_args()
    if args.check:print(json.dumps({'status':'QUEUE_ELIGIBILITY_ONLY',**probe(args.gpu)},indent=2));return
    if not args.run:ap.error('choose --check or --run')
    STATE.mkdir(parents=True,exist_ok=True)
    with (STATE/'worker.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SystemExit('Another reviewer-full worker holds the lock')
        evidence=[STATE/'worker_status.json',*(STATE/f'{x}.log' for x in ('smoke_reference','smoke_runtime','smoke_node_only','full_reference','full_runtime','full_node_only')),
                  *(ROOT/'experiments/e2r/runs'/f'gpu_{x}_v1' for x in ('smoke_reference','smoke_runtime','smoke_node_only','train_reference','train_runtime','train_node_only'))]
        if any(x.exists() for x in evidence):raise SystemExit('Existing reviewer-full evidence found; nothing overwritten')
        (STATE/'worker.pid').write_text(str(os.getpid())+'\n');signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
        env={**os.environ,'CUDA_VISIBLE_DEVICES':str(args.gpu),'OMP_NUM_THREADS':'2','MKL_NUM_THREADS':'2','OPENBLAS_NUM_THREADS':'2','PYTHONDONTWRITEBYTECODE':'1'}
        python=sys.executable;manifest=str(ROOT/'experiments/e2r/runs/full_data_v2/manifest.json');dyrca=str(ROOT.parent/'dyrca')
        stages=[]
        for variant in ('reference','runtime','node_only'):
            stages.append((f'smoke_{variant}',[python,'scripts/e2r/run_graph_experiment.py','--manifest',manifest,'--dyrca-root',dyrca,'--device','cuda:0','--threads','2','--training-variant',variant,'--limit','40','--epochs','2','--seed','42','--out-dir',str(ROOT/f'experiments/e2r/runs/gpu_smoke_{variant}_v1')]))
        for variant in ('reference','runtime','node_only'):
            stages.append((f'full_{variant}',[python,'scripts/e2r/run_graph_experiment.py','--manifest',manifest,'--dyrca-root',dyrca,'--device','cuda:0','--threads','2','--training-variant',variant,'--seeds','42','43','44','--out-dir',str(ROOT/f'experiments/e2r/runs/gpu_train_{variant}_v1')]))
        try:
            for name,command in stages:wait_idle(args.gpu,name,args.poll_seconds);run_stage(name,command,env)
            record('COMPLETED',results=[str(ROOT/f'experiments/e2r/runs/gpu_train_{v}_v1/results.json') for v in ('reference','runtime','node_only')])
        except Exception as exc:record('FAILED',error=str(exc));raise


if __name__=='__main__':main()
