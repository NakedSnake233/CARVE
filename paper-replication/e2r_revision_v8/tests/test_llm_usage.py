import json
from urllib.error import URLError

from rca_bench import llm_rca


class Response:
    def __init__(self, usage=None):
        self.usage = usage

    def read(self):
        return json.dumps({"choices": [{"message": {"content": "{}"}}],
                           "usage": self.usage}).encode()


def config():
    return {"model": "test-model", "api_key": "test-secret", "base_url": "https://example.invalid",
            "max_retries": 2}


def test_failed_attempt_is_not_counted_as_zero_cost(monkeypatch):
    responses = iter([URLError("temporary"), Response({"prompt_tokens": 20, "completion_tokens": 3})])

    def call(*args, **kwargs):
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(llm_rca.urllib.request, "urlopen", call)
    monkeypatch.setattr(llm_rca.time, "sleep", lambda _: None)
    telemetry = {}
    assert llm_rca._call_llm(config(), "system", "user", telemetry) == "{}"
    assert len(telemetry["attempts"]) == 2
    assert telemetry["attempts"][0]["usage"] is None
    assert telemetry["attempts"][1]["usage"]["prompt_tokens"] == 20
    assert "test-secret" not in json.dumps(telemetry)


def test_missing_usage_and_invalid_ranking_remain_auditable(monkeypatch):
    monkeypatch.setattr(llm_rca.urllib.request, "urlopen", lambda *a, **k: Response())
    monkeypatch.setattr(llm_rca, "_load_cfg", config)
    monkeypatch.setattr(llm_rca, "_log", lambda _: None)
    ctx = llm_rca.MultimodalContext("ns", "train-ticket", ["a"])
    adapter = llm_rca.LLMRCA()
    adapter.diagnose(ctx)
    assert adapter.last_diagnostics["used_llm"] is False
    assert adapter.last_diagnostics["attempts"][0]["success"] is True
    assert adapter.last_diagnostics["attempts"][0]["usage"] is None
    assert "train-ticket" in llm_rca._build_user(ctx)
