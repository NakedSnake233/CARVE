"""Action-conditioned observations and common structural gates for laboratory studies."""
import copy
import json
import time
from dataclasses import asdict
from pathlib import Path
from .action_context import capture_configuration,project_action_context
from .campaign import append_json
from .contracts import check_plan,feasible
from .schema import input_bundle
from .evidence_reuse import namespace_versions
from .goal_policy import NativeGoalPolicyBackend,METHODS,minimum_cover
from .goal_transition import goal_state
from .schema import GraphObservation,merge_observation
from .stateful_evidence import StatefulEvidenceBackend


def write_json(path,value):
    Path(path).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')


class ContextEvidenceMixin:
    def baseline_complete(self,case):
        super().baseline_complete(case)
        graph=self.provider.collect()
        self.provider.references['baseline_context']=self.timed_call('capture_baseline_context',capture_configuration,self.client,graph,reference=None)
        write_json(self.current_dir/'context_version_reference.json',self.provider.references)

    def capture_action_context(self,graph,phase):
        snapshot=self.timed_call('capture_action_context_'+phase,capture_configuration,self.client,graph,reference=self.provider.references)
        append_json(self.current_dir/'action_context_events.jsonl',{'phase':phase,'recorded_at':time.time(),'snapshot':snapshot})
        return snapshot

    def observe(self,case):
        bundle=super().observe(case)
        graph,plans=input_bundle(bundle)
        self.context_snapshot=self.capture_action_context(graph,'initial')
        write_json(self.current_dir/'action_context_before.json',self.context_snapshot)
        candidates={plan.id:project_action_context(graph,plan,self.policy,self.context_snapshot) for plan in plans}
        write_json(self.current_dir/'action_context_candidates.json',{'schema':'action_context_candidates_v1','candidates':candidates})
        return bundle


class ContextEvidenceBackend(ContextEvidenceMixin,StatefulEvidenceBackend):
    """Assigned experiments measure outcomes even for predicted ineffective actions."""
    def __init__(self,config,out_dir):
        super().__init__(config,out_dir)
        self.api_events=[]
        for method in ('_call','_call_cluster'):
            original=getattr(self.client,method)
            def measured(*args,_original=original,_method=method,**kwargs):
                started=time.perf_counter()
                try:return _original(*args,**kwargs)
                finally:
                    if self.probe_accountant is not None and self.probe_accountant.closed is None:
                        self.api_events.append({'kind':_method,'verb':str(args[0][0]) if args and args[0] else 'unknown',
                                                'seconds':time.perf_counter()-started,'completed_at':time.time()})
            setattr(self.client,method,measured)

    def start(self,case,directory):
        self.api_events=[]
        return super().start(case,directory)

    def execute(self,graph,plan,directory):
        try:
            result=super().execute(graph,plan,directory)
            result['goal_policy_diagnostics']={'method':'assigned_context_evidence','api_calls':len(self.api_events),
                'api_reads':sum(e['verb']=='get' for e in self.api_events),'api_seconds':sum(e['seconds'] for e in self.api_events),
                'api_call_scope':'probe budget start through execution close; baseline/verification/cleanup outside this API clock',
                'registered_probe_report':self.probe_accountant.report()}
            return result
        finally:
            write_json(Path(directory)/'goal_policy_api_events.json',self.api_events)

    def restore(self):
        try:return super().restore()
        finally:
            if getattr(self,'current_dir',None):write_json(self.current_dir/'goal_policy_api_events.json',self.api_events)


class NativeContextPolicyBackend(ContextEvidenceMixin,NativeGoalPolicyBackend):
    @staticmethod
    def load_transition_model(path):
        from .context_transition import ContextTransitionModel
        return ContextTransitionModel.load(path)

    def _rank(self,graph,plans,method):
        rows=[]
        for plan in plans:
            projection=project_action_context(graph,plan,self.policy,self.context_snapshot)
            operation=plan.actions[0].operation if plan.actions else 'no_action'
            forecast=self.model.predict_features(projection['features'],operation,blind=method=='blind_fresh')
            witness=goal_state(graph,plan,self.policy)
            checks=check_plan(graph,plan,self.policy)
            excluded=any(c.status=='violated' for c in checks) or bool(plan.actions and (projection['status']=='blocked' or witness['status']=='fail'))
            model_known=forecast.get('status')=='ok' or method=='expert_fresh' or not plan.actions
            score=forecast.get('utility',-1.)
            if method=='expert_fresh':score=2. if operation=='restore_selector' else 1. if plan.actions else 0.
            rows.append({'plan_id':plan.id,'score':score,'excluded':excluded,'checks':[asdict(c) for c in checks],
                         'goal':witness,'forecast':forecast,'action_context':projection,
                         'feasible':feasible(checks,allow_deferred=False) and (not plan.actions or projection['status']=='eligible' and witness['status']=='pass' and model_known)})
        return sorted(rows,key=lambda r:(r['excluded'],-r['score'],r['plan_id']))

    def _fresh_configuration(self,graph,phase):
        current=self.provider.collect()
        graph=merge_observation(graph,GraphObservation(current.entities,current.facts,current.relations),current.at)
        self.context_snapshot=self.capture_action_context(graph,phase)
        return graph

    def choose_plan(self,graph,plans,method):
        if method not in METHODS:raise ValueError('unknown context method')
        self.method=method;self.strategy_started_at=self.observation_strategy_started_at
        self.before_versions=namespace_versions(self.client) if method=='goal_reuse' else None
        self.passive_observed_at=graph.at;used=set();fresh_done=False
        for _ in range(len(self.provider.probes)+2):
            rows=self._rank(graph,plans,method)
            best=next((r for r in rows if not r['excluded']),None)
            if best is None:break
            if best['feasible']:
                unresolved=[r for r in rows if not r['excluded'] and r['action_context']['status']=='unknown']
                if best['plan_id']=='no_action' and unresolved and not fresh_done and self.probe_accountant.permit_write():
                    graph=self._fresh_configuration(graph,'decision_refresh');fresh_done=True;continue
                break
            missing={tuple(k) for c in best['checks'] for k in c['missing_variables']}
            probes=minimum_cover([p for p in self.provider.probes if p.id not in used],missing)
            if probes:
                probe=probes[0];graph=self._query(graph,probe,'adaptive');used.add(probe.id)
                continue
            if best['action_context']['status']=='unknown' and not fresh_done and self.probe_accountant.permit_write():
                graph=self._fresh_configuration(graph,'decision_refresh');fresh_done=True;continue
            break
        rows=self._rank(graph,plans,method)
        selected=next((r for r in rows if not r['excluded'] and r['feasible']),None)
        self.strategy_completed_at=time.time();self.decision_finished_at=self.strategy_completed_at
        action=bool(selected and next(p for p in plans if p.id==selected['plan_id']).actions)
        result={'schema':'e2r_intervention_decision_v1','decision':'execute' if action else 'no_action' if selected else 'abstain',
                'selected_plan':selected['plan_id'] if selected else None,'graph':graph.to_dict(),'candidates':rows,'observations':list(self.acquired),
                'scope':'State-conditioned laboratory comparison, structural gates shared across all methods; not production qualification',
                'method':method,'validation_profile_status':'not_qualified_laboratory_experiment','strict_production_eligible':False,
                'experimental_action_selected':action,'context_refresh_used':fresh_done}
        self.last_decision=result
        write_json(self.current_dir/'action_context_decision.json',{'snapshot':self.context_snapshot,'candidates':{r['plan_id']:r['action_context'] for r in rows}})
        return result

    def additional_authorization(self,graph,plan):
        # Fresh configuration evidence is mandatory even if dynamic probe results
        # can be reused. Parent rechecks reuse expiry *after* this timed I/O.
        snapshot=self.capture_action_context(graph,'prewrite')
        projection=project_action_context(graph,plan,self.policy,snapshot)
        operation=plan.actions[0].operation if plan.actions else 'no_action'
        forecast=self.model.predict_features(projection['features'],operation,blind=self.method=='blind_fresh')
        approved=projection['status']=='eligible' and (self.method=='expert_fresh' or forecast.get('status')=='ok')
        result={'approved':approved,'reason':None if approved else 'context_model_unknown' if projection['status']=='eligible' else 'action_context_'+projection['status'],
                'projection':projection,'forecast':forecast,'scope':'Fresh observed configuration + approved action projection; not measured post-action health'}
        append_json(self.current_dir/'action_context_authorization.jsonl',{'checked_at':time.time(),**result})
        return result
