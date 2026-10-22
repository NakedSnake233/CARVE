#!/usr/bin/env python3
"""Offline audit; reads saved artifacts, never starts cluster or network processes."""
from pathlib import Path
import copy
import hashlib
import json
import math
import random
import re
import statistics
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT))
from methods.PlanRCAThinkRemed import safety_guard
from methods.PlanRCAThinkRemed.schemas import RepairPlan
from methods.PlanRCAThinkRemed.cluster_client import ClusterClientContext

RUN = ROOT / 'experiments/e2r/runs/recovery_matched_frontend_cpu_v1'
OUT = RUN / 'audit_v1'
read = lambda path: json.loads(path.read_text())
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
block = read(RUN / 'block.json')
control = read(RUN / 'episodes/00_no_action.json')
typed = read(RUN / 'episodes/01_typed_cleanup.json')
sidecar = read(RUN / 'episodes/01_typed_cleanup.interruption.json')
final = read(RUN / 'block.interruption.json')
protocol = read(ROOT / 'experiments/e2r/recovery_matched_cpu_protocol_v1.json')
baseline = read(Path(block['baseline_path']))['baseline']
checks = []

def check(label, condition, detail=None):
    checks.append({'check': label, 'pass': bool(condition), 'detail': detail})

files = [RUN / 'block.json', RUN / 'episodes/00_no_action.json',
         RUN / 'episodes/01_typed_cleanup.json', RUN / 'block.interruption.json',
         RUN / 'episodes/01_typed_cleanup.interruption.json',
         ROOT / 'experiments/e2r/recovery_matched_cpu_protocol_v1.json',
         Path(block['baseline_path']), Path(block['manifest_path']),
         ROOT / 'scripts/e2r/run_recovery_matched_block.py',
         ROOT / 'scripts/e2r/run_recovery_sanity.py',
         ROOT / 'scripts/e2r/finalize_interrupted_matched_block.py',
         ROOT / 'scripts/e2r/run_recovery_baseline.py',
         ROOT / 'rca_bench/workload_oracle.py', ROOT / 'rca_bench/recovery_oracle.py',
         ROOT / 'methods/PlanRCAThinkRemed/safety_guard.py',
         ROOT / 'methods/PlanRCAThinkRemed/schemas.py',
         ROOT / 'methods/PlanRCAThinkRemed/playbook_generator_agent.py',
         ROOT / 'methods/PlanRCAThinkRemed/repair_planner_agent.py',
         ROOT / 'tests/e2r/test_recovery_matched_block.py',
         ROOT / 'tests/test_workload_oracle.py',
         ROOT / 'refine-logs/FULL_EXPERIMENT_CODE_REVIEW.md',
         ROOT / 'refine-logs/EXPERIMENT_TRACKER.md',
         ROOT / 'refine-logs/EXPERIMENT_PLAN.md',
         RUN / 'analysis_v1/analysis.json', RUN / 'analysis_v1/REPORT.md']
hashes = {str(p.relative_to(ROOT)): sha(p) for p in files}
for name, expected in final['source_hashes'].items():
    path = RUN / name if name == 'block.json' else RUN / 'episodes' / name
    check('source hash ' + name, sha(path) == expected)
check('typed sidecar source hash', sha(RUN / 'episodes/01_typed_cleanup.json') == sidecar['source_sha256'])
for kind in ('protocol', 'baseline', 'manifest'):
    check('block binding ' + kind, sha(Path(block[kind + '_path'])) == block[kind + '_sha256'])
for kind in ('baseline', 'manifest'):
    check('protocol binding ' + kind, sha(ROOT / protocol[kind + '_path']) == protocol[kind + '_sha256'])
review = (ROOT / 'refine-logs/FULL_EXPERIMENT_CODE_REVIEW.md').read_text()
fingerprints = []
for expected, relative in re.findall(r'^([a-f0-9]{64})  (\S+)$', review, flags=re.M):
    if relative in hashes and (relative.startswith('scripts/e2r/run_recovery_matched')
            or relative.startswith('scripts/e2r/finalize_interrupted')
            or relative.startswith('experiments/e2r/runs/recovery_matched')
            or relative.startswith('experiments/e2r/recovery_matched')
            or expected == hashes[relative]):
        fingerprints.append({'path': relative, 'expected': expected,
                             'actual': hashes[relative], 'matches': expected == hashes[relative]})
        check('review fingerprint ' + relative, expected == hashes[relative])
order = ['typed_cleanup', 'no_action']
random.Random(protocol['order_seed']).shuffle(order)
check('seeded frozen order', order == protocol['arms_in_order'] == block['arms_in_order'])
check('exact original episode membership', sorted(p.name for p in (RUN / 'episodes').glob('*.json')
      if not p.name.endswith('.interruption.json')) == ['00_no_action.json', '01_typed_cleanup.json'])
check('retry policy no replacement', protocol['retry_policy'] == 'none; any gate failure stops the block and remains in the denominator')

thresholds = {'error_rate_max': baseline['error_rate'] + protocol['thresholds']['error_margin'],
              'p95_ms_max': baseline['p95_ms'] * protocol['thresholds']['p95_multiplier'],
              'throughput_rps_min': baseline['throughput_rps'] * protocol['thresholds']['throughput_fraction']}
windows = []
for prefix in ('pre', 'fault'):
    w, raw = typed[prefix + '_window'], typed[prefix + '_raw']
    values = raw['latencies_ms']
    n = len(values)
    p95 = sorted(values)[math.ceil(.95 * n) - 1]
    throughput = raw['successes'] / raw['elapsed_seconds']
    error_rate = (raw['attempts'] - raw['successes']) / raw['attempts']
    health = {'latency': p95 <= thresholds['p95_ms_max'],
              'throughput': throughput >= thresholds['throughput_rps_min'],
              'error': error_rate <= thresholds['error_rate_max'], 'canary': w['state_canary_ok'] is True}
    check(prefix + ' all latency values valid retained', all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in values))
    check(prefix + ' denominator', n == raw['attempts'] == raw['valid_latency_samples'] == w['samples'] == 150)
    check(prefix + ' successes all attempts', raw['successes'] == raw['attempts'])
    check(prefix + ' saved p95', p95 == w['p95_ms'])
    check(prefix + ' saved throughput', throughput == w['throughput_rps'])
    check(prefix + ' saved error', error_rate == w['error_rate'])
    check(prefix + ' matching window identity', w['window_id'] == raw['window_id'] and w['source_id'] == baseline['source_id'])
    check(prefix + ' declared workload', raw['offered_rps'] == 5 and raw['target_window_seconds'] == 30)
    check(prefix + ' wall interval consistent with monotonic elapsed', abs(w['end_time'] - w['start_time'] - raw['elapsed_seconds']) < .001)
    windows.append({'window_id': w['window_id'], 'samples': n, 'successes': raw['successes'],
        'mean_latency_ms_audit_derived': statistics.mean(values), 'p95_ms': p95,
        'min_latency_ms': min(values), 'max_latency_ms': max(values),
        'throughput_rps': throughput, 'error_rate': error_rate,
        'elapsed_seconds': raw['elapsed_seconds'], 'wall_interval_seconds': w['end_time'] - w['start_time'],
        'start_time': w['start_time'], 'end_time': w['end_time'], 'business_criteria': health})
check('typed pre-health gate', all(windows[0]['business_criteria'].values()) == typed['pre_health_gate'] is True)
check('typed fault measurable', typed['fault_window_measurable'] is True and len(typed['fault_raw']['latencies_ms']) >= 20)
check('typed fault degradation', (not all(windows[1]['business_criteria'].values())) == typed['initial_business_degradation'] is True)
check('typed pre precedes injection', typed['pre_window']['end_time'] < typed['injection_request_started_at'])
check('typed fault settle at least 30s', typed['fault_window']['start_time'] - typed['injection_request_started_at'] >= 30)

context = ClusterClientContext('${SCOPED_KUBECONFIG}')
precondition_replays = []
for d in (control, typed):
    prefix = d['arm']
    plan = RepairPlan.from_dict(d['repair_plan'])
    plan_ok, plan_reasons = safety_guard.check_repair_plan(plan, allowed_namespace=d['namespace'])
    yaml_ok, yaml_reasons = safety_guard.check_playbook_yaml(d['playbook'], allowed_namespace=d['namespace'],
        expected_commands=safety_guard.expected_playbook_commands(plan), client_context=context)
    check(prefix + ' pure safety replay exact', {'plan_ok': plan_ok, 'yaml_ok': yaml_ok,
        'reasons': plan_reasons + yaml_reasons} == d['safety'])
    # The only external dependency in run_preconditions is replaced by stored rows.
    with patch('methods.PlanRCAThinkRemed.safe_probe_agent.run_probes', return_value=copy.deepcopy(d['preconditions']['results'])):
        replay = safety_guard.run_preconditions(plan, client_context=context)
    check(prefix + ' precondition replay exact', replay == d['preconditions'])
    observed = json.loads(d['initial_fault_object_probe']['stdout'])
    preobj = json.loads(d['preconditions']['results'][0]['stdout'])
    check(prefix + ' initial object context/rc', d['initial_fault_object_probe']['returncode'] == 0 and
          d['initial_fault_object_probe']['client_context'] == context.to_dict())
    for label, obj in (('initial', observed), ('precondition', preobj)):
        cond = {(r['type'], r['status']) for r in obj['status']['conditions']}
        check(prefix + ' exact identity ' + label, obj['kind'] == 'StressChaos' and
            obj['metadata']['name'] == d['chaos_name'] and obj['metadata']['namespace'] == d['namespace'] and
            obj['metadata']['uid'] == d['chaos_uid'] and obj['spec']['selector']['labelSelectors'] == {'app': 'frontend'} and
            obj['spec']['selector']['namespaces'] == [d['namespace']])
        check(prefix + ' active exact fault ' + label, ('AllInjected', 'True') in cond and ('AllRecovered', 'False') in cond)
        check(prefix + ' exact fault profile ' + label, obj['spec']['duration'] == '180s' and obj['spec']['mode'] == 'one' and
              obj['spec']['stressors']['cpu'] == {'load': 100, 'workers': 10})
    check(prefix + ' pinned UID', observed['metadata']['uid'] == preobj['metadata']['uid'] == d['chaos_uid'] and d['precondition_same_chaos_uid'] is True)
    check(prefix + ' initial raw evidence matches plan', json.loads(plan.rca_report.evidence[0].raw_excerpt) == observed)
    precondition_replays.append({'arm': prefix, 'uid': d['chaos_uid'], 'preconditions_match': replay == d['preconditions'],
                                 'safety': d['safety'], 'observed_object_sha256': replay['captures'][0]['observed_object_sha256']})

execution = typed['execution']
remaining = protocol['timeline']['latest_repair_completion_seconds'] - typed['pre_execution_fault_age_seconds']
expected_timeout = min(30, max(0, math.floor(remaining)))
check('bounded typed Ansible timeout', typed['ansible_timeout_seconds'] == execution['timeout_seconds'] == expected_timeout == 30)
check('typed explicit successful delete', execution['returncode'] == 0 and 'deleted from e2r-supplement-20260925 namespace' in execution['stdout']
      and 'e2r-matched-cpu-typed' in execution['stdout'] and 'failed=0' in execution['stdout'] and 'unreachable=0' in execution['stdout'])
check('reviewed venv executor', execution['executable'] == str(ROOT / '.venv-e2r/bin/ansible-playbook'))
check('typed completion deadline', execution['completed_before_deadline'] is True and execution['repair_completion_fault_age_seconds'] < 150)
check('typed duration consistent with age progression', 0 <= execution['repair_completion_fault_age_seconds'] - typed['pre_execution_fault_age_seconds'] - execution['seconds'] < .1)
check('typed no completed evaluation raw/windows', typed['evaluation_windows'] == [] and typed['evaluation_raw'] == [])
absent_fields = ['technical', 'oracle', 'cleanup', 'cleanup_absence_probe', 'cleanup_verified_absent',
                 'completed_at', 'post_cleanup_pods', 'post_cleanup_runtime', 'runtime_identity_unchanged']
check('typed missing outcome and cleanup fields', all(k not in typed for k in absent_fields), absent_fields)
check('typed null oracle and unknown in-flight preserved', sidecar['saved_oracle_status'] is None and sidecar['saved_evaluation_window_count'] == 0
      and sidecar['in_flight_evaluation_requests_unknown'] is True)

rows = [control, typed]
counts = {'planned_episode_count': len(protocol['arms_in_order']), 'episode_attempts_started': len(rows),
          'injection_attempts': sum(d['injection_attempted'] is True for d in rows),
          'confirmed_fault_injections': sum(d['fault_injected'] is True for d in rows),
          'injection_unknown_outcomes': sum(d['injection_outcome_unknown'] is True for d in rows),
          'repair_process_invocations': sum(d['repair_process_attempted'] is True for d in rows),
          'repair_process_unknown_outcomes': sum(d['repair_process_outcome_unknown'] is True for d in rows),
          'confirmed_repair_mutations': sum(d['repair_executed'] is True for d in rows),
          'oracle_statuses': [d.get('oracle', {}).get('status') for d in rows],
          'complete_matched_pairs': int(all(d.get('completed_at') is not None and 'oracle' in d for d in rows))}
for k, v in counts.items():
    check('sidecar count ' + k, final[k] == v)
check('invalid comparison terminal status', final['status'] == 'failed_interrupted' and final['valid_matched_comparison'] is False
      and final['rerun_or_replacement_authorized'] is False and sidecar['valid_recovery_outcome'] is False)
for name, row in sidecar['late_fault_absence'].items():
    probe = row['probe']
    check('late exact NotFound ' + name, row['absent'] is True and probe['returncode'] != 0 and 'NotFound' in probe['stderr']
          and probe['argv'] == ['kubectl', 'get', 'stresschaos', name, '-n', protocol['namespace'], '-o', 'json'])
check('sidecar late absence agrees', sidecar['late_fault_absence'] == final['late_fault_absence'])
check('late runtime unavailable not empty healthy population', sidecar['late_pod_probe']['returncode'] == 1 and
      'Unauthorized' in sidecar['late_pod_probe']['stderr'] and sidecar['late_pods'] is None and sidecar['late_runtime'] is None and
      sidecar['late_runtime_matches_block_start'] is None and final['late_runtime_matches_block_start'] is None)
repair_wall = typed['injection_request_started_at'] + execution['repair_completion_fault_age_seconds']
delay = sidecar['late_observation_captured_at'] - repair_wall
check('reconstructed repair wall timestamp', repair_wall == sidecar['repair_completion_wall_time_reconstructed_from_trace'])
check('late delay and timestamp', delay == sidecar['late_observation_delay_from_repair_seconds'] and
      final['finalized_at'] == sidecar['late_observation_captured_at'])
check('30 second washout', typed['started_at'] - control['completed_at'] >= protocol['timeline']['washout_seconds'])

analysis = read(RUN / 'analysis_v1/analysis.json')
check('analysis accounting', analysis['accounting'] == {k:v for k,v in counts.items() if k != 'complete_matched_pairs'})
check('analysis preserves null and invalidity', analysis['typed_partial']['oracle_status'] is None and
      analysis['complete_matched_pairs'] == 0 and analysis['valid_matched_comparison'] is False)
for row, w in zip(analysis['typed_partial']['windows'], windows):
    check('analysis typed window ' + row['window_id'], all(row[k] == w[k] for k in
        ('window_id', 'samples', 'successes', 'p95_ms', 'throughput_rps', 'error_rate', 'start_time', 'end_time')))

result = {'deterministic_verification': 'PASS' if all(c['pass'] for c in checks) else 'FAIL',
          'check_count': len(checks), 'failed_checks': [c for c in checks if not c['pass']],
          'checks': checks, 'typed_windows': windows, 'thresholds': thresholds,
          'precondition_replays': precondition_replays, 'execution': execution,
          'typed_missing_fields': absent_fields, 'accounting': counts,
          'saved_observations': {'control': 750, 'typed': 300, 'total': 1050,
             'interpretation': 'Retained latency observations with aggregate success counts; issued total including in-flight requests unknown.'},
          'repair_completion_reconstructed_wall': repair_wall, 'late_delay_seconds': delay,
          'review_fingerprints': fingerprints, 'audited_input_hashes': hashes,
          'limits': ['All replay uses stored raw data; no fresh external observations.',
              'Saved elapsed/clock values and canary booleans cannot be independently measured from absent per-request timestamps/responses.',
              'Unauthorized proves late scoped read failure; expired token is the executor-reported cause, not derivable solely from stderr.',
              'No hidden episode/retry exists in audited directory or runner control flow; absence elsewhere is not proven.']}
OUT.mkdir(exist_ok=True)
(OUT / 'recompute_typed_and_accounting.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({k:result[k] for k in ('deterministic_verification','check_count','failed_checks','accounting','thresholds')},indent=2))
if result['failed_checks']:
    raise SystemExit(1)
