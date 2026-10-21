"""Structured logging helpers for PlanRCAThinkRemed.

Pure stdlib. The coordinator builds a single ``trace`` dict per episode and uses
:func:`save_trace` to persist it. The human-readable role/content conversation
returned to MicroRemed is built separately (see :class:`ConversationLog`).
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional


def estimate_token_count(conversation: List[Dict[str, Any]]) -> int:
    """Match MicroRemed's own rough estimate: ~1 token per 4 characters."""
    try:
        total = sum(len(json.dumps(msg, ensure_ascii=False)) for msg in conversation)
    except Exception:
        total = sum(len(str(msg)) for msg in conversation)
    return total // 4


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", str(text)).strip("-") or "episode"


def save_trace(trace: Dict[str, Any], *, save_debug_dir: Optional[str],
               save_path_fallback: Optional[str] = None,
               namespace: str = "", env: str = "",
               episode_tag: str = "episode") -> Optional[str]:
    """Persist the structured debug trace as JSON.

    Writes under ``save_debug_dir`` if given, else under ``save_path_fallback``
    (the benchmark's --save-path). Never raises -- logging must not break a run.
    Returns the written path, or ``None`` if nothing was written.
    """
    target_dir = save_debug_dir or save_path_fallback
    if not target_dir:
        return None
    try:
        os.makedirs(target_dir, exist_ok=True)
        # Avoid Date.now()-style nondeterminism issues: use the tag + attempts.
        name = f"planrca_trace_{_slug(namespace)}_{_slug(episode_tag)}.json"
        path = os.path.join(target_dir, name)
        # If a file with this name already exists, disambiguate by index.
        if os.path.exists(path):
            i = 1
            base, ext = os.path.splitext(path)
            while os.path.exists(f"{base}_{i}{ext}"):
                i += 1
            path = f"{base}_{i}{ext}"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(trace, fh, ensure_ascii=False, indent=2, default=str)
        return path
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[PlanRCAThinkRemed] WARN: failed to save trace: {exc}")
        return None


class ConversationLog:
    """Accumulates the role/content message list returned to MicroRemed.

    MicroRemed expects ``conversations`` to be a list of ``{role, content}``
    dicts (it serializes them and estimates tokens). We keep the surface compact
    and human-auditable while the full structured state lives in the trace.
    """

    def __init__(self) -> None:
        self._messages: List[Dict[str, str]] = []

    def system(self, content: str) -> None:
        self._messages.append({"role": "system", "content": content})

    def user(self, content: str) -> None:
        self._messages.append({"role": "user", "content": content})

    def assistant(self, content: str) -> None:
        self._messages.append({"role": "assistant", "content": content})

    def add(self, role: str, content: str) -> None:
        self._messages.append({"role": role, "content": content})

    @property
    def messages(self) -> List[Dict[str, str]]:
        return self._messages

    def token_estimate(self) -> int:
        return estimate_token_count(self._messages)
