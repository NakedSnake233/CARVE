import json
import time

import scripts.e2r.run_recovery_matched_block as matched
from rca_bench.recovery_oracle import WorkloadWindow
from scripts.e2r.run_recovery_matched_block import active_chaos,load_protocol
from methods.PlanRCAThinkRemed.cluster_client import ClusterClientContext


def test_protocol_is_fixed_two_arm_no_retry_block():
    protocol,digest=load_protocol()
    assert digest=='b01798e4174651abf550bfe4f5f7f8d84c48c414e904ad5e26a5f2e8c8e7032d'
    assert protocol['arms_in_order']==['no_action','typed_cleanup']
    assert protocol['episodes_per_arm']==1
    assert protocol['fault']['duration_seconds']==180
    assert protocol['timeline']['latest_final_observation_seconds']==165
    assert protocol['timeline']['latest_technical_observation_seconds']==170
    assert protocol['timeline']['technical_probe_timeout_seconds']==3
    assert protocol['timeline']['latest_cleanup_completion_seconds']==175
    assert protocol['retry_policy'].startswith('none;')


def test_active_chaos_requires_selector_and_injected_not_recovered():
    value={'spec':{'selector':{'labelSelectors':{'app':'frontend'}}},
           'status':{'conditions':[{'type':'AllInjected','status':'True'},
                                   {'type':'AllRecovered','status':'False'}]}}
    assert active_chaos(value,'frontend')
    changed=json.loads(json.dumps(value));changed['status']['conditions'][1]['status']='True'
    assert not active_chaos(changed,'frontend')
    assert not active_chaos(value,'cartservice')


def test_episode_wrapper_terminalizes_unhandled_setup_and_cleanup(monkeypatch,tmp_path):
    protocol,_=load_protocol();root=tmp_path/'episodes';root.mkdir()
    monkeypatch.setattr(matched,'_run_episode_core',lambda **_kwargs:(_ for _ in ()).throw(
        TimeoutError('port-forward setup timed out')))
    def fake_run(argv,**_kwargs):
        if argv[1]=='delete':return {'returncode':0,'stdout':'deleted','stderr':''}
        return {'returncode':1,'stdout':'','stderr':'Error from server (NotFound)'}
    monkeypatch.setattr(matched,'run',fake_run)
    monkeypatch.setattr(matched,'get_pods',lambda _context:(_ for _ in ()).throw(
        RuntimeError('post-cleanup read failed')))
    ok=matched.run_episode(arm='no_action',index=0,protocol=protocol,baseline=None,
        scoped=object(),admin=object(),block_runtime={},root=root)
    assert not ok
    trace=json.loads((root/'00_no_action.json').read_text())
    assert trace['status']=='failed_unhandled'
    assert trace['emergency_cleanup_verified_absent'] is True
    assert trace['post_cleanup_observation_error']['type']=='RuntimeError'


def test_mocked_no_action_and_typed_episodes_complete_expected(monkeypatch,tmp_path):
    protocol,_=load_protocol();state={'active':False,'uid':'uid-fixed','arm':None}
    namespace=protocol['namespace'];service=protocol['service']
    def chaos_object(name):
        return {'apiVersion':'chaos-mesh.org/v1alpha1','kind':'StressChaos',
            'metadata':{'name':name,'namespace':namespace,'uid':state['uid']},
            'spec':{'selector':{'labelSelectors':{'app':service}}},
            'status':{'conditions':[{'type':'AllInjected','status':'True'},
                                    {'type':'AllRecovered','status':'False'}]}}
    def fake_run(argv,**_kwargs):
        if argv[1]=='apply':state['active']=True;return {'returncode':0,'stdout':'created','stderr':''}
        if argv[1]=='delete':state['active']=False;return {'returncode':0,'stdout':'deleted','stderr':''}
        if argv[1]=='get' and argv[2]=='stresschaos':
            if state['active']:return {'returncode':0,'stdout':json.dumps(chaos_object(argv[3])),'stderr':''}
            return {'returncode':1,'stdout':'','stderr':'Error from server (NotFound)'}
        raise AssertionError(argv)
    monkeypatch.setattr(matched,'run',fake_run)
    class Forward:
        startup_log=['Forwarding from 127.0.0.1:1 -> 80']
        def __init__(self,**_kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*_args):return None
    monkeypatch.setattr(matched,'KubectlPortForward',Forward)
    monkeypatch.setattr(matched,'choose_port',lambda:18080)
    monkeypatch.setattr(matched.time,'sleep',lambda _seconds:None)
    def fake_measure(_collector,window_id):
        end=time.time();start=end-1e-6
        phase=('pre' if window_id.endswith('-pre') else
               'fault' if 'fault-validation' in window_id else 'evaluation')
        p95=100 if phase=='pre' else 400 if phase=='fault' or state['arm']=='no_action' else 90
        window=WorkloadWindow(samples=30,throughput_rps=9,error_rate=0,p95_ms=p95,
            state_canary_ok=True,window_id=window_id,source_id='source',start_time=start,end_time=end)
        return window,{'attempts':30,'successes':30,'latencies_ms':[p95]*30}
    monkeypatch.setattr(matched,'measure',fake_measure)
    def fake_preconditions(plan,**_kwargs):
        name=plan.actions[0].target_name;obj=chaos_object(name)
        return {'status':'PASS','passed':True,'results':[{'stdout':json.dumps(obj)}],
                'captures':[{'target':{'name':name}}],'client_context':{}}
    monkeypatch.setattr(matched.safety_guard,'run_preconditions',fake_preconditions)
    def fake_ansible(_playbook,_timeout):
        state['active']=False
        return {'returncode':0,'stdout':'deleted','stderr':'','seconds':.1}
    monkeypatch.setattr(matched,'run_ansible',fake_ansible)
    runtime={'pod-uid':{'server':0,'sidecar-busybox':0}}
    monkeypatch.setattr(matched,'get_pods',lambda _context:({'returncode':0},{'items':[]},runtime))
    def fake_technical(plan,**_kwargs):
        name=plan.actions[0].target_name
        if state['active']:
            row={'returncode':0,'stdout':json.dumps(chaos_object(name)),'stderr':''}
            absent=False
        else:
            row={'returncode':1,'stdout':'','stderr':'Error from server (NotFound)'}
            absent=True
        return {'checks':{'fault_absent':absent,'resource_restored':True,'ready_endpoints':True},
                'probes':[{'command':'get chaos','results':[row]}]}
    monkeypatch.setattr(matched,'collect_technical_checks',fake_technical)
    baseline=WorkloadWindow(samples=100,throughput_rps=10,error_rate=0,p95_ms=100,
        state_canary_ok=True,window_id='baseline',source_id='source',
        start_time=time.time()-200,end_time=time.time()-100)
    root=tmp_path/'episodes';root.mkdir()
    for index,arm in enumerate(protocol['arms_in_order']):
        state.update(active=False,arm=arm)
        assert matched.run_episode(arm=arm,index=index,protocol=protocol,baseline=baseline,
            scoped=ClusterClientContext('/tmp/fake'),admin=object(),block_runtime=runtime,root=root)
        trace=json.loads((root/f'{index:02d}_{arm}.json').read_text())
        assert trace['status']=='complete_expected'
        assert trace['cleanup_verified_absent'] is True
        assert len(trace['evaluation_windows'])==3
    no_action=json.loads((root/'00_no_action.json').read_text())
    typed=json.loads((root/'01_typed_cleanup.json').read_text())
    assert no_action['oracle']['status']=='FAIL'
    assert no_action['no_action_active_at_technical_observation'] is True
    assert typed['oracle']['status']=='PASS' and typed['repair_executed'] is True
    assert no_action['injection_attempted'] is True and no_action['repair_process_attempted'] is False
    assert typed['injection_attempted'] is True and typed['repair_process_attempted'] is True
    assert not no_action['injection_outcome_unknown'] and not typed['repair_process_outcome_unknown']

    state.update(active=False,arm='typed_cleanup')
    monkeypatch.setattr(matched,'run_ansible',lambda *_args,**_kwargs:(_ for _ in ()).throw(
        TimeoutError('execution outcome unknown')))
    assert not matched.run_episode(arm='typed_cleanup',index=2,protocol=protocol,
        baseline=baseline,scoped=ClusterClientContext('/tmp/fake'),admin=object(),
        block_runtime=runtime,root=root)
    timed=json.loads((root/'02_typed_cleanup.json').read_text())
    assert timed['repair_process_attempted'] is True
    assert timed['repair_process_outcome_unknown'] is True
    assert timed['repair_executed'] is False
