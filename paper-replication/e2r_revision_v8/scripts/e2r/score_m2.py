#!/usr/bin/env python3
"""Score completed M2 responses with frozen M1 checkpoints on one explicit device."""
from pathlib import Path
from collections import Counter
import argparse
import json
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from methods.E2R.graph import (execute, graphs_for, operation_catalog, select_plan,
                               tensors, use_upstream)
from methods.E2R.llm_pilot import journal_state, validate_output
from methods.E2R.paths import default_dyrca_root, observation_file
from scripts.e2r.run_graph_experiment import move_item, resolve_device, synchronize


def rank_graph(torch, model, item, true_service, device):
    moved = move_item(item, device)
    synchronize(torch, device)
    started = time.perf_counter()
    scores = model(moved['x'], moved['edge_index'], edge_weight=moved['edge_weight'])
    values = scores[moved['candidate_indices']].detach().cpu().tolist()
    synchronize(torch, device)
    pairs = zip(moved['candidate_services'], values)
    ranking = [service for service, _ in sorted(pairs, key=lambda x: (-x[1], x[0]))]
    return ranking, ranking.index(true_service) + 1, time.perf_counter() - started


def direct_row(arm, base, validation, truth):
    if validation and validation.get('accepted'):
        ranking = validation['ranking']
        return {**base, 'arm': arm, 'status': 'ranked', 'ranking': ranking,
                'rank': ranking.index(truth) + 1}
    errors = validation.get('errors', []) if validation else ['transport_failure']
    return {**base, 'arm': arm, 'status': 'failed', 'rank': None, 'errors': errors}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--campaign-dir', required=True)
    ap.add_argument('--manifest', required=True)
    ap.add_argument('--checkpoints', required=True)
    ap.add_argument('--out-dir', required=True)
    ap.add_argument('--dyrca-root', default=str(default_dyrca_root()))
    ap.add_argument('--device', default='cuda:0')
    ap.add_argument('--threads', type=int, default=2)
    args = ap.parse_args()
    use_upstream(args.dyrca_root)
    import torch
    from dyrca_rca.models.graphsage import WeightedGraphSAGE
    torch.set_num_threads(args.threads)
    device = resolve_device(torch, args.device)
    if device.type == 'cuda':
        torch.cuda.set_device(device)
        torch.cuda.reset_peak_memory_stats(device)
    campaign_dir = Path(args.campaign_dir)
    campaign = json.loads((campaign_dir / 'campaign.json').read_text())
    request_status = json.loads((campaign_dir / 'status.json').read_text())
    if request_status['status'] != 'complete' or request_status['actual_http_attempts_started'] != campaign['hard_request_budget']:
        raise SystemExit('Refusing to score an incomplete/unknown-outcome request campaign')
    started, finished = journal_state(campaign_dir / 'requests.jsonl')
    if set(started) != set(finished) or len(finished) != campaign['hard_request_budget']:
        raise ValueError('request journal coverage mismatch')
    manifest_path = Path(args.manifest).resolve()
    manifest = json.loads(manifest_path.read_text())
    rows = {row['case_id']: row for row in manifest['cases']}
    selected = [row['case_id'] for row in campaign['cases']]
    observations = {case: json.loads(observation_file(rows[case], manifest_path).read_text()) for case in selected}
    checkpoints = Path(args.checkpoints)
    models = {}
    for seed in (42, 43, 44):
        state = torch.load(checkpoints / f'model_seed{seed}.pt', map_location='cpu', weights_only=True)
        if state.get('seed') != seed or state.get('training_variant') != 'patch':
            raise ValueError('checkpoint identity/training condition mismatch')
        model = WeightedGraphSAGE(state['in_dim'], hidden_dim=128, num_layers=2, dropout=.1)
        model.load_state_dict(state['state_dict']); model.to(device); model.eval()
        models[seed] = model
    predictions = []
    with torch.no_grad():
        for case in selected:
            obs = observations[case]; truth = rows[case]['true_service']; catalog = operation_catalog(obs)
            variants, _, _ = graphs_for(obs)
            base_items = {name: tensors(graph, obs) for name, graph in variants.items()}
            for seed, model in models.items():
                for arm, graph_name in [('reference', 'reference'), ('runtime', 'runtime'), ('rule_patch', 'patch')]:
                    ranking, rank, seconds = rank_graph(torch, model, base_items[graph_name], truth, device)
                    predictions.append({'case_id': case, 'app': rows[case]['app'], 'scenario': rows[case]['scenario'],
                                        'true_service': truth, 'generation': None, 'seed': seed, 'arm': arm,
                                        'status': 'ranked', 'ranking': ranking, 'rank': rank,
                                        'inference_seconds': seconds, 'fallback_used': False})
            for generation in range(campaign['generations']):
                proposal_id = f'{case}-g{generation}-proposal'
                direct_id = f'{case}-g{generation}-direct'
                proposal_event = finished[proposal_id]; direct_event = finished[direct_id]
                proposal_validation = None; direct_validation = None
                if proposal_event.get('transport_success'):
                    proposal_validation = validate_output(
                        proposal_event['response_text'], 'proposal', obs, catalog)
                    if proposal_validation != proposal_event.get('validation'):
                        raise ValueError(f'saved proposal validation mismatch: {proposal_id}')
                if direct_event.get('transport_success'):
                    direct_validation = validate_output(direct_event['response_text'], 'direct', obs, catalog)
                    if direct_validation != direct_event.get('validation'):
                        raise ValueError(f'saved direct validation mismatch: {direct_id}')
                common = {'case_id': case, 'app': rows[case]['app'], 'scenario': rows[case]['scenario'],
                          'true_service': truth, 'generation': generation, 'seed': None,
                          'request_id': proposal_id, 'fallback_used': False}
                predictions.append(direct_row('proposal_rank_no_gnn', common, proposal_validation, truth))
                predictions.append(direct_row('direct_rank', {**common, 'request_id': direct_id}, direct_validation, truth))
                plan_valid = bool(proposal_validation and proposal_validation.get('accepted'))
                llm_item = None
                if plan_valid:
                    plan = select_plan(obs, catalog, proposal_validation['selected_operation_ids'])
                    llm_item = tensors(execute(obs, plan), obs)
                for seed, model in models.items():
                    score_base = {**common, 'seed': seed, 'request_id': proposal_id}
                    if plan_valid:
                        ranking, rank, seconds = rank_graph(torch, model, llm_item, truth, device)
                        row = {**score_base, 'status': 'ranked', 'ranking': ranking, 'rank': rank,
                               'inference_seconds': seconds, 'fallback_used': False}
                        predictions.append({**row, 'arm': 'llm_patch_no_fallback'})
                        predictions.append({**row, 'arm': 'llm_patch_with_fallback'})
                    else:
                        errors = proposal_validation.get('errors', []) if proposal_validation else ['transport_failure']
                        predictions.append({**score_base, 'arm': 'llm_patch_no_fallback',
                                            'status': 'failed', 'rank': None, 'errors': errors,
                                            'fallback_used': False})
                        ranking, rank, seconds = rank_graph(torch, model, base_items['patch'], truth, device)
                        predictions.append({**score_base, 'arm': 'llm_patch_with_fallback',
                                            'status': 'ranked', 'ranking': ranking, 'rank': rank,
                                            'inference_seconds': seconds, 'fallback_used': True,
                                            'fallback_reason': errors})
    out = Path(args.out_dir)
    if out.exists():
        raise SystemExit('Refusing to overwrite M2 score output')
    out.mkdir(parents=True)
    with (out / 'predictions.jsonl').open('w') as stream:
        for row in predictions:
            stream.write(json.dumps(row) + '\n')
    synchronize(torch, device)
    result = {'status': 'completed_m2_pilot_scoring', 'evaluation_type': 'real_gt_pilot',
              'campaign': str(campaign_dir.resolve()), 'manifest': str(manifest_path),
              'checkpoints': str(checkpoints.resolve()), 'cases': len(selected),
              'generations': campaign['generations'], 'seeds': [42, 43, 44],
              'prediction_rows': len(predictions),
              'row_status': dict(Counter(row['status'] for row in predictions)),
              'runtime': {'device': str(device), 'torch': torch.__version__,
                          'cuda_runtime': torch.version.cuda,
                          'gpu_name': torch.cuda.get_device_name(device) if device.type == 'cuda' else None,
                          'peak_allocated_bytes': torch.cuda.max_memory_allocated(device) if device.type == 'cuda' else None},
              'limits': [f'{len(selected)}-incident, two-generation pilot only.',
                         'Frozen patch-trained M1 scorers; reference/runtime are input substitutions.',
                         'No recovery action or external baseline is evaluated.']}
    (out / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
