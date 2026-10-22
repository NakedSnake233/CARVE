#!/usr/bin/env python3
"""Register the prospective 104-position state-conditioned repair study."""
import argparse
import copy
import json
import random
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.e2r.build_fault_coverage import ARMS, CONDITIONS, FAULTS, PROBES
from scripts.e2r.build_fault_coverage import configure_fault as configure_base_fault
from scripts.e2r.build_parallel_native_resume import remap
from scripts.e2r.profile_native_intervention import db_binding
from methods.E2R.intervention.goal_policy import METHODS
from methods.E2R.intervention.station_goal import goal_step
from methods.E2R.intervention.stateful_evidence import validate_state_goal

PHASES = {
    'training': {'owner': 'shared-db', 'rate': 2., 'positions': 24},
    'validation': {'owner': 'private-db', 'rate': 2., 'positions': 24},
    'test': {'owner': 'shared-db', 'rate': 2.5, 'positions': 24},
    'policy': {'owner': 'shared-db', 'rate': 2.5, 'positions': 32},
}
DEFAULT_SOURCE = ROOT / 'enhancement/method-stage/fault_coverage_20261003'


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def configure_fault(config, mechanism):
    configure_base_fault(config, mechanism)
    if mechanism == 'owner_dependency':
        owner = config['state_continuity']['owner_database']
        if owner not in ('shared-db', 'private-db'):
            raise ValueError('unregistered owner database')
        entity = 'db' if owner == 'shared-db' else 'db_private'
        config['cases'][0].update(
            fault_changes=[{'entity': entity, 'path': '/spec/replicas', 'value': 0}],
            fault_wait_entities=[entity])


def build(root, runtime, source=DEFAULT_SOURCE):
    root, runtime, source = (Path(p).resolve() for p in (root, runtime, source))
    root.mkdir(parents=True, exist_ok=True)
    # Registration is prospective: never overwrite an existing queue or result set.
    for name in [*(stage + '_v1' for stage in PHASES), 'master_v1']:
        if (root / name).exists():
            raise FileExistsError(root / name)
    endpoints_path = root / 'SERVICE_ENDPOINTS.json'
    if not endpoints_path.exists():
        endpoints_path.write_bytes((source / 'SERVICE_ENDPOINTS.json').read_bytes())
    endpoints = json.loads(endpoints_path.read_text())['namespaces']
    base_file = source / 'assigned_v1/configs/assigned-application_readiness-shared_state-r0.json'
    base = json.loads(base_file.read_text())
    base_ns = base['experimental_namespace']
    py = str(ROOT / '.venv-e2r/bin/python')
    model = root / 'model_v1/model.json'
    all_jobs = []
    preregistration_time = time.time()
    previous = {'training': [], 'validation': ['training_results'],
                'test': ['fit_context_transition'], 'policy': ['evaluate_context_transition']}
    for stage, settings in PHASES.items():
        q = root / (stage + '_v1')
        (q / 'configs').mkdir(parents=True)
        jobs, assignments, first = [], [], []
        slot = 0
        owner, rate = settings['owner'], settings['rate']
        other = 'private-db' if owner == 'shared-db' else 'shared-db'
        for fi, fault in enumerate(FAULTS):
            for ci, condition in enumerate(CONDITIONS):
                lane = (fi + 2 * ci) % 4
                ns = 'e2r-isg-trainticket' + ('' if lane == 0 else f'-p{lane + 1}')
                ips = {v['clusterIP']: endpoints[ns][name]['clusterIP']
                       for name, v in endpoints[base_ns].items()}
                for mi, method in enumerate(METHODS if stage == 'policy' else ('assigned',)):
                    c = remap(copy.deepcopy(base), base_ns, ns, ips)
                    case = c['cases'][0]
                    identity = f'{stage}-{fault}-{condition}-r0'
                    if stage == 'policy':
                        identity += '-' + method
                    group = f'context-repair-{stage}-{fault}'
                    cond = fault + '__' + condition
                    case.update(group_id=group, comparison_group=group, condition_id=cond,
                                diagnosis_label=fi + 1, offered_rps=rate, peer_rps=rate)
                    bindings = {'new': owner, 'old': owner if condition == 'shared_state' else other,
                                'peer': other}
                    for change in case['baseline_changes']:
                        if change['path'] == '/spec/template' and change['entity'] in bindings:
                            change['value'] = db_binding(change['value'], 'trainticket', bindings[change['entity']])
                    case['baseline_changes'] = [v for v in case['baseline_changes']
                                               if not (v['path'] == '/spec/replicas' and v['entity'] in ('db', 'db_private'))]
                    case['baseline_changes'] += [{'entity': role, 'path': '/spec/replicas', 'value': 1}
                                                 for role in ('db', 'db_private')]
                    healthy = next(v['value'] for v in case['baseline_changes']
                                   if v['entity'] == 'new' and v['path'] == '/spec/template')
                    c['policy']['rollback_options']['new']['known_good']['template'] = copy.deepcopy(healthy)
                    goal_uuid = uuid.uuid5(uuid.NAMESPACE_URL, 'e2r-context-repair-v1-' + identity)
                    goal = {'id': str(goal_uuid), 'name': 'e2r_ack_ctx_' + goal_uuid.hex, 'stay_time': 3}
                    c['state_continuity'].update(owner_database=owner, required_station=goal)
                    for rel in c['inventory']['relations']:
                        if rel['source'] == 'ack_state':
                            rel['target'] = 'db' if owner == 'shared-db' else 'db_private'
                    for spec in (c['workloads']['business'], c['workloads']['business']['canary']):
                        spec['steps'] = [s for s in spec['steps'] if not s.get('station_goal_check')] + [goal_step(goal)]
                    for name in ('business', 'peer'):
                        c['workloads'][name]['offered_rps'] = rate
                        c['workloads'][name]['requests'] = int(rate * c['workloads'][name]['duration'])
                    configure_fault(c, fault)
                    c.update(family='native_state_conditioned_context_repair', stage=stage,
                             mode='fixed_observation_evidence', arms=list(ARMS), repeats=1, seed=20261003,
                             action_context_schema='action_context_v1',
                             station_registration=str(q / 'PREREGISTRATION.json'),
                             station_release=str(q / 'RELEASE.json'), record_nondegrading_attempts=False)
                    c['diagnosis_label_mapping'] = {str(i + 1): name for i, name in enumerate(FAULTS)}
                    c['inventory']['probes'] = [p for p in c['inventory']['probes'] if p['id'] in PROBES]
                    c['initial_probes'] = [p['id'] for p in c['inventory']['probes']]
                    c['collector_query_order_protocol'] = {
                        'schema': 'context_repair_five_application_probes_v1', 'initial': c['initial_probes'],
                        'configuration_bindings': 'actual Kubernetes GET configuration, not effect labels',
                        'omitted_probe': 'SQL shared_dependency stats; no database scale action in catalog'}
                    if stage == 'policy':
                        c.update(mode='goal_policy_comparison', arms=list(METHODS), initial_probes=[],
                                 goal_policy_authority='user_authorized_laboratory_only',
                                 goal_transition_model=str(model), goal_registration=str(q / 'PREREGISTRATION.json'),
                                 goal_release=str(q / 'RELEASE.json'), evidence_reuse_max_age_seconds=10.,
                                 retry_expired_reuse_once=True)
                        c['collector_query_order_protocol']['initial'] = []
                        c['goal_policy_protocol'] = {
                            'schema': 'context_repair_goal_v1', 'methods': list(METHODS),
                            'reuse_max_age_seconds': 10., 'expiry_retry_limit': 1,
                            'training_source': str(root / 'training_v1'),
                            'model': str(model), 'blind_head': 'same_training_data_coarse',
                            'structural_gate': 'shared_by_all_four_methods',
                            'scope': 'known-mechanism laboratory comparison; not production qualification'}
                    arms = [method] if stage == 'policy' else list(ARMS)
                    random.Random(20261003 + fi * 100 + ci * 10).shuffle(arms)
                    c['scheduled_jobs'] = []
                    for arm in arms:
                        c['scheduled_jobs'].append({'group_id': group, 'condition_id': cond,
                                                    'repeat': 0, 'arm': arm, 'original_schedule_slot': slot})
                        slot += 1
                    validate_state_goal(c)
                    config, out = q / 'configs' / (identity + '.json'), q / 'campaigns' / identity
                    save(config, c)
                    job = {'id': identity,
                           'command': [py, '-u', str(runtime / 'scripts/e2r/run_native_campaign.py'),
                                       '--config', str(config), '--out', str(out)],
                           'resources': ['namespace:' + ns],
                           'requires': [str(root / 'RELEASE.json'), str(q / 'RELEASE.json'), str(runtime / 'RUNTIME.json')],
                           'outputs': [{'path': str(out / 'summary.json'), 'json_equals': {'status': 'complete'}}]}
                    included = (ci == fi % 2 and (stage != 'policy' or mi == fi))
                    if stage in ('training', 'policy'):
                        if included:
                            first.append(identity)
                            if previous[stage]:
                                job['depends_on'] = previous[stage]
                        else:
                            job['depends_on'] = [stage + '_sanity']
                    else:
                        job['depends_on'] = previous[stage]
                    jobs.append(job)
                    assignments.append({'id': identity, 'stage': stage, 'config': str(config), 'out': str(out),
                                        'fault': fault, 'condition': cond, 'state_condition': condition,
                                        'repeat': 0, 'owner': owner, 'rate': rate, 'namespace': ns, 'method': method})
        random.Random(20261003).shuffle(jobs)
        summaries = [('sanity', first)] if stage in ('training', 'policy') else []
        summaries.append(('final', [a['id'] for a in assignments]))
        for phase, deps in summaries:
            jobs.append({'id': stage + ('_sanity' if phase == 'sanity' else '_results'), 'kind': 'gate',
                         'depends_on': deps,
                         'command': [py, '-u', str(runtime / 'scripts/e2r/summarize_context_repair.py'),
                                     '--root', str(root), '--stage', stage, '--phase', phase],
                         'outputs': [{'path': str(q / ('SANITY_GATE.json' if phase == 'sanity' else 'RESULTS.json')),
                                      'json_equals': {'status': 'passed' if phase == 'sanity' else 'complete'}}]})
        assert slot == settings['positions']
        save(q / 'manifest.json', {'cwd': str(ROOT), 'max_parallel': 4, 'jobs': jobs})
        save(q / 'PREREGISTRATION.json', {
            'created_at': preregistration_time, 'schema': 'context_repair_registration_v1', 'stage': stage,
            'cohort': 'context_repair_20261003_' + stage, 'planned_positions': slot, 'planned_attempts': slot,
            'owner': owner, 'rate': rate, 'assignments': assignments, 'included_first_jobs': first,
            'runtime': str(runtime), 'source_configuration': str(base_file),
            'model_protocol': {'fit_stage': 'training', 'seeds': [42, 43, 44],
                               'selection': 'arithmetic mean of all three fixed seeds',
                               'model_plan': str(root / 'MODEL_PLAN.json'), 'test_parameter_search': False,
                               'rejected_labels': 'missing, never zero'},
            'scope': 'Prospective known-mechanism configuration/load evaluation; no full-application or unseen-fault claim.'})
        all_jobs.extend(jobs)
    all_jobs.extend([
        {'id': 'fit_context_transition', 'kind': 'gate', 'depends_on': ['validation_results'],
         'command': [py, '-u', str(runtime / 'scripts/e2r/fit_context_transition.py'),
                     '--train-queue', str(root / 'training_v1'), '--validation-queue', str(root / 'validation_v1'),
                     '--out', str(root / 'model_v1')],
         'outputs': [{'path': str(root / 'model_v1/RESULTS.json'), 'json_equals': {'status': 'complete'}},
                     {'path': str(model)}]},
        {'id': 'evaluate_context_transition', 'kind': 'gate', 'depends_on': ['test_results'],
         'command': [py, '-u', str(runtime / 'scripts/e2r/evaluate_context_transition.py'),
                     '--queue', str(root / 'test_v1'), '--model', str(model), '--out', str(root / 'evaluation_v1')],
         'outputs': [{'path': str(root / 'evaluation_v1/RESULTS.json'), 'json_equals': {'status': 'complete'}}]},
        {'id': 'context_repair_report', 'kind': 'gate', 'depends_on': ['policy_results'],
         'command': [py, '-u', str(runtime / 'scripts/e2r/summarize_context_repair.py'), '--root', str(root)],
         'outputs': [{'path': str(root / 'RESULTS.json'), 'json_equals': {'status': 'complete'}}]},
    ])
    master = root / 'master_v1'
    master.mkdir()
    save(master / 'manifest.json', {'cwd': str(ROOT), 'max_parallel': 4,
                                   'max_dispatch_seconds': 10800, 'jobs': all_jobs})
    return {**{s: p['positions'] for s, p in PHASES.items()}, 'positions': 104,
            'jobs': len(all_jobs), 'master': str(master)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--runtime', required=True)
    parser.add_argument('--source', default=str(DEFAULT_SOURCE))
    args = parser.parse_args()
    print(json.dumps(build(args.root, args.runtime, args.source)))
