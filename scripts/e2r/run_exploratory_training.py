#!/usr/bin/env python3
"""Fixed v2 development fitting and known-configuration transfer evaluation."""
import argparse
import copy
import json
from pathlib import Path
import resource
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.e2r.run_revision_training import load_development_records
from methods.E2R.intervention.data import group_key, validate_record
from methods.E2R.intervention.learning_v2 import check_partitions, fit_v2, RelationalPredictor, save
from methods.E2R.intervention.study_analysis_v2 import prediction_metrics_v2


def load_records(plan, *, include_transfer=False):
    original = json.loads(Path(plan['original_split']).read_text())
    rows, split = load_development_records(plan['historical_data'], original)
    added = plan['added_split']
    split['train'] = split['train'] + added['train']
    split['transfer'] = added['transfer']
    expected = set(added['train'] + added['transfer'])
    if len(expected) != len(added['train']) + len(added['transfer']):
        raise ValueError('new train/transfer groups overlap')
    new_rows = [json.loads(line) for line in Path(plan['added_data']).read_text().splitlines() if line.strip()]
    if {group_key(r) for r in new_rows} != expected:
        raise ValueError('new data differs from declared groups')
    if any(r['source'] != 'live' for r in new_rows):
        raise ValueError('only live prospective records are allowed')
    all_rows = rows + new_rows
    if len({r['episode_id'] for r in all_rows}) != len(all_rows):
        raise ValueError('duplicate merged attempt identity')
    # Uses identifiers only: keep all conditions/replicas of a parent together.
    check_partitions(all_rows, split)
    included = [r for r in new_rows if include_transfer or group_key(r) in added['train']]
    for row in included:
        validate_record(row)
    return rows + included, split


def evaluate(rows, predictor, *, view, minimum_repeats):
    # All models see the identical saved prefix. This is input ablation at
    # inference on a full-trained model, not an initial-only trained model.
    observed = copy.copy(predictor)
    observed.metadata = {**predictor.metadata, 'input_view': view}
    result = prediction_metrics_v2(rows, observed, minimum_repeats)
    result['training_input_view'] = predictor.metadata.get('input_view', 'full')
    result['evaluation_input_view'] = view
    result['scope'] = 'exploratory known configurations; no independent confirmation or policy execution'
    return result


def run(plan_path, identity):
    plan = json.loads(Path(plan_path).read_text())
    entry = next(m for m in plan['models'] if m['id'] == identity)
    profile = entry.get('profile', False)
    if str(ROOT) != plan['runtime']:
        raise ValueError('use the frozen exploratory training runtime')
    release = json.loads(Path(plan['review_release']).read_text())
    if release.get('status') != 'passed' or release.get('scope') != 'exploratory_training_no_live_authorization':
        raise ValueError('fresh training review is required')
    if not profile:
        gate = json.loads(Path(plan['native_profile_gate']).read_text())
        if gate.get('status') != 'passed' or gate.get('test_evaluated') is not False or gate.get('transfer_evaluated') is not False:
            raise ValueError('native resource/sanity profile is required')
    started = time.time()
    rows, split = load_records(plan)
    # Transfer rows never enter fit_v2, target construction, normalizer or epoch selection.
    result = fit_v2(rows, split, entry['out'], run_metadata={
        'plan': str(Path(plan_path).resolve()), 'id': identity, 'scope': plan['scope'],
    }, **entry['parameters'])
    predictor = RelationalPredictor(entry['out'], seeds=entry['parameters']['seeds'])
    if not profile:
        rows, _ = load_records(plan, include_transfer=True)
    partitions = ('development',) if profile else ('development', 'transfer')
    summaries = {}
    for partition in partitions:
        selected = [r for r in rows if group_key(r) in split[partition]]
        for view in ('full', 'initial'):
            metrics = evaluate(selected, predictor, view=view, minimum_repeats=entry['parameters']['minimum_repeats'])
            name = partition + '_' + view
            save(Path(entry['out']) / (name + '.json'), metrics)
            summaries[name] = {k: v for k, v in metrics.items() if k not in ('predictions', 'distinguishability')}
            summaries[name]['distinguishability'] = {k: v for k, v in metrics['distinguishability'].items() if k != 'pairs'}
    report = {'status': 'complete', 'id': identity, 'runtime': str(ROOT),
              'test_evaluated': False, 'transfer_evaluated': not profile,
              'admission_status': 'uncalibrated_no_live_authorization',
              'wall_seconds': time.time() - started,
              'peak_rss_mib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
              'completed_at': time.time(), 'training_summary': result['seeds'],
              'train_pair_count': result['training']['pair_count'],
              'train_pair_kind_counts': result['training']['pair_kind_counts'],
              'metrics': summaries}
    save(Path(entry['out']) / 'EXPLORATORY_DONE.json', report)
    print(json.dumps({k: v for k, v in report.items() if k != 'metrics'}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True)
    parser.add_argument('--id', required=True)
    args = parser.parse_args()
    run(args.plan, args.id)
