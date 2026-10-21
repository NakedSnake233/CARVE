#!/usr/bin/env python3
"""Wait for one already running native pilot to exit and verify its completion record."""
import argparse
import json
from pathlib import Path
import time

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--campaign',type=Path,required=True)
    p.add_argument('--pid',type=int,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--timeout',type=float,default=10800)
    a=p.parse_args();deadline=time.monotonic()+a.timeout
    while time.monotonic()<deadline:
        try:command=Path(f'/proc/{a.pid}/cmdline').read_bytes().split(b'\0')
        except FileNotFoundError:command=[]
        alive=str(a.campaign.resolve()).encode() in command
        summary=a.campaign/'summary.json'
        if not alive:
            if not summary.exists():raise RuntimeError('pilot exited without final summary')
            value=json.loads(summary.read_text())
            if value.get('status')!='complete' or value.get('planned')!=30 or value.get('attempted')!=30:
                raise RuntimeError('pilot stopped before completing all planned attempts')
            with a.out.open('x') as stream:json.dump({'status':'complete','campaign':str(a.campaign),'summary':value},stream,indent=2)
            break
        time.sleep(5)
    else:raise TimeoutError('pilot is still running after the bounded wait')
