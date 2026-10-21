#!/usr/bin/env python3
"""Run/resume the hard-budgeted fresh-LLM portion of the E2R M2 pilot."""
from pathlib import Path
import argparse
import json
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from methods.E2R.graph import use_upstream
from methods.E2R.llm_pilot import (acquire_campaign_lock, append_event, call_once, choose_cases,
                                   journal_state, load_model_config, messages_for, phase_stop,
                                   task_list, validate_output)
from methods.E2R.paths import default_dyrca_root, observation_file
from rca_bench.llm_transport import request_settings


def write_status(out, campaign, started, finished):
    unknown = sorted(set(started) - set(finished))
    result = {'status': 'complete' if len(started) == campaign['hard_request_budget'] and not unknown else
              'interrupted_requests_present' if unknown else 'in_progress',
              'hard_request_budget': campaign['hard_request_budget'],
              'actual_http_attempts_started': len(started), 'finished_attempts': len(finished),
              'unknown_outcome_attempts': unknown,
              'transport_successes': sum(bool(x.get('transport_success')) for x in finished.values()),
              'response_models': sorted({x.get('response_model') for x in finished.values() if x.get('response_model')}),
              'provider_usage_missing_attempts': sum(x.get('usage') is None for x in finished.values())}
    (out / 'status.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--manifest', default=str(ROOT / 'experiments/e2r/runs/full_data_v2/manifest.json'))
    ap.add_argument('--split', default=str(ROOT / 'experiments/e2r/runs/gpu_full_4090d/split.json'))
    ap.add_argument('--out-dir', required=True)
    ap.add_argument('--config', default=str(ROOT / 'llm_rca_config.json'))
    ap.add_argument('--model-alias', default='qwen3.8-flash')
    ap.add_argument('--cases', type=int, default=10)
    ap.add_argument('--generations', type=int, default=2)
    ap.add_argument('--selection-seed', type=int, default=20260925)
    ap.add_argument('--hard-request-budget', type=int, default=40)
    ap.add_argument('--phase', required=True, choices=['sanity', 'recovery-sanity', 'pilot'],
                    help='sanity starts two attempts; recovery-sanity spends attempts 3-4 only after two transport failures; pilot requires a passed pair')
    args = ap.parse_args()
    if args.cases * args.generations * 2 != args.hard_request_budget or args.hard_request_budget > 40:
        raise SystemExit('M2 requires exactly cases*generations*2 attempts and a hard ceiling of 40')
    use_upstream(default_dyrca_root())
    manifest_path = Path(args.manifest).resolve()
    manifest = json.loads(manifest_path.read_text())
    by_id = {row['case_id']: row for row in manifest['cases']}
    split = json.loads(Path(args.split).read_text())
    test_ids = split['test']
    if len(test_ids) != len(set(test_ids)) or any(case not in by_id for case in test_ids):
        raise ValueError('invalid fixed test split')
    observations = {case: json.loads(observation_file(by_id[case], manifest_path).read_text()) for case in test_ids}
    selected = choose_cases([by_id[case] for case in test_ids], observations, args.cases, args.selection_seed)
    cfg = load_model_config(args.config, args.model_alias)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    try:
        campaign_lock = acquire_campaign_lock(out)
    except ValueError as exc:
        raise SystemExit(str(exc))
    # This live file object keeps the exclusive budget lock until main returns.
    campaign = {'schema': 'e2r_m2_pilot_v1', 'source_manifest': str(manifest_path),
                'source_split': str(Path(args.split).resolve()), 'model_alias': args.model_alias,
                'request_model': cfg['model'], 'actual_request_settings': request_settings(cfg,max_tokens=2000),
                'cases': [{'case_id': row['case_id'], 'app': row['app'], 'scenario': row['scenario'],
                           'catalog_operations': len(messages_for(observations[row['case_id']], 'proposal')[1])}
                          for row in selected],
                'generations': args.generations, 'routes': list(('proposal', 'direct')),
                'selection_seed': args.selection_seed, 'hard_request_budget': args.hard_request_budget,
                'retry_policy': 'none; every failed/unknown HTTP attempt consumes budget',
                'prompt_policy': 'same cutoff-bounded facts; proposal additionally receives deterministic supported-operation catalog; no labels/scenario/fault/post/cache'}
    campaign_path = out / 'campaign.json'
    if campaign_path.exists():
        if json.loads(campaign_path.read_text()) != campaign:
            raise SystemExit('Existing campaign does not match requested immutable configuration')
    else:
        campaign_path.write_text(json.dumps(campaign, indent=2) + '\n')
    journal = out / 'requests.jsonl'
    started, finished = journal_state(journal)
    tasks = task_list(selected, args.generations)
    if len({task['request_id'] for task in tasks}) != len(tasks):
        raise AssertionError('duplicate task ID')
    try:
        stop = phase_stop(args.phase, args.hard_request_budget, tasks, started, finished)
    except ValueError as exc:
        raise SystemExit(str(exc))
    for task in tasks:
        if len(started) >= stop or len(started) >= args.hard_request_budget:
            break
        request_id = task['request_id']
        if request_id in started:  # Includes unknown outcomes: never rebill/retry them.
            continue
        obs = observations[task['case_id']]
        messages, catalog = messages_for(obs, task['route'])
        start_event = {'event': 'request_started', **task, 'attempt_number': len(started) + 1,
                       'time_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                       'model_alias': args.model_alias, 'request_model': cfg['model'],
                       'actual_request_settings': request_settings(cfg,max_tokens=2000), 'messages': messages}
        append_event(journal, start_event)
        started[request_id] = start_event
        outcome = call_once(cfg, messages)
        finish_event = {'event': 'request_finished', **task,
                        'time_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), **outcome}
        if outcome.get('transport_success'):
            finish_event['validation'] = validate_output(
                outcome['response_text'], task['route'], obs, catalog)
        append_event(journal, finish_event)
        finished[request_id] = finish_event
        status = write_status(out, campaign, started, finished)
        print(json.dumps({k: status[k] for k in ('actual_http_attempts_started', 'finished_attempts',
                                                'transport_successes', 'unknown_outcome_attempts')}), flush=True)
    status = write_status(out, campaign, started, finished)
    print(json.dumps(status, indent=2))
    campaign_lock.close()


if __name__ == '__main__':
    main()
