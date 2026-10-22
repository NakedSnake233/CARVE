"""Provider-usage and token-accounting helpers for RCA/recovery episodes.

The benchmark keeps provider-reported usage separate from character estimates.
Missing usage is ``unknown`` and is never converted to zero.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional


def estimate_messages(messages: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    chars = 0
    for message in messages:
        try:
            chars += len(json.dumps(message, ensure_ascii=False))
        except Exception:
            chars += len(str(message))
    return {"input_tokens": None, "output_tokens": None,
            "total_tokens": max(chars // 4, 0), "token_source": "estimate",
            "estimated_chars": chars}


def _add_number(out: Dict[str, Any], key: str, value: Any) -> None:
    if isinstance(value, (int, float)) and value >= 0:
        current = out.get(key)
        out[key] = (current if isinstance(current, (int, float)) else 0) + value


def summarize_attempts(attempts: Optional[Iterable[Dict[str, Any]]]) -> Dict[str, Any]:
    rows = list(attempts or [])
    out: Dict[str, Any] = {
        "attempts": len(rows), "successful_attempts": 0,
        "input_tokens": None, "output_tokens": None, "total_tokens": None,
        "cache_read_tokens": None, "cache_creation_tokens": None,
        "token_source": "unknown",
    }
    found = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("success"):
            out["successful_attempts"] += 1
        usage = row.get("usage")
        if not isinstance(usage, dict):
            continue
        found = True
        aliases = {
            "input_tokens": ("input_tokens", "prompt_tokens"),
            "output_tokens": ("output_tokens", "completion_tokens"),
            "total_tokens": ("total_tokens",),
            "cache_read_tokens": ("cache_read_input_tokens", "cache_read_tokens"),
            "cache_creation_tokens": ("cache_creation_input_tokens", "cache_creation_tokens"),
        }
        for target, names in aliases.items():
            for name in names:
                if isinstance(usage.get(name), (int, float)):
                    _add_number(out, target, usage[name]); break
    if found:
        out["token_source"] = "provider"
        if out["total_tokens"] is None and out["input_tokens"] is not None and out["output_tokens"] is not None:
            out["total_tokens"] = out["input_tokens"] + out["output_tokens"]
    return out


def normalize_token_record(record: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(record, dict):
        return summarize_attempts([])
    if isinstance(record.get("usage"), dict):
        return summarize_attempts([{"success": True, "usage": record["usage"]}])
    if isinstance(record.get("attempts"), list):
        return summarize_attempts(record["attempts"])
    if isinstance(record.get("token_usage"), dict):
        return dict(record["token_usage"])
    if isinstance(record.get("token_estimate"), (int, float)):
        return {"input_tokens": None, "output_tokens": None,
                "total_tokens": record["token_estimate"], "token_source": "estimate"}
    return summarize_attempts([])


def combine(*records: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    rows = [normalize_token_record(r) for r in records]
    out = {"input_tokens": None, "output_tokens": None, "total_tokens": None,
           "cache_read_tokens": None, "cache_creation_tokens": None,
           "token_source": "unknown"}
    for key in ("input_tokens", "output_tokens", "total_tokens", "cache_read_tokens", "cache_creation_tokens"):
        values = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
        if values: out[key] = sum(values)
    sources = {r.get("token_source") for r in rows}
    out["token_source"] = "provider" if "provider" in sources else ("estimate" if "estimate" in sources else "unknown")
    return out
