#!/usr/bin/env python3
"""Run a pre-registered two-episode no-action/typed-cleanup recovery block."""
from __future__ import annotations

import argparse,hashlib,json,math,os,random,subprocess,sys,time
from dataclasses import asdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from methods.PlanRCAThinkRemed import playbook_generator_agent,repair_planner_agent,safety_guard
from methods.PlanRCAThinkRemed.cluster_client import ClusterClientContext,DEFAULT_CLUSTER_CONTEXT
from methods.PlanRCAThinkRemed.schemas import EvidenceItem,RCAReport,RepairAction,RepairPlan
from rca_bench.recovery_oracle import WorkloadWindow,evaluate_recovery
from rca_bench.workload_oracle import HTTPWindowCollector,KubectlPortForward,collect_technical_checks
from scripts.e2r.run_recovery_sanity import (EXPECTED_BASELINE,EXPECTED_MANIFEST,
    EXPECTED_SCOPED_KUBECONFIG,canary,choose_port,measure,run,run_ansible,
    validate_static_bindings)

PROTOCOL_PATH=(ROOT/'experiments/e2r/recovery_matched_cpu_protocol_v1.json').resolve()
PROTOCOL_SHA256='b01798e4174651abf550bfe4f5f7f8d84c48c414e904ad5e26a5f2e8c8e7032d'


def load_protocol():
    payload=PROTOCOL_PATH.read_bytes();digest=hashlib.sha256(payload).hexdigest()
    if digest!=PROTOCOL_SHA256:raise ValueError('matched protocol digest mismatch')
    value=json.loads(payload)
    if (value.get('schema')!='e2r_recovery_matched_v1'
            or value.get('arms_in_order')!=['no_action','typed_cleanup']
            or value.get('episodes_per_arm')!=1
            or value.get('retry_policy')!='none; any gate failure stops the block and remains in the denominator'):
        raise ValueError('matched protocol contract mismatch')
    randomized=['typed_cleanup','no_action'];random.Random(value.get('order_seed')).shuffle(randomized)
    if randomized!=value['arms_in_order']:raise ValueError('recorded arm order does not match order seed')
    return value,digest


def parse_object(result,kind,name,namespace):
    if result.get('returncode')!=0:return None
    try:value=json.loads(result.get('stdout') or '')
    except (TypeError,ValueError):return None
    metadata=value.get('metadata') or {}
    if value.get('kind')!=kind or metadata.get('name')!=name or metadata.get('namespace')!=namespace:
        return None
    return value


def active_chaos(value,service):
    conditions={(row.get('type'),row.get('status')) for row in
                (value.get('status') or {}).get('conditions',[])}
    selector=((value.get('spec') or {}).get('selector') or {}).get('labelSelectors') or {}
    return (selector.get('app')==service and ('AllInjected','True') in conditions
            and ('AllRecovered','False') in conditions)


def pod_runtime(payload):
    rows=payload.get('items',[])
    if len(rows)!=12:return None
    result={}
    for row in rows:
        statuses=row.get('status',{}).get('containerStatuses') or []
        if row.get('status',{}).get('phase')!='Running' or len(statuses)!=2 or not all(s.get('ready') is True for s in statuses):
            return None
        result[row['metadata']['uid']]={s['name']:s.get('restartCount') for s in statuses}
    return result


def get_pods(context):
    result=run(['kubectl','get','pods','-n','e2r-supplement-20260925','-o','json'],context=context)
    value=json.loads(result['stdout']) if result['returncode']==0 else None
    return result,value,pod_runtime(value or {})


def save(path,value):
    """Atomically replace one JSON artifact after flushing its bytes to disk."""
    temporary=path.with_name(path.name+'.tmp')
    with temporary.open('w') as handle:
        handle.write(json.dumps(value,indent=2)+'\n');handle.flush();os.fsync(handle.fileno())
    os.replace(temporary,path)
    descriptor=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)


def _run_episode_core(*,arm,index,protocol,baseline,scoped,admin,block_runtime,root):
    namespace=protocol['namespace'];service=protocol['service']
    name=f"e2r-matched-cpu-{'noaction' if arm=='no_action' else 'typed'}"
    trace={'schema':'e2r_recovery_matched_episode_v1','episode_index':index,'arm':arm,
           'namespace':namespace,'service':service,'chaos_name':name,'status':'in_progress',
           'injection_attempted':False,'injection_outcome_unknown':False,
           'fault_injected':False,'repair_process_attempted':False,
           'repair_process_outcome_unknown':False,'repair_executed':False,
           'events':[],'started_at':time.time()}
    path=root/f'{index:02d}_{arm}.json';save(path,trace)
    duration=protocol['fault']['duration_seconds'];deadline=protocol['timeline']['latest_final_observation_seconds']
    chaos={'apiVersion':'chaos-mesh.org/v1alpha1','kind':'StressChaos',
        'metadata':{'name':name,'namespace':namespace},
        'spec':{'selector':{'namespaces':[namespace],'labelSelectors':{'app':service}},
                'mode':'one','duration':f'{duration}s','stressors':{'cpu':{
                    'workers':protocol['fault']['workers'],'load':protocol['fault']['load']}}}}
    existing=run(['kubectl','get','stresschaos',name,'-n',namespace,'-o','json'],context=admin)
    if existing['returncode']==0 or ('NotFound' not in existing['stderr'] and 'not found' not in existing['stderr'].lower()):
        trace.update(status='failed',error='cannot establish clean episode start');save(path,trace);return False
    port=choose_port();source=f'k8s://{namespace}/service/frontend/http-root'
    workload=protocol['workload'];collector=HTTPWindowCollector(
        url=f'http://127.0.0.1:{port}/',source_id=source,workers=workload['workers'],
        request_count=workload['requests_per_window'],window_duration_seconds=workload['window_seconds'],
        offered_rps=workload['offered_rps'],timeout=5,
        state_canary=lambda:canary(f"http://127.0.0.1:{port}{workload['state_canary_path']}"))
    injected=False;request_mono=None
    with KubectlPortForward(namespace=namespace,service='frontend',local_port=port,
                            client_context=scoped) as forward:
      trace['port_forward_startup_log']=forward.startup_log;save(path,trace)
      try:
        pre,pre_raw=measure(collector,f'{arm}-pre');trace['pre_window']=asdict(pre);trace['pre_raw']=pre_raw
        thresholds=protocol['thresholds']
        pre_ok=(pre.error_rate<=baseline.error_rate+thresholds['error_margin']
                and isinstance(pre.p95_ms,(int,float)) and math.isfinite(pre.p95_ms)
                and pre.p95_ms<=baseline.p95_ms*thresholds['p95_multiplier']
                and pre.throughput_rps>=baseline.throughput_rps*thresholds['throughput_fraction']
                and pre.state_canary_ok is True)
        trace['pre_health_gate']=pre_ok;save(path,trace)
        if not pre_ok:raise RuntimeError('pre-injection workload outside baseline tolerance')
        request_wall=time.time();request_mono=time.monotonic();started=time.perf_counter()
        trace['injection_attempted']=True;save(path,trace)
        try:injection=run(['kubectl','apply','-n',namespace,'-f','-'],context=admin,
                          input_text=json.dumps(chaos))
        except BaseException:
            trace['injection_outcome_unknown']=True;save(path,trace);raise
        trace['events'].append({'stage':'injection','seconds':time.perf_counter()-started,'result':injection})
        if injection['returncode']!=0:raise RuntimeError('fault injection failed')
        injected=True;trace['fault_injected']=True;trace['injection_request_started_at']=request_wall;save(path,trace)
        time.sleep(protocol['fault']['settle_seconds'])
        fault,fault_raw=measure(collector,f'{arm}-fault-validation')
        trace['fault_window']=asdict(fault);trace['fault_raw']=fault_raw
        measurable=(fault.samples>=20 and all(isinstance(value,(int,float)) and math.isfinite(value)
                    for value in (fault.throughput_rps,fault.error_rate,fault.p95_ms))
                    and type(fault.state_canary_ok) is bool)
        degraded=(measurable and (fault.error_rate>baseline.error_rate+thresholds['error_margin']
                  or fault.p95_ms>baseline.p95_ms*thresholds['p95_multiplier']
                  or fault.throughput_rps<baseline.throughput_rps*thresholds['throughput_fraction']
                  or fault.state_canary_ok is False))
        trace['fault_window_measurable']=measurable;trace['initial_business_degradation']=degraded;save(path,trace)
        if not measurable or not degraded:raise RuntimeError('fault degradation gate failed')
        observed_result=run(['kubectl','get','stresschaos',name,'-n',namespace,'-o','json'],context=scoped)
        trace['initial_fault_object_probe']=observed_result;save(path,trace)
        observed=parse_object(observed_result,'StressChaos',name,namespace)
        if observed is None or not active_chaos(observed,service):raise RuntimeError('fault is not actively injected')
        chaos_uid=(observed.get('metadata') or {}).get('uid')
        if not isinstance(chaos_uid,str) or not chaos_uid:
            raise RuntimeError('observed fault object lacks a stable UID')
        trace['chaos_uid']=chaos_uid;save(path,trace)
        evidence=EvidenceItem(source='live-chaos-object',command=f'kubectl get stresschaos {name}',
            service=service,modality='config',finding=f"StressChaos '{name}' actively targets service '{service}'.",
            raw_excerpt=json.dumps(observed,sort_keys=True),confidence=.99)
        report=RCAReport(root_cause_service=service,failure_category='cpu-stress',
            mechanism='active target-bound CPU stress',confidence=.99,evidence=[evidence])
        plan=repair_planner_agent.build_repair_plan(rca_report=report,evidence_history=[evidence],
            runtime_envs='isolated k3s benchmark',namespace=namespace,env=namespace,use_llm=False)
        plan_ok,plan_reasons=safety_guard.check_repair_plan(plan,allowed_namespace=namespace)
        playbook=playbook_generator_agent.generate_playbook(plan,namespace=namespace,
            inventory_content='[k3s_control_plane]\nlocalhost ansible_connection=local\n',use_llm=False,
            client_context=scoped)
        yaml_ok,yaml_reasons=safety_guard.check_playbook_yaml(playbook,allowed_namespace=namespace,
            expected_commands=safety_guard.expected_playbook_commands(plan),client_context=scoped)
        trace['repair_plan']=plan.to_dict();trace['safety']={'plan_ok':plan_ok,'yaml_ok':yaml_ok,
            'reasons':plan_reasons+yaml_reasons};trace['playbook']=playbook;save(path,trace)
        if not plan_ok or not yaml_ok:raise RuntimeError('safety gate failed')
        preconditions=safety_guard.run_preconditions(plan,client_context=scoped)
        trace['preconditions']=preconditions;save(path,trace)
        if not preconditions['passed']:raise RuntimeError('precondition gate failed')
        try:
            precondition_object=json.loads(preconditions['results'][0]['stdout'])
            precondition_uid=(precondition_object.get('metadata') or {}).get('uid')
        except (KeyError,IndexError,TypeError,ValueError):precondition_uid=None
        trace['precondition_same_chaos_uid']=(precondition_uid==chaos_uid);save(path,trace)
        if precondition_uid!=chaos_uid:raise RuntimeError('precondition fault UID changed')
        intervention_time=time.time()
        if arm=='typed_cleanup':
            age=time.monotonic()-request_mono;remaining=protocol['timeline']['latest_repair_completion_seconds']-age
            timeout=min(30,max(0,math.floor(remaining)))
            trace['pre_execution_fault_age_seconds']=age;trace['ansible_timeout_seconds']=timeout;save(path,trace)
            if timeout<5:raise RuntimeError('insufficient repair attribution budget')
            trace['repair_process_attempted']=True;save(path,trace)
            try:execution=run_ansible(playbook,timeout)
            except BaseException:
                trace['repair_process_outcome_unknown']=True;save(path,trace);raise
            completion_age=time.monotonic()-request_mono
            execution['repair_completion_fault_age_seconds']=completion_age
            execution['completed_before_deadline']=completion_age<protocol['timeline']['latest_repair_completion_seconds']
            trace['execution']=execution;trace['repair_executed']=execution['returncode']==0;save(path,trace)
            if execution['returncode']!=0 or not execution['completed_before_deadline']:
                if execution['returncode']!=0:
                    trace['rollback']=safety_guard.execute_rollback(preconditions,client_context=scoped);save(path,trace)
                raise RuntimeError('typed cleanup execution/deadline failed')
            intervention_time=time.time()
        else:
            trace['no_action_marker_fault_age_seconds']=time.monotonic()-request_mono;save(path,trace)
        evaluation=[];evaluation_raw=[];liveness=[]
        trace['evaluation_windows']=[];trace['evaluation_raw']=[]
        trace['no_action_liveness_probes']=[];save(path,trace)
        for window_index in range(protocol['timeline']['evaluation_windows']):
            age=time.monotonic()-request_mono
            if age+workload['window_seconds']+2>=deadline:
                raise RuntimeError('insufficient final-observation deadline budget')
            if arm=='no_action':
                probe=run(['kubectl','get','stresschaos',name,'-n',namespace,'-o','json'],context=scoped)
                value=parse_object(probe,'StressChaos',name,namespace)
                same_uid=bool(value and (value.get('metadata') or {}).get('uid')==chaos_uid)
                liveness.append({'fault_age_seconds':age,'probe':probe,'same_uid':same_uid,
                                 'active':bool(same_uid and active_chaos(value,service))})
                trace['no_action_liveness_probes']=liveness;save(path,trace)
                if not liveness[-1]['active']:raise RuntimeError('no-action fault ceased before observation')
            window,raw=measure(collector,f'{arm}-evaluation-{window_index}')
            evaluation.append(window);evaluation_raw.append(raw)
            trace['evaluation_windows']=[asdict(value) for value in evaluation]
            trace['evaluation_raw']=evaluation_raw;save(path,trace)
            if time.monotonic()-request_mono>=deadline:raise RuntimeError('final observation crossed deadline')
            if window_index+1<protocol['timeline']['evaluation_windows']:
                time.sleep(protocol['timeline']['inter_window_seconds'])
        trace['evaluation_windows']=[asdict(window) for window in evaluation]
        trace['evaluation_raw']=evaluation_raw;trace['no_action_liveness_probes']=liveness;save(path,trace)
        technical_deadline=protocol['timeline']['latest_technical_observation_seconds']
        technical_timeout=protocol['timeline']['technical_probe_timeout_seconds']
        pre_technical_age=time.monotonic()-request_mono
        # Three sequential exact-object reads are required. Refuse to start if
        # their declared worst-case budget reaches the common deadline.
        if pre_technical_age+3*technical_timeout>=technical_deadline:
            raise RuntimeError('insufficient technical-observation deadline budget')
        technical_package=collect_technical_checks(plan,client_context=scoped,
            include_evidence=True,probe_timeout=technical_timeout)
        technical_age=time.monotonic()-request_mono
        trace['pre_technical_fault_age_seconds']=pre_technical_age
        trace['technical_observation_fault_age_seconds']=technical_age
        trace['technical_observation_deadline_seconds']=technical_deadline
        trace['technical']=technical_package;save(path,trace)
        if technical_age>=technical_deadline:
            raise RuntimeError('technical observations crossed the common deadline')
        if arm=='no_action':
            first_probe=(technical_package.get('probes') or [{}])[0]
            rows=first_probe.get('results') or []
            live_value=None
            if len(rows)==1 and rows[0].get('returncode')==0:
                try:live_value=json.loads(rows[0].get('stdout') or '')
                except (TypeError,ValueError):live_value=None
            trace['no_action_active_at_technical_observation']=bool(
                isinstance(live_value,dict)
                and (live_value.get('metadata') or {}).get('uid')==chaos_uid
                and active_chaos(live_value,service))
            save(path,trace)
            if (technical_package['checks'].get('fault_absent') is not False
                    or not trace['no_action_active_at_technical_observation']):
                raise RuntimeError('no-action fault was not active at technical observation')
        oracle=evaluate_recovery(baseline=baseline,post_windows=evaluation,
            technical=technical_package['checks'],required_windows=thresholds['required_consecutive_windows'],
            error_margin=thresholds['error_margin'],p95_multiplier=thresholds['p95_multiplier'],
            throughput_fraction=thresholds['throughput_fraction'],require_state_canary=True,
            repair_completed_at=intervention_time,evaluated_at=time.time(),max_window_age_seconds=120)
        trace['technical']=technical_package;trace['oracle']=oracle
        expected=protocol['expected_oracle_status'][arm]
        trace['expected_oracle_status']=expected
        trace['no_action_oracle_interpretation']=(
            'joint FAIL is structurally required by active fault_absent=False; '
            'business persistence must be read from raw evaluation windows'
            if arm=='no_action' else None)
        trace['final_observation_fault_age_seconds']=time.monotonic()-request_mono
        trace['status']='complete_expected' if oracle['status']==expected else 'complete_unexpected';save(path,trace)
      except Exception as exc:
        trace['status']='failed';trace['error_type']=type(exc).__name__;trace['error']=str(exc);save(path,trace)
      finally:
        cleanup=None;absence=None;cleanup_error=None;absence_error=None
        cleanup_timeout=10
        if request_mono is not None:
            cleanup_age=time.monotonic()-request_mono
            cleanup_timeout=max(1,min(10,math.floor(
                protocol['timeline']['latest_cleanup_completion_seconds']-cleanup_age)))
            trace['cleanup_start_fault_age_seconds']=cleanup_age
            trace['cleanup_timeout_seconds']=cleanup_timeout
        try:cleanup=run(['kubectl','delete','stresschaos',name,'-n',namespace,'--ignore-not-found'],
                        context=admin,timeout=cleanup_timeout)
        except Exception as exc:cleanup_error={'type':type(exc).__name__,'error':str(exc)}
        try:absence=run(['kubectl','get','stresschaos',name,'-n',namespace,'-o','json'],
                        context=admin,timeout=3)
        except Exception as exc:absence_error={'type':type(exc).__name__,'error':str(exc)}
        absent=(absence is not None and absence['returncode']!=0 and
                ('NotFound' in absence['stderr'] or 'not found' in absence['stderr'].lower()))
        trace['cleanup']=cleanup;trace['cleanup_error']=cleanup_error;trace['cleanup_absence_probe']=absence
        trace['cleanup_absence_error']=absence_error;trace['cleanup_verified_absent']=absent
        cleanup_completion_age=(time.monotonic()-request_mono if request_mono is not None else None)
        trace['cleanup_completion_fault_age_seconds']=cleanup_completion_age
        cleanup_before_deadline=(cleanup_completion_age is None or cleanup_completion_age<
            protocol['timeline']['latest_cleanup_completion_seconds'])
        trace['cleanup_completed_before_deadline']=cleanup_before_deadline
        pods=None;runtime=None;post_cleanup_error=None
        try:_,pods,runtime=get_pods(scoped)
        except Exception as exc:post_cleanup_error={'type':type(exc).__name__,'error':str(exc)}
        trace['post_cleanup_pods']=pods;trace['post_cleanup_runtime']=runtime
        trace['post_cleanup_observation_error']=post_cleanup_error
        trace['runtime_identity_unchanged']=(runtime==block_runtime)
        if (cleanup_error is not None or cleanup is None or cleanup['returncode']!=0
                or absence_error is not None or not absent or not cleanup_before_deadline
                or post_cleanup_error is not None or runtime!=block_runtime):
            trace['status']='failed_cleanup_or_runtime'
        trace['completed_at']=time.time();save(path,trace)
    return trace['status']=='complete_expected'


def run_episode(**kwargs):
    """Fail-closed wrapper that always leaves a terminal episode artifact."""
    arm=kwargs['arm'];index=kwargs['index'];root=kwargs['root'];protocol=kwargs['protocol']
    namespace=protocol['namespace'];admin=kwargs['admin'];scoped=kwargs['scoped']
    name=f"e2r-matched-cpu-{'noaction' if arm=='no_action' else 'typed'}"
    path=root/f'{index:02d}_{arm}.json'
    try:
        return _run_episode_core(**kwargs)
    except BaseException as exc:
        try:trace=json.loads(path.read_text()) if path.exists() else {}
        except Exception:trace={}
        trace.update(schema='e2r_recovery_matched_episode_v1',episode_index=index,
                     arm=arm,namespace=namespace,service=protocol['service'],chaos_name=name,
                     status='failed_unhandled',unhandled_error_type=type(exc).__name__,
                     unhandled_error=str(exc))
        trace.setdefault('injection_attempted',False);trace.setdefault('injection_outcome_unknown',False)
        trace.setdefault('fault_injected',False);trace.setdefault('repair_process_attempted',False)
        trace.setdefault('repair_process_outcome_unknown',False);trace.setdefault('repair_executed',False)
        cleanup=None;absence=None;cleanup_error=None;absence_error=None
        try:cleanup=run(['kubectl','delete','stresschaos',name,'-n',namespace,'--ignore-not-found'],
                        context=admin,timeout=5)
        except BaseException as error:cleanup_error={'type':type(error).__name__,'error':str(error)}
        try:absence=run(['kubectl','get','stresschaos',name,'-n',namespace,'-o','json'],
                        context=admin,timeout=3)
        except BaseException as error:absence_error={'type':type(error).__name__,'error':str(error)}
        absent=(absence is not None and absence.get('returncode')!=0 and
                ('NotFound' in absence.get('stderr','') or 'not found' in absence.get('stderr','').lower()))
        try:_,pods,runtime=get_pods(scoped);runtime_error=None
        except BaseException as error:
            pods=None;runtime=None;runtime_error={'type':type(error).__name__,'error':str(error)}
        trace.update(emergency_cleanup=cleanup,emergency_cleanup_error=cleanup_error,
                     emergency_absence_probe=absence,emergency_absence_error=absence_error,
                     emergency_cleanup_verified_absent=absent,post_cleanup_pods=pods,
                     post_cleanup_runtime=runtime,post_cleanup_observation_error=runtime_error,
                     completed_at=time.time())
        try:save(path,trace)
        except Exception:pass
        return False


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out-dir',required=True)
    args=parser.parse_args();out=Path(args.out_dir)
    if out.exists():raise SystemExit('Refusing overwrite')
    protocol,protocol_sha=load_protocol()
    baseline_bytes=EXPECTED_BASELINE.read_bytes();manifest_bytes=EXPECTED_MANIFEST.read_bytes()
    bindings=validate_static_bindings(EXPECTED_BASELINE,baseline_bytes,EXPECTED_MANIFEST,manifest_bytes)
    if protocol['baseline_sha256']!=bindings['baseline_sha256'] or protocol['manifest_sha256']!=bindings['manifest_sha256']:
        raise ValueError('protocol artifact binding mismatch')
    scoped_path=EXPECTED_SCOPED_KUBECONFIG
    if not scoped_path.is_file() or scoped_path.stat().st_mode&0o077:raise RuntimeError('scoped kubeconfig unavailable')
    scoped=ClusterClientContext(str(scoped_path));admin=DEFAULT_CLUSTER_CONTEXT
    baseline_artifact=json.loads(baseline_bytes);baseline=WorkloadWindow(**baseline_artifact['baseline'])
    out.mkdir(parents=True);episodes=out/'episodes';episodes.mkdir()
    block={'schema':'e2r_recovery_matched_block_v1','status':'in_progress','protocol_path':str(PROTOCOL_PATH),
           'protocol_sha256':protocol_sha,**bindings,'arms_in_order':protocol['arms_in_order'],
           'planned_episode_count':len(protocol['arms_in_order']),
           'episode_attempts_started':0,'injection_attempts':0,'fault_injections':0,
           'injection_unknown_outcomes':0,'repair_process_invocations':0,
           'repair_process_unknown_outcomes':0,'repair_mutations':0,
           'started_at':time.time(),'episodes':[]}
    block_path=out/'block.json';save(block_path,block)
    rbac={}
    checks=[
        ('isolated_get',['kubectl','--kubeconfig',str(scoped_path),'auth','can-i','get','pods','-n',protocol['namespace']],True),
        ('isolated_delete_chaos',['kubectl','--kubeconfig',str(scoped_path),'auth','can-i','delete','stresschaos.chaos-mesh.org','-n',protocol['namespace']],True),
        ('isolated_portforward',['kubectl','--kubeconfig',str(scoped_path),'auth','can-i','create','pods','--subresource=portforward','-n',protocol['namespace']],True),
        ('user_namespace_denied',['kubectl','--kubeconfig',str(scoped_path),'auth','can-i','get','pods','-n','online-boutique'],False),
    ]
    for name,argv,expected in checks:
        try:result=subprocess.run(argv,capture_output=True,text=True,timeout=15,shell=False)
        except BaseException as exc:
            block.update(status='failed_rbac_query',rbac_error=name,
                         setup_error_type=type(exc).__name__,setup_error=str(exc),completed_at=time.time())
            save(block_path,block);raise SystemExit(2)
        answer=result.stdout.strip().lower()
        if answer=='yes' and result.returncode==0:observed=True
        elif answer=='no' and result.returncode==1:observed=False
        else:block.update(status='failed_rbac_query',rbac_error=name);save(block_path,block);raise SystemExit(2)
        rbac[name]=observed
        if observed!=expected:block.update(status='failed_rbac_policy',rbac_error=name);save(block_path,block);raise SystemExit(2)
    block['rbac_checks']=rbac;save(block_path,block)
    # Same scoped read-only executor preflight before either episode.
    preflight_plan=RepairPlan(actions=[RepairAction(action_type='WaitAndVerify',namespace=protocol['namespace'],
        command=f"kubectl get pods -n {protocol['namespace']}",risk='low')])
    try:preflight=run_ansible(playbook_generator_agent.render_playbook(preflight_plan,client_context=scoped),20)
    except BaseException as exc:
        block.update(status='failed_preflight',setup_error_type=type(exc).__name__,
                     setup_error=str(exc),completed_at=time.time());save(block_path,block);raise SystemExit(2)
    block['ansible_preflight']=preflight;save(block_path,block)
    if preflight['returncode']!=0 or 'frontend-' not in preflight['stdout']:
        block['status']='failed_preflight';save(block_path,block);raise SystemExit(2)
    try:_,pods,runtime=get_pods(scoped)
    except BaseException as exc:
        block.update(status='failed_initial_health',setup_error_type=type(exc).__name__,
                     setup_error=str(exc),completed_at=time.time());save(block_path,block);raise SystemExit(2)
    block['initial_pods']=pods;block['initial_runtime']=runtime;save(block_path,block)
    if runtime is None: block['status']='failed_initial_health';save(block_path,block);raise SystemExit(2)
    success=True
    for index,arm in enumerate(protocol['arms_in_order']):
        ok=run_episode(arm=arm,index=index,protocol=protocol,baseline=baseline,scoped=scoped,
                       admin=admin,block_runtime=runtime,root=episodes)
        block['episodes'].append({'index':index,'arm':arm,
            'path':str((episodes/f'{index:02d}_{arm}.json').resolve()),'completed_expected':ok})
        block['episode_attempts_started']=len(block['episodes'])
        episode_rows=[];accounting_error=None
        for item in block['episodes']:
            try:episode_rows.append(json.loads(Path(item['path']).read_text()))
            except Exception as exc:
                accounting_error={'path':item['path'],'type':type(exc).__name__,'error':str(exc)};break
        if accounting_error is not None:
            block.update(status='failed_accounting',accounting_error=accounting_error,
                         completed_at=time.time());save(block_path,block);success=False;break
        block['injection_attempts']=sum(row.get('injection_attempted') is True for row in episode_rows)
        block['fault_injections']=sum(row.get('fault_injected') is True for row in episode_rows)
        block['injection_unknown_outcomes']=sum(row.get('injection_outcome_unknown') is True for row in episode_rows)
        block['repair_process_invocations']=sum(row.get('repair_process_attempted') is True for row in episode_rows)
        block['repair_process_unknown_outcomes']=sum(row.get('repair_process_outcome_unknown') is True for row in episode_rows)
        block['repair_mutations']=sum(row.get('repair_executed') is True for row in episode_rows)
        block['oracle_statuses']=[row.get('oracle',{}).get('status') for row in episode_rows]
        save(block_path,block)
        if not ok:success=False;break
        if index+1<len(protocol['arms_in_order']):
            try:time.sleep(protocol['timeline']['washout_seconds'])
            except BaseException as exc:
                block.update(status='failed_washout',setup_error_type=type(exc).__name__,
                             setup_error=str(exc),completed_at=time.time());save(block_path,block);raise SystemExit(2)
    block['status']='complete_expected' if success and len(block['episodes'])==2 else 'failed'
    block['completed_at']=time.time();save(block_path,block)
    print(json.dumps({'status':block['status'],'episodes':block['episodes']},indent=2))
    if block['status']!='complete_expected':raise SystemExit(2)


if __name__=='__main__':main()
