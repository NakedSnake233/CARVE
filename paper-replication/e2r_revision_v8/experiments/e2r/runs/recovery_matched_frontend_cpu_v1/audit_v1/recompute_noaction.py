#!/usr/bin/env python3
"""Offline deterministic arithmetic/trace verifier; never executes experiment code.

Reads recorded JSON and source text only. Reimplements metric/oracle predicates
without importing repository modules. No runtime, network or cluster operations.
The output is fact extraction, not a semantic experiment-audit verdict.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import random
import re
import statistics


ROOT = Path(__file__).resolve().parents[5]
RUN = Path('experiments/e2r/runs/recovery_matched_frontend_cpu_v1')
EPISODE = RUN / 'episodes/00_no_action.json'
BASELINE = Path('experiments/e2r/runs/recovery_baseline_v2/baseline.json')
PROTOCOL = Path('experiments/e2r/recovery_matched_cpu_protocol_v1.json')
BLOCK = RUN / 'block.json'
MATCHED_SOURCE = Path('scripts/e2r/run_recovery_matched_block.py')
MANIFEST = Path('experiments/e2r/isolated_boutique.yaml')
SOURCES = [MATCHED_SOURCE, Path('scripts/e2r/run_recovery_sanity.py'),
           Path('scripts/e2r/run_recovery_baseline.py'),
           Path('rca_bench/workload_oracle.py'), Path('rca_bench/recovery_oracle.py')]
checks = []


def read(path):
    return json.loads((ROOT / path).read_text())


def digest(path):
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def ref(path, key=None, value=None):
    """Locate an artifact field/value exactly for human-review evidence."""
    if key is not None:
        needle = '  ' + json.dumps(key) + ':'
        rows = [i for i, line in enumerate((ROOT / path).read_text().splitlines(), 1)
                if line.startswith(needle)]
    else:
        needle = json.dumps(value)
        rows = [i for i, line in enumerate((ROOT / path).read_text().splitlines(), 1)
                if needle in line]
    return [f'{path}:{i}' for i in rows]


def eq(name, saved, computed):
    passed = type(saved) == type(computed) and saved == computed
    if isinstance(saved, (int, float)) and not isinstance(saved, bool):
        passed = isinstance(computed, (int, float)) and not isinstance(computed, bool) and saved == computed
    checks.append({'field': name, 'saved_or_expected': saved, 'recomputed': computed,
                   'matches_exactly': passed})
    return passed


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def window_metrics(window, raw, name, artifact):
    latencies = [float(v) for v in raw['latencies_ms'] if finite(v) and v >= 0]
    ordered = sorted(latencies)
    attempts, successes, elapsed = raw['attempts'], raw['successes'], raw['elapsed_seconds']
    p95_index = math.ceil(.95 * len(ordered)) - 1
    computed = dict(window)
    computed.update(samples=attempts, throughput_rps=successes / elapsed,
                    error_rate=(attempts - successes) / attempts,
                    p95_ms=ordered[p95_index] if len(ordered) == attempts and ordered else None)
    for key in ('samples', 'throughput_rps', 'error_rate', 'p95_ms'):
        eq(f'{name}.{key}', window[key], computed[key])
    eq(f'{name}.valid_latency_samples', raw['valid_latency_samples'], len(latencies))
    eq(f'{name}.attempts_equals_latency_count', attempts, len(latencies))
    eq(f'{name}.raw_window_id', window['window_id'], raw['window_id'])
    eq(f'{name}.source_id', f"k8s://{protocol['namespace']}/service/frontend/http-root", window['source_id'])
    eq(f'{name}.raw_sorted', True, raw['latencies_ms'] == ordered)
    eq(f'{name}.target_seconds', protocol['workload']['window_seconds'], raw['target_window_seconds'])
    eq(f'{name}.offered_rps', protocol['workload']['offered_rps'], raw['offered_rps'])
    eq(f'{name}.scheduled_attempts', math.ceil(raw['target_window_seconds'] * raw['offered_rps']), attempts)
    wall_elapsed = window['end_time'] - window['start_time']
    metrics = {
        'window_id': window['window_id'], 'attempts': attempts, 'successes_recorded': successes,
        'errors_from_recorded_aggregate': attempts - successes,
        'latency_count': len(ordered), 'latency_mean_ms': statistics.fmean(ordered),
        'latency_p95_ms': computed['p95_ms'], 'p95_rank_one_based': p95_index + 1,
        'latency_min_ms': ordered[0], 'latency_max_ms': ordered[-1],
        'throughput_rps': computed['throughput_rps'], 'error_rate': computed['error_rate'],
        'monotonic_elapsed_seconds_recorded': elapsed, 'wall_duration_seconds': wall_elapsed,
        'wall_minus_monotonic_seconds': wall_elapsed - elapsed,
        'state_canary_ok_recorded': window['state_canary_ok'],
        'start_time_recorded': window['start_time'], 'end_time_recorded': window['end_time'],
        'evidence': ref(artifact, value=window['window_id']),
    }
    return computed, metrics


def runtime(payload, require_matched_contract=True):
    rows = payload.get('items', [])
    if require_matched_contract and len(rows) != 12:
        return None
    result = {}
    for row in rows:
        containers = row.get('status', {}).get('containerStatuses') or []
        if (row.get('status', {}).get('phase') != 'Running' or not containers
                or (require_matched_contract and len(containers) != 2)
                or not all(c.get('ready') is True for c in containers)):
            return None
        result[row['metadata']['uid']] = {c['name']: c.get('restartCount') for c in containers}
    return result


def object_from_run(row, kind, name):
    if row.get('returncode') != 0:
        return None
    try:
        obj = json.loads(row['stdout'])
    except (TypeError, ValueError, KeyError):
        return None
    meta = obj.get('metadata', {})
    return obj if (obj.get('kind') == kind and meta.get('name') == name
                   and meta.get('namespace') == protocol['namespace']) else None


def is_active(obj):
    if not obj:
        return False
    conditions = {(r.get('type'), r.get('status')) for r in obj.get('status', {}).get('conditions', [])}
    return (obj.get('spec', {}).get('selector', {}).get('labelSelectors', {}).get('app') == protocol['service']
            and ('AllInjected', 'True') in conditions and ('AllRecovered', 'False') in conditions)


def technical_probe(package, command, kind, name):
    for key, expected in [('command', command), ('expected_kind', kind),
                          ('expected_name', name), ('expected_namespace', protocol['namespace'])]:
        eq(f'technical.{kind}.{key}', expected, package[key])
    rows = package.get('results', [])
    if len(rows) != 1:
        return 'UNKNOWN', None
    row = rows[0]
    if (row.get('command') != command or row.get('allowed') is not True
            or row.get('timed_out') is not False or type(row.get('returncode')) is not int
            or row.get('client_context') != {'kubeconfig': '${SCOPED_KUBECONFIG}'}):
        return 'UNKNOWN', None
    if row['returncode'] != 0:
        err = row.get('stderr', '')
        return ('NOT_FOUND' if 'NotFound' in err or 'not found' in err.lower() else 'UNKNOWN'), None
    obj = object_from_run(row, kind, name)
    return ('PASS', obj) if obj is not None else ('UNKNOWN', None)


def independent_oracle(baseline, windows, technical, repair, evaluated, thresholds):
    required = ('fault_absent', 'resource_restored', 'ready_endpoints')
    invalid = [k for k in required if type(technical.get(k)) not in (bool, type(None))]
    missing = [k for k in required if technical.get(k) is None or k in invalid]
    failed = [k for k in required if technical.get(k) is False]
    baseline_identity = (bool(baseline['window_id']) and bool(baseline['source_id'])
                         and finite(baseline['start_time']) and finite(baseline['end_time'])
                         and baseline['start_time'] < baseline['end_time']
                         and finite(repair) and baseline['end_time'] <= repair)
    baseline_valid = (type(baseline['samples']) is int and baseline['samples'] >= thresholds['min_samples']
                      and all(finite(baseline[k]) for k in ('throughput_rps', 'error_rate', 'p95_ms'))
                      and baseline['throughput_rps'] > 0 and 0 <= baseline['error_rate'] <= 1
                      and baseline['p95_ms'] > 0)
    details, seen, previous_end = [], set(), None
    consecutive = maximum = 0
    for index, w in enumerate(windows):
        measurable = (type(w['samples']) is int and w['samples'] >= thresholds['min_samples']
                      and all(finite(w[k]) for k in ('throughput_rps', 'error_rate', 'p95_ms'))
                      and w['throughput_rps'] >= 0 and 0 <= w['error_rate'] <= 1 and w['p95_ms'] >= 0)
        state_unknown = type(w['state_canary_ok']) not in (bool, type(None)) or w['state_canary_ok'] is None
        state_ok = w['state_canary_ok'] is True
        identity = (bool(w['window_id']) and w['window_id'] not in seen and w['source_id'] == baseline['source_id']
                    and finite(w['start_time']) and finite(w['end_time']) and w['start_time'] < w['end_time']
                    and finite(repair) and w['start_time'] >= repair
                    and (previous_end is None or w['start_time'] >= previous_end))
        seen.add(w['window_id']); previous_end = w['end_time']
        passed = (measurable and identity and state_ok and w['error_rate'] <= thresholds['error_rate_max']
                  and w['p95_ms'] <= thresholds['p95_ms_max'] and w['throughput_rps'] >= thresholds['throughput_rps_min'])
        consecutive = consecutive + 1 if passed else 0
        maximum = max(maximum, consecutive)
        details.append(dict(index=index, window=w, measurable=measurable, identity_valid=identity,
                            state_ok=state_ok, state_unknown=state_unknown, passed=passed))
    n = protocol['thresholds']['required_consecutive_windows']
    qualifying = windows[-n:] if len(windows) >= n else []
    fresh = (finite(evaluated) and len(qualifying) == n
             and all(finite(w['end_time']) and w['end_time'] <= evaluated
                     and evaluated - w['end_time'] <= thresholds['max_window_age_seconds'] for w in qualifying))
    unknown = (not baseline_valid or not baseline_identity or not fresh or missing or len(windows) < n
               or any(not d['measurable'] or not d['identity_valid'] or d['state_unknown'] for d in details))
    status = 'UNKNOWN' if unknown else 'FAIL' if failed or consecutive < n else 'PASS'
    return dict(status=status, success=status == 'PASS', technical=technical,
                missing_technical_checks=missing, invalid_technical_checks=invalid, failed_technical_checks=failed,
                baseline_measurable=baseline_valid, baseline_identity_valid=baseline_identity,
                repair_completed_at=repair, evaluated_at=evaluated, measurement_freshness_valid=fresh,
                required_consecutive_windows=n, max_consecutive_passing_windows=maximum,
                trailing_consecutive_passing_windows=consecutive, thresholds=thresholds, windows=details)


protocol, baseline_artifact, episode, block = map(read, (PROTOCOL, BASELINE, EPISODE, BLOCK))
matched_text = (ROOT / MATCHED_SOURCE).read_text()
expected_protocol_sha = re.search(r"^PROTOCOL_SHA256='([0-9a-f]+)'", matched_text, re.M).group(1)
eq('binding.protocol_sha256', expected_protocol_sha, digest(PROTOCOL))
eq('binding.baseline_sha256', protocol['baseline_sha256'], digest(BASELINE))
eq('binding.manifest_sha256', protocol['manifest_sha256'], digest(MANIFEST))
for key, path in [('protocol_sha256', PROTOCOL), ('baseline_sha256', BASELINE), ('manifest_sha256', MANIFEST)]:
    eq('block.' + key, block[key], digest(path))
order = ['typed_cleanup', 'no_action']; random.Random(protocol['order_seed']).shuffle(order)
eq('protocol.arm_order_from_seed', protocol['arms_in_order'], order)

baseline_windows, baseline_metrics = [], []
for i, (window, raw) in enumerate(zip(baseline_artifact['windows'], baseline_artifact['raw_window_diagnostics'])):
    computed, metrics = window_metrics(window, raw, f'baseline.windows[{i}]', BASELINE)
    baseline_windows.append(computed); baseline_metrics.append(metrics)
eq('baseline.window_count', baseline_artifact['workload']['windows'], len(baseline_windows))
eq('baseline.raw_window_count', len(baseline_windows), len(baseline_artifact['raw_window_diagnostics']))
pooled = sorted(v for raw in baseline_artifact['raw_window_diagnostics'] for v in raw['latencies_ms'])
baseline = dict(baseline_artifact['baseline'])
baseline.update(samples=sum(w['samples'] for w in baseline_windows),
                throughput_rps=statistics.median(w['throughput_rps'] for w in baseline_windows),
                error_rate=sum(round(w['samples'] * w['error_rate']) for w in baseline_windows) / len(pooled),
                p95_ms=pooled[math.ceil(.95 * len(pooled)) - 1],
                state_canary_ok=all(w['state_canary_ok'] is True for w in baseline_windows),
                start_time=baseline_windows[0]['start_time'], end_time=baseline_windows[-1]['end_time'])
eq('baseline.aggregate', baseline_artifact['baseline'], baseline)
baseline_runtime_before = runtime(baseline_artifact['cluster_snapshot']['pods'])
baseline_runtime_after = runtime(baseline_artifact['cluster_snapshot_after']['pods'])
eq('baseline.runtime_identity_stable_across_calibration', baseline_artifact['runtime_identity_stable_across_calibration'],
   baseline_runtime_before is not None and baseline_runtime_before == baseline_runtime_after)

episode_pairs = [('pre', episode['pre_window'], episode['pre_raw']),
                 ('fault', episode['fault_window'], episode['fault_raw'])]
episode_pairs += [(f'evaluation[{i}]', w, r) for i, (w, r) in
                  enumerate(zip(episode['evaluation_windows'], episode['evaluation_raw']))]
windows, metrics = [], []
for name, window, raw in episode_pairs:
    computed, diagnostic = window_metrics(window, raw, 'episode.' + name, EPISODE)
    windows.append(computed); metrics.append(diagnostic)
eq('episode.evaluation_window_count', protocol['timeline']['evaluation_windows'], len(episode['evaluation_windows']))
eq('episode.evaluation_raw_count', protocol['timeline']['evaluation_windows'], len(episode['evaluation_raw']))
t = protocol['thresholds']
thresholds = dict(min_samples=20, error_rate_max=baseline['error_rate'] + t['error_margin'],
                  p95_ms_max=baseline['p95_ms'] * t['p95_multiplier'],
                  throughput_rps_min=baseline['throughput_rps'] * t['throughput_fraction'],
                  require_state_canary=True, max_window_age_seconds=120)
eq('oracle.thresholds', episode['oracle']['thresholds'], thresholds)
for row, w in zip(metrics, windows):
    row['business_threshold_checks'] = dict(error_rate_ok=w['error_rate'] <= thresholds['error_rate_max'],
         p95_ok=w['p95_ms'] <= thresholds['p95_ms_max'], throughput_ok=w['throughput_rps'] >= thresholds['throughput_rps_min'],
         state_canary_ok_recorded=w['state_canary_ok'] is True)
    row['p95_ratio_to_baseline'] = w['p95_ms'] / baseline['p95_ms']
pre, fault = windows[:2]
pre_health = (pre['error_rate'] <= thresholds['error_rate_max'] and finite(pre['p95_ms'])
              and pre['p95_ms'] <= thresholds['p95_ms_max'] and pre['throughput_rps'] >= thresholds['throughput_rps_min']
              and pre['state_canary_ok'] is True)
measurable = (fault['samples'] >= 20 and all(finite(fault[k]) for k in ('throughput_rps', 'error_rate', 'p95_ms'))
              and type(fault['state_canary_ok']) is bool)
degraded = measurable and not all(metrics[1]['business_threshold_checks'].values())
eq('episode.pre_health_gate', episode['pre_health_gate'], pre_health)
eq('episode.fault_window_measurable', episode['fault_window_measurable'], measurable)
eq('episode.initial_business_degradation', episode['initial_business_degradation'], degraded)

initial_obj = object_from_run(episode['initial_fault_object_probe'], 'StressChaos', episode['chaos_name'])
uid = initial_obj['metadata']['uid']
eq('episode.chaos_uid', episode['chaos_uid'], uid)
eq('episode.initial_fault_active', True, is_active(initial_obj))
precondition_obj = object_from_run(episode['preconditions']['results'][0], 'StressChaos', episode['chaos_name'])
eq('episode.precondition_same_chaos_uid', episode['precondition_same_chaos_uid'], precondition_obj['metadata']['uid'] == uid)
observations = [('initial', initial_obj), ('precondition', precondition_obj)]
liveness = []
for i, saved in enumerate(episode['no_action_liveness_probes']):
    obj = object_from_run(saved['probe'], 'StressChaos', episode['chaos_name'])
    same = bool(obj and obj['metadata']['uid'] == uid)
    active = bool(same and is_active(obj))
    eq(f'episode.liveness[{i}].same_uid', saved['same_uid'], same)
    eq(f'episode.liveness[{i}].active', saved['active'], active)
    observations.append((f'evaluation-{i}', obj))
    liveness.append(dict(index=i, recorded_fault_age_seconds=saved['fault_age_seconds'], same_uid=same,
                         active=active, scheduling_budget_sum_seconds=saved['fault_age_seconds'] + protocol['workload']['window_seconds'] + 2,
                         scheduling_budget_pass=saved['fault_age_seconds'] + protocol['workload']['window_seconds'] + 2 < protocol['timeline']['latest_final_observation_seconds']))
eq('episode.liveness_count', protocol['timeline']['evaluation_windows'], len(liveness))

ns, service, name = protocol['namespace'], protocol['service'], episode['chaos_name']
probe_specs = [(f'kubectl get stresschaos {name} -n {ns} -o json', 'StressChaos', name),
               (f'kubectl get deployment {service} -n {ns} -o json', 'Deployment', service),
               (f'kubectl get endpoints {service} -n {ns} -o json', 'Endpoints', service)]
probe_results = [technical_probe(p, *s) for p, s in zip(episode['technical']['probes'], probe_specs)]
eq('technical.probe_count', 3, len(episode['technical']['probes']))
chaos_status, chaos_obj = probe_results[0]; dep_status, dep = probe_results[1]; ep_status, endpoint = probe_results[2]
fault_absent = True if chaos_status == 'NOT_FOUND' else False if chaos_status == 'PASS' else None
replicas, ready = dep['spec'].get('replicas', 1), dep['status'].get('readyReplicas', 0)
addresses = [a for s in endpoint.get('subsets', []) for a in s.get('addresses', [])]
technical = dict(fault_absent=fault_absent,
                  resource_restored=isinstance(replicas, int) and isinstance(ready, int) and ready >= replicas > 0,
                  ready_endpoints=bool(addresses))
eq('episode.technical.checks', episode['technical']['checks'], technical)
eq('episode.no_action_active_at_technical_observation', episode['no_action_active_at_technical_observation'],
   bool(chaos_obj and chaos_obj['metadata']['uid'] == uid and is_active(chaos_obj)))
observations.append(('technical', chaos_obj))
identity = []
for label, obj in observations:
    expected_spec = dict(selector=dict(namespaces=[ns], labelSelectors={'app': service}), mode='one',
                         duration=f"{protocol['fault']['duration_seconds']}s",
                         stressors=dict(cpu=dict(workers=protocol['fault']['workers'], load=protocol['fault']['load'])))
    eq(f'fault.{label}.spec_matches_protocol', expected_spec, obj['spec'])
    eq(f'fault.{label}.same_uid', uid, obj['metadata']['uid'])
    identity.append(dict(observation=label, uid=obj['metadata']['uid'], active=is_active(obj),
                         resource_version=obj['metadata'].get('resourceVersion'),
                         creation_timestamp=obj['metadata'].get('creationTimestamp'),
                         container_records=obj.get('status', {}).get('experiment', {}).get('containerRecords')))

oracle = independent_oracle(baseline, windows[2:], technical, episode['oracle']['repair_completed_at'],
                           episode['oracle']['evaluated_at'], thresholds)
eq('episode.oracle.complete_object', episode['oracle'], oracle)
eq('episode.expected_oracle_status', episode['expected_oracle_status'], protocol['expected_oracle_status']['no_action'])
for field, expected in [('injection_attempted', True), ('injection_outcome_unknown', False), ('fault_injected', True),
                        ('repair_process_attempted', False), ('repair_process_outcome_unknown', False), ('repair_executed', False)]:
    eq('episode.' + field, expected, episode[field])
eq('episode.successful_injection_event', True, any(e['stage'] == 'injection' and e['result']['returncode'] == 0 for e in episode['events']))
eq('episode.execution_absent', True, 'execution' not in episode)

timeline = protocol['timeline']
ages = {k: v for k, v in episode.items() if k.endswith('fault_age_seconds')}
timeline_checks = {
    'evaluation_start_budgets_pass': all(r['scheduling_budget_pass'] for r in liveness),
    'pretechnical_worst_case_budget_pass': episode['pre_technical_fault_age_seconds'] + 3 * timeline['technical_probe_timeout_seconds'] < timeline['latest_technical_observation_seconds'],
    'technical_before_deadline': episode['technical_observation_fault_age_seconds'] < timeline['latest_technical_observation_seconds'],
    'final_saved_observation_before_deadline': episode['final_observation_fault_age_seconds'] < timeline['latest_final_observation_seconds'],
    'cleanup_before_deadline': episode['cleanup_completion_fault_age_seconds'] < timeline['latest_cleanup_completion_seconds'],
    'technical_before_configured_auto_expiry': episode['technical_observation_fault_age_seconds'] < protocol['fault']['duration_seconds'],
    'cleanup_before_configured_auto_expiry': episode['cleanup_completion_fault_age_seconds'] < protocol['fault']['duration_seconds'],
    'age_order_monotonic': all(a <= b for a, b in zip(list(ages.values()), list(ages.values())[1:])),
}
for key, value in timeline_checks.items():
    eq('deadline_predicate.' + key, True, value)
eq('episode.technical_observation_deadline_seconds', episode['technical_observation_deadline_seconds'], timeline['latest_technical_observation_seconds'])
expected_cleanup_timeout = max(1, min(10, math.floor(timeline['latest_cleanup_completion_seconds'] - episode['cleanup_start_fault_age_seconds'])))
eq('episode.cleanup_timeout_seconds', episode['cleanup_timeout_seconds'], expected_cleanup_timeout)
eq('episode.cleanup_completed_before_deadline', episode['cleanup_completed_before_deadline'], timeline_checks['cleanup_before_deadline'])
wall_ages = [dict(window_id=w['window_id'], start_fault_age_wall_seconds=w['start_time'] - episode['injection_request_started_at'],
                  end_fault_age_wall_seconds=w['end_time'] - episode['injection_request_started_at'],
                  freshness_age_seconds=episode['oracle']['evaluated_at'] - w['end_time']) for w in windows]

absence = episode['cleanup_absence_probe']
absent = bool(absence and absence['returncode'] != 0 and
              ('NotFound' in absence['stderr'] or 'not found' in absence['stderr'].lower()))
eq('episode.cleanup_verified_absent', episode['cleanup_verified_absent'], absent)
initial_runtime = runtime(block['initial_pods'])
post_runtime = runtime(episode['post_cleanup_pods'])
eq('block.initial_runtime', block['initial_runtime'], initial_runtime)
eq('episode.post_cleanup_runtime', episode['post_cleanup_runtime'], post_runtime)
eq('episode.runtime_identity_unchanged', episode['runtime_identity_unchanged'], post_runtime == initial_runtime)
cleanup_ok = (episode['cleanup_error'] is None and episode['cleanup']['returncode'] == 0
              and episode['cleanup_absence_error'] is None and absent and timeline_checks['cleanup_before_deadline']
              and episode['post_cleanup_observation_error'] is None and post_runtime == initial_runtime)
status = 'complete_expected' if oracle['status'] == protocol['expected_oracle_status']['no_action'] else 'complete_unexpected'
if not cleanup_ok:
    status = 'failed_cleanup_or_runtime'
eq('episode.status_from_oracle_cleanup_and_runtime', episode['status'], status)

report = {
    'schema': 'e2r_noaction_offline_deterministic_recompute_v1',
    'scope': 'Fact extraction and arithmetic/probe consistency only; no semantic experiment-integrity verdict.',
    'input_sha256': {str(p): digest(p) for p in [PROTOCOL, BASELINE, EPISODE, BLOCK, MANIFEST] + SOURCES},
    'limitations': [
        'Raw diagnostics contain sorted latency observations and aggregate attempts/successes, not per-request status, dispatch/completion timestamps or response bodies. Error rates and throughput are recomputed from recorded aggregates.',
        'Canary booleans and wall/monotonic clock readings are recorded inputs, not independently re-observed. No raw canary response/status/latency is saved. Canary checks occur after each workload window end and elapsed measurement.',
        'Request latency starts inside _request after scheduled-worker waiting, and covers reading only the first 1024 response bytes. It excludes executor scheduling backlog and the canary; it is not full HTTP response or end-to-end business transaction latency.',
        'Deadline predicates are recomputed from recorded monotonic ages; clock readings cannot be reconstructed from absent per-request timestamps. Wall timestamps are separate consistency evidence with microsecond clock-read differences.',
        'resource_restored checks ready replica counts and ready_endpoints checks a nonempty address list. Neither predicate independently measures CPU recovery. The active fault predicate examines selector and Chaos conditions.',
        'The saved final_observation_fault_age_seconds is recorded after technical probes and oracle evaluation, not at the final workload-window end.',
        'Runtime stability means the saved 12 pod UIDs, two ready Running containers per pod, and restart counts match before/after; snapshots do not rule out every transient between observations.',
    ],
    'metric_definition_evidence': {
        'request': 'rca_bench/workload_oracle.py:112', 'scheduling': 'rca_bench/workload_oracle.py:139',
        'metrics': 'rca_bench/workload_oracle.py:153', 'raw_fields': 'rca_bench/workload_oracle.py:174',
        'canary': 'scripts/e2r/run_recovery_sanity.py:56',
        'baseline_aggregation': 'scripts/e2r/run_recovery_baseline.py:118',
        'pre_and_fault_gates': 'scripts/e2r/run_recovery_matched_block.py:114',
        'technical_probe_contract': 'rca_bench/workload_oracle.py:186',
        'technical_predicates': 'rca_bench/workload_oracle.py:243',
        'oracle': 'rca_bench/recovery_oracle.py:49',
        'deadline_and_liveness': 'scripts/e2r/run_recovery_matched_block.py:198',
        'cleanup': 'scripts/e2r/run_recovery_matched_block.py:269',
        'runtime': 'scripts/e2r/run_recovery_matched_block.py:55',
    },
    'baseline': {'windows': baseline_metrics, 'aggregate': baseline,
                 'pooled_latency_mean_ms': statistics.fmean(pooled), 'pooled_p95_rank_one_based': math.ceil(.95 * len(pooled)),
                 'sum_measured_window_seconds': sum(r['elapsed_seconds'] for r in baseline_artifact['raw_window_diagnostics']),
                 'aggregate_wall_span_seconds': baseline['end_time'] - baseline['start_time'],
                 'throughput_method': 'median of six window successful requests / measured elapsed, not samples / aggregate wall span',
                 'runtime_stable': baseline_runtime_before == baseline_runtime_after},
    'episode_windows': metrics,
    'thresholds': thresholds,
    'technical': {'checks': technical, 'raw_probe_statuses': [r[0] for r in probe_results],
                  'deployment_spec_replicas': replicas, 'deployment_ready_replicas': ready,
                  'ready_endpoint_addresses': addresses, 'evidence': ref(EPISODE, 'technical')},
    'chaos_uid_observations': identity,
    'liveness': liveness,
    'oracle': oracle,
    'timing': {'recorded_monotonic_fault_ages': ages, 'predicates': timeline_checks,
               'wall_clock_cross_checks': wall_ages,
               'marker_wall_age_seconds': episode['oracle']['repair_completed_at'] - episode['injection_request_started_at'],
               'evaluated_at_wall_age_seconds': episode['oracle']['evaluated_at'] - episode['injection_request_started_at'],
               'completed_at_wall_age_seconds': episode['completed_at'] - episode['injection_request_started_at'],
               'technical_budget_sum_seconds': episode['pre_technical_fault_age_seconds'] + 3 * timeline['technical_probe_timeout_seconds'],
               'final_deadline_margin_seconds': timeline['latest_final_observation_seconds'] - episode['final_observation_fault_age_seconds'],
               'technical_deadline_margin_seconds': timeline['latest_technical_observation_seconds'] - episode['technical_observation_fault_age_seconds'],
               'cleanup_deadline_margin_seconds': timeline['latest_cleanup_completion_seconds'] - episode['cleanup_completion_fault_age_seconds']},
    'cleanup_runtime': {'cleanup_returncode': episode['cleanup']['returncode'], 'absence_returncode': absence['returncode'],
                        'cleanup_verified_absent': absent, 'cleanup_ok': cleanup_ok,
                        'pod_count': len(episode['post_cleanup_pods']['items']),
                        'container_count': sum(len(v) for v in post_runtime.values()),
                        'restart_count_sum': sum(n for v in post_runtime.values() for n in v.values()),
                        'runtime_matches_block_initial': post_runtime == initial_runtime,
                        'runtime_matches_baseline_before_and_after': post_runtime == baseline_runtime_before == baseline_runtime_after,
                        'post_cleanup_runtime': post_runtime},
    'artifact_field_evidence': {key: ref(EPISODE, key) for key in ['pre_window', 'pre_raw', 'fault_window', 'fault_raw',
        'initial_fault_object_probe', 'chaos_uid', 'preconditions', 'precondition_same_chaos_uid', 'evaluation_windows',
        'evaluation_raw', 'no_action_liveness_probes', 'pre_technical_fault_age_seconds', 'technical_observation_fault_age_seconds',
        'technical', 'oracle', 'final_observation_fault_age_seconds', 'cleanup_completion_fault_age_seconds', 'post_cleanup_pods',
        'post_cleanup_runtime', 'runtime_identity_unchanged']},
    'checks': checks,
    'comparison_count': len(checks),
    'mismatches': [c for c in checks if not c['matches_exactly']],
}
report['mismatch_count'] = len(report['mismatches'])
output = ROOT / RUN / 'audit_v1/recompute_noaction.json'
output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
print(json.dumps({'output': str(output.relative_to(ROOT)), 'comparisons': len(checks),
                  'mismatches': report['mismatch_count'], 'oracle_status': oracle['status']}, indent=2))
