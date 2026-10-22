import json
import os
from pathlib import Path

from rca_bench.llm_transport import build_request, parse_response, request_settings

ROOT = Path(__file__).resolve().parents[1]


def headers(request):
    return {key.lower(): value for key, value in request.header_items()}


def test_opencode_openai_request_and_response_contract():
    cfg = {'api_style': 'openai', 'base_url': 'https://opencode.ai/zen/go/v1',
           'api_key': 'private-test', 'model': 'deepseek-v4.1-flash',
           'temperature': 0, 'user_agent': 'rca2remed-research/0.1'}
    request, sid = build_request(cfg, [{'role': 'user', 'content': 'hi'}],
                                 max_tokens=17, request_session='stable-session')
    assert request.full_url == 'https://opencode.ai/zen/go/v1/chat/completions'
    assert sid == 'stable-session'
    assert headers(request)['authorization'] == 'Bearer private-test'
    assert headers(request)['x-opencode-session'] == 'stable-session'
    assert headers(request)['user-agent'] == 'rca2remed-research/0.1'
    body = json.loads(request.data)
    assert body == {'model': 'deepseek-v4.1-flash',
                    'messages': [{'role': 'user', 'content': 'hi'}],
                    'temperature': 0, 'max_tokens': 17, 'stream': False}
    parsed = parse_response({'id': 'o1', 'model': cfg['model'],
        'choices': [{'message': {'content': 'ok'}}],
        'usage': {'prompt_tokens': 3, 'completion_tokens': 2, 'total_tokens': 5}}, 'openai')
    assert parsed['response_text'] == 'ok' and parsed['usage']['total_tokens'] == 5


def test_opencode_anthropic_request_and_response_contract():
    cfg = {'api_style': 'anthropic', 'base_url': 'https://opencode.ai/zen/go/v1',
           'api_key': 'private-test', 'model': 'qwen3.8-flash',
           'temperature': 0, 'anthropic_version': '2023-06-01',
           'thinking': {'type':'disabled'}}
    messages = [{'role': 'system', 'content': 'rules'}, {'role': 'user', 'content': 'hi'}]
    request, _ = build_request(cfg, messages, max_tokens=19, request_session='stable-session')
    assert request.full_url == 'https://opencode.ai/zen/go/v1/messages'
    assert headers(request)['x-api-key'] == 'private-test'
    assert 'authorization' not in headers(request)
    assert headers(request)['anthropic-version'] == '2023-06-01'
    body = json.loads(request.data)
    assert body == {'model': 'qwen3.8-flash', 'messages': [{'role': 'user', 'content': 'hi'}],
                    'temperature': 0, 'max_tokens': 19, 'stream': False, 'system': 'rules',
                    'thinking': {'type':'disabled'}}
    parsed = parse_response({'id': 'a1', 'model': cfg['model'],
        'content': [{'type': 'thinking', 'thinking': 'hidden'}, {'type': 'text', 'text': 'ok'}],
        'usage': {'input_tokens': 7, 'output_tokens': 3, 'cache_read_input_tokens': 2}}, 'anthropic')
    assert parsed['response_text'] == 'ok'
    assert parsed['usage']['prompt_tokens'] == 7
    assert parsed['usage']['completion_tokens'] == 3
    assert parsed['usage']['total_tokens'] == 10


def test_opencode_responses_request_and_response_contract():
    cfg = {'api_style': 'responses', 'base_url': 'https://opencode.ai/zen/go/v1',
           'api_key': 'private-test', 'model': 'gpt-5.6-luna'}
    messages = [{'role': 'system', 'content': 'rules'}, {'role': 'user', 'content': 'hi'}]
    request, _ = build_request(cfg, messages, max_tokens=23, request_session='stable-session')
    assert request.full_url == 'https://opencode.ai/zen/go/v1/responses'
    assert headers(request)['authorization'] == 'Bearer private-test'
    assert headers(request)['x-opencode-session'] == 'stable-session'
    body = json.loads(request.data)
    assert body == {'model': 'gpt-5.6-luna', 'input': [{'role': 'user', 'content': 'hi'}],
                    'max_output_tokens': 23, 'stream': False, 'instructions': 'rules'}
    parsed = parse_response({'id': 'r1', 'model': cfg['model'],
        'output': [{'type': 'reasoning', 'summary': []},
                   {'type': 'message', 'content': [{'type': 'output_text', 'text': 'ok'}]}],
        'usage': {'input_tokens': 11, 'output_tokens': 4, 'total_tokens': 15,
                  'output_tokens_details': {'reasoning_tokens': 1}}}, 'responses')
    assert parsed['response_text'] == 'ok'
    assert parsed['usage']['prompt_tokens'] == 11
    assert parsed['usage']['completion_tokens'] == 4
    assert parsed['usage']['total_tokens'] == 15
    assert request_settings(cfg,max_tokens=23)=={
        'api_style':'responses','model':'gpt-5.6-luna','stream':False,'max_output_tokens':23}


def test_private_configs_match_official_opencode_go_routes():
    for relative in ('llm_rca_config.json', 'datasets/aiops2022/llm_config.json'):
        path = ROOT / relative
        cfg = json.loads(path.read_text())
        assert cfg['active'] == 'qwen3.8-flash'
        assert set(cfg['models']) == {'qwen3.8-flash', 'deepseek-v4.1-flash', 'gpt-5.6-luna'}
        qwen = cfg['models']['qwen3.8-flash']
        deepseek = cfg['models']['deepseek-v4.1-flash']
        luna = cfg['models']['gpt-5.6-luna']
        assert qwen['base_url'] == deepseek['base_url'] == luna['base_url'] == 'https://opencode.ai/zen/go/v1'
        assert (qwen['api_style'], qwen['model']) == ('anthropic', 'qwen3.8-flash')
        assert (deepseek['api_style'], deepseek['model']) == ('openai', 'deepseek-v4.1-flash')
        assert (luna['api_style'], luna['model']) == ('responses', 'gpt-5.6-luna')
        assert qwen['api_key'] == deepseek['api_key'] == luna['api_key']
        assert qwen['api_key'].startswith('oc_sk_')
        assert os.stat(path).st_mode & 0o777 == 0o600
