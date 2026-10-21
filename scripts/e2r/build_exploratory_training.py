#!/usr/bin/env python3
"""Materialize the predeclared 18-model matrix; never choose from outcomes."""
import argparse
import json
from pathlib import Path
import time


def build(root, out, runtime, queue):
    root, out, runtime, queue = (Path(p).resolve() for p in (root, out, runtime, queue))
    out.mkdir(parents=True, exist_ok=False)
    queue.mkdir(parents=True, exist_ok=False)
    v2 = root / 'enhancement/revision-stage/v2'
    historical = root / 'enhancement/data/native_formal_20261001_v1'
    apps = ('boutique-native-dependency-slice', 'trainticket-native-dependency-slice')
    plan = {'schema': 'exploratory_training_plan_v1', 'created_at': time.time(),
            'scope': 'Known development configuration replications; full input fitting, historical validation checkpoint selection; old test excluded. No live policy authorization.',
            'runtime': str(runtime), 'historical_data': str(historical / 'episodes.jsonl'),
            'original_split': str(historical / 'base_split.json'),
            'added_data': str(v2 / 'data/exploratory_v1/episodes.jsonl'),
            'added_summary': str(v2 / 'data/exploratory_v1/summary.json'),
            'added_split': {'train': [app + '/v2explore-dependency-low-01' for app in apps],
                            'transfer': [app + '/v2explore-dependency-stressed-01' for app in apps]},
            'review_release': str(out / 'RELEASE.json'), 'native_profile_gate': str(out / 'PROFILE_GATE.json'),
            'input_evaluation': 'full and saved initial prefix for every model; initial is an inference input ablation of full-trained models, not initial-only fitting or no-query policy efficacy',
            'models': []}
    cells = [('graph', 'effect_distinguish'), ('set', 'effect_distinguish'), ('local', 'effect_distinguish'),
             ('graph', 'effect'), ('graph', 'diagnosis'), ('graph', 'state')]
    for variant, objective in cells:
        for seed in (42, 43, 44):
            identity = f'{variant}_{objective}_s{seed}'
            plan['models'].append({'id': identity, 'out': str(out / 'models' / identity), 'parameters': {
                'variant': variant, 'objective': objective, 'epochs': 120, 'hidden': 64, 'layers': 3,
                'seeds': [seed], 'learning_rate': .003, 'distinguish_weight': .2, 'minimum_repeats': 5, 'input_view': 'full'}})
    # Engineering profiles exercise every distinct objective's training branch;
    # all use train/development only and are excluded from the 18 research runs.
    profiles = []
    for variant, objective in cells:
        identity = f'profile_{variant}_{objective}'
        parameters = {**next(m['parameters'] for m in plan['models'] if m['parameters']['variant'] == variant and m['parameters']['objective'] == objective), 'epochs': 2}
        profiles.append({'id': identity, 'out': str(out / 'profiles' / identity), 'profile': True, 'parameters': parameters})
    plan['models'].extend(profiles)
    plan_path = out / 'PLAN.json'
    jobs = []
    for entry in plan['models']:
        if entry.get('profile'):
            continue
        jobs.append({'id': entry['id'], 'command': [str(root / '.venv-e2r/bin/python'), '-u',
            str(runtime / 'scripts/e2r/run_exploratory_training.py'), '--plan', str(plan_path), '--id', entry['id']],
            'resources': ['model:' + entry['id']],
            'requires': [plan['review_release'], plan['native_profile_gate']],
            'outputs': [{'path': str(Path(entry['out']) / 'EXPLORATORY_DONE.json'),
                         'json_equals': {'status': 'complete', 'test_evaluated': False, 'transfer_evaluated': True}}]})
    result_path = out / 'RESULTS.json'
    research_count = len(jobs)
    jobs.append({'id': 'aggregate_results', 'kind': 'analysis', 'depends_on': [j['id'] for j in jobs],
                 'command': [str(root / '.venv-e2r/bin/python'), '-u',
                             str(runtime / 'scripts/e2r/summarize_exploratory_training.py'),
                             '--plan', str(plan_path), '--out', str(result_path)],
                 'outputs': [{'path': str(result_path), 'json_equals': {'status': 'complete', 'models_completed': 18, 'test_evaluated': False}}]})
    plan_path.write_text(json.dumps(plan, indent=2) + '\n')
    (queue / 'manifest.json').write_text(json.dumps({'cwd': str(root), 'max_parallel': 2, 'jobs': jobs}, indent=2) + '\n')
    print(json.dumps({'plan': str(plan_path), 'queue': str(queue), 'research_models': research_count, 'engineering_profiles': len(profiles)}))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', required=True)
    p.add_argument('--runtime', required=True)
    p.add_argument('--queue', required=True)
    a = p.parse_args()
    build(Path(__file__).resolve().parents[2], a.out, a.runtime, a.queue)
