"""Bounded, auditable LLM routes for the observation-only E2R M2 pilot."""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import fcntl
import json
import os
import random
import time
import urllib.error
import urllib.request

from methods.E2R.graph import execute, model_input, operation_catalog, select_plan
from rca_bench.llm_transport import build_request, parse_response, session_id
ROUTES = ('proposal', 'direct')  # Legacy bounded pilot routes.
FORMAL_ROUTES = ('proposal', 'raw_direct', 'catalog_direct')
FORBIDDEN_PROMPT_KEYS = (
    'true_service', 'true_category', 'target_service', 'scenario_id',
    'fault_params', 'ground_truth', 'original_id', 'fault_end_time', 'directory',
)

SYSTEM = (
    'You diagnose one microservice incident using only the supplied pre-event and '
    'diagnosis-time observations. Hidden injection metadata and post-recovery state '
    'do not exist for this task. Do not invent services, topology, telemetry or graph '
    'operations. Return exactly one JSON object and no prose.'
)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _forbidden_keys(value):
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in FORBIDDEN_PROMPT_KEYS:
                found.append(key)
            found.extend(_forbidden_keys(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_forbidden_keys(child))
    return found


def strict_json_object(text):
    """Accept exactly one JSON object, optionally enclosed by one code fence."""
    if not isinstance(text, str):
        return None
    candidate = text.strip()
    if candidate.startswith('```'):
        lines = candidate.splitlines()
        if len(lines) < 3 or lines[-1].strip() != '```':
            return None
        candidate = '\n'.join(lines[1:-1]).strip()
    try:
        value = json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def factual_payload(obs):
    """Return the shared factual input, excluding the plan representation itself."""
    catalog = operation_catalog(obs)
    value = model_input(obs, catalog)
    value.pop('supported_operations')
    # Counts discovered only by scanning excluded/future records are not model evidence.
    value['coverage'] = {key: amount for key, amount in value.get('coverage', {}).items()
                         if key.endswith('_accepted')}
    return value, catalog


def messages_for(obs, route):
    facts, catalog = factual_payload(obs)
    if route == 'proposal':
        task = {
            'task': 'select_observation_supported_graph_operations_and_rank_services',
            'requirements': [
                'selected_operation_ids must contain only IDs from supported_operations',
                'include every requires ID for a selected operation',
                'an empty selected_operation_ids list is allowed when no update is warranted',
                'ranking must contain every candidate exactly once using verbatim names',
                'set abstain=true only when the supplied observations cannot support an answer',
            ],
            'response_schema': {
                'selected_operation_ids': ['op0000'], 'ranking': ['service-a'],
                'abstain': False, 'rationale': 'one short observation-grounded sentence',
            },
            'evidence': facts,
            'supported_operations': catalog,
        }
    elif route in {'direct','raw_direct','catalog_direct'}:
        task = {
            'task': ('rank_root_cause_services_from_facts_and_supported_operation_catalog_without_emitting_a_plan'
                     if route=='catalog_direct' else
                     'rank_root_cause_services_directly_without_a_graph_patch_plan'),
            'requirements': [
                'ranking must contain every candidate exactly once using verbatim names',
                'set abstain=true only when the supplied observations cannot support an answer',
            ],
            'response_schema': {
                'ranking': ['service-a'], 'abstain': False,
                'rationale': 'one short observation-grounded sentence',
            },
            'evidence': facts,
        }
        if route=='catalog_direct':
            task['supported_operations']=catalog
    else:
        raise ValueError(f'unknown route: {route}')
    messages = [{'role': 'system', 'content': SYSTEM},
                {'role': 'user', 'content': json.dumps(task, sort_keys=True, separators=(',', ':'))}]
    leaked = sorted(set(_forbidden_keys(task)))
    if leaked:
        raise ValueError(f'forbidden model-input keys: {leaked}')
    return messages, catalog


def validate_output(text, route, obs, catalog):
    raw = strict_json_object(text)
    result = {'json_valid': isinstance(raw, dict), 'ranking_valid': False,
              'plan_valid': None, 'abstained': False, 'errors': []}
    if not isinstance(raw, dict):
        result['errors'].append('response_is_not_one_json_object')
        return result
    abstain_valid = isinstance(raw.get('abstain'), bool)
    result['abstained'] = raw.get('abstain') is True
    if not abstain_valid:
        result['errors'].append('abstain_must_be_boolean')
    ranking = raw.get('ranking')
    candidates = obs['candidates']
    if (isinstance(ranking, list) and all(isinstance(x, str) for x in ranking)
            and len(ranking) == len(candidates) and len(set(ranking)) == len(ranking)
            and set(ranking) == set(candidates)):
        result['ranking_valid'] = abstain_valid and not result['abstained']
        result['ranking'] = ranking
    else:
        result['errors'].append('ranking_must_be_complete_candidate_permutation')
    rationale = raw.get('rationale')
    if isinstance(rationale, str):
        result['rationale'] = rationale
    if route == 'proposal':
        ids = raw.get('selected_operation_ids')
        if not isinstance(ids, list) or any(not isinstance(value, str) for value in ids):
            result['plan_valid'] = False
            result['errors'].append('selected_operation_ids_must_be_string_list')
        elif not abstain_valid:
            result['plan_valid'] = False
        elif result['abstained']:
            result['plan_valid'] = False
            result['errors'].append('planner_abstained')
        else:
            try:
                plan = select_plan(obs, catalog, ids)
                execute(obs, plan)  # Membership alone is not accepted as validation.
                result['plan_valid'] = True
                result['selected_operation_ids'] = ids
            except (ValueError, KeyError, TypeError) as exc:
                result['plan_valid'] = False
                result['errors'].append(f'plan_rejected:{type(exc).__name__}:{exc}')
    elif route in {'direct','raw_direct','catalog_direct'}:
        if result['abstained']:
            result['errors'].append('direct_ranker_abstained')
    else:
        raise ValueError(route)
    result['accepted'] = ((bool(result['plan_valid']) and result['ranking_valid'])
                          if route == 'proposal' else result['ranking_valid'])
    return result


def load_model_config(path, alias):
    cfg = json.loads(Path(path).read_text())
    if alias not in cfg.get('models', {}):
        raise KeyError(f'unknown model alias: {alias}')
    selected = dict(cfg['models'][alias])
    request = dict(cfg.get('request', {}))
    request.update(selected)
    request['alias'] = alias
    request['base_url'] = os.environ.get('LLM_BASE_URL', request['base_url'])
    request['api_key'] = os.environ.get('LLM_API_KEY', request['api_key'])
    request['model'] = os.environ.get('RCA_LLM_MODEL', request['model'])
    if not request.get('api_key'):
        raise ValueError('configured API key is empty')
    return request


def call_once(cfg, messages, *, opener=None):
    """Make exactly one HTTP attempt. The caller journals intent before calling."""
    request, _ = build_request(cfg, messages, max_tokens=2000,
                               request_session=cfg.get('session_id') or session_id('e2r-m2'))
    started = time.perf_counter()
    open_call = urllib.request.build_opener(_NoRedirect).open if opener is None else (
        opener.open if hasattr(opener, 'open') else opener)
    try:
        response = json.loads(open_call(request, timeout=cfg.get('timeout_s', 120)).read())
        base = {'transport_success': True, 'usage': response.get('usage'),
                'response_model': response.get('model'), 'provider_request_id': response.get('id'),
                'request_seconds': time.perf_counter() - started}
        try:
            normalized = parse_response(response, cfg.get('api_style', 'openai'))
            return {**base, **normalized, 'provider_response_valid': True,
                    'provider_response_envelope':response}
        except (KeyError, IndexError, TypeError, ValueError):
            return {**base, 'provider_response_valid': False, 'response_text': '',
                    'provider_response_envelope':response,
                    'error_type': 'InvalidResponseEnvelope'}
    except urllib.error.HTTPError as exc:
        return {'transport_success': False, 'error_type': type(exc).__name__,
                'http_status': exc.code, 'usage': None,
                'request_seconds': time.perf_counter() - started}
    except Exception as exc:
        return {'transport_success': False, 'error_type': type(exc).__name__,
                'usage': None, 'request_seconds': time.perf_counter() - started}


def choose_cases(test_rows, observations, count, seed):
    groups = defaultdict(list)
    for row in test_rows:
        groups[(row['app'], row['scenario'])].append(row)
    rng = random.Random(seed)
    for group in groups.values():
        group.sort(key=lambda row: row['case_id'])
        rng.shuffle(group)
    chosen = []
    selected_by_group = Counter()
    selected_by_app = Counter()
    while len(chosen) < count:
        available = [key for key, values in groups.items() if values]
        if not available: break
        minimum_app = min(selected_by_app[key[0]] for key in available)
        available = [key for key in available if selected_by_app[key[0]] == minimum_app]
        minimum_group = min(selected_by_group[key] for key in available)
        available = [key for key in available if selected_by_group[key] == minimum_group]
        key = rng.choice(sorted(available))
        chosen.append(groups[key].pop())
        selected_by_group[key] += 1
        selected_by_app[key[0]] += 1
    if len(chosen) != count:
        raise ValueError(f'could select only {len(chosen)} of {count} cases')
    # Keep the random set but make the first live sanity exercise a nonempty catalog.
    for i, row in enumerate(chosen):
        if operation_catalog(observations[row['case_id']]):
            chosen.insert(0, chosen.pop(i))
            break
    return chosen


def append_event(path, event):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def acquire_campaign_lock(out):
    """Return an exclusive lock stream that the caller keeps until it exits."""
    stream = (Path(out) / 'campaign.lock').open('a')
    try:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        stream.close()
        raise ValueError('Another M2 process already holds this campaign lock')
    return stream


def journal_state(path):
    started, finished = {}, {}
    if not path.is_file():
        return started, finished
    for line in path.read_text().splitlines():
        event = json.loads(line)
        request_id = event['request_id']
        target = started if event['event'] == 'request_started' else finished
        if request_id in target:
            raise ValueError(f'duplicate journal event: {event["event"]} {request_id}')
        target[request_id] = event
    if not set(finished).issubset(started):
        raise ValueError('finished request without a start record')
    return started, finished


def task_list(cases, repeats, routes=ROUTES):
    return [{'request_id': f'{row["case_id"]}-g{generation}-{route}',
             'case_id': row['case_id'], 'generation': generation, 'route': route}
            for row in cases for generation in range(repeats) for route in routes]


def phase_stop(phase, hard_budget, tasks, started, finished):
    """Validate sanity/pilot transition and return the started-attempt stop count."""
    if phase == 'sanity':
        if started:
            raise ValueError('Sanity phase requires a fresh campaign; existing requests are preserved')
        return 2
    if phase not in {'recovery-sanity', 'pilot'}:
        raise ValueError(f'unknown phase: {phase}')
    if len(started) < 2:
        raise ValueError('Pilot phase requires the two completed sanity attempts')
    if set(started) - set(finished):
        raise ValueError('Unknown-outcome request exists; preserving the campaign and refusing more spend')
    def passed(pair):
        return (all(event is not None and event.get('transport_success') for event in pair)
                and pair[0]['validation'].get('accepted')
                and pair[1]['validation'].get('ranking_valid'))
    first = [finished.get(task['request_id']) for task in tasks[:2]]
    if phase == 'recovery-sanity':
        if len(started) != 2 or any(event is None or event.get('transport_success') for event in first):
            raise ValueError('Recovery sanity is only for two completed transport failures')
        return 4
    second = [finished.get(task['request_id']) for task in tasks[2:4]] if len(tasks) >= 4 else []
    if not passed(first) and not (len(started) >= 4 and passed(second)):
        raise ValueError('Live sanity did not pass both strict routes; refusing remaining requests')
    return hard_budget
