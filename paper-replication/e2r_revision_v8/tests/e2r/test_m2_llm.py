import json
from pathlib import Path

from methods.E2R.graph import operation_catalog, use_upstream
from methods.E2R.llm_pilot import (_NoRedirect, acquire_campaign_lock, call_once, choose_cases,
                                   factual_payload, journal_state, messages_for, phase_stop,
                                   task_list, validate_output)
from methods.E2R.paths import default_dyrca_root


def observation():
    return {
        'case_id': 'case00001', 'candidates': ['a', 'b'],
        'pre': {
            'timestamp': '2026-01-01T00:00:00Z',
            'pods': [{'name': 'b-old', 'service': 'b', 'ip': '10.0.0.2', 'version': 'v1'}],
            'deployments': [{'name': 'a', 'replicas': 1, 'readyReplicas': 1},
                            {'name': 'b', 'replicas': 1, 'readyReplicas': 1}],
            'services': [{'name': 'a', 'endpoint_ips': []},
                         {'name': 'b', 'endpoint_ips': ['10.0.0.2']}],
        },
        'during': {
            'timestamp': '2026-01-01T00:01:00Z',
            'pods': [{'name': 'a-new', 'service': 'a', 'ip': '10.0.0.1', 'version': 'v1'},
                     {'name': 'b-old', 'service': 'b', 'ip': '10.0.0.2', 'version': 'v1'}],
            'deployments': [{'name': 'a', 'replicas': 1, 'readyReplicas': 1},
                            {'name': 'b', 'replicas': 1, 'readyReplicas': 1}],
            'services': [{'name': 'a', 'endpoint_ips': ['10.0.0.1']},
                         {'name': 'b', 'endpoint_ips': ['10.0.0.2']}],
        },
        'observed_metrics': {}, 'call_edges': {'pre': [], 'during': []},
        'window': {'start': 1, 'baseline_end': 2, 'cutoff': 3, 'max_used_timestamp': 3},
        'coverage': {'metric_accepted': 0, 'trace_accepted': 0, 'log_accepted': 0},
    }


def test_prompts_are_label_free_and_direct_deletes_plan_representation():
    use_upstream(default_dyrca_root())
    obs = observation()
    facts, catalog = factual_payload(obs)
    assert 'supported_operations' not in facts and catalog
    assert facts['coverage'] == {'metric_accepted': 0, 'trace_accepted': 0, 'log_accepted': 0}
    proposal, _ = messages_for(obs, 'proposal'); direct, _ = messages_for(obs, 'direct')
    raw_direct, _ = messages_for(obs, 'raw_direct')
    catalog_direct, _ = messages_for(obs, 'catalog_direct')
    forbidden = ['true_service', 'true_category', 'target_service', 'scenario_id',
                 'fault_params', 'ground_truth', 'original_id', 'fault_end_time', 'directory']
    assert all(key not in json.dumps(proposal) for key in forbidden)
    assert 'supported_operations' in proposal[1]['content']
    assert 'supported_operations' not in direct[1]['content']
    assert 'supported_operations' not in raw_direct[1]['content']
    assert 'supported_operations' in catalog_direct[1]['content']
    assert json.loads(proposal[1]['content'])['evidence'] == json.loads(direct[1]['content'])['evidence']
    assert json.loads(proposal[1]['content'])['evidence'] == json.loads(catalog_direct[1]['content'])['evidence']
    assert json.loads(proposal[1]['content'])['supported_operations'] == json.loads(catalog_direct[1]['content'])['supported_operations']
    poisoned = json.loads(json.dumps(obs))
    poisoned['observed_metrics']['a'] = {'true_service': 'a'}
    import pytest
    with pytest.raises(ValueError, match='forbidden model-input'):
        messages_for(poisoned, 'direct')


def test_response_validation_checks_complete_ranking_dependencies_and_execution():
    use_upstream(default_dyrca_root())
    obs = observation(); catalog = operation_catalog(obs)
    add_node = next(row for row in catalog if row['operation']['op'] == 'ADD_NODE')
    dependent = next(row for row in catalog if add_node['id'] in row['requires'])
    bad = json.dumps({'selected_operation_ids': [dependent['id']], 'ranking': ['a'],
                      'abstain': False, 'rationale': 'observed endpoint'})
    rejected = validate_output(bad, 'proposal', obs, catalog)
    assert not rejected['ranking_valid'] and not rejected['plan_valid']
    good_ids = [add_node['id'], dependent['id']]
    good = json.dumps({'selected_operation_ids': good_ids, 'ranking': ['a', 'b'],
                       'abstain': False, 'rationale': 'observed endpoint'})
    accepted = validate_output(good, 'proposal', obs, catalog)
    assert accepted['ranking_valid'] and accepted['plan_valid']
    assert accepted['accepted']
    assert accepted['selected_operation_ids'] == good_ids
    missing_abstain = validate_output(json.dumps({'ranking': ['a', 'b']}), 'direct', obs, catalog)
    assert not missing_abstain['ranking_valid'] and 'abstain_must_be_boolean' in missing_abstain['errors']
    missing_ids = validate_output(json.dumps({'ranking': ['a', 'b'], 'abstain': False}),
                                  'proposal', obs, catalog)
    assert not missing_ids['plan_valid']
    assert 'selected_operation_ids_must_be_string_list' in missing_ids['errors']
    concatenated = validate_output(good + good, 'proposal', obs, catalog)
    assert not concatenated['json_valid']
    catalog_rank = validate_output(json.dumps({'ranking':['a','b'],'abstain':False}),
                                   'catalog_direct',obs,catalog)
    assert catalog_rank['accepted'] and catalog_rank['ranking_valid']


def test_one_call_has_no_hidden_retry_and_preserves_provider_usage():
    calls = []
    class Response:
        def read(self):
            return json.dumps({'id': 'req1', 'model': 'm-returned',
                'choices': [{'message': {'content': '{"ranking":["a","b"],"abstain":false}'}}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 4, 'total_tokens': 14}}).encode()
    def opener(request, timeout):
        calls.append((request, timeout)); return Response()
    result = call_once({'model': 'm', 'base_url': 'https://example.invalid/v1',
                        'api_key': 'not-recorded', 'timeout_s': 5},
                       [{'role': 'user', 'content': 'safe'}], opener=opener)
    assert len(calls) == 1 and result['transport_success']
    assert result['usage']['total_tokens'] == 14 and result['response_model'] == 'm-returned'
    assert 'api_key' not in result and 'base_url' not in result
    assert result['provider_response_envelope']['id'] == 'req1'
    malformed = call_once({'model': 'm', 'base_url': 'https://example.invalid/v1',
                           'api_key': 'not-recorded', 'timeout_s': 5}, [],
                          opener=lambda *a, **k: type('R', (), {'read': lambda self: json.dumps({
                              'id': 'bad', 'model': 'm-returned', 'choices': [],
                              'usage': {'total_tokens': 17}}).encode()})())
    assert malformed['transport_success'] and not malformed['provider_response_valid']
    assert malformed['usage']['total_tokens'] == 17 and malformed['provider_request_id'] == 'bad'
    assert _NoRedirect().redirect_request(None, None, 302, '', {}, 'https://redirect.invalid') is None


def test_balanced_selection_task_count_and_unknown_journal_attempt(tmp_path):
    rows = [{'case_id': f'c{i}', 'app': f'a{i % 2}', 'scenario': f'T{i % 4}'} for i in range(16)]
    observations = {row['case_id']: observation() | {'case_id': row['case_id']} for row in rows}
    chosen = choose_cases(rows, observations, 10, 9)
    assert len(chosen) == 10 and len(task_list(chosen, 2)) == 40
    apps = [row['app'] for row in chosen]
    assert apps.count('a0') == apps.count('a1') == 5
    assert len({(row['app'], row['scenario']) for row in chosen}) == 4
    path = tmp_path / 'requests.jsonl'
    event = {'event': 'request_started', 'request_id': 'c0-g0-proposal'}
    path.write_text(json.dumps(event) + '\n')
    started, finished = journal_state(path)
    assert set(started) == {'c0-g0-proposal'} and not finished


def test_phase_gate_requires_sanity_but_allows_clean_partial_resume():
    tasks = [{'request_id': 'p'}, {'request_id': 'd'}, {'request_id': 'p2'}, {'request_id': 'd2'},
             {'request_id': 'later'}]
    passed = {'transport_success': True, 'validation': {'accepted': True}}
    direct = {'transport_success': True, 'validation': {'ranking_valid': True}}
    failed = {'transport_success': False}
    assert phase_stop('sanity', 40, tasks, {}, {}) == 2
    assert phase_stop('pilot', 40, tasks, {'p': {}, 'd': {}}, {'p': passed, 'd': direct}) == 40
    assert phase_stop('pilot', 40, tasks, {'p': {}, 'd': {}, 'later': {}},
                      {'p': passed, 'd': direct, 'later': {'transport_success': False}}) == 40
    import pytest
    with pytest.raises(ValueError, match='Unknown-outcome'):
        phase_stop('pilot', 40, tasks, {'p': {}, 'd': {}, 'later': {}}, {'p': passed, 'd': direct})
    assert phase_stop('recovery-sanity', 40, tasks, {'p': {}, 'd': {}},
                      {'p': failed, 'd': failed}) == 4
    recovered_started = {'p': {}, 'd': {}, 'p2': {}, 'd2': {}}
    recovered_finished = {'p': failed, 'd': failed, 'p2': passed, 'd2': direct}
    assert phase_stop('pilot', 40, tasks, recovered_started, recovered_finished) == 40


def test_campaign_lock_refuses_concurrent_writer(tmp_path):
    first = acquire_campaign_lock(tmp_path)
    import pytest
    with pytest.raises(ValueError, match='already holds'):
        acquire_campaign_lock(tmp_path)
    first.close()
    second = acquire_campaign_lock(tmp_path)
    second.close()
