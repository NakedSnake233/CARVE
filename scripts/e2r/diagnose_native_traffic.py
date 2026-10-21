#!/usr/bin/env python3
"""Measure a bounded healthy workload and retain timing/GC diagnostics; no labels."""
import argparse
import fcntl
import json
from pathlib import Path
import sys
import time
import traceback
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.native import NativeBackend

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--windows',type=int,default=24)
    p.add_argument('--cyclic-gc',action='store_true')
    a=p.parse_args();c=json.loads(a.config.read_text());a.out.mkdir(parents=True,exist_ok=False)
    c['traffic_gc_enabled']=a.cyclic_gc
    lockpath=Path(c['resource_lock_directory'])/(c['experimental_namespace']+'.lock')
    lock=lockpath.open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    backend=NativeBackend(c,a.out);backend.current_dir=a.out
    backend.offered_rps=c['cases'][0]['offered_rps']
    backend.workloads['business'].offered_rps=backend.offered_rps
    backend.workloads['peer'].offered_rps=c['cases'][0]['peer_rps']
    result={'source':'live_diagnostic_only','status':'running','namespace':c['experimental_namespace']}
    try:
        backend.reset_application_state(a.out,'before')
        backend.start_traffic();after=backend.traffic.origin_wall
        for index in range(a.windows):
            cycle=backend.traffic.take(after,1,30)[0]
            after=max(w.end_time for w,_ in cycle.values())
            d=backend.traffic.diagnostics()
            (a.out/'latest_diagnostics.json').write_text(json.dumps(d,indent=2)+'\n')
            print(json.dumps({'window':index,'at':time.time(),'gc_max_seconds':max([x['duration_seconds'] for x in d['gc_pauses']],default=0)}),flush=True)
        result['status']='complete'
    except Exception as exc:
        result.update(status='failed',error=type(exc).__name__,detail=str(exc))
        (a.out/'traceback.txt').write_text(traceback.format_exc())
    finally:
        backend.close_traffic()
        (a.out/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
