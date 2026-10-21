"""Lightweight, pure-Python Cloud-OpsBench retriever used as an *RCA enhancer*.

Cloud-OpsBench cases are used only as **historical, clearly-separated few-shot
examples** to bias diagnostic planning toward useful tools / evidence patterns.
They are NEVER copied in as the current case's labels (see the explicit
abstraction step in :func:`_abstract_example`).

No heavy dependencies (no sklearn / faiss / embeddings). Retrieval is lexical
token overlap, which is plenty for "find the most similar past incident".

Expected Cloud-OpsBench layout::

    benchmark/<system>/<fault_category>/<case_id>/
        metadata.json
        tool_cache.json
        raw_data/{alert.json,k8s_states.json,logs.json,metrics.csv}
    golden-trajectory/<system>/<fault_category>/<case_id>/{path1.json,...}

The method works fine if Cloud-OpsBench is absent: every loader degrades to an
empty list instead of raising.
"""

from __future__ import annotations

import glob
import json
import os
import re
from typing import Any, Dict, List, Optional

# MicroRemed env identifier -> Cloud-OpsBench system folder name.
# simple-micro intentionally maps to None: it has no Cloud-OpsBench analogue, so
# RAG returns empty examples for it.
ENV_TO_SYSTEM: Dict[str, Optional[str]] = {
    "online-boutique": "boutique",
    "boutique": "boutique",
    "train-ticket": "trainticket",
    "trainticket": "trainticket",
    "simple-micro": None,
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "of", "to", "in", "on", "and",
    "or", "for", "with", "service", "pod", "error", "failure", "fault", "issue",
}


def _tokenize(text: str) -> List[str]:
    if not text:
        return []
    return [t for t in _TOKEN_RE.findall(str(text).lower()) if t not in _STOPWORDS and len(t) > 1]


def _safe_load_json(path: str) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _read_head(path: str, max_chars: int = 4000) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read(max_chars)
    except Exception:
        return ""


def _stringify(value: Any, limit: int = 1200) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:limit]
    try:
        return json.dumps(value, ensure_ascii=False)[:limit]
    except Exception:
        return str(value)[:limit]


# ---------------------------------------------------------------------------
# Loading + abstraction
# ---------------------------------------------------------------------------

def _extract_tool_names(tool_cache: Any) -> List[str]:
    names: List[str] = []
    if isinstance(tool_cache, dict):
        for key in ("tools", "tool_calls", "calls"):
            seq = tool_cache.get(key)
            if isinstance(seq, list):
                for item in seq:
                    if isinstance(item, dict):
                        n = item.get("name") or item.get("tool") or item.get("tool_name")
                        if n:
                            names.append(str(n))
                    elif isinstance(item, str):
                        names.append(item)
        # fall back to the dict keys themselves (cache keyed by tool name)
        if not names:
            names = [str(k) for k in tool_cache.keys()]
    elif isinstance(tool_cache, list):
        for item in tool_cache:
            if isinstance(item, dict):
                n = item.get("name") or item.get("tool")
                if n:
                    names.append(str(n))
            elif isinstance(item, str):
                names.append(item)
    # dedupe, cap
    out: List[str] = []
    for n in names:
        if n not in out:
            out.append(n)
    return out[:20]


def _extract_diagnosis(metadata: Any, golden: List[Any]) -> str:
    parts: List[str] = []
    if isinstance(metadata, dict):
        for key in ("diagnosis", "root_cause", "description", "summary", "analysis"):
            v = metadata.get(key)
            if v:
                parts.append(_stringify(v, 400))
    for g in golden:
        if isinstance(g, dict):
            for key in ("diagnosis", "conclusion", "reasoning", "answer"):
                v = g.get(key)
                if v:
                    parts.append(_stringify(v, 400))
    return " ".join(parts)[:1200]


def _abstract_example(system: str, fault_category: str, case_id: str,
                      metadata: Any, tool_cache: Any, alert: Any,
                      golden: List[Any]) -> Dict[str, Any]:
    """Reduce a raw case to an *abstract pattern* safe to show the model.

    We keep: fault category, a symptom summary, the diagnostic tools that were
    useful, an evidence pattern, and (only if inferable) a repair hint. The case
    is always tagged as a historical example with its own case id so the prompt
    layer can present it clearly separated from the current incident.
    """
    services: List[str] = []
    if isinstance(metadata, dict):
        for key in ("service", "services", "target_service", "component"):
            v = metadata.get(key)
            if isinstance(v, str):
                services.append(v)
            elif isinstance(v, list):
                services.extend(str(x) for x in v)

    symptom = ""
    if isinstance(alert, dict):
        symptom = _stringify(alert.get("alert") or alert.get("message") or alert, 300)
    elif alert:
        symptom = _stringify(alert, 300)
    if not symptom and isinstance(metadata, dict):
        symptom = _stringify(metadata.get("symptom") or metadata.get("description"), 300)

    diagnosis = _extract_diagnosis(metadata, golden)
    tools = _extract_tool_names(tool_cache)

    repair_hint = ""
    if isinstance(metadata, dict):
        repair_hint = _stringify(metadata.get("remediation") or metadata.get("repair") or
                                 metadata.get("solution") or metadata.get("mitigation"), 300)
    for g in golden:
        if not repair_hint and isinstance(g, dict):
            repair_hint = _stringify(g.get("remediation") or g.get("action"), 300)

    return {
        "is_historical_example": True,
        "system": system,
        "case_id": case_id,
        "fault_category": fault_category,
        "symptom_summary": symptom,
        "useful_tools": tools,
        "evidence_pattern": diagnosis,
        "services": [s for s in services if s][:8],
        "repair_hint": repair_hint,
        # Pre-tokenized text used for lexical scoring (kept out of prompts).
        "_search_blob": " ".join([
            fault_category, symptom, diagnosis, " ".join(tools), " ".join(services), case_id,
        ]),
    }


def load_cloudops_examples(root: str) -> List[Dict[str, Any]]:
    """Scan a Cloud-OpsBench checkout and return abstracted example patterns.

    Returns ``[]`` if ``root`` is missing/empty (never raises).
    """
    if not root or not os.path.isdir(root):
        return []

    benchmark_root = os.path.join(root, "benchmark")
    if not os.path.isdir(benchmark_root):
        # Allow pointing directly at a benchmark/ dir.
        benchmark_root = root if os.path.basename(os.path.normpath(root)) == "benchmark" else benchmark_root
    if not os.path.isdir(benchmark_root):
        return []

    golden_root = os.path.join(root, "golden-trajectory")
    examples: List[Dict[str, Any]] = []

    for system in sorted(os.listdir(benchmark_root)):
        sys_dir = os.path.join(benchmark_root, system)
        if not os.path.isdir(sys_dir):
            continue
        for fault_category in sorted(os.listdir(sys_dir)):
            fc_dir = os.path.join(sys_dir, fault_category)
            if not os.path.isdir(fc_dir):
                continue
            for case_id in sorted(os.listdir(fc_dir)):
                case_dir = os.path.join(fc_dir, case_id)
                if not os.path.isdir(case_dir):
                    continue
                metadata = _safe_load_json(os.path.join(case_dir, "metadata.json"))
                tool_cache = _safe_load_json(os.path.join(case_dir, "tool_cache.json"))
                alert = _safe_load_json(os.path.join(case_dir, "raw_data", "alert.json"))

                golden: List[Any] = []
                gdir = os.path.join(golden_root, system, fault_category, case_id)
                if os.path.isdir(gdir):
                    for gpath in sorted(glob.glob(os.path.join(gdir, "*.json"))):
                        gd = _safe_load_json(gpath)
                        if gd is not None:
                            golden.append(gd)

                examples.append(_abstract_example(
                    system, fault_category, case_id, metadata, tool_cache, alert, golden,
                ))
    return examples


def load_index(index_path: str) -> List[Dict[str, Any]]:
    """Load a pre-built JSONL index produced by scripts/build_cloudops_rca_index.py."""
    if not index_path or not os.path.isfile(index_path):
        return []
    out: List[Dict[str, Any]] = []
    try:
        with open(index_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    return out


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

def _score(query_tokens: List[str], example: Dict[str, Any], system: Optional[str]) -> float:
    blob = example.get("_search_blob") or " ".join([
        str(example.get("fault_category", "")),
        str(example.get("symptom_summary", "")),
        str(example.get("evidence_pattern", "")),
        " ".join(example.get("useful_tools", []) or []),
        " ".join(example.get("services", []) or []),
    ])
    ex_tokens = set(_tokenize(blob))
    if not ex_tokens or not query_tokens:
        base = 0.0
    else:
        overlap = sum(1 for t in query_tokens if t in ex_tokens)
        base = overlap / (len(query_tokens) ** 0.5)
    # Prefer examples from the same system.
    if system and example.get("system") == system:
        base += 0.5
    return base


def retrieve_examples(query: str, env: str, k: int = 3,
                      examples: Optional[List[Dict[str, Any]]] = None,
                      cloudopsbench_root: Optional[str] = None,
                      index_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return up to ``k`` abstracted historical examples most similar to ``query``.

    Resolution order for the corpus: explicit ``examples`` arg -> pre-built
    ``index_path`` -> scan ``cloudopsbench_root`` in memory. Unmapped envs
    (e.g. simple-micro) always return ``[]``.
    """
    if k <= 0:
        return []
    system = ENV_TO_SYSTEM.get((env or "").strip().lower(), None)
    if env and (env.strip().lower() in ENV_TO_SYSTEM) and system is None:
        # Explicitly unmapped env (simple-micro) -> no Cloud-OpsBench examples.
        return []

    corpus = examples
    if corpus is None and index_path:
        corpus = load_index(index_path)
    if corpus is None and cloudopsbench_root:
        corpus = load_cloudops_examples(cloudopsbench_root)
    if not corpus:
        return []

    # Restrict to the mapped system when we know it.
    if system:
        same = [e for e in corpus if e.get("system") == system]
        corpus = same or corpus

    query_tokens = _tokenize(query)
    scored = [(_score(query_tokens, e, system), e) for e in corpus]
    scored = [pair for pair in scored if pair[0] > 0.0]
    scored.sort(key=lambda pair: pair[0], reverse=True)

    out: List[Dict[str, Any]] = []
    for _, ex in scored[:k]:
        # Strip the internal search blob before returning to callers/prompts.
        clean = {key: val for key, val in ex.items() if not key.startswith("_")}
        out.append(clean)
    return out
