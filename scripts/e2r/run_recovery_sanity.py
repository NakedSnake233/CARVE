#!/usr/bin/env python3
"""One gated live E2R recovery sanity in the isolated namespace."""
from __future__ import annotations

import argparse,hashlib,json,math,os,socket,subprocess,sys,tempfile,time,urllib.request
from dataclasses import asdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.PlanRCAThinkRemed import playbook_generator_agent,repair_planner_agent,safety_guard
from methods.PlanRCAThinkRemed.cluster_client import ClusterClientContext,DEFAULT_CLUSTER_CONTEXT
from methods.PlanRCAThinkRemed.schemas import EvidenceItem,RCAReport,RepairAction,RepairPlan
from rca_bench.recovery_oracle import WorkloadWindow,evaluate_recovery
from rca_bench.workload_oracle import (HTTPWindowCollector,KubectlPortForward,
                                       collect_technical_checks)

NAMESPACE='e2r-supplement-20260925';SERVICE='frontend';CHAOS_NAME='e2r-sanity-cpu'
CHAOS_DURATION_SECONDS=180;ATTRIBUTION_MARGIN_SECONDS=30
LATEST_REPAIR_COMPLETION_SECONDS=CHAOS_DURATION_SECONDS-ATTRIBUTION_MARGIN_SECONDS
EARLY_PRE_REPAIR_STOP_SECONDS=120;MAX_ANSIBLE_TIMEOUT_SECONDS=30
EXPECTED_BASELINE=(ROOT/'experiments/e2r/runs/recovery_baseline_v2/baseline.json').resolve()
EXPECTED_SCOPED_KUBECONFIG=Path('/tmp/e2r-executor.kubeconfig')
EXPECTED_MANIFEST=(ROOT/'experiments/e2r/isolated_boutique.yaml').resolve()
APPROVED_BASELINE_SHA256='3957fa90acce446bd248c6e493c2374307454cc96d4644b99ec60fc04b84642d'
APPROVED_MANIFEST_SHA256='9150029373d00e9882b83922071d69c12bcd36b04af5cab16ea90a2caa0882bb'


def run(argv,*,context,input_text=None,timeout=30):
    result=subprocess.run(argv,input=input_text,capture_output=True,text=True,timeout=timeout,
                          shell=False,env=context.environment())
    return {'argv':argv,'returncode':result.returncode,'stdout':result.stdout.strip(),
            'stderr':result.stderr.strip(),'client_context':context.to_dict()}


def validate_static_bindings(baseline_path,baseline_bytes,manifest_path,manifest_bytes):
    baseline_path=Path(baseline_path).resolve();manifest_path=Path(manifest_path).resolve()
    if baseline_path!=EXPECTED_BASELINE:
        raise ValueError('sanity requires the approved immutable baseline path')
    if manifest_path!=EXPECTED_MANIFEST:
        raise ValueError('sanity requires the approved isolated manifest path')
    baseline_sha=hashlib.sha256(baseline_bytes).hexdigest()
    manifest_sha=hashlib.sha256(manifest_bytes).hexdigest()
    if baseline_sha!=APPROVED_BASELINE_SHA256:
        raise ValueError('approved baseline content digest mismatch')
    if manifest_sha!=APPROVED_MANIFEST_SHA256:
        raise ValueError('approved isolated manifest content digest mismatch')
    return {'baseline_path':str(baseline_path),'baseline_sha256':baseline_sha,
            'manifest_path':str(manifest_path),'manifest_sha256':manifest_sha}


def choose_port():
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as handle:
        handle.bind(('127.0.0.1',0));return handle.getsockname()[1]


def canary(url):
    try:
        with urllib.request.urlopen(url,timeout=5) as response:
            response.read(1024);return int(response.status)==200
    except Exception:return False


def measure(collector,window_id):
    window,raw=collector.collect(window_id);return window,raw


def run_ansible(playbook,timeout):
    executable=Path(sys.executable).parent/'ansible-playbook'
    if not executable.is_file():
        raise RuntimeError('venv-local ansible-playbook is unavailable')
    with tempfile.TemporaryDirectory(prefix='e2r-ansible-') as name:
        temp=Path(name);(temp/'inventory.ini').write_text(
            '[k3s_control_plane]\nlocalhost ansible_connection=local\n')
        (temp/'remediation.yml').write_text(playbook)
        local_temp=temp/'local-tmp';remote_temp=temp/'remote-tmp'
        local_temp.mkdir(mode=0o700);remote_temp.mkdir(mode=0o700)
        env=dict(os.environ);env['ANSIBLE_LOCAL_TEMP']=str(local_temp)
        env['ANSIBLE_REMOTE_TEMP']=str(remote_temp)
        started=time.perf_counter()
        process=subprocess.run([str(executable),'-i','inventory.ini','remediation.yml'],cwd=temp,
            capture_output=True,text=True,timeout=timeout,shell=False,env=env)
        return {'returncode':process.returncode,'stdout':process.stdout[-4000:],
                'stderr':process.stderr[-4000:],'seconds':time.perf_counter()-started,
                'executable':str(executable),'timeout_seconds':timeout}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir',required=True)
    parser.add_argument('--baseline',default=str(EXPECTED_BASELINE))
    parser.add_argument('--scoped-kubeconfig',default='/tmp/e2r-executor.kubeconfig')
    args=parser.parse_args();out=Path(args.out_dir)
    if out.exists():raise SystemExit('Refusing overwrite')
    scoped_path=Path(args.scoped_kubeconfig)
    if scoped_path!=EXPECTED_SCOPED_KUBECONFIG:
        raise ValueError('sanity requires the reviewed scoped kubeconfig path')
    out.mkdir(parents=True)
    if not scoped_path.is_file() or scoped_path.stat().st_mode&0o077:
        raise RuntimeError('scoped kubeconfig missing or permissions are not 0600')
    baseline_bytes=Path(args.baseline).read_bytes();manifest_bytes=EXPECTED_MANIFEST.read_bytes()
    bindings=validate_static_bindings(args.baseline,baseline_bytes,EXPECTED_MANIFEST,manifest_bytes)
    scoped=ClusterClientContext(str(scoped_path))
    baseline_artifact=json.loads(baseline_bytes)
    if (baseline_artifact.get('status')!='complete' or baseline_artifact.get('namespace')!=NAMESPACE
            or baseline_artifact.get('fault_injected') is not False):
        raise RuntimeError('healthy isolated baseline contract mismatch')
    baseline=WorkloadWindow(**baseline_artifact['baseline'])
    trace={'status':'in_progress','evaluation_type':'live_isolated_single_sanity',
           'namespace':NAMESPACE,'service':SERVICE,'fault':'cpu-stress',
           'client_context':scoped.to_dict(),'events':[],'fault_injected':False,
           'repair_executed':False,'started_at':time.time(),**bindings}
    trace_path=out/'trace.json'
    def save():trace_path.write_text(json.dumps(trace,indent=2)+'\n')
    save();admin=DEFAULT_CLUSTER_CONTEXT
    # Runtime isolation/RBAC assertions before mutation.
    checks={}
    for name,argv,expected in [
        ('isolated_get',['kubectl','--kubeconfig',str(scoped_path),'auth','can-i','get','pods','-n',NAMESPACE],True),
        ('isolated_delete_chaos',['kubectl','--kubeconfig',str(scoped_path),'auth','can-i','delete','stresschaos.chaos-mesh.org','-n',NAMESPACE],True),
        ('isolated_portforward',['kubectl','--kubeconfig',str(scoped_path),'auth','can-i','create','pods','--subresource=portforward','-n',NAMESPACE],True),
        ('user_namespace_denied',['kubectl','--kubeconfig',str(scoped_path),'auth','can-i','get','pods','-n','online-boutique'],False)]:
        result=subprocess.run(argv,capture_output=True,text=True,timeout=15,shell=False)
        answer=result.stdout.strip().lower()
        if answer=='yes' and result.returncode==0:observed=True
        elif answer=='no' and result.returncode==1:observed=False
        else:raise RuntimeError(f'RBAC query failed: {name}')
        checks[name]=observed
        if observed!=expected:raise RuntimeError(f'RBAC gate failed: {name}')
    trace['rbac_checks']=checks;save()
    preflight_plan=RepairPlan(actions=[RepairAction(action_type='WaitAndVerify',
        namespace=NAMESPACE,command=f'kubectl get pods -n {NAMESPACE}',risk='low')])
    preflight_playbook=playbook_generator_agent.render_playbook(
        preflight_plan,client_context=scoped)
    preflight_yaml_ok,preflight_reasons=safety_guard.check_playbook_yaml(
        preflight_playbook,allowed_namespace=NAMESPACE,
        expected_commands=safety_guard.expected_playbook_commands(preflight_plan),
        client_context=scoped)
    if not preflight_yaml_ok:raise RuntimeError(f'Ansible preflight YAML rejected: {preflight_reasons}')
    trace['ansible_preflight']=run_ansible(preflight_playbook,timeout=20);save()
    if (trace['ansible_preflight']['returncode']!=0
            or 'frontend-' not in trace['ansible_preflight']['stdout']):
        raise RuntimeError('scoped read-only Ansible preflight failed')
    existing=run(['kubectl','get','stresschaos',CHAOS_NAME,'-n',NAMESPACE,'-o','json'],context=admin)
    if existing['returncode']==0:raise RuntimeError('sanity chaos object already exists')
    if 'NotFound' not in existing['stderr'] and 'not found' not in existing['stderr'].lower():
        raise RuntimeError('cannot establish absence of the sanity chaos object')
    port=choose_port();source=f'k8s://{NAMESPACE}/service/frontend/http-root'
    collector=HTTPWindowCollector(url=f'http://127.0.0.1:{port}/',source_id=source,
        workers=5,request_count=30,window_duration_seconds=30,offered_rps=5,timeout=5,
        state_canary=lambda:canary(f'http://127.0.0.1:{port}/product/OLJCESPC7Z'))
    chaos={'apiVersion':'chaos-mesh.org/v1alpha1','kind':'StressChaos',
        'metadata':{'name':CHAOS_NAME,'namespace':NAMESPACE},
        'spec':{'selector':{'namespaces':[NAMESPACE],'labelSelectors':{'app':SERVICE}},
                'mode':'one','duration':f'{CHAOS_DURATION_SECONDS}s',
                'stressors':{'cpu':{'workers':10,'load':100}}}}
    injected=False
    with KubectlPortForward(namespace=NAMESPACE,service='frontend',local_port=port,
                            client_context=scoped) as forward:
      trace['port_forward_startup_log']=forward.startup_log
      try:
        pre_window,pre_raw=measure(collector,'sanity-pre')
        trace['pre_window']=asdict(pre_window);trace['pre_raw']=pre_raw;save()
        pre_healthy=(pre_window.error_rate<=baseline.error_rate+.01
                     and isinstance(pre_window.p95_ms,(int,float)) and math.isfinite(pre_window.p95_ms)
                     and pre_window.p95_ms<=baseline.p95_ms*1.2
                     and pre_window.throughput_rps>=baseline.throughput_rps*.8
                     and pre_window.state_canary_ok is True)
        trace['pre_health_gate']=pre_healthy;save()
        if not pre_healthy:raise RuntimeError('pre-injection workload is outside baseline tolerance')
        injection_request_started_wall=time.time();injection_request_started_mono=time.monotonic()
        inject_started=time.perf_counter()
        injection=run(['kubectl','apply','-n',NAMESPACE,'-f','-'],context=admin,
                      input_text=json.dumps(chaos))
        trace['events'].append({'stage':'injection','seconds':time.perf_counter()-inject_started,
                                'result':injection})
        if injection['returncode']!=0:raise RuntimeError('fault injection failed')
        injected=True;trace['fault_injected']=True
        trace['injection_request_started_at']=injection_request_started_wall
        trace['injection_completed_at']=time.time();save();time.sleep(30)
        fault_window,fault_raw=measure(collector,'sanity-fault')
        trace['fault_window']=asdict(fault_window);trace['fault_raw']=fault_raw
        fault_measurable=(fault_window.samples>=20
                          and isinstance(fault_window.throughput_rps,(int,float))
                          and math.isfinite(fault_window.throughput_rps)
                          and isinstance(fault_window.error_rate,(int,float))
                          and math.isfinite(fault_window.error_rate)
                          and isinstance(fault_window.p95_ms,(int,float))
                          and math.isfinite(fault_window.p95_ms)
                          and type(fault_window.state_canary_ok) is bool)
        trace['fault_window_measurable']=fault_measurable;save()
        if not fault_measurable:
            raise RuntimeError('injected-fault workload window is unmeasurable')
        degraded=(fault_window.error_rate>baseline.error_rate+.01
                  or fault_window.p95_ms>baseline.p95_ms*1.2
                  or fault_window.throughput_rps<baseline.throughput_rps*.8
                  or fault_window.state_canary_ok is False)
        trace['initial_business_degradation']=degraded;save()
        if not degraded:raise RuntimeError('injected fault did not produce measurable business degradation')
        observed_result=run(['kubectl','get','stresschaos',CHAOS_NAME,'-n',NAMESPACE,'-o','json'],
                            context=scoped)
        if observed_result['returncode']!=0:raise RuntimeError('cannot observe injected chaos object')
        observed_chaos=json.loads(observed_result['stdout'])
        metadata=observed_chaos.get('metadata') or {};selector=(observed_chaos.get('spec') or {}).get('selector') or {}
        if (observed_chaos.get('kind')!='StressChaos' or metadata.get('name')!=CHAOS_NAME
                or metadata.get('namespace')!=NAMESPACE
                or (selector.get('labelSelectors') or {}).get('app')!=SERVICE):
            raise RuntimeError('observed chaos object identity/selector mismatch')
        fault_age=time.monotonic()-injection_request_started_mono
        trace['pre_repair_fault_age_seconds']=fault_age
        trace['early_pre_repair_stop_seconds']=EARLY_PRE_REPAIR_STOP_SECONDS
        trace['latest_repair_completion_seconds']=LATEST_REPAIR_COMPLETION_SECONDS;save()
        if fault_age>=EARLY_PRE_REPAIR_STOP_SECONDS:
            raise RuntimeError('fault is too close to natural expiry for repair attribution')
        evidence=EvidenceItem(source='live-chaos-object',command=f'kubectl get stresschaos {CHAOS_NAME}',
            service=SERVICE,modality='config',
            finding=f"StressChaos '{CHAOS_NAME}' actively targets service '{SERVICE}'.",
            raw_excerpt=json.dumps(observed_chaos,sort_keys=True),confidence=.99)
        report=RCAReport(root_cause_service=SERVICE,failure_category='cpu-stress',
            mechanism='active target-bound CPU stress',confidence=.99,evidence=[evidence])
        plan=repair_planner_agent.build_repair_plan(rca_report=report,evidence_history=[evidence],
            runtime_envs='isolated k3s benchmark',namespace=NAMESPACE,env=NAMESPACE,use_llm=False)
        plan_ok,plan_reasons=safety_guard.check_repair_plan(plan,allowed_namespace=NAMESPACE)
        playbook=playbook_generator_agent.generate_playbook(plan,namespace=NAMESPACE,
            inventory_content='[k3s_control_plane]\nlocalhost ansible_connection=local\n',
            use_llm=False,client_context=scoped)
        yaml_ok,yaml_reasons=safety_guard.check_playbook_yaml(playbook,
            allowed_namespace=NAMESPACE,expected_commands=safety_guard.expected_playbook_commands(plan),
            client_context=scoped)
        trace['repair_plan']=plan.to_dict();trace['safety']={'plan_ok':plan_ok,
            'yaml_ok':yaml_ok,'reasons':plan_reasons+yaml_reasons};trace['playbook']=playbook;save()
        if not plan_ok or not yaml_ok:raise RuntimeError('repair safety gate failed')
        preconditions=safety_guard.run_preconditions(plan,client_context=scoped)
        trace['preconditions']=preconditions;save()
        if not preconditions['passed']:raise RuntimeError('repair precondition failed')
        pre_execution_age=time.monotonic()-injection_request_started_mono
        remaining_attribution_budget=LATEST_REPAIR_COMPLETION_SECONDS-pre_execution_age
        execution_timeout=min(MAX_ANSIBLE_TIMEOUT_SECONDS,
                              max(0,math.floor(remaining_attribution_budget)))
        trace['pre_execution_fault_age_seconds']=pre_execution_age
        trace['remaining_attribution_budget_seconds']=remaining_attribution_budget
        trace['ansible_timeout_seconds']=execution_timeout;save()
        if execution_timeout<5:
            raise RuntimeError('insufficient remaining lifetime for attributable repair execution')
        execution=run_ansible(playbook,timeout=execution_timeout)
        repair_completion_age=time.monotonic()-injection_request_started_mono
        execution['repair_completion_fault_age_seconds']=repair_completion_age
        execution['completed_before_attribution_deadline']=(
            repair_completion_age<LATEST_REPAIR_COMPLETION_SECONDS)
        trace['execution']=execution;trace['repair_executed']=execution['returncode']==0
        repair_completed_at=time.time();trace['repair_completed_at']=repair_completed_at;save()
        if execution['returncode']!=0:
            trace['rollback']=safety_guard.execute_rollback(preconditions,client_context=scoped);save()
            raise RuntimeError('repair execution failed')
        if not execution['completed_before_attribution_deadline']:
            raise RuntimeError('repair completed after the conservative attribution deadline')
        post=[];post_raw=[]
        for index in range(3):
            window,raw=measure(collector,f'sanity-post-{index}');post.append(window);post_raw.append(raw)
            if index<2:time.sleep(1)
        technical=collect_technical_checks(plan,client_context=scoped)
        oracle=evaluate_recovery(baseline=baseline,post_windows=post,technical=technical,
            required_windows=3,require_state_canary=True,repair_completed_at=repair_completed_at,
            evaluated_at=time.time(),max_window_age_seconds=120)
        trace['post_windows']=[asdict(window) for window in post];trace['post_raw']=post_raw
        trace['technical_checks']=technical;trace['oracle']=oracle
        trace['status']='complete_pass' if oracle['status']=='PASS' else 'complete_nonpass';save()
      except Exception as exc:
        trace['status']='failed';trace['error_type']=type(exc).__name__;trace['error']=str(exc);save()
      finally:
        cleanup=None;cleanup_error=None;absent=None;absence_error=None
        try:
            cleanup=run(['kubectl','delete','stresschaos',CHAOS_NAME,'-n',NAMESPACE,
                         '--ignore-not-found'],context=admin)
        except Exception as cleanup_exc:
            cleanup_error={'type':type(cleanup_exc).__name__,'error':str(cleanup_exc)}
        try:
            absent=run(['kubectl','get','stresschaos',CHAOS_NAME,'-n',NAMESPACE,'-o','json'],context=admin)
        except Exception as absence_exc:
            absence_error={'type':type(absence_exc).__name__,'error':str(absence_exc)}
        cleanup_absent=(absent is not None and absent['returncode']!=0 and
            ('NotFound' in absent['stderr'] or 'not found' in absent['stderr'].lower()))
        trace['cleanup']=cleanup;trace['cleanup_error']=cleanup_error
        trace['cleanup_absence_probe']=absent;trace['cleanup_absence_error']=absence_error
        trace['cleanup_verified_absent']=cleanup_absent
        if (cleanup_error is not None or cleanup is None or cleanup['returncode']!=0
                or absence_error is not None or not cleanup_absent):
            trace['status']='failed_cleanup'
        trace['completed_at']=time.time();save()
    print(json.dumps({'status':trace['status'],'initial_business_degradation':trace.get('initial_business_degradation'),
                      'repair_executed':trace['repair_executed'],
                      'oracle_status':trace.get('oracle',{}).get('status')},indent=2))
    if trace['status']!='complete_pass':raise SystemExit(2)


if __name__=='__main__':main()
