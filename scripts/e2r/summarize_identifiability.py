#!/usr/bin/env python3
"""Describe the preregistered symmetric pilot; never infer labels from bindings."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import time


def summarize(rows):
    arms = ('no_action', 'switch_standby', 'switch_alternative', 'rollback_active')
    conditions = ('affinity_A', 'affinity_B')
    cells = defaultdict(list)
    repeats = defaultdict(list)
    for row in rows:
        if row['condition_id'] not in conditions or row['chosen_plan'] not in arms:
            raise ValueError('unexpected condition or arm in bounded pilot')
        cells[(row['condition_id'], row['chosen_plan'])].append(row)
        repeats[(row['repeat'], row['condition_id'], row['chosen_plan'])].append(row)
    result = {}
    for condition in conditions:
        result[condition] = {}
        for arm in arms:
            samples = cells[(condition, arm)]
            effects = [r.get('outcome', {}).get('effects', {}) for r in samples]
            result[condition][arm] = {'attempts': len(samples),
                'recovery_true': sum(e.get('business_recovered') is True for e in effects),
                'recovery_false': sum(e.get('business_recovered') is False for e in effects),
                'recovery_unknown': sum(e.get('business_recovered') is None for e in effects),
                'safe_true': sum(e.get('non_target_safe') is True for e in effects),
                'safe_false': sum(e.get('non_target_safe') is False for e in effects),
                'safe_unknown': sum(e.get('non_target_safe') is None for e in effects),
                'restoration_failures': sum(r.get('restore', {}).get('status') != 'restored' for r in samples),
                'attempt_ids': [r['episode_id'] for r in samples]}
    comparisons = []
    for repeat in (0, 1):
        pair = {'repeat': repeat}
        for condition in conditions:
            values = []
            for arm in ('switch_standby', 'switch_alternative'):
                samples = repeats[(repeat, condition, arm)]
                # Retries remain in the denominator and make this two-repeat
                # mechanism gate inconclusive; never choose the nicest retry.
                effects = samples[0].get('outcome', {}).get('effects', {}) if len(samples) == 1 else {}
                known = all(type(effects.get(key)) is bool for key in ('business_recovered', 'non_target_safe'))
                values.append(int(effects['business_recovered'] and effects['non_target_safe']) if known else None)
            pair[condition] = {'safe_recovery_standby': values[0], 'safe_recovery_alternative': values[1],
                               'alternative_minus_standby': values[1] - values[0] if None not in values else None}
        comparisons.append(pair)
    exact = (len(rows) == 16 and len({r['episode_id'] for r in rows}) == 16
             and all(len(repeats[(r, c, a)]) == 1 for r in (0, 1) for c in conditions for a in arms))
    restoration_ok = all(r.get('restore', {}).get('status') == 'restored' for r in rows)
    differences = [p[c]['alternative_minus_standby'] for p in comparisons for c in conditions]
    if not exact or not restoration_ok or None in differences:
        verdict = 'inconclusive_missing_unknown_or_repeated_attempts'
    elif any(comparisons[0][condition] != comparisons[1][condition] for condition in conditions):
        verdict = 'inconclusive_inconsistent_two_repeats_stop_expansion'
    elif differences[0] * differences[1] < 0:
        verdict = 'coherent_crossover_feasibility_only_new_preregistered_replication_required'
    elif all(v >= 0 for v in differences) or all(v <= 0 for v in differences):
        verdict = 'stop_design_fixed_destination_weakly_dominates_or_no_difference'
    else:
        verdict = 'inconclusive_inconsistent_two_repeats_stop_expansion'
    return {'schema': 'symmetric_native_mechanism_feasibility_v1', 'checked_at': time.time(),
            'attempts': len(rows), 'exact_16_unique_design': exact, 'all_restored': restoration_ok, 'cells': result,
            'repeat_comparisons': comparisons, 'verdict': verdict,
            'crossover_direction': ('expected_avoid_shared' if differences == [1,-1,1,-1] else
                                    'reverse_to_hypothesis' if differences == [-1,1,-1,1] else None),
            'claims': {'C1': 'not_established', 'C2': 'not_evaluated'},
            'scope': 'One native Cart/Redis dependency design, joint peer/background assignment; descriptive two-repeat feasibility.',
            'controls': 'Report all four arms. A coherent switch crossover does not establish superiority over rollback or the explicit dependency rule.',
            'uncertainty': 'No inferential significance or independent configuration generalization from these two replicas.'}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--episodes', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    rows = [json.loads(line) for line in a.episodes.read_text().splitlines() if line.strip()]
    with a.out.open('x') as stream:
        json.dump(summarize(rows), stream, indent=2, allow_nan=False)
        stream.write('\n')
