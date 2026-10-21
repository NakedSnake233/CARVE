#!/usr/bin/env python3
"""Describe the fixed diagnostic cap without changing remediation labels."""
import argparse
from collections import Counter
import json
from pathlib import Path
import statistics
import time


def summarize(queue,out):
    queue=Path(queue);registration=json.loads((queue/'PREREGISTRATION.json').read_text());records=[]
    for assignment in registration['assignments']:
        folder=Path(assignment['out']);result=json.loads((folder/'result.json').read_text())
        if result['status']!='complete' or not result['cleanup_passed']:raise ValueError('diagnostic has unverified cleanup')
        if result['diagnostic']['id']!=assignment['id']:raise ValueError('trajectory assignment mismatch')
        raw=json.loads((folder/'continuous_traffic.json').read_text()) if (folder/'continuous_traffic.json').exists() else []
        origin=result.get('traffic_origin');streams={}
        for name in ('business','peer','background'):
            windows=[c[name]['window'] for c in raw if name in c and origin is not None
                     and origin-1e-6<=c[name]['window']['start_time']<origin+200-1e-6]
            identities=[w['window_id'] for w in windows]
            if len(set(identities))!=len(identities):raise ValueError('duplicate physical workload cohort')
            if result['measurement_status']=='observed' and len(windows)!=20:raise ValueError('observed trajectory does not retain all20cohorts')
            baseline=result.get('baseline_windows',{}).get(name)
            protocol=json.loads((folder/'protocol.json').read_text())['verification']
            ceiling=max(baseline['p95_ms']*protocol['latency_multiplier'],protocol['latency_floor_ms']) if baseline else None
            streams[name]={'windows':windows,'cohorts':len(windows),'baseline':baseline,'latency_ceiling_ms':ceiling,
                           'latency_only_breaches_after40s':sum(w['start_time']>=origin+40-1e-6 and w['p95_ms']>ceiling for w in windows) if ceiling is not None else None}
        cpu={}
        for db,before in result.get('cpu_before',{}).items():
            after=result.get('cpu_after',{}).get(db,{})
            same=before.get('pod_uid') and before.get('pod_uid')==after.get('pod_uid')
            if same and before.get('values') and after.get('values'):
                delta={key:after['values'][key]-value for key,value in before['values'].items() if key in after['values']}
                if any(v<0 for v in delta.values()):delta=None
            else:delta=None
            cpu[db]={'same_pod':bool(same),'counter_delta':delta,
                     'scope':'Cumulative beforetraffic through drainedtraffic, including workload warmup; not isolated causal probe cost.'}
        records.append({'id':assignment['id'],'replica':assignment['replica'],'condition':assignment['condition'],
                        'mode':assignment['mode'],'measurement_status':result['measurement_status'],'cleanup_passed':True,
                        'streams':streams,'cpu':cpu,'final_threshold_checks':result.get('final_threshold_checks'),
                        'query_budget':result.get('total_probe_budget'),'pods':result.get('pods_after_setup'),
                        'raw_result':str(folder/'result.json')})
    if len(records)!=8:raise ValueError('fixed diagnostic registration must contain eight trajectories')
    report={'schema':'native_stability_diagnostic_summary_v1','checked_at':time.time(),'status':'complete_scoped',
            'trajectories':len(records),'measurement_statuses':dict(Counter(r['measurement_status'] for r in records)),
            'records':records,'original16_labels_unchanged':True,'C1_supported':False,'C2_supported':False,
            'scope':'One host/two native dependency-slice replicas. Fixed no-fault protocol validity diagnostic, not repair efficacy or population safety validation.',
            'interpretation_rule':'Review actual absolute trajectories and registered failure rates. Do not tune thresholds, extend this cap, or relabel the closed16.'}
    with Path(out).open('x') as stream:json.dump(report,stream,indent=2,allow_nan=False);stream.write('\n')
    print(json.dumps({k:v for k,v in report.items() if k!='records'}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--queue',required=True,type=Path);p.add_argument('--out',required=True,type=Path)
    a=p.parse_args();summarize(a.queue,a.out)
