#!/usr/bin/env python3
"""Synthetic paired-incident proxy for bounded multi-root sensitivity."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from methods.E2R.graph import graphs_for, owner, tensors, use_upstream
from methods.E2R.paths import default_dyrca_root, observation_file
from scripts.e2r.run_graph_experiment import forward, move_item, resolve_device


def make_pairs(rows: Sequence[Dict[str, Any]], seed: int = 20260926):
    """Greedily pair each case once, preferring a different scenario and root."""
    rng = random.Random(seed)
    remaining = sorted(rows, key=lambda row: row['case_id'])
    rng.shuffle(remaining)
    pairs = []
    while len(remaining) >= 2:
        left = remaining.pop()
        eligible = [
            (index, right) for index, right in enumerate(remaining)
            if right['app'] == left['app']
            and right['true_service'] != left['true_service']
        ]
        if not eligible:
            continue
        eligible.sort(key=lambda item: (
            item[1]['scenario'] == left['scenario'], item[1]['case_id']))
        best_score = eligible[0][1]['scenario'] == left['scenario']
        tied = [item for item in eligible
                if (item[1]['scenario'] == left['scenario']) == best_score]
        index, right = rng.choice(tied)
        remaining.pop(index)
        pairs.append((left, right))
    used = {row['case_id'] for pair in pairs for row in pair}
    leftovers = [row for row in rows if row['case_id'] not in used]
    return sorted(pairs, key=lambda pair: (pair[0]['app'], pair[0]['case_id'],
                                           pair[1]['case_id'])), leftovers


def _relation_union(left, right):
    """Union directed relations; aggregate source-window counts by maximum."""
    relations = {}
    duplicates = 0
    for edge in list(left) + list(right):
        key = (edge.get('source'), edge.get('target'))
        if not all(isinstance(value, str) and value for value in key):
            raise ValueError('call edge lacks a service identity')
        if key in relations:
            duplicates += 1
            previous = relations[key]
            previous['count'] = max(int(previous.get('count', 0) or 0),
                                    int(edge.get('count', 0) or 0))
        else:
            relations[key] = copy.deepcopy(edge)
    return [relations[key] for key in sorted(relations)], duplicates


def _valid_endpoint_ip(value):
    return isinstance(value,str) and value.strip().lower() not in {'','unknown','none','<none>'}


def _phase_merge(base_phase, overlay_phase, root, candidates, pair_id, phase):
    overlay_services = [row for row in overlay_phase['services']
                        if row.get('name') == root]
    if len(overlay_services) != 1:
        raise ValueError('overlay root must have exactly one Service observation')
    root_service = copy.deepcopy(overlay_services[0])
    endpoint_ips = set(root_service.get('endpoint_ips') or [])
    selected_pods = [row for row in overlay_phase['pods']
                     if owner(row.get('service'), candidates) == root
                     or row.get('ip') in endpoint_ips]
    tag = hashlib.sha256(pair_id.encode()).hexdigest()[:10]
    ip_map = {};imported_pods = []
    for pod in sorted(selected_pods,
                      key=lambda row: (row.get('name',''),row.get('ip',''))):
        value = copy.deepcopy(pod)
        source_name = pod.get('name')
        if not isinstance(source_name,str) or not source_name:
            raise ValueError('selected overlay pod lacks stable source name')
        identity = hashlib.sha256(
            f'{pair_id}:pod:{source_name}'.encode()).hexdigest()[:10]
        value['name'] = f"{source_name}__mr_{identity}"
        original_ip = pod.get('ip')
        if _valid_endpoint_ip(original_ip):
            if original_ip in ip_map:
                raise ValueError('overlay source assigns one IP to multiple selected pods')
            ip_identity = hashlib.sha256(
                f'{pair_id}:pod:{source_name}:ip:{original_ip}'.encode()
            ).hexdigest()[:16]
            synthetic_ip = f'mr-{ip_identity}'
            ip_map[original_ip] = synthetic_ip;value['ip'] = synthetic_ip
        else:
            value['ip'] = None
        imported_pods.append(value)
    missing = sorted(endpoint_ips - set(ip_map))
    if missing:
        raise ValueError('overlay root has endpoint IPs without source pods')
    root_service['endpoint_ips'] = [ip_map[ip] for ip in root_service.get('endpoint_ips',[])]
    cross_service_root_ips = {
        ip for service in base_phase['services'] if service.get('name') != root
        for ip in service.get('endpoint_ips', [])
    }
    preserved_base_root_pods = [
        copy.deepcopy(row) for row in base_phase['pods']
        if owner(row.get('service'), candidates) == root
        and row.get('ip') in cross_service_root_ips
    ]
    pods = ([copy.deepcopy(row) for row in base_phase['pods']
             if owner(row.get('service'), candidates) != root]
            + preserved_base_root_pods + imported_pods)
    deployments = [copy.deepcopy(row) for row in base_phase['deployments']
                   if row.get('name') != root]
    deployments += [copy.deepcopy(row) for row in overlay_phase['deployments']
                    if row.get('name') == root]
    services = [copy.deepcopy(row) for row in base_phase['services']
                if row.get('name') != root] + [root_service]
    ip_owners = defaultdict(set)
    for pod in pods:
        if _valid_endpoint_ip(pod.get('ip')):
            ip_owners[pod['ip']].add(owner(pod.get('service'), candidates))
    ambiguous = {ip: sorted(values) for ip, values in ip_owners.items() if len(values) != 1}
    if ambiguous:
        raise ValueError(f'composite has ambiguous pod IP ownership: {ambiguous}')
    known_ips = set(ip_owners)
    dangling = sorted({ip for service in services for ip in service.get('endpoint_ips',[])
                       if ip not in known_ips})
    if dangling:
        raise ValueError('composite introduced dangling endpoint IPs')
    return {'timestamp': f'synthetic:{pair_id}:{phase}:no-shared-clock',
            'pods': pods, 'deployments': deployments, 'services': services}, {
                'imported_pods': len(imported_pods),
                'preserved_base_root_pods_for_cross_service_endpoints': len(preserved_base_root_pods),
                'remapped_endpoint_ips': len(endpoint_ips),
                'ambiguous_ip_owners': 0,
                'dangling_endpoint_ips': 0,
            }


def merge_observations(base: Dict[str, Any], overlay: Dict[str, Any],
                       overlay_root: str, pair_id: str) -> Dict[str, Any]:
    """Overlay one root's observable state; never embed either reference label."""
    if base['candidates'] != overlay['candidates']:
        raise ValueError('paired observations must have identical candidate order')
    candidates = set(base['candidates'])
    if overlay_root not in candidates:
        raise ValueError('overlay root is outside the common candidate set')
    merged = copy.deepcopy(base)
    merged['case_id'] = pair_id
    phase_diagnostics = {}
    for phase in ('pre', 'during'):
        merged[phase], phase_diagnostics[phase] = _phase_merge(
            base[phase], overlay[phase], overlay_root, candidates, pair_id, phase)
        edges, duplicates = _relation_union(
            base['call_edges'][phase], overlay['call_edges'][phase])
        merged['call_edges'][phase] = edges
        phase_diagnostics[phase]['duplicate_relation_rows_collapsed'] = duplicates
    if overlay_root in overlay['observed_metrics']:
        merged['observed_metrics'][overlay_root] = copy.deepcopy(
            overlay['observed_metrics'][overlay_root]
        )
    for key in merged.get('coverage', {}):
        merged['coverage'][key] = int(base.get('coverage', {}).get(key, 0)) + int(
            overlay.get('coverage', {}).get(key, 0)
        )
    merged['window'] = {
        'synthetic': True,
        'time_policy': 'no_shared_physical_clock_source_local_windows_only',
        'base_source_window': copy.deepcopy(base.get('window', {})),
        'overlay_source_window': copy.deepcopy(overlay.get('window', {})),
    }
    merged['synthetic_composition'] = {
        'schema': 'two-recorded-incidents-service-overlay-v1',
        'base_case_id': base['case_id'],
        'overlay_case_id': overlay['case_id'],
        'overlay_service_state_only': True,
        'construction_uses_evaluator_root_to_select_overlay_service': True,
        'call_edges': 'directed_relation_union_max_source_count',
        'time_policy': 'source_local_no_shared_physical_clock',
        'phase_diagnostics': phase_diagnostics,
    }
    return merged


def average_precision(ranking: Sequence[str], roots: Sequence[str]) -> float:
    relevant = set(roots);hits = 0;total = 0.0
    for rank, service in enumerate(ranking, 1):
        if service in relevant:
            hits += 1;total += hits / rank
    return total / len(relevant)


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    result = {}
    groups = defaultdict(list)
    for row in rows:
        groups[(row['seed'], row['orientation'])].append(row)
    for (seed, orientation), group in sorted(groups.items()):
        n = len(group)
        result[f'{seed}:{orientation}'] = {
            'n': n,
            'Recall@1': sum(row['hits_at_1'] for row in group) / (2 * n),
            'Recall@2': sum(row['hits_at_2'] for row in group) / (2 * n),
            'Recall@5': sum(row['hits_at_5'] for row in group) / (2 * n),
            'ExactSet@2': sum(row['exact_set_at_2'] for row in group) / n,
            'MAP': sum(row['average_precision'] for row in group) / n,
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--split', required=True)
    parser.add_argument('--checkpoints', required=True)
    parser.add_argument('--out-dir', required=True)
    parser.add_argument('--device', default='cpu')
    parser.add_argument('--dyrca-root', default=str(default_dyrca_root()))
    parser.add_argument('--seed', type=int, default=20260926)
    args = parser.parse_args()

    out = Path(args.out_dir)
    if out.exists():
        raise SystemExit('Refusing overwrite')
    out.mkdir(parents=True)
    use_upstream(args.dyrca_root)
    import torch
    from dyrca_rca.models.graphsage import WeightedGraphSAGE
    device = resolve_device(torch, args.device)
    if device.type == 'cuda':
        torch.cuda.set_device(device)
    manifest_path = Path(args.manifest).resolve()
    manifest = json.loads(manifest_path.read_text())
    by_id = {row['case_id']: row for row in manifest['cases']}
    split = json.loads(Path(args.split).read_text())
    test_rows = [by_id[case_id] for case_id in split['test']]
    grouped = defaultdict(list)
    for row in test_rows:
        grouped[row['app']].append(row)
    pairs = [];leftovers = []
    for offset, app in enumerate(sorted(grouped)):
        app_pairs, app_leftovers = make_pairs(grouped[app], args.seed + offset)
        pairs.extend(app_pairs);leftovers.extend(app_leftovers)

    observations = {
        row['case_id']: json.loads(observation_file(row, manifest_path).read_text())
        for row in test_rows
    }
    models = {}
    checkpoint_hashes = {}
    for seed in (42, 43, 44):
        checkpoint = Path(args.checkpoints) / f'model_seed{seed}.pt'
        checkpoint_hashes[str(seed)] = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
        state = torch.load(checkpoint, map_location='cpu', weights_only=True)
        if state.get('seed') != seed or state.get('training_variant') != 'patch':
            raise ValueError('checkpoint provenance mismatch')
        model = WeightedGraphSAGE(state['in_dim'], 128, 2, .1)
        model.load_state_dict(state['state_dict']);model.to(device);model.eval()
        models[seed] = model

    predictions = [];pair_rows = []
    started = time.perf_counter()
    with torch.no_grad():
        for pair_index, (left, right) in enumerate(pairs):
            roots = sorted([left['true_service'], right['true_service']])
            pair_record = {
                'pair_id': f'pair{pair_index:03d}', 'app': left['app'],
                'left_case_id': left['case_id'], 'right_case_id': right['case_id'],
                'left_scenario': left['scenario'], 'right_scenario': right['scenario'],
                'root_count': 2,
            }
            pair_rows.append(pair_record)
            for orientation, base_row, overlay_row in (
                ('left_base', left, right), ('right_base', right, left)):
                synthetic_id = f"{pair_record['pair_id']}:{orientation}"
                observation = merge_observations(
                    observations[base_row['case_id']],
                    observations[overlay_row['case_id']],
                    overlay_row['true_service'], synthetic_id,
                )
                graph = graphs_for(observation)[0]['patch']
                item = move_item(tensors(graph, observation), device)
                for seed, model in models.items():
                    logits = forward(model, item)
                    values = logits[item['candidate_indices']].detach().cpu().tolist()
                    ranking = [service for service, _score in sorted(
                        zip(item['candidate_services'], values),
                        key=lambda pair: (-pair[1], pair[0]))]
                    relevant = set(roots)
                    row = {
                        **pair_record, 'orientation': orientation, 'seed': seed,
                        'roots': roots, 'ranking': ranking,
                        'hits_at_1': len(relevant.intersection(ranking[:1])),
                        'hits_at_2': len(relevant.intersection(ranking[:2])),
                        'hits_at_5': len(relevant.intersection(ranking[:5])),
                        'exact_set_at_2': set(ranking[:2]) == relevant,
                        'average_precision': average_precision(ranking, roots),
                    }
                    predictions.append(row)
    with (out / 'predictions.jsonl').open('w') as handle:
        for row in predictions:
            handle.write(json.dumps(row, sort_keys=True) + '\n')
    (out / 'pairs.json').write_text(json.dumps({
        'seed': args.seed, 'pairs': pair_rows,
        'leftovers': [row['case_id'] for row in leftovers],
    }, indent=2) + '\n')
    result = {
        'status': 'completed_synthetic_multi_root_proxy',
        'evaluation_type': 'simulation_only',
        'source_test_incidents': len(test_rows),
        'pairs': len(pairs),
        'orientations': 2,
        'prediction_rows': len(predictions),
        'checkpoint_sha256': checkpoint_hashes,
        'construction_uses_evaluator_labels': True,
        'relation_count_aggregation': 'maximum source-window count per directed relation',
        'synthetic_time_policy': 'source-local observations; no shared physical cutoff',
        'summary': summarize(predictions),
        'elapsed_seconds': time.perf_counter() - started,
        'limitations': [
            'Each composite overlays two independently recorded single-fault observations; it is not a live concurrent-fault incident.',
            'The second root contributes its service-local topology and telemetry while call edges are unioned; causal interactions are unavailable.',
            'Ground-truth root labels select the two service-local states used to construct the proxy; they are used for construction/evaluation, never model features.',
            'Pod names/IPs are deterministically remapped, endpoint ownership is total and unique, and directed relation counts use the maximum source-window count.',
            'Source observations have different physical times; the composite declares no shared cutoff and makes no simultaneity claim.',
            'Both base orientations are reported because the composition is asymmetric.',
            'Results support only a synthetic multi-root sensitivity statement, not open-world or production recovery.',
        ],
    }
    (out / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result['summary'], indent=2))


if __name__ == '__main__':
    main()
