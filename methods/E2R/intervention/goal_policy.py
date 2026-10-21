"""Registered laboratory goal policies with revision-checked probe reuse."""
import copy,json,time,itertools
from dataclasses import asdict
from pathlib import Path
from .stateful_evidence import StatefulEvidenceBackend,validate_state_goal
from .native import NativeBackend
from .policy_campaign import AccountedNativeProvider
from .goal_transition import GoalTransitionModel,goal_state
from .evidence_reuse import namespace_versions,check_reuse
from .contracts import check_plan,feasible,required_variables
from .platform import Executor
from .schema import EvidenceGraph,GraphObservation,merge_observation
from .campaign import append_json

METHODS=('goal_reuse','goal_fresh','expert_fresh','blind_fresh')

def retryable_expiry(result,applied):
    checks=result.get('refreshes',[])
    return (result.get('status')=='empirical_recheck_rejected' and not result.get('steps') and not applied
            and bool(checks) and checks[-1].get('empirical_recheck',{}).get('reason')=='reuse_expired_before_write')


def minimum_cover(probes,missing):
    def supplies(probe):
        out={(probe.entity,probe.variable)}
        for value in probe.possible_values:
            if isinstance(value,dict):out|={(f['entity'],f['variable']) for f in value.get('facts',[])}
        return out
    candidates=[p for p in probes if supplies(p)&missing]
    options=[]
    for n in range(1,len(candidates)+1):
        for subset in itertools.combinations(candidates,n):
            covered=set().union(*(supplies(p) for p in subset))
            if missing<=covered:options.append((sum(p.cost for p in subset),n,tuple(p.id for p in subset),subset))
    if not options:return []
    return list(min(options,key=lambda x:x[:3])[3])

class NativeGoalPolicyBackend(StatefulEvidenceBackend):
    observation_method='registered_goal_policy_comparison'
    def __init__(self,config,out_dir):
        delay=config.get('engineering_prewrite_delay_seconds',0)
        if type(delay) not in (int,float) or not 0<=delay<=15 or (delay and config.get('stage')!='goal_expiry_engineering_sanity'):
            raise ValueError('unregistered engineering delay')
        if type(config.get('retry_expired_reuse_once',False)) is not bool:raise ValueError('retry flag must be boolean')
        if (config.get('mode')!='goal_policy_comparison' or not set(config.get('arms',[]))<=set(METHODS)
            or not config.get('scheduled_jobs') or config.get('initial_probes')!=[] or config.get('monitor_execution_safety') is not True
            or config.get('goal_policy_authority')!='user_authorized_laboratory_only'
            or any(k in config for k in ('validation_profile','admission_ledger')) or any(len(p['actions'])>1 for p in config['plans'])):raise ValueError('unregistered goal policy laboratory configuration')
        registration=json.loads(Path(config['goal_registration']).read_text());release=json.loads(Path(config['goal_release']).read_text());runtime=str(Path(__file__).resolve().parents[3])
        assigned=[a for a in registration['assignments'] if Path(a['out']).resolve()==Path(out_dir).resolve()]
        if release.get('status')!='passed' or release.get('runtime')!=runtime or len(assigned)!=1 or json.loads(Path(assigned[0]['config']).read_text())!=config:raise ValueError('goal policy release/configuration mismatch')
        validate_state_goal(config)
        NativeBackend.__init__(self,config,out_dir)
        self.provider=AccountedNativeProvider(config['inventory'],self.client,self);self.probe_accountant=None;self.continuity_receipt=None
        self.model=self.load_transition_model(config['goal_transition_model']);self.api_events=[]
        (Path(out_dir)/'goal_model_snapshot.json').write_text(json.dumps(self.model.metadata,indent=2)+'\n')
        for method in ('_call','_call_cluster'):
            original=getattr(self.client,method)
            def measured(*args,_original=original,_method=method,**kwargs):
                start=time.perf_counter()
                try:return _original(*args,**kwargs)
                finally:
                    if self.probe_accountant is not None and self.probe_accountant.closed is None:
                        self.api_events.append({'kind':_method,'verb':str(args[0][0]) if args and args[0] else 'unknown','seconds':time.perf_counter()-start,'completed_at':time.time()})
            setattr(self.client,method,measured)

    @staticmethod
    def load_transition_model(path):
        return GoalTransitionModel.load(path)

    def additional_authorization(self,graph,plan):
        return None

    def observe(self,case):
        self.api_events=[];self.acquired=[];self.before_versions=None
        return super().observe(case)

    def _query(self,graph,probe,phase):
        self.probe_accountant.phase=phase;started=time.perf_counter();observation=self.provider.probe(probe,graph);stamp=time.time()
        record={'probe':asdict(probe),'observation':asdict(observation),'observation_type':'graph' if isinstance(observation,GraphObservation) else 'fact','seconds':time.perf_counter()-started}
        if phase=='adaptive':
            self.acquired.append({'probe_id':probe.id,'completed_at':stamp,'cost':probe.cost})
            self.query_observations.append(record)
        else:append_json(Path(self.current_dir)/'goal_mandatory_observations.jsonl',record)
        if isinstance(observation,GraphObservation):graph=merge_observation(graph,observation,stamp)
        else:graph=copy.deepcopy(graph);graph.facts.append(observation);graph.at=stamp;graph.__post_init__()
        return graph

    def _structure_probe(self):
        return min((p for p in self.provider.probes if p.kind=='native_graph' and any(v.get('relations') for v in p.possible_values if isinstance(v,dict))),key=lambda p:(p.cost,p.id))

    def _rank(self,graph,plans,method):
        rows=[]
        for plan in plans:
            forecast=self.model.predict(graph,plan,self.policy,blind=method=='blind_fresh');checks=check_plan(graph,plan,self.policy)
            excluded=any(c.status=='violated' for c in checks)
            if method!='blind_fresh' and plan.actions and forecast['goal']['status']=='fail':excluded=True
            score=forecast['utility']
            if method=='expert_fresh':score=(2. if plan.actions and plan.actions[0].operation=='restore_selector' else 1. if plan.actions else 0.)
            rows.append({'plan_id':plan.id,'score':score,'excluded':excluded,'checks':[asdict(c) for c in checks],
                'goal':forecast['goal'],'forecast':forecast,'feasible':feasible(checks,allow_deferred=False) and (method=='blind_fresh' or not plan.actions or forecast['goal']['status']=='pass')})
        return sorted(rows,key=lambda r:(r['excluded'],-r['score'],r['plan_id']))

    def choose_plan(self,graph,plans,method):
        if method not in METHODS:raise ValueError('unknown registered method')
        self.method=method;self.strategy_started_at=self.observation_strategy_started_at
        self.before_versions=namespace_versions(self.client) if method=='goal_reuse' else None;used=set()
        self.passive_observed_at=graph.at
        needs_structure=any(graph.read(p.actions[0].target,'dependencies_known') is None or (method!='blind_fresh' and goal_state(graph,p,self.policy)['status']=='unknown') for p in plans if p.actions)
        if needs_structure:
            structure=self._structure_probe();graph=self._query(graph,structure,'adaptive');used.add(structure.id)
        while True:
            rows=self._rank(graph,plans,method);best=next((r for r in rows if not r['excluded']),None)
            if best is None:break
            if best['feasible']:break
            missing={tuple(k) for c in best['checks'] for k in c['missing_variables']}
            probes=minimum_cover([p for p in self.provider.probes if p.id not in used],missing)
            if not probes:break
            chosen=probes[0];graph=self._query(graph,chosen,'adaptive');used.add(chosen.id)
        rows=self._rank(graph,plans,method);selected=next((r for r in rows if not r['excluded'] and r['feasible']),None)
        self.strategy_completed_at=time.time();self.decision_finished_at=self.strategy_completed_at
        result={'schema':'e2r_intervention_decision_v1','decision':'execute' if selected and next(p for p in plans if p.id==selected['plan_id']).actions else 'no_action' if selected else 'abstain',
            'selected_plan':selected['plan_id'] if selected else None,'graph':graph.to_dict(),'candidates':rows,'observations':list(self.acquired),
            'scope':'laboratory experimental comparison, not qualified production admission','method':method,
            'validation_profile_status':'not_qualified_laboratory_experiment','strict_production_eligible':False,'experimental_action_selected':bool(selected and next(p for p in plans if p.id==selected['plan_id']).actions)}
        self.last_decision=result;return result

    def execute(self,graph,plan,directory):
        self.execution_started_at=time.time();self.probe_accountant.phase='mandatory';certificate=None;force_fresh=False
        executor=Executor(self.client,self.provider,self.policy,Path(directory)/'execution_private.jsonl',convergence_timeout=self.config.get('convergence_timeout',60));self.executors.append(executor)
        original_get=self.client.get;delay_done=False
        def engineering_get(*args,**kwargs):
            nonlocal delay_done
            delay=self.config.get('engineering_prewrite_delay_seconds',0)
            if delay and not delay_done and certificate is not None and certificate['valid'] and not force_fresh:
                delay_done=True;start=time.time();time.sleep(delay)
                append_json(Path(directory)/'engineering_delay.jsonl',{'scope':'developmental first prewrite targetGET delay only','started_at':start,'completed_at':time.time(),'requested_seconds':delay})
            return original_get(*args,**kwargs)
        if self.config.get('engineering_prewrite_delay_seconds',0):self.client.get=engineering_get
        def refresh():
            nonlocal certificate
            if self.method=='goal_reuse' and not force_fresh:
                action=plan.actions[0];required=required_variables(graph,action);sources={action.target};historical=set()
                if action.operation=='restore_selector':required.add((action.destination,'dependencies_known'));sources.add(action.destination)
                elif action.operation=='rollback_version':historical.add(action.destination)
                certificate=check_reuse(self.client,graph,self.before_versions,[{'completed_at':self.passive_observed_at,'kind':'passive_configuration'},*self.acquired],max_age=self.config['evidence_reuse_max_age_seconds'],required_keys=required,historical_entities=historical,relation_sources=sources)
                append_json(Path(directory)/'evidence_reuse.jsonl',certificate)
                if certificate['valid']:
                    current=copy.deepcopy(graph);current.at=time.time();current.__post_init__();return current
            if self.method=='expert_fresh':
                current=self.provider.collect();used=set()
                while True:
                    checks=check_plan(current,plan,self.policy)
                    missing={tuple(k) for c in checks for k in c.missing_variables}
                    if not missing:break
                    probes=minimum_cover([p for p in self.provider.probes if p.id not in used],missing)
                    if not probes:break
                    probe=probes[0];current=self._query(current,probe,'mandatory');used.add(probe.id)
            else:current=self.provider.refresh(graph,plan.actions[0])
            # Every goal-aware fresh policy rechecks goal-support relations too.
            if self.method!='blind_fresh' and goal_state(current,plan,self.policy)['status']=='unknown':
                current=self._query(current,self._structure_probe(),'mandatory')
            return current
        def authorize(current,remaining):
            witness=goal_state(current,remaining,self.policy)
            goal_ok=self.method=='blind_fresh' or witness['status']=='pass'
            reason=None if goal_ok else 'goal_not_verified'
            additional=self.additional_authorization(current,remaining) if goal_ok else None
            if additional is not None and additional.get('approved') is not True:
                goal_ok=False;reason=additional.get('reason','context_not_verified')
            if goal_ok and certificate is not None and certificate['valid'] and time.time()>certificate['latest_allowed_write_at']:
                goal_ok=False;reason='reuse_expired_before_write'
            approved=goal_ok and self.probe_accountant.permit_write()
            return {'approved':approved,'reason':reason if reason else None if approved else 'budget_rejected','goal':witness,'authority':'registered_goal_policy_laboratory_only','reuse_certificate':certificate,**({'action_context':additional} if additional is not None else {})}
        try:
            result=executor.execute(graph,plan,dry_run=False,refresh=refresh,authorize=authorize)
            if self.config.get('retry_expired_reuse_once',False) and self.method=='goal_reuse' and retryable_expiry(result,executor.applied):
                initial=result;force_fresh=True;certificate=None
                result=executor.execute(graph,plan,dry_run=False,refresh=refresh,authorize=authorize)
                result['expired_reuse_retry']={'maximum_retries':1,'initial_execution':initial,'same_accountant_and_deadline':True,'scope':'no writes before retry; one fresh refresh with original goal/CAS/budget checks'}
            result['goal_policy_diagnostics']={'method':self.method,'api_calls':len(self.api_events),'api_reads':sum(e['verb']=='get' for e in self.api_events),'api_call_scope':'budget start through controller convergence; writes included in calls but not reads','api_seconds':sum(x['seconds'] for x in self.api_events),
                'registered_probe_report':self.probe_accountant.report(),'reuse_certificate':certificate}
            return result
        finally:
            self.client.get=original_get
            self.probe_accountant.close();(Path(directory)/'goal_policy_api_events.json').write_text(json.dumps(self.api_events,indent=2)+'\n')

    def restore(self):
        try:return super().restore()
        finally:
            if getattr(self,'current_dir',None):
                (Path(self.current_dir)/'goal_policy_api_events.json').write_text(json.dumps(self.api_events,indent=2)+'\n')

    def stop_reason(self,row):
        if row.get('execution',{}).get('status') in ('execution_unknown','write_deviation'):
            return 'uncertain_native_write_requires_inspection_before_next_slot'
        return None
