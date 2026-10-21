#!/usr/bin/env python3
"""Record host resource headroom while a local native experiment queue runs."""
import argparse
import json
import os
from pathlib import Path
import time

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--queue',type=Path,required=True)
    a=p.parse_args();minimum=None;maximum=0;count=0
    with (a.queue/'resource_usage.jsonl').open('x') as log:
        while True:
            mem={l.split(':')[0]:int(l.split()[1]) for l in Path('/proc/meminfo').read_text().splitlines()}
            available=mem['MemAvailable']/1024**2;load=os.getloadavg()[0]
            minimum=available if minimum is None else min(minimum,available);maximum=max(maximum,load);count+=1
            state_path=a.queue/'queue_state.json';state=json.loads(state_path.read_text()) if state_path.exists() else {}
            running=sum(j['status'] in ('running','launching') for j in state.get('jobs',{}).values())
            row={'at':time.time(),'available_memory_gib':available,'load_1m':load,'active_jobs':running}
            log.write(json.dumps(row)+'\n');log.flush()
            (a.queue/'resource_summary.json').write_text(json.dumps({'minimum_available_memory_gib':minimum,
                'maximum_load_1m':maximum,'samples':count,'last':row},indent=2)+'\n')
            if state.get('status') in ('complete','stopped'):break
            time.sleep(5)
