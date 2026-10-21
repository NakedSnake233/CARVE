import copy
from pathlib import Path
from types import SimpleNamespace
from methods.E2R.intervention.context_policy import NativeContextPolicyBackend
from methods.E2R.intervention.schema import Plan,Action


def backend(tmp_path,monkeypatch,status='eligible'):
    import methods.E2R.intervention.context_policy as mod
    b=NativeContextPolicyBackend.__new__(NativeContextPolicyBackend)
    b.current_dir=tmp_path;b.context_snapshot={'status':status};b.policy=None
    b.model=SimpleNamespace(predict_features=lambda features,operation,blind=False:{'status':'ok','utility':.9 if operation!='no_action' else 0.,'success':.8,'safety':.9})
    b.provider=SimpleNamespace(probes=[]);b.client=SimpleNamespace(list=lambda kind:[])
    b.probe_accountant=SimpleNamespace(permit_write=lambda:True);b.observation_strategy_started_at=10.;b.acquired=[]
    monkeypatch.setattr(mod,'project_action_context',lambda graph,plan,policy,snapshot:{'status':snapshot['status'] if plan.actions else 'eligible','features':{},'reasons':[],'reference_status':{'status':'applicable'}})
    monkeypatch.setattr(mod,'goal_state',lambda *args:{'status':'pass'})
    monkeypatch.setattr(mod,'check_plan',lambda *args:[])
    return b


def plans():return [Plan('rollback',(Action('rollback_version','new',{'option':'known_good'},'approved_new'),)),Plan('no_action',())]


def test_shared_projection_gate_blocks_all_methods(tmp_path,monkeypatch):
    b=backend(tmp_path,monkeypatch,'blocked')
    for method in ('goal_reuse','goal_fresh','expert_fresh','blind_fresh'):
        r=next(r for r in b._rank(None,plans(),method) if r['plan_id']=='rollback')
        assert r['excluded'] and not r['feasible']


def test_unknown_is_not_treated_as_recovery_or_write_approval(tmp_path,monkeypatch):
    b=backend(tmp_path,monkeypatch,'unknown');b.method='goal_reuse';seen=[]
    b.capture_action_context=lambda graph,phase:seen.append(phase) or {'status':'unknown'}
    result=b.additional_authorization(None,plans()[0])
    assert seen==['prewrite'] and result['approved'] is False
    assert result['reason']=='action_context_unknown'


def test_prewrite_new_configuration_can_veto_previously_eligible_action(tmp_path,monkeypatch):
    b=backend(tmp_path,monkeypatch,'eligible');b.method='goal_fresh'
    assert b._rank(None,plans(),'goal_fresh')[0]['feasible']
    b.capture_action_context=lambda graph,phase:{'status':'blocked'}
    assert b.additional_authorization(None,plans()[0])['approved'] is False


def test_unknown_refresh_is_bounded_and_falls_back_without_write(tmp_path,monkeypatch):
    b=backend(tmp_path,monkeypatch,'unknown');refreshed=[]
    b._fresh_configuration=lambda graph,phase:refreshed.append(phase) or graph
    graph=SimpleNamespace(at=10.,to_dict=lambda:{})
    result=b.choose_plan(graph,plans(),'goal_reuse')
    assert refreshed==['decision_refresh']
    assert result['selected_plan']=='no_action' and not result['experimental_action_selected']
    assert result['context_refresh_used']


def test_model_unknown_does_not_suppress_independent_rule_with_valid_structure(tmp_path,monkeypatch):
    b=backend(tmp_path,monkeypatch)
    b.model=SimpleNamespace(predict_features=lambda *args,**kwargs:{'status':'unknown','utility':-1.})
    learned=next(r for r in b._rank(None,plans(),'goal_fresh') if r['plan_id']=='rollback')
    expert=next(r for r in b._rank(None,plans(),'expert_fresh') if r['plan_id']=='rollback')
    assert not learned['feasible'] and expert['feasible']


def test_assigned_context_api_clock_is_measured_and_stops_with_budget(tmp_path,monkeypatch):
    import json
    from methods.E2R.intervention.context_policy import ContextEvidenceBackend
    from methods.E2R.intervention.stateful_evidence import StatefulEvidenceBackend
    def init(self,config,out_dir):
        self.client=SimpleNamespace(_call=lambda *a,**k:{'ok':True},_call_cluster=lambda *a,**k:{'ok':True})
        self.probe_accountant=SimpleNamespace(closed=None,report=lambda:{'calls':0,'cost':0})
        self.current_dir=tmp_path
    monkeypatch.setattr(StatefulEvidenceBackend,'__init__',init)
    monkeypatch.setattr(StatefulEvidenceBackend,'execute',lambda *a,**k:{'status':'no_action'})
    b=ContextEvidenceBackend({},tmp_path)
    b.client._call(['get','deployments']);b.client._call_cluster(['get','nodes'])
    assert len(b.api_events)==2 and all(x['verb']=='get' for x in b.api_events)
    b.probe_accountant.closed=1;b.client._call(['get','pods']);assert len(b.api_events)==2
    result=b.execute(None,plans()[1],tmp_path)
    assert result['goal_policy_diagnostics']['api_calls']==2
    assert result['goal_policy_diagnostics']['api_reads']==2
    assert result['goal_policy_diagnostics']['api_seconds']>=0
    assert len(json.loads((tmp_path/'goal_policy_api_events.json').read_text()))==2
