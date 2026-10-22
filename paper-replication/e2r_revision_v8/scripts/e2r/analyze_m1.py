#!/usr/bin/env python3
"""Recompute M1 metrics and paired incident bootstrap from completed raw rankings."""
from pathlib import Path
import argparse
from collections import defaultdict
import json
import statistics
import numpy as np

METRICS = {'AC@1': lambda r: float(r <= 1), 'AC@3': lambda r: float(r <= 3),
           'AC@5': lambda r: float(r <= 5), 'MRR': lambda r: 1.0 / r}
VARIANTS = ['reference', 'runtime', 'patch']


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--run-dir', required=True)
    ap.add_argument('--manifest', default='experiments/e2r/runs/full_data_v2/manifest.json')
    ap.add_argument('--out-dir', required=True)
    args = ap.parse_args()
    run = Path(args.run_dir); out = Path(args.out_dir)
    if out.exists():
        raise SystemExit('Use a new analysis output directory')
    result = json.loads((run / 'results.json').read_text())
    assert result['status'] == 'completed_offline_structural_ablation', 'Formal results required, not smoke'
    split = json.loads((run / 'split.json').read_text())
    assert set(split) == {'train', 'val', 'test'}
    assert all(ids and len(ids) == len(set(ids)) for ids in split.values())
    assert not (set(split['train']) & set(split['val']) or set(split['train']) & set(split['test']) or set(split['val']) & set(split['test']))
    manifest = {r['case_id']: r for r in json.loads(Path(args.manifest).read_text())['cases']}
    assert set().union(*(set(ids) for ids in split.values())) == set(manifest)
    predictions = [json.loads(line) for line in (run / 'predictions.jsonl').read_text().splitlines() if line]
    seeds = result['seeds']; index = {}
    assert len(seeds) == 3 and set(seeds) == {42, 43, 44}, 'Frozen M1 requires all three seeds'
    for seed in seeds:
        assert (run / f'model_seed{seed}.pt').is_file()
    for row in predictions:
        key = row['seed'], row['case_id'], row['variant']
        assert key not in index
        assert row['true_service'] == manifest[row['case_id']]['true_service']
        assert row['app'] == manifest[row['case_id']]['app']
        assert len(row['ranking']) == len(set(row['ranking']))
        assert row['rank'] == row['ranking'].index(row['true_service']) + 1
        index[key] = row
    assert set(index) == {(s, c, v) for s in seeds for c in split['test'] for v in VARIANTS}
    groups = {'all': split['test']}
    for case in split['test']:
        app = manifest[case]['app']
        groups.setdefault(app, []).append(case)
    summaries = {}; contrasts = {}
    for group, cases in groups.items():
        summaries[group] = {}; contrasts[group] = {}
        for variant in VARIANTS:
            by_seed = {str(seed): {metric: statistics.mean(fn(index[seed, c, variant]['rank']) for c in cases)
                                  for metric, fn in METRICS.items()} for seed in seeds}
            summaries[group][variant] = {'incidents': len(cases), 'by_seed': by_seed,
                'mean': {m: statistics.mean(row[m] for row in by_seed.values()) for m in METRICS},
                'sample_std': {m: statistics.stdev(row[m] for row in by_seed.values()) for m in METRICS}}
            if group == 'all':
                for seed in seeds:
                    for metric in METRICS:
                        assert abs(by_seed[str(seed)][metric] - result['summary'][f'{seed}:{variant}'][metric]) < 1e-12
        for baseline in ['reference', 'runtime']:
            paired = {}
            for metric, fn in METRICS.items():
                deltas = np.array([statistics.mean(fn(index[s, c, 'patch']['rank']) - fn(index[s, c, baseline]['rank']) for s in seeds) for c in cases])
                # Same incident remains paired across arms; seeds aren't extra test cases.
                rng = np.random.default_rng(20260925)
                draws = rng.integers(0, len(cases), size=(5000, len(cases)))
                boot = deltas[draws].mean(axis=1)
                delta = float(deltas.mean()); base = summaries[group][baseline]['mean'][metric]
                paired[metric] = {'absolute_delta': delta,
                    'relative_delta': delta / base if base else None,
                    'incident_bootstrap_95ci': np.quantile(boot, [.025, .975]).tolist(),
                    'n_incidents': len(cases), 'bootstrap_resamples': 5000}
            contrasts[group]['patch-minus-' + baseline] = paired
    output = {'source_run': str(run.resolve()), 'validation': 'raw predictions recomputed; labels, split and summary checked',
        'seeds': seeds, 'split_counts': {k: len(v) for k, v in split.items()}, 'metrics': summaries,
        'paired_contrasts': contrasts, 'runtime': result['runtime'],
        'training': [{k: row[k] for k in ['seed', 'epochs', 'seconds']} for row in result['training']],
        'limits': ['Rule-patch M1 only; no LLM or recovery claim.',
                   'Each scorer was trained and validated on patch graphs only; this is input substitution for a patch-trained model, not separately trained baseline performance.',
                   'TrainTicket has no diagnostic-period telemetry features in the current snapshots.',
                   'Bootstrap resamples held-out incidents after averaging seeds; conditional on one fixed split, not a new-data/generalization guarantee.',
                   'Intervals are descriptive, without correction for multiple comparisons.']}
    out.mkdir(parents=True)
    (out / 'analysis.json').write_text(json.dumps(output, indent=2) + '\n')
    lines = ['# M1 固定权重结构对照结果', '', '原始逐案例预测已重新计分，并核对标签、split、三seed×三变体覆盖和汇总一致性。下表为seed均值±样本标准差。', '',
             '| 分组 | 图 | 测试案例 | AC@1 | AC@3 | AC@5 | MRR |', '|---|---|---:|---:|---:|---:|---:|']
    for group, variants in summaries.items():
        for variant, data in variants.items():
            values = [f"{data['mean'][m]:.4f} ± {data['sample_std'][m]:.4f}" for m in METRICS]
            lines.append('| ' + ' | '.join([group, variant, str(data['incidents']), *values]) + ' |')
    lines += ['', '## 配对比较', '', '| 分组 | 比较 | AC@1差值 | 95%事件bootstrap区间 |', '|---|---|---:|---|']
    for group, pairs in contrasts.items():
        for name, data in pairs.items():
            v = data['AC@1'];lo, hi = v['incident_bootstrap_95ci']
            lines.append(f"| {group} | {name} | {v['absolute_delta']:+.4f} | [{lo:+.4f}, {hi:+.4f}] |")
    lines += ['', '## 解释范围', '', *['- ' + item for item in output['limits']]]
    (out / 'REPORT.md').write_text('\n'.join(lines) + '\n')
    print(json.dumps(summaries, indent=2))


if __name__ == '__main__':
    main()
