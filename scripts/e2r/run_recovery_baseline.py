#!/usr/bin/env python3
"""Collect one immutable no-fault workload baseline in the isolated namespace."""
from __future__ import annotations

import argparse,json,math,socket,statistics,subprocess,sys,time,urllib.request
from dataclasses import asdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.PlanRCAThinkRemed.cluster_client import DEFAULT_CLUSTER_CONTEXT
from rca_bench.recovery_oracle import WorkloadWindow
from rca_bench.workload_oracle import HTTPWindowCollector,KubectlPortForward

ISOLATED_NAMESPACE='e2r-supplement-20260925'
EXPECTED_DEPLOYMENTS={'emailservice','checkoutservice','recommendationservice','frontend',
    'paymentservice','productcatalogservice','cartservice','loadgenerator','currencyservice',
    'shippingservice','redis-cart','adservice'}


def kubectl_json(args):
    command=['kubectl',*args]
    result=subprocess.run(command,capture_output=True,text=True,timeout=20,shell=False,
                          env=DEFAULT_CLUSTER_CONTEXT.environment())
    if result.returncode!=0:raise RuntimeError(result.stderr.strip() or 'kubectl failed')
    return json.loads(result.stdout)


def choose_port():
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as handle:
        handle.bind(('127.0.0.1',0));return handle.getsockname()[1]


def pod_runtime_identity(payload):
    return {row['metadata']['uid']:{status['name']:status.get('restartCount')
            for status in row.get('status',{}).get('containerStatuses',[])}
            for row in payload.get('items',[])}


def pods_ready(payload):
    rows=payload.get('items',[])
    return bool(rows) and all(
        row.get('metadata',{}).get('namespace')==ISOLATED_NAMESPACE
        and row.get('status',{}).get('phase')=='Running'
        and row.get('status',{}).get('containerStatuses')
        and all(container.get('ready') is True for container in
                row.get('status',{}).get('containerStatuses',[]))
        for row in rows)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--namespace',default=ISOLATED_NAMESPACE)
    parser.add_argument('--out-dir',required=True)
    parser.add_argument('--warmup-seconds',type=float,default=30)
    parser.add_argument('--windows',type=int,default=6)
    parser.add_argument('--requests-per-window',type=int,default=30)
    parser.add_argument('--workers',type=int,default=5)
    parser.add_argument('--window-seconds',type=float,default=30)
    parser.add_argument('--offered-rps',type=float,default=5)
    parser.add_argument('--baseline-id',default='baseline-calibration-v1')
    args=parser.parse_args()
    if args.namespace!=ISOLATED_NAMESPACE:
        raise ValueError(f'baseline is restricted to isolated namespace {ISOLATED_NAMESPACE}')
    if (args.windows<6 or args.requests_per_window<20 or args.workers<1
            or args.window_seconds<30 or args.offered_rps<=0):
        raise ValueError('baseline requires >=6 x 30-second windows and positive offered load')
    if not args.baseline_id.startswith('baseline-calibration-v'):
        raise ValueError('baseline-id must be a versioned calibration identifier')
    out=Path(args.out_dir)
    if out.exists():raise SystemExit('Refusing overwrite')
    out.mkdir(parents=True)
    deployments=kubectl_json(['get','deployments','-n',args.namespace,'-o','json'])
    pods=kubectl_json(['get','pods','-n',args.namespace,'-o','json'])
    services=kubectl_json(['get','services','-n',args.namespace,'-o','json'])
    deployment_rows=deployments.get('items',[])
    names={row.get('metadata',{}).get('name') for row in deployment_rows}
    if names!=EXPECTED_DEPLOYMENTS:raise RuntimeError('isolated deployment identity mismatch')
    if any(row.get('metadata',{}).get('namespace')!=ISOLATED_NAMESPACE
           for row in deployment_rows):raise RuntimeError('deployment namespace mismatch')
    unavailable=[]
    for deployment in deployment_rows:
        desired=(deployment.get('spec') or {}).get('replicas',1)
        available=(deployment.get('status') or {}).get('availableReplicas',0)
        if (type(desired) is not int or desired<=0 or type(available) is not int
                or available<desired):
            unavailable.append(deployment['metadata']['name'])
    if unavailable:raise RuntimeError('unavailable deployments: '+','.join(unavailable))
    if not pods_ready(pods):
        raise RuntimeError('isolated pod health gate failed')
    before_runtime=pod_runtime_identity(pods)

    port=choose_port();source=f'k8s://{args.namespace}/service/frontend/http-root'
    windows=[];raw=[]
    with KubectlPortForward(namespace=args.namespace,service='frontend',local_port=port) as forward:
        if args.warmup_seconds>0:time.sleep(args.warmup_seconds)
        def canary():
            try:
                with urllib.request.urlopen(
                    f'http://127.0.0.1:{port}/product/OLJCESPC7Z',timeout=5
                ) as response:
                    response.read(1024);return int(response.status)==200
            except Exception:return False
        collector=HTTPWindowCollector(
            url=f'http://127.0.0.1:{port}/',source_id=source,
            request_count=args.requests_per_window,workers=args.workers,
            timeout=5,state_canary=canary,
            window_duration_seconds=args.window_seconds,offered_rps=args.offered_rps)
        for index in range(args.windows):
            window,diagnostics=collector.collect(f'baseline-{index}')
            windows.append(window);raw.append(diagnostics)
            if index+1<args.windows:time.sleep(1)
        forwarding_log=list(forward.startup_log)
    deployments_after=kubectl_json(['get','deployments','-n',args.namespace,'-o','json'])
    pods_after=kubectl_json(['get','pods','-n',args.namespace,'-o','json'])
    services_after=kubectl_json(['get','services','-n',args.namespace,'-o','json'])
    runtime_stable=(pods_ready(pods_after)
                    and pod_runtime_identity(pods_after)==before_runtime)
    all_latencies=[value for row in raw for value in row['latencies_ms']]
    p95=(sorted(all_latencies)[max(0,math.ceil(.95*len(all_latencies))-1)]
         if all_latencies else None)
    total_samples=sum(window.samples for window in windows)
    total_errors=sum(round(window.samples*window.error_rate) for window in windows)
    baseline=WorkloadWindow(
        samples=total_samples,throughput_rps=statistics.median(
            window.throughput_rps for window in windows),
        error_rate=total_errors/total_samples,p95_ms=p95,
        state_canary_ok=all(window.state_canary_ok is True for window in windows),
        window_id=args.baseline_id,source_id=source,
        start_time=windows[0].start_time,end_time=windows[-1].end_time)
    stable=(runtime_stable and all(window.samples>=20 and window.error_rate<=.05
                and window.state_canary_ok is True
                and isinstance(window.p95_ms,(int,float))
                and math.isfinite(window.p95_ms)
                for window in windows))
    result={'status':'complete' if stable else 'failed_calibration',
            'evaluation_type':'live_no_fault_baseline_only','namespace':args.namespace,
            'fault_injected':False,'repair_executed':False,
            'client_context':DEFAULT_CLUSTER_CONTEXT.to_dict(),
            'workload':{'manifest_users':10,'active_url_path':'/','canary_path':'/product/OLJCESPC7Z',
                        'warmup_seconds':args.warmup_seconds,'windows':args.windows,
                        'window_seconds':args.window_seconds,'offered_rps':args.offered_rps,
                        'scheduled_requests_per_window':math.ceil(args.window_seconds*args.offered_rps),
                        'requests_per_window_fallback':args.requests_per_window,'workers':args.workers},
            'baseline':asdict(baseline),'windows':[asdict(window) for window in windows],
            'raw_window_diagnostics':raw,'port_forward_startup_log':forwarding_log,
            'cluster_snapshot':{'deployments':deployments,'pods':pods,'services':services},
            'cluster_snapshot_after':{'deployments':deployments_after,'pods':pods_after,
                                      'services':services_after},
            'runtime_identity_stable_across_calibration':runtime_stable,
            'limits':['No fault was injected and no repair was planned or executed.',
                      'HTTP root/product checks characterize this isolated benchmark workload only.']}
    (out/'baseline.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'status':result['status'],'baseline':result['baseline']},indent=2))
    if not stable:raise SystemExit(2)


if __name__=='__main__':main()
