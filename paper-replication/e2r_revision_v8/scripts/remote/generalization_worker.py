#!/usr/bin/env python3
"""Idle-gated LOFO and cross-application E2R training queue."""
from pathlib import Path
import argparse,fcntl,json,os,signal,subprocess,sys,time

ROOT=Path(__file__).resolve().parents[2];STATE=ROOT/'experiments/e2r/remote/generalization';CHILD=None


def probe(gpu):
    out=subprocess.check_output(['nvidia-smi','-i',str(gpu),'--query-gpu=name,memory.used,memory.total,utilization.gpu','--format=csv,noheader,nounits'],text=True,timeout=15).strip()
    name,used,total,util=[x.strip() for x in out.split(',')]
    return {'gpu':name,'memory_used_mib':int(used),'memory_total_mib':int(total),'utilization_percent':int(util),'idle':int(used)<500 and int(util)<5}


def record(status,**fields):
    value={'status':status,'pid':os.getpid(),'time_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),**fields}
    tmp=STATE/'worker_status.json.tmp';tmp.write_text(json.dumps(value,indent=2)+'\n');tmp.replace(STATE/'worker_status.json');print(json.dumps(value),flush=True)


def stop(signum,frame):
    global CHILD
    if CHILD is not None and CHILD.poll() is None:os.killpg(CHILD.pid,signal.SIGTERM);CHILD.wait()
    record('STOPPED',signal=signum);raise SystemExit(128+signum)


def main():
    global CHILD
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--run',action='store_true');ap.add_argument('--check',action='store_true')
    ap.add_argument('--gpu',type=int,default=0);ap.add_argument('--poll-seconds',type=float,default=30);args=ap.parse_args()
    if args.check:print(json.dumps({'status':'QUEUE_ELIGIBILITY_ONLY',**probe(args.gpu)},indent=2));return
    if not args.run:ap.error('choose --run or --check')
    STATE.mkdir(parents=True,exist_ok=True)
    with (STATE/'worker.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SystemExit('Another generalization worker holds the lock')
        names=[f'lofo_{x}' for x in ('T1','T2','T3','T4')]+['boutique_to_trainticket','trainticket_to_boutique']
        evidence=[STATE/'worker_status.json',*(STATE/f'{name}.log' for name in names),
                  *(ROOT/f'experiments/e2r/runs/generalization_{name}_v1' for name in names)]
        if any(x.exists() for x in evidence):raise SystemExit('Existing generalization evidence found; nothing overwritten')
        (STATE/'worker.pid').write_text(str(os.getpid())+'\n');signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
        env={**os.environ,'CUDA_VISIBLE_DEVICES':str(args.gpu),'OMP_NUM_THREADS':'2','MKL_NUM_THREADS':'2','OPENBLAS_NUM_THREADS':'2','PYTHONDONTWRITEBYTECODE':'1'}
        python=sys.executable;manifest=str(ROOT/'experiments/e2r/runs/full_data_v2/manifest.json');dyrca=str(ROOT.parent/'dyrca');split_root=ROOT/'experiments/e2r/splits/generalization_v1'
        try:
            for name in names:
                consecutive=0
                while consecutive<2:
                    sample=probe(args.gpu);consecutive=consecutive+1 if sample['idle'] else 0
                    record('WAIT_GPU_IDLE',next_stage=name,consecutive_idle_samples=consecutive,**sample)
                    if consecutive<2:time.sleep(args.poll_seconds)
                command=[python,'scripts/e2r/run_graph_experiment.py','--manifest',manifest,'--split-file',str(split_root/f'{name}.json'),
                         '--dyrca-root',dyrca,'--device','cuda:0','--threads','2','--training-variant','patch','--seeds','42','43','44',
                         '--out-dir',str(ROOT/f'experiments/e2r/runs/generalization_{name}_v1')]
                with (STATE/f'{name}.log').open('w') as log:
                    CHILD=subprocess.Popen(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                    record('RUNNING',stage=name,child_pid=CHILD.pid,command=command);code=CHILD.wait();CHILD=None
                if code:raise RuntimeError(f'{name} exited {code}')
                record('STAGE_DONE',stage=name)
            record('COMPLETED',runs=names)
        except Exception as exc:record('FAILED',error=str(exc));raise


if __name__=='__main__':main()
