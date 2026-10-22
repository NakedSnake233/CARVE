#!/usr/bin/env python3
"""Release eight-lane collection only after real native sanity and resource gates."""
import json
import argparse
from pathlib import Path
import subprocess
import sys
import time
import traceback

ROOT=Path(__file__).resolve().parents[2]
PYTHON=ROOT/'.venv-e2r/bin/python'
STAGE=ROOT/'enhancement/execution-stage/parallel8_20260930'
TWO=ROOT/'enhancement/queues/parallel2_sanity_v1'
EIGHT=ROOT/'enhancement/queues/parallel8_sanity_v1'
FORMAL=ROOT/'enhancement/queues/parallel8_formal_v1'


def record(phase, **details):
    value={'phase':phase,'at':time.time(),**details}
    (STAGE/'pipeline_state.json').write_text(json.dumps(value,indent=2)+'\n')
    print(json.dumps(value),flush=True)


def wait_queue(directory):
    while True:
        state=json.loads((directory/'queue_state.json').read_text()) if (directory/'queue_state.json').exists() else {}
        if state.get('status')=='complete':return
        if state.get('status')=='stopped':raise RuntimeError('queue stopped: '+str(directory))
        time.sleep(5)


def verify_sanity(directory):
    manifest=json.loads((directory/'manifest.json').read_text());evidence=[]
    for job in manifest['jobs']:
        path=Path(job['outputs'][0]['path']).parent
        summary=json.loads((path/'summary.json').read_text())
        rows=[json.loads(s) for s in (path/'episodes.jsonl').read_text().splitlines()]
        if summary['status']!='complete' or len(rows)!=2 or any(r['status']=='failed' or r['restore']['status']!='restored' for r in rows):
            raise RuntimeError('sanity attempt/restoration gate failed: '+job['id'])
        executed=[r for r in rows if r['status']=='observed' and r['execution']['status']=='executed']
        if not executed:raise RuntimeError('sanity did not exercise a native action: '+job['id'])
        for f in path.glob('episode-*/traffic_sessions.jsonl'):
            for line in f.read_text().splitlines():
                d=json.loads(line)
                if d.get('error') or d.get('ipc_error') or d.get('drained') is not True:
                    raise RuntimeError('traffic validity/drain gate failed: '+str(f))
        evidence.append({'id':job['id'],'observed':len(executed),'statuses':[r['status'] for r in rows],
                         'executions':[r.get('execution',{}).get('status') for r in rows],
                         'attempt_seconds':[r['timings_seconds']['total_attempt'] for r in rows]})
    (directory/'SANITY_GATE.json').write_text(json.dumps({'status':'passed','jobs':evidence},indent=2)+'\n')


def available_memory():
    mem={l.split(':')[0]:int(l.split()[1]) for l in Path('/proc/meminfo').read_text().splitlines()}
    return mem['MemAvailable']/1024**2


def launch(directory):
    if available_memory()<8:raise RuntimeError('less than 8 GiB memory available before new wave')
    with (directory/'scheduler.log').open('x') as log:
        scheduler=subprocess.Popen([str(PYTHON),'-u',str(ROOT/'scripts/e2r/local_experiment_queue.py'),
            '--manifest',str(directory/'manifest.json'),'--directory',str(directory)],stdin=subprocess.DEVNULL,
            stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    with (directory/'monitor.log').open('x') as log:
        monitor=subprocess.Popen([str(PYTHON),str(ROOT/'scripts/e2r/monitor_native_resources.py'),
            '--queue',str(directory)],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    (directory/'launch.json').write_text(json.dumps({'scheduler_pid':scheduler.pid,'monitor_pid':monitor.pid,
        'launched_at':time.time()},indent=2)+'\n')
    return scheduler,monitor


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--two',type=Path,default=TWO);p.add_argument('--eight',type=Path,default=EIGHT)
    p.add_argument('--formal',type=Path,default=FORMAL);p.add_argument('--stage',type=Path,default=STAGE)
    p.add_argument('--runtime',type=Path,default=ROOT/'enhancement/runtime/formal_v7')
    args=p.parse_args();TWO=args.two.resolve();EIGHT=args.eight.resolve();FORMAL=args.formal.resolve()
    STAGE=args.stage.resolve();STAGE.mkdir(parents=True,exist_ok=True)
    try:
        record('waiting_two_lane_sanity')
        wait_queue(TWO);verify_sanity(TWO)
        record('running_eight_lane_sanity')
        scheduler,monitor=launch(EIGHT)
        wait_queue(EIGHT);scheduler.wait(timeout=10);monitor.wait(timeout=15)
        verify_sanity(EIGHT)
        resources=json.loads((EIGHT/'resource_summary.json').read_text())
        samples=[json.loads(s) for s in (EIGHT/'resource_usage.jsonl').read_text().splitlines()]
        if resources['minimum_available_memory_gib']<8 or not any(s['active_jobs']==8 for s in samples):
            raise RuntimeError('eight-way concurrent resource gate not established')
        record('building_formal_amendment',resources=resources)
        subprocess.run([str(PYTHON),str(ROOT/'scripts/e2r/build_parallel_native_resume.py'),'--out',str(FORMAL),
            '--runtime',str(args.runtime.resolve()),'--lanes','4','--mode','formal',
            '--sanity',str(EIGHT/'manifest.json')],check=True)
        scheduler,monitor=launch(FORMAL)
        record('formal_collection_running',queue=str(FORMAL),scheduler_pid=scheduler.pid,monitor_pid=monitor.pid,
               eight_lane_minimum_available_memory_gib=resources['minimum_available_memory_gib'])
        state_path=ROOT/'enhancement/execution-stage/STATE.json';state=json.loads(state_path.read_text())
        state.update(phase='formal_native_collection_running',formal_status='running_eight_independent_environments',updated_at=time.time())
        state['parallel_recovery'].update(sanity_queue=str(EIGHT),formal_queue=str(FORMAL),scheduler_pid=scheduler.pid,
            sanity_gate=str(EIGHT/'SANITY_GATE.json'),minimum_available_memory_gib=resources['minimum_available_memory_gib'])
        state_path.write_text(json.dumps(state,indent=2)+'\n')
    except Exception as exc:
        record('stopped_requires_inspection',error=type(exc).__name__,detail=str(exc))
        (STAGE/'pipeline_traceback.txt').write_text(traceback.format_exc())
        raise
