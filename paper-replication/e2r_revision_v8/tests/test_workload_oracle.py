import math
import time

from methods.PlanRCAThinkRemed.cluster_client import DEFAULT_CLUSTER_CONTEXT
from methods.PlanRCAThinkRemed.schemas import RepairAction,RepairPlan,RCAReport
from rca_bench.recovery_oracle import WorkloadWindow
from rca_bench import workload_oracle


def test_http_window_collector_records_identity_and_latency():
    collector=workload_oracle.HTTPWindowCollector(
        url='http://127.0.0.1:1/',source_id='synthetic-source',
        request_count=20,workers=4,state_canary=lambda:True,
        request_fn=lambda _url,_timeout:(True,12.5))
    window,raw=collector.collect('window-1')
    assert window.samples==20 and window.error_rate==0
    assert window.p95_ms==12.5 and window.state_canary_ok is True
    assert window.window_id=='window-1' and window.source_id=='synthetic-source'
    assert window.start_time < window.end_time
    assert raw['successes']==20 and len(raw['latencies_ms'])==20


def test_invalid_latency_does_not_shrink_the_p95_denominator():
    calls={'n':0}
    def request(_url,_timeout):
        calls['n']+=1
        return True,(10.0 if calls['n']==1 else float('nan'))
    collector=workload_oracle.HTTPWindowCollector(
        url='http://127.0.0.1:1/',source_id='synthetic-source',
        request_count=20,workers=1,request_fn=request)
    window,raw=collector.collect('invalid-latency')
    assert math.isnan(window.p95_ms)
    assert raw['valid_latency_samples']==1


def test_duration_mode_schedules_a_fixed_offered_load(monkeypatch):
    clock={'wall':100.0,'mono':50.0}
    monkeypatch.setattr(workload_oracle.time,'time',lambda:clock['wall'])
    monkeypatch.setattr(workload_oracle.time,'perf_counter',lambda:clock['mono'])
    def sleep(seconds):
        clock['mono']+=seconds;clock['wall']+=seconds
    monkeypatch.setattr(workload_oracle.time,'sleep',sleep)
    collector=workload_oracle.HTTPWindowCollector(
        url='http://x',source_id='duration',workers=1,window_duration_seconds=3,
        offered_rps=2,request_fn=lambda _url,_timeout:(True,1.0))
    window,raw=collector.collect('duration-window')
    assert window.samples==6 and raw['offered_rps']==2
    assert raw['target_window_seconds']==3
    assert window.end_time-window.start_time==3


def test_port_forward_refuses_a_preexisting_listener(monkeypatch):
    class OccupiedSocket:
        def __enter__(self):return self
        def __exit__(self,*_args):return None
        def bind(self,_address):raise OSError('occupied')
    monkeypatch.setattr(workload_oracle.socket,'socket',lambda *_a,**_k:OccupiedSocket())
    forward=workload_oracle.KubectlPortForward(
        namespace='test',service='frontend',local_port=18080,startup_timeout=.1)
    import pytest
    with pytest.raises(RuntimeError,match='already in use'):
        forward.__enter__()


def test_business_callback_requires_fresh_distinct_windows_and_technical_checks(monkeypatch):
    now=time.time()
    baseline=WorkloadWindow(samples=30,throughput_rps=100,error_rate=0,p95_ms=15,
        state_canary_ok=True,window_id='baseline',source_id='synthetic-source',
        start_time=now-20,end_time=now-10)
    collector=workload_oracle.HTTPWindowCollector(
        url='http://127.0.0.1:1/',source_id='synthetic-source',
        request_count=20,workers=4,state_canary=lambda:True,
        request_fn=lambda _url,_timeout:(True,10.0))
    monkeypatch.setattr(workload_oracle,'collect_technical_checks',lambda *a,**k:{
        'fault_absent':True,'resource_restored':True,'ready_endpoints':True})
    callback=workload_oracle.build_business_oracle(
        collector=collector,baseline=baseline,plan=RepairPlan(),
        inter_window_seconds=0,require_state_canary=True)
    result=callback(repair_completed_at=now-1,
                    client_context=DEFAULT_CLUSTER_CONTEXT)
    assert result['status']=='PASS' and result['success']
    assert len(result['windows'])==3
    assert result['measurement_freshness_valid']


def test_technical_checks_require_exact_command_context_and_object_identity(monkeypatch):
    namespace='e2r-supplement-20260925';service='cartservice'
    action=RepairAction(action_type='RecoverConfigError',namespace=namespace,
        target_kind='Deployment',target_name=service,target_container='server')
    plan=RepairPlan(rca_report=RCAReport(root_cause_service=service),actions=[action])
    deployment={'apiVersion':'apps/v1','kind':'Deployment',
        'metadata':{'name':service,'namespace':namespace},
        'spec':{'replicas':1,'template':{'spec':{'containers':[{'name':'server',
            'resources':{'limits':{'cpu':'500m','memory':'512Mi'},
                         'requests':{'cpu':'100m','memory':'128Mi'}}}]}}},
        'status':{'readyReplicas':1}}
    endpoints={'apiVersion':'v1','kind':'Endpoints',
        'metadata':{'name':service,'namespace':namespace},
        'subsets':[{'addresses':[{'ip':'10.0.0.1'}]}]}
    def runner(commands,**_kwargs):
        command=commands[0];value=endpoints if 'endpoints' in command else deployment
        return [{'command':command,'allowed':True,'returncode':0,'timed_out':False,
                 'stdout':__import__('json').dumps(value),'stderr':'',
                 'client_context':DEFAULT_CLUSTER_CONTEXT.to_dict(),'output':'structured'}]
    monkeypatch.setattr(workload_oracle,'run_probes',runner)
    assert workload_oracle.collect_technical_checks(plan)=={
        'fault_absent':True,'resource_restored':True,'ready_endpoints':True}
    package=workload_oracle.collect_technical_checks(plan,include_evidence=True)
    assert package['checks']=={'fault_absent':True,'resource_restored':True,
                               'ready_endpoints':True}
    assert len(package['probes'])==3
    assert all(probe['results'][0]['command']==probe['command'] for probe in package['probes'])

    def wrong_identity(commands,**_kwargs):
        rows=runner(commands)
        value=__import__('json').loads(rows[0]['stdout'])
        value['metadata']['name']='frontend'
        rows[0]['stdout']=__import__('json').dumps(value)
        return rows
    monkeypatch.setattr(workload_oracle,'run_probes',wrong_identity)
    assert all(value is None for value in
        workload_oracle.collect_technical_checks(plan).values())

    def wrong_context(commands,**_kwargs):
        rows=runner(commands);rows[0]['client_context']={'kubeconfig':'/tmp/other'}
        return rows
    monkeypatch.setattr(workload_oracle,'run_probes',wrong_context)
    assert all(value is None for value in
        workload_oracle.collect_technical_checks(plan).values())


def test_baseline_cli_rejects_nonisolated_namespace(monkeypatch,tmp_path):
    import scripts.e2r.run_recovery_baseline as baseline
    monkeypatch.setattr('sys.argv',['run_recovery_baseline.py','--namespace','online-boutique',
        '--out-dir',str(tmp_path/'out')])
    import pytest
    with pytest.raises(ValueError,match='restricted to isolated namespace'):
        baseline.main()


def test_chaos_absence_retains_raw_probe_evidence(monkeypatch):
    namespace='e2r-supplement-20260925';service='frontend';name='cpu-test'
    action=RepairAction(action_type='RemoveInjectedStress',namespace=namespace,
        target_kind='StressChaos',target_name=name)
    plan=RepairPlan(rca_report=RCAReport(root_cause_service=service),actions=[action])
    deployment={'kind':'Deployment','metadata':{'name':service,'namespace':namespace},
                'spec':{'replicas':1},'status':{'readyReplicas':1}}
    endpoints={'kind':'Endpoints','metadata':{'name':service,'namespace':namespace},
               'subsets':[{'addresses':[{'ip':'10.0.0.1'}]}]}
    def runner(commands,**kwargs):
        command=commands[0]
        base={'command':command,'allowed':True,'timed_out':False,
              'client_context':DEFAULT_CLUSTER_CONTEXT.to_dict(),'output':'structured'}
        if 'stresschaos' in command:
            return [{**base,'returncode':1,'stdout':'','stderr':'Error from server (NotFound)'}]
        value=endpoints if 'endpoints' in command else deployment
        return [{**base,'returncode':0,'stdout':__import__('json').dumps(value),'stderr':''}]
    monkeypatch.setattr(workload_oracle,'run_probes',runner)
    package=workload_oracle.collect_technical_checks(plan,include_evidence=True,probe_timeout=3)
    assert package['checks']=={'fault_absent':True,'resource_restored':True,'ready_endpoints':True}
    assert package['probes'][0]['results'][0]['returncode']==1
    assert 'NotFound' in package['probes'][0]['results'][0]['stderr']
