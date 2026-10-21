#!/usr/bin/env python3
"""Fixed no-fault trajectories to inspect background and observation stability."""
import argparse
import copy
from dataclasses import asdict
import fcntl
import json
from pathlib import Path
import subprocess
import sys
import time
import traceback
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.E2R.intervention.campaign import KubernetesBackend,append_json,http_request
from methods.E2R.intervention.native import NativeBackend
from methods.E2R.intervention.policy_campaign import AccountedNativeProvider
from methods.E2R.intervention.probe_budget import ProbeAccountant
from methods.E2R.intervention.runtime import ObservationBudget
from methods.E2R.intervention.schema import number
from methods.E2R.intervention.verification import business_windows,execution_overlap_windows
from rca_bench.recovery_oracle import WorkloadWindow
from methods.E2R.intervention.reference_validity import pool_requests,bidirectional_latency,all_arrival_windows

REFERENCE_PROTOCOL={'schema':'fixed_startup_raw_reference_validity_v1','startup_seconds':60,'reference_seconds':60,
                    'passive_holdout_seconds':60,'query_deadline_seconds':300,'end_seconds':340,
                    'bidirectional_reference_checks':True,'all_post_reference_windows':True}


def select_baseline(cycles,workloads,protocol):
    """Same availability test, bounded to the original first four cohorts."""
    stable=0;previous_end=None
    for index,cycle in enumerate(cycles[:4]):
        starts=[w.start_time for w,_ in cycle.values()];ends=[w.end_time for w,_ in cycle.values()]
        valid=set(cycle)==set(workloads)
        for name,(w,_) in cycle.items():
            measured=w.measurement_completed_at if w.measurement_completed_at is not None else w.end_time
            valid &= (type(w.samples) is int and w.samples>=protocol.min_samples and w.state_canary_ok is True
                and all(number(x) for x in (w.start_time,w.end_time,measured,w.p95_ms,w.throughput_rps,w.error_rate))
                and w.start_time<w.end_time<=measured<=time.time() and w.p95_ms>0 and w.throughput_rps>0
                and 0<=w.error_rate<=.01 and w.source_id==workloads[name].source_id)
        valid &= max(starts)-min(starts)<1e-6 and max(ends)-min(ends)<1e-6
        if previous_end is not None and abs(min(starts)-previous_end)>1e-6:stable=0
        previous_end=max(ends);stable=stable+1 if valid else 0
        if stable>=2:return index,{name:w for name,(w,_) in cycle.items()}
    return None,{}


def pod_receipts(backend):
    names={e['id']:e['name'] for e in backend.config['inventory']['entities'] if e.get('kind')=='Deployment'}
    pods=backend.client.list('Pod');result={}
    for role in ('old','alternative','peer'):
        result[role]=[{'uid':p['metadata']['uid'],'name':p['metadata']['name'],'start_time':p.get('status',{}).get('startTime'),
                      'containers':[{k:s.get(k) for k in ('name','imageID','restartCount','ready')} for s in p.get('status',{}).get('containerStatuses',[])]}
                     for p in pods if p['metadata'].get('labels',{}).get('app')==names[role] and not p['metadata'].get('deletionTimestamp')]
    return result


def cpu_receipts(backend):
    result={}
    for name in ('shared-db','private-db','reference-db'):
        try:
            pods=[p for p in backend.client.list('Pod','app='+name) if not p['metadata'].get('deletionTimestamp')]
            if len(pods)!=1:raise ValueError('database CPU sample requires one current Pod')
            pod=pods[0]['metadata']
            process=subprocess.run(['kubectl','-n',backend.client.namespace,'exec','pod/'+pod['name'],'--','cat','/sys/fs/cgroup/cpu.stat'],
                env=backend.client.context.environment(),capture_output=True,text=True,timeout=15)
            values={line.split()[0]:int(line.split()[1]) for line in process.stdout.splitlines()} if process.returncode==0 else None
            result[name]={'status':'observed' if values else 'unknown','pod_name':pod['name'],'pod_uid':pod['uid'],
                          'values':values,'error':process.stderr[:300]}
        except Exception as exc:result[name]={'status':'unknown','error':str(exc)[:300]}
    return result


def run(config,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    diag=config['diagnostic'];case=config['cases'][0]
    pooled=config.get('baseline_reference_protocol') is not None
    if pooled and config['baseline_reference_protocol']!=REFERENCE_PROTOCOL:raise ValueError('fixed reference protocol differs')
    cohorts=34 if pooled else 20;query_deadline=300 if pooled else 160;final_start=310 if pooled else 170
    if (config['native_application']!='boutique' or case.get('fault_changes') or diag['cohorts']!=cohorts
            or diag['mode'] not in ('full8','passive') or len(config['cases'])!=1):raise ValueError('outside fixed no-fault diagnostic')
    if pooled and diag['mode']!='full8':raise ValueError('reference gate supports only fixed full8 acquisition')
    lock=(Path(config['resource_lock_directory'])/(config['experimental_namespace']+'.lock')).open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    (out/'protocol.json').write_text(json.dumps(config,indent=2)+'\n')
    b=NativeBackend(config,out);b.current_dir=out;b.case=case;b.fault_window=None
    b.provider=AccountedNativeProvider(config['inventory'],b.client,b);b.probe_accountant=None
    result={'schema':'native_stability_diagnostic_v1','status':'started','started_at':time.time(),
            'measurement_status':'not_started','scope':'No-fault protocol diagnostic; not a remediation or learned-policy result',
            'diagnostic':diag,'namespace':config['experimental_namespace']}
    if pooled:result['validity_gate_passed']=False
    try:
        result['pods_before']=pod_receipts(b)
        b.configure_background(case,out)
        b.reset_application_state(out,'before')
        b._setup(case['baseline_changes'])
        if not b.wait_controllers():raise TimeoutError('diagnostic baseline did not settle')
        b.timed_call('baseline_fixed_wait',time.sleep,case['baseline_settle_seconds'])
        result['pods_after_setup']=pod_receipts(b)
        for role,after in result['pods_after_setup'].items():
            if len(after)!=1 or {x['uid'] for x in after}&{x['uid'] for x in result['pods_before'][role]}:
                raise ValueError('synchronized role did not start a distinct process')
        result['cpu_before']=cpu_receipts(b)
        b.offered_rps=10.;b.workloads['business'].offered_rps=10.;b.workloads['peer'].offered_rps=20.
        b.start_traffic();origin=b.traffic.origin_wall;result['traffic_origin']=origin
        first=b.traffic.take(origin,12 if pooled else 4,170 if pooled else 100)
        result['baseline_cohorts']=[{n:{'window':asdict(w),'raw':raw} for n,(w,raw) in cycle.items()} for cycle in first]
        if pooled:
            index=None;baselines={};receipts={};stability={}
            try:
                for name,workload in b.workloads.items():
                    baselines[name],receipts[name]=pool_requests(first[6:12],name,origin+60,origin+120,workload.source_id,b.protocol,expected_rate=workload.offered_rps)
                    left,_=pool_requests(first[6:9],name,origin+60,origin+90,workload.source_id,b.protocol,expected_rate=workload.offered_rps)
                    right,_=pool_requests(first[9:12],name,origin+90,origin+120,workload.source_id,b.protocol,expected_rate=workload.offered_rps)
                    stability[name]=bidirectional_latency(left,right,b.protocol)
                result.update(reference_receipts=receipts,reference_half_stability=stability,reference_frozen_at=time.time())
                (out/'FROZEN_REFERENCE.json').write_text(json.dumps({'frozen_at':result['reference_frozen_at'],
                    'scope':'Only60–120s raw arrival cohorts; later observations cannot change this reference',
                    'references':receipts},indent=2)+'\n')
                if result['reference_frozen_at']>=origin+180:raise ValueError('reference was not ready before acquisition phase')
            except ValueError as exc:
                baselines={};result['reference_error']=str(exc)
        else:index,baselines=select_baseline(first,b.workloads,b.protocol)
        result['selected_baseline_index']=index;result['baseline_windows']={n:asdict(w) for n,w in baselines.items()}
        if not baselines:
            result['measurement_status']='invalid_reference_fixed_cap' if pooled else 'invalid_baseline_fixed_cap'
        else:
            b.baselines=baselines
            if pooled:
                holdout=b.traffic.take(origin+120,6,max(1,origin+210-time.time()))
                checks={};holds={}
                for name,workload in b.workloads.items():
                    pooled_hold,holds[name]=pool_requests(holdout,name,origin+120,origin+180,workload.source_id,b.protocol,require_healthy=False,expected_rate=workload.offered_rps)
                    checks[name]=bidirectional_latency(baselines[name],pooled_hold,b.protocol)
                result.update(passive_holdout_receipts=holds,reference_holdout_stability=checks)
            result['acquisition_started_at']=time.time()
            # The registered absolute deadline includes delayed cohort delivery;
            # it is not restarted at each probe.
            allowance=max(0.,origin+query_deadline-time.time())
            b.probe_accountant=ProbeAccountant(ObservationBudget(8,sum(p.cost for p in b.provider.probes if p.id in config['initial_probes']),allowance),
                record=lambda event:append_json(out/'registered_probe_events.jsonl',event))
            try:
                result['input']=b.observe(case)
                result['measurement_status']='observed'
            except (TimeoutError,RuntimeError,ValueError) as exc:
                result.update(measurement_status='invalid_acquisition',acquisition_error=type(exc).__name__,acquisition_detail=str(exc))
                (out/'acquisition_traceback.txt').write_text(traceback.format_exc())
            result['acquisition_completed_at']=time.time();b.probe_accountant.close()
            result['total_probe_budget']=b.probe_accountant.report()
            result['initial_snapshot']=getattr(b,'masked_input_graph',None)
            result['initial_observations']=b.query_observations
            if result['acquisition_completed_at']>origin+query_deadline:result['measurement_status']='invalid_acquisition_deadline'
            final=b.traffic.take(origin+final_start,3,max(1,origin+final_start+50-time.time()))
            result['final_windows']=[{n:{'window':asdict(w),'raw':raw} for n,(w,raw) in cycle.items()} for cycle in final]
            result['final_threshold_checks']={name:business_windows(baselines[name],[c[name][0] for c in final],
                completed_at=origin+final_start,evaluated_at=time.time(),protocol=b.protocol) for name in baselines}
            if pooled:
                history=b.traffic.history();all_checks={}
                for name in baselines:
                    windows=[WorkloadWindow(**c[name]['window']) for c in history if origin+120-1e-6<=c[name]['window']['start_time']<origin+340-1e-6]
                    all_checks[name]=all_arrival_windows(baselines[name],windows,b.protocol,origin+120,origin+340,time.time())
                background=[c['background'] for c in history if origin-1e-6<=c['background']['window']['start_time']<origin+340-1e-6]
                background_valid=len(background)==34 and all(w['raw']['attempts']==320 and w['raw']['successes']==320 for w in background)
                result.update(all_window_validity=all_checks,background_protocol_valid=background_valid)
                result['validity_gate_passed']=(result['measurement_status']=='observed' and background_valid
                    and all(c['status']=='pass' for group in (result['reference_half_stability'],result['reference_holdout_stability'],all_checks) for c in group.values()))
    except Exception as exc:
        result.update(measurement_status='infrastructure_error',error=type(exc).__name__,detail=str(exc))
        (out/'traceback.txt').write_text(traceback.format_exc())
    finally:
        if b.probe_accountant is not None:b.probe_accountant.close();result['total_probe_budget']=b.probe_accountant.report()
        try:
            KubernetesBackend.close_traffic(b);result['traffic_drained']=b.traffic is None
            result['cpu_after']=cpu_receipts(b)
        except Exception as exc:result['drain_error']=str(exc)
        if result.get('traffic_drained') and b.traffic is None:
            try:
                # Original configuration has a different context; measure restoration
                # by exact recorded fields, controller readiness and native canaries.
                b.baselines={};result['restore']=b.restore()
                b.reset_application_state(out,'after')
                result['restore_canaries']={n:http_request(s['canary'])[0] for n,s in config['direct_workloads'].items()}
            except Exception as exc:result['restore']={'status':'failed','error':str(exc)}
        else:
            result['restore']={'status':'not_attempted','reason':'traffic_ownership_retained; drain before any restore/reset'}
        (out/'native_probe_measurements.json').write_text(json.dumps(b.provider.raw_probes,indent=2,allow_nan=False)+'\n')
        result['cleanup_passed']=bool(result.get('traffic_drained') and result.get('restore',{}).get('status')=='restored'
            and result.get('restore_canaries') and all(result['restore_canaries'].values()))
        result['status']='complete' if result['cleanup_passed'] and result['measurement_status']!='infrastructure_error' else 'stopped'
        result['completed_at']=time.time();result['phase_timings_seconds']=getattr(b,'phase_timings',{})
        (out/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n');lock.close()
    print(json.dumps({k:result[k] for k in ('status','measurement_status','cleanup_passed')}),flush=True)
    return 0 if result['status']=='complete' else 2


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',required=True,type=Path);parser.add_argument('--out',required=True,type=Path)
    a=parser.parse_args();raise SystemExit(run(json.loads(a.config.read_text()),a.out))
