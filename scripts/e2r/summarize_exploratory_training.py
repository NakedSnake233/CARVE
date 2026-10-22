#!/usr/bin/env python3
"""Describe every preregistered model; never select a winner using transfer scores."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics
import sys
import time
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from methods.E2R.intervention.learning_v2 import save
from methods.E2R.intervention.study_analysis import paired_prediction_difference


def summarize(plan_path, out):
    plan = json.loads(Path(plan_path).read_text())
    reports, metrics, grouped = {}, {}, defaultdict(list)
    entries = [m for m in plan['models'] if not m.get('profile')]
    if len(entries) != 18:
        raise ValueError('the registered matrix requires all 18 models')
    for entry in entries:
        directory = Path(entry['out'])
        report = json.loads((directory / 'EXPLORATORY_DONE.json').read_text())
        if (report['status'] != 'complete' or report['test_evaluated'] is not False
                or report['transfer_evaluated'] is not True or report['id'] != entry['id']):
            raise ValueError('incomplete or mismatched model result')
        reports[entry['id']] = report
        p = entry['parameters']
        for part in ('development', 'transfer'):
            for view in ('full', 'initial'):
                key = part + '_' + view
                value = json.loads((directory / (key + '.json')).read_text())
                metrics[entry['id'], key] = value
                grouped[p['variant'], p['objective'], key].append({
                    'id': entry['id'], 'seed': p['seeds'][0],
                    'business_brier': value['effects']['business_recovered']['brier'],
                    'safety_brier': value['effects']['non_target_safe']['brier'],
                    'pair_count': value['distinguishability']['scored_pairs'],
                    'pair_distance_mae': value['distinguishability']['distance_mae'],
                    'attempts': value['attempts'], 'statuses': value['failures'],
                    'business_unknown_or_unpredicted': value['effects']['business_recovered']['unknown_or_unpredicted'],
                    'safety_unknown_or_unpredicted': value['effects']['non_target_safe']['unknown_or_unpredicted']})
    summaries = []
    for (variant, objective, evaluation), values in grouped.items():
        result = {'variant': variant, 'objective': objective, 'evaluation': evaluation, 'seeds': values}
        for metric in ('business_brier', 'safety_brier', 'pair_distance_mae'):
            known = [v[metric] for v in values if v[metric] is not None]
            result[metric] = {'n': len(known), 'mean': statistics.mean(known) if known else None,
                              'sample_std': statistics.stdev(known) if len(known) > 1 else None}
        summaries.append(result)
    comparisons = []
    for seed in (42, 43, 44):
        left = f'graph_effect_distinguish_s{seed}'
        for control in ('set_effect_distinguish', 'local_effect_distinguish', 'graph_effect', 'graph_diagnosis', 'graph_state'):
            right = f'{control}_s{seed}'
            for evaluation in ('development_full', 'development_initial', 'transfer_full', 'transfer_initial'):
                for effect in ('business_recovered', 'non_target_safe'):
                    difference = paired_prediction_difference(metrics[left, evaluation]['predictions'], metrics[right, evaluation]['predictions'], effect)
                    comparisons.append({'left': left, 'right': right, 'evaluation': evaluation, **difference})
    out = Path(out)
    if out.exists():
        raise ValueError('refuse to overwrite existing aggregate results')
    result = {'status': 'complete', 'completed_at': time.time(), 'models_completed': len(entries),
              'test_evaluated': False, 'summaries': summaries, 'paired_comparisons': comparisons,
              'compute': {'total_worker_wall_seconds': sum(r['wall_seconds'] for r in reports.values()),
                          'maximum_worker_peak_rss_mib': max(r['peak_rss_mib'] for r in reports.values())},
              'training_pairs': {k: {'count': v['train_pair_count'], 'kinds': v['train_pair_kind_counts']} for k, v in reports.items()},
              'claims': {'C1': 'requires_fresh_result_to_claim', 'C2': 'not_evaluated_by_offline_prediction'},
              'scope': 'Exploratory repeated known configurations. Transfer has only two application/configuration groups. Bootstrap intervals are descriptive, not independent confirmation; seeds are not new data groups. Initial-view scoring does not measure no-query policy efficacy. No model or threshold selected from these scores.'}
    save(out, result)
    print(json.dumps({k: result[k] for k in ('status', 'models_completed', 'test_evaluated', 'compute')}))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan', required=True)
    p.add_argument('--out', required=True)
    a = p.parse_args()
    summarize(a.plan, a.out)
