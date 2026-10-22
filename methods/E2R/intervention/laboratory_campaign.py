"""Scoped experimental model decisions; never a qualified/production release."""
from dataclasses import asdict
import json
from pathlib import Path
import resource
import time
from .bounded_decision import budgeted_decision
from .evidence_campaign import NativeFixedEvidenceBackend
from .native import NativeBackend
from .platform import Executor
from .policy_campaign import AccountedNativeProvider
from .predictors import load_effect_predictor
from .runtime import ObservationBudget, rank_candidates
from .schema import GraphObservation, number


def laboratory_candidates(graph, plans, policy, predictor, *, hypothetical=False):
    ranked = rank_candidates(graph, plans, policy, predictor, hypothetical=hypothetical)
    for row in ranked:
        row['strict_admission_diagnostic'] = {'candidate_eligible': row['executable'],
            'reasons': list(row['reasons']), 'qualified_profile': False,
            'scope': 'unchanged strict candidate rules; no qualified profile exists for this laboratory campaign'}
        forecast = row['empirical_effects']
        reasons = []
        if any(c['status'] not in ('pass', 'deferred') for c in row['checks']):
            reasons.append('operation_constraints')
        if forecast is None or forecast.get('source') != 'live':
            reasons.append('no_live_effect_model')
        elif any(not number(forecast['probabilities'].get(k)) or not 0 <= forecast['probabilities'][k] <= 1
                 for k in ('business_recovered', 'non_target_safe')):
            reasons.append('unobserved_or_invalid_outcome_dimension')
        row.update(executable=not reasons, laboratory_eligible=not reasons, reasons=reasons,
                   execution_authority='registered_laboratory_experiment_only')
    return ranked


class NativeLaboratoryPolicyBackend(NativeFixedEvidenceBackend):
    observation_method = 'laboratory_online_policy'

    def __init__(self, config, out_dir):
        if (config.get('mode') != 'laboratory_policy_comparison'
                or config.get('experimental_evaluation', {}).get('authority') != 'user_authorized_laboratory_only'
                or not config.get('scheduled_jobs') or not config.get('total_probe_budget')
                or config.get('monitor_execution_safety') is not True
                or any(k in config for k in ('validation_profile', 'admission_ledger'))
                or any(len(p['actions']) > 1 for p in config['plans'])
                or not set(config['arms']) <= {'decision', 'fixed', 'random', 'none'}):
            raise ValueError('laboratory mode requires bounded registered strategies, single-action catalog, budgets and safety; no qualified profile')
        release = json.loads(Path(config['laboratory_release']).read_text())
        registration = json.loads(Path(config['laboratory_registration']).read_text())
        runtime = str(Path(__file__).resolve().parents[3])
        assignments = [a for a in registration['assignments'] if Path(a['out']).resolve() == Path(out_dir).resolve()]
        if (release.get('status') != 'passed' or release.get('scope') != 'laboratory_only_online_comparison'
                or release.get('runtime') != runtime or len(assignments) != 1
                or json.loads(Path(assignments[0]['config']).read_text()) != config):
            raise ValueError('unregistered laboratory configuration/output/runtime')
        self.predictor = load_effect_predictor(config['model'], seeds=config['prediction_seeds'])
        if self.predictor.metadata.get('schema') != 'e2r_intervention_training_v2' or self.predictor.metadata.get('source') != 'live':
            raise ValueError('laboratory model must be the registered native v2 predictor')
        NativeBackend.__init__(self, config, out_dir)
        self.provider = AccountedNativeProvider(config['inventory'], self.client, self)
        self.probe_accountant = None
        self.budget = ObservationBudget(**config['observation_budget'])
        subset=config.get('fixed_adaptive_probe_ids')
        if subset is not None and (not subset or len(subset)!=len(set(subset)) or not set(subset)<={p.id for p in self.provider.probes}):
            raise ValueError('fixed adaptive subset must contain unique registered probes')

    def choose_plan(self, graph, plans, strategy):
        self.strategy_started_at = self.observation_strategy_started_at
        def observe(probe, current):
            start = time.perf_counter()
            result = self.provider.probe(probe, current)
            self.query_observations.append({'probe': asdict(probe), 'observation': asdict(result),
                'observation_type': 'graph' if isinstance(result, GraphObservation) else 'fact',
                'seconds': time.perf_counter() - start})
            return result
        subset=self.config.get('fixed_adaptive_probe_ids') if strategy=='fixed' else None
        adaptive=[p for p in self.provider.probes if p.id in subset] if subset is not None else None
        result = budgeted_decision(graph, plans, self.policy, self.predictor, self.provider, observe,
            budget=self.budget, account=self.probe_accountant, strategy=strategy, seed=self.config['seed'],
            fixed_order=self.config.get('fixed_probe_order'), candidate_ranker=laboratory_candidates,adaptive_probes=adaptive)
        result['decision_scope'] = 'laboratory experiment; not qualified automatic admission'
        result['validation_profile_status'] = 'not_qualified_laboratory_experiment'
        result['experimental_action_selected'] = result['decision'] == 'execute'
        self.last_decision = result
        self.strategy_completed_at = time.time()
        return result

    def execute(self, graph, plan, directory):
        self.execution_started_at = time.time()
        self.probe_accountant.phase = 'mandatory'
        executor = Executor(self.client, self.provider, self.policy, Path(directory)/'execution_private.jsonl',
                            convergence_timeout=self.config.get('convergence_timeout', 60))
        self.executors.append(executor)
        def authorize(current, remaining):
            candidate = laboratory_candidates(current, [remaining], self.policy, self.predictor)[0]
            budget_ok = self.probe_accountant.permit_write()
            return {'approved': candidate['executable'] and budget_ok, 'candidate': candidate,
                    'authority': 'registered_laboratory_experiment_only',
                    'total_probe_budget_allows_write': budget_ok}
        try:
            return executor.execute(graph, plan, dry_run=False, authorize=authorize)
        finally:
            self.probe_accountant.close()

    def finalize_campaign(self):
        result = super().finalize_campaign()
        parent = resource.getrusage(resource.RUSAGE_SELF)
        child = resource.getrusage(resource.RUSAGE_CHILDREN)
        result['laboratory_process_peak_rss_mib'] = parent.ru_maxrss/1024
        result['maximum_child_rss_mib'] = child.ru_maxrss/1024
        result['process_cpu_seconds'] = {'parent_user':parent.ru_utime,'parent_system':parent.ru_stime,
            'reaped_children_user':child.ru_utime,'reaped_children_system':child.ru_stime,
            'scope':'Campaign process lifetime including imports/model load; child accounting excludes native application Pods.'}
        (self.out_dir/'campaign_baseline_restore.json').write_text(json.dumps(result,indent=2)+'\n')
        return result

    def stop_reason(self, row):
        if row.get('execution', {}).get('status') in ('execution_unknown', 'write_deviation'):
            return 'uncertain_native_write_requires_inspection_before_next_slot'
        return None
