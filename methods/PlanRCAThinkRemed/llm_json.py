"""Robust JSON-over-tool-call helper around MicroRemed's ``models.llm.chat_api``.

MicroRemed's ``chat_api(prompts, tools)`` returns ``(content, tool_calls)`` where
``tool_calls[i]["function"]["arguments"]`` is either a JSON string (qwen / gpt /
local models) or already a dict (claude). For some providers ``chat_api`` only
returns tool calls when ``tools`` is non-empty, so we ALWAYS drive structured
output through a single function-calling tool.

Everything here is defensive: any failure (network, malformed JSON, empty
response) yields the caller-supplied ``fallback`` instead of raising, so a bad
LLM turn never crashes the benchmark.

``chat_api`` is imported lazily so the pure modules / offline tests don't need
httpx, tenacity, or ``LLM_API_KEY`` just to import this file.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        # remove ```json ... ``` or ``` ... ```
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _extract_balanced(text: str, open_ch: str, close_ch: str) -> Optional[str]:
    start = text.find(open_ch)
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def robust_json_loads(text: Any) -> Optional[Any]:
    """Best-effort parse of possibly-messy model output into a Python object."""
    if text is None:
        return None
    if isinstance(text, (dict, list)):
        return text
    if not isinstance(text, str):
        text = str(text)
    candidate = _strip_code_fences(text)
    if not candidate:
        return None
    # 1) direct
    try:
        return json.loads(candidate)
    except Exception:
        pass
    # 2) first balanced object, then array
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        blob = _extract_balanced(candidate, open_ch, close_ch)
        if blob:
            try:
                return json.loads(blob)
            except Exception:
                continue
    return None


def _tool_arguments(tool_calls: Any) -> Optional[Any]:
    if not tool_calls or not isinstance(tool_calls, list):
        return None
    first = tool_calls[0]
    if not isinstance(first, dict):
        return None
    fn = first.get("function") or {}
    args = fn.get("arguments")
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        return robust_json_loads(args)
    return None


def call_tool_json(messages: List[Dict[str, str]], tool_schema: Dict[str, Any], *,
                   fallback: Optional[Any] = None,
                   chat_api: Any = None) -> Any:
    """Call the LLM forcing a single tool and return its parsed JSON arguments.

    Parameters
    ----------
    messages : the prompt list ([{role, content}, ...]).
    tool_schema : an OpenAI-style function tool definition.
    fallback : returned on any error / empty / unparseable response.
    chat_api : optional injection point for tests; defaults to
               ``models.llm.chat_api`` imported lazily.
    """
    if chat_api is None:
        try:
            from models.llm import chat_api as _chat_api  # MicroRemed runtime dep
            chat_api = _chat_api
        except Exception as exc:
            print(f"[PlanRCAThinkRemed] WARN: models.llm.chat_api unavailable ({exc}); using fallback")
            return fallback

    try:
        content, tool_calls = chat_api(messages, [tool_schema])
    except Exception as exc:
        print(f"[PlanRCAThinkRemed] WARN: chat_api error ({exc}); using fallback")
        return fallback

    parsed = _tool_arguments(tool_calls)
    if parsed is not None:
        return parsed
    # Some providers may return JSON in plain content instead of a tool call.
    parsed = robust_json_loads(content)
    if parsed is not None:
        return parsed
    return fallback


def call_text(messages: List[Dict[str, str]], tool_schema: Dict[str, Any], *,
              key: str = "code", fallback: str = "",
              chat_api: Any = None) -> str:
    """Like :func:`call_tool_json` but pull a single string field (e.g. playbook code)."""
    result = call_tool_json(messages, tool_schema, fallback=None, chat_api=chat_api)
    if isinstance(result, dict) and isinstance(result.get(key), str):
        return result[key]
    if isinstance(result, str):
        return result
    return fallback
