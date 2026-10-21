#!/usr/bin/env python3
"""Recompute block-aware descriptive metrics for the completed M2 pilot."""
from pathlib import Path
from collections import Counter, defaultdict
import argparse
import json
import statistics
import numpy as np

METRICS = {'AC@1': lambda rank: float(rank is not None and rank <= 1),
           'AC@3': lambda rank: float(rank is not None and rank <= 3),
           'AC@5': lambda rank: float(rank is not None and rank <= 5),
           'MRR': lambda rank: 0.0 if rank is None else 1.0 / rank}
ARMS = ('reference', 'runtime', 'rule_patch', 'llm_patch_no_fallback',
        'llm_patch_with_fallback', 'proposal_rank_no_gnn', 'direct_rank')


def mean_unit(rows, metric):
    """Average seeds, then generations, so incidents remain the evaluation units."""
    fn = METRICS[metric]
    by_generation = defaultdict(list)
    for row in rows:
        by_generation[row.get('generation')].append(fn(row.get('rank')))
    return statistics.mean(statistics.mean(values) for values in by_generation.values())


def bootstrap(values, seed=20260925):
    data = np.asarray(values, dtype=float)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(data), size=(5000, len(data)))
    samples = data[draws].mean(axis=1)
    return np.quantile(samples, [.025, .975]).tolist()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--score-dir', required=True)
    ap.add_argument('--campaign-dir', required=True)
    ap.add_argument('--out-dir', required=True)
    args = ap.parse_args()
    score_dir = Path(args.score_dir); campaign_dir = Path(args.campaign_dir)
    result = json.loads((score_dir / 'results.json').read_text())
    rows = [json.loads(line) for line in (score_dir / 'predictions.jsonl').read_text().splitlines() if line]
    campaigns = json.loads((campaign_dir / 'campaign.json').read_text())
    if (result['status'] != 'completed_m2_pilot_scoring'
            or result['cases'] != len(campaigns['cases'])
            or result['generations'] != campaigns['generations']
            or campaigns['generations'] != 2):
        raise ValueError('score/campaign scope mismatch or non-frozen generation count')
    started, finished = {}, {}
    for line in (campaign_dir / 'requests.jsonl').read_text().splitlines():
        event = json.loads(line); target = started if event['event'] == 'request_started' else finished
        if event['request_id'] in target: raise ValueError('duplicate request event')
        target[event['request_id']] = event
    if len(started) != campaigns['hard_request_budget'] or set(started) != set(finished):
        raise ValueError('request campaign does not match its completed hard budget')
    selected = [row['case_id'] for row in campaigns['cases']]
    by_key = defaultdict(list)
    row_keys = set()
    for row in rows:
        if row['case_id'] not in selected or row['arm'] not in ARMS:
            raise ValueError('unexpected prediction row')
        if row['status'] == 'ranked':
            if len(row['ranking']) != len(set(row['ranking'])) or row['rank'] != row['ranking'].index(row['true_service']) + 1:
                raise ValueError('saved rank/ranking mismatch')
        elif row.get('rank') is not None:
            raise ValueError('failed row has a rank')
        identity = (row['case_id'], row['arm'], row.get('generation'), row.get('seed'))
        if identity in row_keys:
            raise ValueError(f'duplicate prediction identity: {identity}')
        row_keys.add(identity)
        by_key[row['case_id'], row['arm']].append(row)
    expected_counts = {'reference': 3, 'runtime': 3, 'rule_patch': 3,
                       'llm_patch_no_fallback': 6, 'llm_patch_with_fallback': 6,
                       'proposal_rank_no_gnn': 2, 'direct_rank': 2}
    expected_keys = set()
    for case in selected:
        for arm in ('reference', 'runtime', 'rule_patch'):
            expected_keys.update((case, arm, None, seed) for seed in (42, 43, 44))
        for generation in range(campaigns['generations']):
            expected_keys.update((case, arm, generation, None)
                                 for arm in ('proposal_rank_no_gnn', 'direct_rank'))
            expected_keys.update((case, arm, generation, seed)
                                 for arm in ('llm_patch_no_fallback', 'llm_patch_with_fallback')
                                 for seed in (42, 43, 44))
    if row_keys != expected_keys:
        raise ValueError('prediction row identity coverage mismatch')
    for case in selected:
        for arm, count in expected_counts.items():
            if len(by_key[case, arm]) != count:
                raise ValueError(f'row coverage mismatch {case} {arm}')
    case_app = {row['case_id']: row['app'] for row in rows}
    groups = {'all': selected}
    for case in selected: groups.setdefault(case_app[case], []).append(case)
    metrics = {}
    per_case = {}
    for group, cases in groups.items():
        metrics[group] = {}
        for arm in ARMS:
            per_metric = {metric: [mean_unit(by_key[case, arm], metric) for case in cases] for metric in METRICS}
            metrics[group][arm] = {'incidents': len(cases),
                'ranked_row_coverage': sum(row['status'] == 'ranked' for case in cases for row in by_key[case, arm]) /
                                       sum(len(by_key[case, arm]) for case in cases),
                **{metric: {'mean': float(np.mean(values)), 'incident_bootstrap_95ci': bootstrap(values)}
                   for metric, values in per_metric.items()}}
            for case, value in zip(cases, per_metric['AC@1']):
                per_case.setdefault(group, {}).setdefault(case, {})[arm] = value
    comparisons = {}
    pairs = [('llm_patch_no_fallback', 'direct_rank'),
             ('llm_patch_with_fallback', 'direct_rank'),
             ('llm_patch_no_fallback', 'rule_patch'),
             ('llm_patch_no_fallback', 'proposal_rank_no_gnn')]
    for group, cases in groups.items():
        comparisons[group] = {}
        for left, right in pairs:
            values = [per_case[group][case][left] - per_case[group][case][right] for case in cases]
            comparisons[group][f'{left}-minus-{right}'] = {
                'AC@1_delta': float(np.mean(values)), 'incident_bootstrap_95ci': bootstrap(values),
                'n_incidents': len(cases)}
    request_counts = Counter()
    usage_totals = Counter(); usage_reported = 0; latency = [];provider_costs=[]
    error_counts = Counter()
    fallback_by_request = {}
    for row in rows:
        if row['arm'] != 'llm_patch_with_fallback': continue
        request_id = row['request_id']
        flag = bool(row['fallback_used'])
        if request_id in fallback_by_request and fallback_by_request[request_id] != flag:
            raise ValueError(f'fallback disagreement across seeds: {request_id}')
        fallback_by_request[request_id] = flag
    expected_proposals = len(selected) * campaigns['generations']
    if len(fallback_by_request) != expected_proposals:
        raise ValueError('fallback request coverage mismatch')
    for event in finished.values():
        route = event['route']; request_counts[f'{route}_attempts'] += 1
        if event.get('transport_success'):
            request_counts[f'{route}_transport_success'] += 1
            validation = event['validation']
            request_counts[f'{route}_ranking_valid'] += int(validation.get('ranking_valid', False))
            if route == 'proposal':
                request_counts['proposal_plan_valid'] += int(validation.get('plan_valid', False))
                request_counts['proposal_accepted'] += int(validation.get('accepted', False))
            error_counts.update(validation.get('errors', []))
        else:
            error_counts[event.get('error_type', 'unknown_transport_error')] += 1
        latency.append(event['request_seconds'])
        try:
            if event.get('provider_cost') is not None:provider_costs.append(float(event['provider_cost']))
        except (TypeError,ValueError):
            error_counts['invalid_provider_cost']+=1
        if event.get('usage') is not None:
            usage_reported += 1
            for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
                value = event['usage'].get(key)
                if isinstance(value, (int, float)): usage_totals[key] += value
    fallback_requests = sum(fallback_by_request.values())
    request_summary = {'actual_attempts': len(started), **dict(request_counts),
                       'fallback_requests': fallback_requests,
                       'fallback_rate': fallback_requests / len(fallback_by_request),
                       'usage_reported_attempts': usage_reported,
                       'usage_missing_attempts': len(finished) - usage_reported,
                       'reported_usage_totals': dict(usage_totals),
                       'provider_cost_reported_attempts':len(provider_costs),
                       'provider_cost_total':sum(provider_costs) if provider_costs else None,
                       'request_seconds_total': sum(latency),
                       'request_seconds_mean': statistics.mean(latency),
                       'request_seconds_p95': float(np.quantile(latency, .95)),
                       'errors': dict(error_counts)}
    output = {'status': 'completed_m2_pilot_analysis', 'evaluation_type': 'real_gt_pilot',
              'scope': {'incidents': len(selected), 'generations': campaigns['generations'], 'model_alias': campaigns['model_alias'],
                        'request_model': campaigns['request_model'], 'seeds': [42, 43, 44]},
              'requests': request_summary, 'metrics': metrics, 'paired_comparisons': comparisons,
              'limits': [f'Pilot only: {len(selected)} held-out incidents and one configured LLM backend.',
                         'Failed/invalid outputs score zero in unconditional accuracy and reciprocal rank.',
                         'Reference/runtime/rule/LLM-patch GNN arms reuse patch-trained M1 scorers.',
                         'Bootstrap intervals are descriptive incident-blocked intervals for this fixed pilot.',
                         'No live recovery, LLM remediation or external baseline conclusion.']}
    out = Path(args.out_dir)
    if out.exists(): raise SystemExit('Refusing to overwrite M2 analysis')
    out.mkdir(parents=True)
    (out / 'analysis.json').write_text(json.dumps(output, indent=2) + '\n')
    lines = ['# E2R M2 新LLM图补丁小试验', '',
             f'这是{len(selected)}个固定测试事件、每事件两次生成的先导试验。失败或无效输出在无条件指标中按0计，不从分母删除。', '',
             '| 分组 | 路线 | 有效行覆盖 | AC@1 | AC@3 | AC@5 | MRR |',
             '|---|---|---:|---:|---:|---:|---:|']
    for group, arms in metrics.items():
        for arm, values in arms.items():
            lines.append('| ' + ' | '.join([group, arm, f"{values['ranked_row_coverage']:.3f}",
                *[f"{values[m]['mean']:.4f}" for m in METRICS]]) + ' |')
    lines += ['', '## 请求与有效性', '', f"- 实际HTTP请求：{request_summary['actual_attempts']}/{campaigns['hard_request_budget']}。",
              f"- proposal有效计划：{request_summary.get('proposal_plan_valid', 0)}/{request_summary.get('proposal_attempts', 0)}；有效完整排名：{request_summary.get('proposal_ranking_valid', 0)}/{request_summary.get('proposal_attempts', 0)}。",
              f"- direct有效完整排名：{request_summary.get('direct_ranking_valid', 0)}/{request_summary.get('direct_attempts', 0)}。",
              f"- 确定性规则回退：{fallback_requests}/{len(fallback_by_request)}个proposal请求。",
              f"- provider usage已报告：{usage_reported}/{len(finished)}；缺失usage保持unknown，不计作0。",
              '', '## 解释边界', '', *['- ' + text for text in output['limits']]]
    (out / 'REPORT.md').write_text('\n'.join(lines) + '\n')
    print(json.dumps(request_summary, indent=2))


if __name__ == '__main__':
    main()
