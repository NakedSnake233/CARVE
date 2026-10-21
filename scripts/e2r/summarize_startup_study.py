#!/usr/bin/env python3
"""Report native startup diagnostic and aligned CPU throttling without relabeling."""
import argparse,json,sys,time,statistics
from collections import defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.data import validate_record
from scripts.e2r.pack_revision_native import pack

def read(p):return json.loads(Path(p).read_text())
def save(p,v):Path(p).write_text(json.dumps(v,indent=2,allow_nan=False)+'\n')
def cpu_window(samples,start,end,role):
    pods=defaultdict(list)
    for sample in samples:
        if sample.get('role')==role and start<=sample['observed_at']<=end:pods[sample['container_id']].append(sample)
    totals=defaultdict(float);covered=0.
    for values in pods.values():
        values=sorted(values,key=lambda x:x['observed_at'])
        if len(values)<2:continue
        first,last=values[0],values[-1];covered+=last['observed_at']-first['observed_at']
        for k in ('usage_usec','nr_periods','nr_throttled','throttled_usec'):totals[k]+=last['cpu'][k]-first['cpu'][k]
    return {**totals,'covered_container_seconds':covered,'throttled_period_fraction':totals['nr_throttled']/totals['nr_periods'] if totals['nr_periods'] else None,
        'cpu_cores':totals['usage_usec']/1e6/covered if covered else None,'scope':'Within-window per-container counter deltas; missing edge samples excluded; throttled_usec is cumulative scheduler accounting, not request latency.'}

def run(q,stage):
    q=Path(q);plan=read(q/'PREREGISTRATION.json');reports=[];selected=[a for a in plan['assignments'] if stage=='final' or a['id'] in plan['included_first_jobs']]
    for a in selected:
        folder=Path(a['out']);summary=read(folder/'summary.json');assert summary['status']=='complete' and summary['campaign_baseline_restore']['status']=='restored' and all(summary['campaign_baseline_restore']['native_canaries'].values())
        ledger=[json.loads(l) for l in (folder/'episodes.jsonl').read_text().splitlines()];assert len(ledger)==1
        r=ledger[0];validate_record(r);assert r['status']=='observed' and r['restore']['status']=='restored'
        episode=folder/r['episode_id'];diag=read(episode/'STARTUP_DIAGNOSTICS.json');assert diag['status']=='complete'
        receipt=read(episode/'acknowledged_state_before_fault.json');assert receipt['completed_at']<r['outcome']['baseline_windows']['business']['start_time']
        samples=[json.loads(l) for l in (episode/'startup_cgroup_samples.jsonl').read_text().splitlines() if l.strip()]
        assert not any('sampling_error' in s for s in samples)
        o=r['outcome'];windows=o['verification']['business']['raw_windows'];aligned=[cpu_window(samples,w['start_time'],w['end_time'],'cart-new') for w in windows]
        assert all(a['covered_container_seconds']>=5 for a in aligned)
        reports.append({'id':a['id'],'variant':a['variant'],'repeat':a['repeat'],'effects':o['effects'],'post_p95_ms':[w['p95_ms'] for w in windows],
            'post_error_rates':[w['error_rate'] for w in windows],'post_state_canaries':[w['state_canary_ok'] for w in windows],
            'baseline_p95_ms':o['baseline_windows']['business']['p95_ms'],'latency_threshold_ms':max(o['baseline_windows']['business']['p95_ms']*o['verification_protocol']['latency_multiplier'],o['verification_protocol']['latency_floor_ms']),
            'cpu_post_windows':aligned,'execution_seconds':r['execution']['timings_seconds'],'probe_budget':{k:r['total_probe_budget'][k] for k in ('calls','cost','actual_runtime_exceeded')},'episode_directory':str(episode)})
    if stage=='sanity':save(q/'SANITY_GATE.json',{'status':'passed','attempts':len(reports),'scope':'Observation/restore/telemetry only; negative business results allowed.'});return
    pack(q,[],q/'data',plan.get('cohort',q.name))
    cells=[]
    for variant in plan.get('variants',['default_jit','optimized_jit']):
        rows=[r for r in reports if r['variant']==variant];cells.append({'variant':variant,'n':len(rows),'business_recovered':sum(r['effects']['business_recovered'] is True for r in rows),
            'non_target_safe':sum(r['effects']['non_target_safe'] is True for r in rows),'median_post_p95_ms':[statistics.median(r['post_p95_ms'][i] for r in rows) for i in range(3)]})
    save(q/'RESULTS.json',{'status':'complete','created_at':time.time(),'cells':cells,'attempts':reports,'scope':plan['scope']})
    print(json.dumps(cells))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--queue',required=True);p.add_argument('--stage',choices=['sanity','final'],required=True);a=p.parse_args();run(a.queue,a.stage)
