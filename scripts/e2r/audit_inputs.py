#!/usr/bin/env python3
"""Describe real input coverage and exact effective graph differences; no training."""
from pathlib import Path
import argparse
from collections import Counter, defaultdict
import json
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from methods.E2R.graph import graphs_for, tensors, use_upstream
from methods.E2R.paths import default_dyrca_root, observation_file
from scripts.e2r.run_graph_experiment import split_cases


def signature(item):
    # Canonicalize edge order while preserving multiplicity and actual tensor values.
    edges = sorted((a, b, w) for (a, b), w in zip(item['edge_index'].t().tolist(), item['edge_weight'].tolist()))
    return item['node_ids'], item['x'].tolist(), edges


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--manifest', default=str(ROOT / 'experiments/e2r/runs/full_data_v2/manifest.json'))
    ap.add_argument('--out-dir', required=True)
    args = ap.parse_args()
    out = Path(args.out_dir)
    if out.exists():
        raise SystemExit('Use a new audit output directory')
    use_upstream(default_dyrca_root())
    manifest = json.loads(Path(args.manifest).read_text())
    split = {row['case_id']: name for name, rows in split_cases(manifest['cases'], 42).items() for row in rows}
    rows = []
    for row in manifest['cases']:
        obs = json.loads(observation_file(row, args.manifest).read_text())
        graphs, catalog, plan = graphs_for(obs)
        inputs = {name: tensors(graph, obs) for name, graph in graphs.items()}
        canonical = {name: signature(item) for name, item in inputs.items()}
        # These eight channels are the ones actually consumed by feature_vector.
        channels = {'cpu_usage', 'latency_p99', 'request_rate', 'service_up', 'trace_ms', 'trace_error', 'log_error'}
        during = {key.split(':', 1)[1] for stats in obs['observed_metrics'].values()
                  for key in stats if key.startswith('during:') and key.split(':', 1)[1] in channels}
        rows.append({'case_id': row['case_id'], 'app': row['app'], 'scenario': row['scenario'],
                     'split': split[row['case_id']], 'during_feature_channels': sorted(during),
                     'has_during_telemetry_features': bool(during), 'operations': len(catalog),
                     'candidate_count': len(obs['candidates']),
                     'graph_sizes': {name: {'nodes': len(item['node_ids']), 'directed_edges': len(item['edge_weight'])} for name, item in inputs.items()},
                     'patch_equals_reference': canonical['patch'] == canonical['reference'],
                     'patch_equals_runtime': canonical['patch'] == canonical['runtime'],
                     'runtime_equals_reference': canonical['runtime'] == canonical['reference'],
                     'coverage': obs['coverage']})
    groups = defaultdict(list)
    for row in rows:
        for key in ['all', row['app'], row['app'] + ':' + row['scenario'], 'split:' + row['split']]:
            groups[key].append(row)
    summary = {}
    for key, group in sorted(groups.items()):
        summary[key] = {'n': len(group), 'with_during_telemetry_features': sum(r['has_during_telemetry_features'] for r in group),
                        'zero_operation_cases': sum(r['operations'] == 0 for r in group),
                        **{name: sum(r[name] for r in group) for name in ['patch_equals_reference', 'patch_equals_runtime', 'runtime_equals_reference']},
                        'operations_total': sum(r['operations'] for r in group)}
    out.mkdir(parents=True)
    (out / 'cases.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
    (out / 'summary.json').write_text(json.dumps({'status': 'input_audit_only_no_training', 'cases': len(rows), 'groups': summary}, indent=2) + '\n')
    lines = ['# E2R 输入覆盖与图差异检查', '', '本检查没有训练或性能结论。相等指候选节点顺序、实际节点特征和带权边多重集相等（忽略边存储顺序）。', '',
             '| 分组 | 案例数 | 有诊断期遥测特征 | 无补丁操作 | patch=reference | patch=runtime |',
             '|---|---:|---:|---:|---:|---:|']
    for key, value in summary.items():
        lines.append('| ' + ' | '.join(str(v) for v in [key, value['n'], value['with_during_telemetry_features'], value['zero_operation_cases'], value['patch_equals_reference'], value['patch_equals_runtime']]) + ' |')
    lines += ['', '遥测缺失不等于无输入：拓扑/副本/endpoint仍作为观测特征。图不同也不等于模型排名有改善；应与M1逐案例预测配对解释。']
    (out / 'REPORT.md').write_text('\n'.join(lines) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
