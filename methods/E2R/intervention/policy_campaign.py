"""Live, independently reset policy comparisons using the same native executor."""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path

from .predictors import load_effect_predictor
from .native import NativeBackend, NativeProvider
from .platform import Executor
from .campaign import append_json
from .probe_budget import ProbeAccountant, refresh_reserve
from .bounded_decision import budgeted_decision
from .runtime import (ObservationBudget, apply_profile_status, execution_deviations,
                      rank_candidates, refine_and_decide, validated_policy)
from .schema import GraphObservation


class AccountedNativeProvider(NativeProvider):
    def probe(self, spec, graph):
        account=getattr(self.study,'probe_accountant',None)
        if account is None:return super().probe(spec,graph)
        return account.invoke(spec,super().probe,spec,graph)


class NativePolicyBackend(NativeBackend):
    def __init__(self, config, out_dir):
        super().__init__(config,out_dir)
        self.probe_accountant=None
        self.provider=AccountedNativeProvider(config['inventory'],self.client,self)
        self.predictor=load_effect_predictor(config['model'], seeds=config.get('prediction_seeds'),
            admission_path=config.get('admission_ledger'))
        profile=json.loads(Path(config['validation_profile']).read_text())
        self.policy,self.profile_error=validated_policy(self.policy,self.predictor,profile,
            run_config=config,out_directory=out_dir,runtime=Path(__file__).resolve().parents[3])
        self.budget=ObservationBudget(**config['observation_budget'])
        if config.get('total_probe_budget') is not None and self.predictor.metadata.get('schema')!='e2r_intervention_training_v2':
            self.profile_error=self.profile_error or 'total_probe_budget_requires_scoped_policy_validation'
        if self.profile_error is not None:
            raise ValueError('campaign not qualified before setup: '+self.profile_error)

    def stop_reason(self,row):
        if self.predictor.metadata.get('schema')!='e2r_intervention_training_v2':return None
        if row.get('execution',{}).get('status') not in ('executed','no_action'):
            return 'bounded_policy_execution_unmeasured_rejected_or_uncertain'
        if row.get('outcome',{}).get('effects',{}).get('non_target_safe') is not True:
            return 'bounded_policy_peer_safety_failed_or_unknown'
        return None

    def start(self, case, directory):
        self.probe_accountant=None
        super().start(case,directory)

    def observe(self, case):
        if self.profile_error is not None:
            raise ValueError('strategy not qualified: '+self.profile_error)
        if self.config.get('total_probe_budget') is not None:
            self.probe_accountant=ProbeAccountant(ObservationBudget(**self.config['total_probe_budget']),
                record=lambda event:append_json(self.current_dir/'registered_probe_events.jsonl',event))
        return super().observe(case)

    def choose_plan(self, graph, plans, strategy):
        self.strategy_started_at=self.observation_strategy_started_at
        def observe(probe,current):
            started=time.perf_counter()
            observation=self.provider.probe(probe,current)
            self.query_observations.append({'probe':asdict(probe),'observation':asdict(observation),
                'observation_type':'graph' if isinstance(observation,GraphObservation) else 'fact',
                'seconds':time.perf_counter()-started})
            return observation
        result=budgeted_decision(graph,plans,self.policy,self.predictor,self.provider,observe,
            budget=self.budget,account=self.probe_accountant,strategy=strategy,seed=self.config['seed'],
            fixed_order=self.config.get('fixed_probe_order'))
        apply_profile_status(result,self.profile_error)
        self.last_decision=result
        self.strategy_completed_at=time.time()
        return result

    def execute(self, graph, plan, directory):
        self.execution_started_at=time.time()
        if self.probe_accountant is not None:self.probe_accountant.phase='mandatory'
        executor=Executor(self.client,self.provider,self.policy,Path(directory)/'execution_private.jsonl',
                          convergence_timeout=self.config.get('convergence_timeout',60))
        self.executors.append(executor)
        def authorize(current, remaining):
            row=rank_candidates(current,[remaining],self.policy,self.predictor)[0]
            budget_ok=self.probe_accountant is None or self.probe_accountant.permit_write()
            return {'approved':row['executable'] and self.profile_error is None and budget_ok,'candidate':row,
                    'total_probe_budget_allows_write':budget_ok,
                    'validation_profile_error':self.profile_error}
        try:
            return executor.execute(graph,plan,dry_run=False,authorize=authorize)
        finally:
            if self.probe_accountant is not None:self.probe_accountant.close()

    def restore(self):
        if self.probe_accountant is not None:self.probe_accountant.close()
        return super().restore()

    def verify(self, graph, plan, execution, completed_at):
        outcome=super().verify(graph,plan,execution,completed_at)
        if getattr(self,'last_decision',{}).get('selected_plan'):
            forecast=next(r['empirical_effects'] for r in self.last_decision['candidates'] if r['plan_id']==plan.id)
            outcome['prediction_deviations']=execution_deviations(plan.id,forecast,outcome['verification'])
        return outcome
