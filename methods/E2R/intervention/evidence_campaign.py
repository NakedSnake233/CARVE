"""Explicitly assigned native arms with complete fixed-observation evidence.

This is an experimental collector, never a learned policy or its release path.
"""
from pathlib import Path
import time
from .campaign import append_json
from .native import NativeBackend
from .platform import Executor
from .policy_campaign import AccountedNativeProvider
from .probe_budget import ProbeAccountant
from .runtime import ObservationBudget


class NativeFixedEvidenceBackend(NativeBackend):
    observation_method='experiment_assigned_fixed_observation'

    def __init__(self,config,out_dir):
        if (config.get('mode')!='fixed_observation_evidence' or not config.get('scheduled_jobs')
                or not config.get('total_probe_budget') or config.get('monitor_execution_safety') is not True
                or any(key in config for key in ('model','validation_profile','admission_ledger'))
                or any(len(p['actions'])>1 for p in config['plans'])):
            raise ValueError('fixed evidence requires explicit bounded assigned single-action arms, budget and safety monitoring; no learned model/profile')
        super().__init__(config,out_dir)
        self.provider=AccountedNativeProvider(config['inventory'],self.client,self)
        self.probe_accountant=None

    def start(self,case,directory):
        self.probe_accountant=None
        super().start(case,directory)

    def observe(self,case):
        self.probe_accountant=ProbeAccountant(ObservationBudget(**self.config['total_probe_budget']),
            record=lambda event:append_json(self.current_dir/'registered_probe_events.jsonl',event))
        bundle=super().observe(case)
        self.strategy_started_at=self.observation_strategy_started_at
        self.strategy_completed_at=time.time()
        return bundle

    def execute(self,graph,plan,directory):
        self.execution_started_at=time.time();self.probe_accountant.phase='mandatory'
        executor=Executor(self.client,self.provider,self.policy,Path(directory)/'execution_private.jsonl',
                          convergence_timeout=self.config.get('convergence_timeout',60))
        self.executors.append(executor)
        def budget_only(current,remaining):
            return {'approved':self.probe_accountant.permit_write(),
                    'authority':'preassigned experimental arm with operation contracts; no learned effect approval'}
        try:return executor.execute(graph,plan,dry_run=False,authorize=budget_only)
        finally:self.probe_accountant.close()

    def restore(self):
        if self.probe_accountant is not None:self.probe_accountant.close()
        return super().restore()
