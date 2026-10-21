#!/usr/bin/env python3
"""Freeze formal latency tolerances from healthy pilot baselines alone, before formal data."""
import argparse
import json
import math
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.pilot_completion import verified_pilot


def freeze(campaign,out):
    summary=json.loads((campaign/'summary.json').read_text())
    config=json.loads((campaign/'protocol.json').read_text())
    config,selected,attempts=verified_pilot(campaign/'summary.json',config['native_application'])
    baselines=[]
    for row in selected:
        folder=Path(row['artifact_directory'])
        for source in ('business','peer'):
            record=json.loads((folder/f'baseline_{source}.json').read_text())
            window=record['window']
            if window['error_rate']!=0 or window['state_canary_ok'] is not True:raise ValueError('unhealthy calibration baseline')
            baselines.append({'episode':row['episode_id'],'source':source,'p95_ms':window['p95_ms']})
    latencies=sorted(r['p95_ms'] for r in baselines)
    q95=latencies[math.ceil(.95*len(latencies))-1]
    protocol={**config['verification'],'latency_floor_ms':2*q95}
    with out.open('x') as stream:
        json.dump({'schema':'native_formal_verification_freeze_v1','application':config['native_application'],
            'protocol':protocol,'source':'healthy pre-fault pilot baseline windows only',
            'source_campaign':str(campaign),'healthy_baselines':baselines,'baseline_p95_q95_ms':q95,
            'all_pilot_attempts':len(attempts),'completed_schedule_references':len(selected),
            'failed_attempts_retained_but_not_used_as_healthy_references':sum(r['status']=='failed' for r in attempts),
            'rule':'formal p95 ceiling = max(current healthy baseline p95 * 1.5, 2 * pilot healthy p95 q95)',
            'scope':'fixed native dependency-slice experimental SLO; not a production SLA',
            'excluded_from_calibration':['fault windows','post-action outcomes','formal validation/test outcomes'],
            'pilot_relabelled':False},stream,indent=2);stream.write('\n')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--campaign',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();freeze(a.campaign,a.out)
