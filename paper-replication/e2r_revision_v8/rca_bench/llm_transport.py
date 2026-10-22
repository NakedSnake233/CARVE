"""Protocol-correct HTTP envelopes for Chat Completions, Responses and Messages."""
from __future__ import annotations

import json
import uuid
import urllib.request


def session_id(prefix: str = "rca2remed") -> str:
    """Create a stable ID for one logical conversation/request-retry group."""
    return f"{prefix}-{uuid.uuid4().hex}"


def request_settings(cfg, *, max_tokens=None):
    """Return the non-secret settings that are actually serialized on the wire."""
    style=cfg.get('api_style','openai')
    limit=max_tokens if max_tokens is not None else cfg.get('max_tokens',2000)
    value={'api_style':style,'model':cfg['model'],'stream':False}
    if style=='responses':
        value['max_output_tokens']=limit
    else:
        value.update({'temperature':cfg.get('temperature',0),'max_tokens':limit})
        if style=='anthropic' and cfg.get('thinking') is not None:
            value['thinking']=cfg['thinking']
    return value


def build_request(cfg, messages, *, max_tokens=None, request_session=None):
    style = cfg.get("api_style", "openai")
    base = cfg["base_url"].rstrip("/")
    sid = request_session or session_id()
    headers = {
        "Content-Type": "application/json",
        "User-Agent": cfg.get("user_agent", "rca2remed-research/0.1"),
        "x-opencode-session": sid,
        "x-opencode-client": cfg.get("client_id", "rca2remed-research"),
    }
    limit = max_tokens if max_tokens is not None else cfg.get("max_tokens", 2000)
    if style == "openai":
        url = base + "/chat/completions"
        headers["Authorization"] = "Bearer " + cfg["api_key"]
        body = {"model": cfg["model"], "messages": messages,
                "temperature": cfg.get("temperature", 0), "max_tokens": limit,
                "stream": False}
    elif style == "responses":
        url = base + "/responses"
        headers["Authorization"] = "Bearer " + cfg["api_key"]
        systems = [m["content"] for m in messages if m.get("role") == "system"]
        turns = [m for m in messages if m.get("role") != "system"]
        body = {"model": cfg["model"], "input": turns,
                "max_output_tokens": limit, "stream": False}
        if systems:
            body["instructions"] = "\n\n".join(str(value) for value in systems)
    elif style == "anthropic":
        url = base + "/messages"
        headers["x-api-key"] = cfg["api_key"]
        headers["anthropic-version"] = cfg.get("anthropic_version", "2023-06-01")
        systems = [m["content"] for m in messages if m.get("role") == "system"]
        turns = [m for m in messages if m.get("role") != "system"]
        body = {"model": cfg["model"], "messages": turns,
                "temperature": cfg.get("temperature", 0), "max_tokens": limit,
                "stream": False}
        if systems:
            body["system"] = "\n\n".join(str(value) for value in systems)
        if cfg.get("thinking") is not None:
            body["thinking"] = cfg["thinking"]
    else:
        raise ValueError(f"unsupported api_style: {style}")
    return urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers), sid


def parse_response(payload, style):
    """Normalize text and usage while retaining protocol-specific usage fields."""
    usage = payload.get("usage")
    if style == "openai":
        message = payload["choices"][0]["message"]
        text = message.get("content") or ""
        safe_usage = None
        if isinstance(usage, dict):
            safe_usage = {key: usage[key] for key in (
                "prompt_tokens", "completion_tokens", "total_tokens",
                "prompt_tokens_details", "completion_tokens_details") if key in usage} or None
    elif style == "responses":
        if isinstance(payload.get("output_text"), str):
            text = payload["output_text"]
        else:
            output = payload["output"]
            if not isinstance(output, list):
                raise TypeError("Responses output must be a list")
            text = "".join(
                block.get("text", "")
                for item in output if isinstance(item, dict) and item.get("type") == "message"
                for block in item.get("content", [])
                if isinstance(block, dict) and block.get("type") in {"output_text", "text"}
            )
        safe_usage = None
        if isinstance(usage, dict):
            safe_usage = {key: usage[key] for key in (
                "input_tokens", "output_tokens", "total_tokens",
                "input_tokens_details", "output_tokens_details") if key in usage}
            if isinstance(usage.get("input_tokens"), (int, float)):
                safe_usage["prompt_tokens"] = usage["input_tokens"]
            if isinstance(usage.get("output_tokens"), (int, float)):
                safe_usage["completion_tokens"] = usage["output_tokens"]
            safe_usage = safe_usage or None
    elif style == "anthropic":
        blocks = payload["content"]
        if not isinstance(blocks, list):
            raise TypeError("Anthropic content must be a list")
        text = "".join(block.get("text", "") for block in blocks
                       if isinstance(block, dict) and block.get("type") == "text")
        safe_usage = None
        if isinstance(usage, dict):
            safe_usage = {key: usage[key] for key in (
                "input_tokens", "output_tokens", "cache_creation_input_tokens",
                "cache_read_input_tokens") if key in usage}
            if isinstance(usage.get("input_tokens"), (int, float)):
                safe_usage["prompt_tokens"] = usage["input_tokens"]
            if isinstance(usage.get("output_tokens"), (int, float)):
                safe_usage["completion_tokens"] = usage["output_tokens"]
            if "prompt_tokens" in safe_usage and "completion_tokens" in safe_usage:
                safe_usage["total_tokens"] = safe_usage["prompt_tokens"] + safe_usage["completion_tokens"]
            safe_usage = safe_usage or None
    else:
        raise ValueError(f"unsupported api_style: {style}")
    return {"response_text": text, "usage": safe_usage,
            "response_model": payload.get("model"), "provider_request_id": payload.get("id"),
            "provider_cost": payload.get("cost")}
