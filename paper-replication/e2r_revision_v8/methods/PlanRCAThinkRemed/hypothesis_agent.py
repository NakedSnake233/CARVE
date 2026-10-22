"""Evidence-grounded hypothesis maintenance and RCA report synthesis.

Public API (per spec):

* ``update_hypotheses(plan, evidence_history) -> list[Hypothesis]``
* ``should_stop_and_repair(hypotheses, evidence_history) -> (bool, str)``
* ``build_rca_report(hypotheses, evidence_history) -> RCAReport``

The deterministic backbone aggregates evidence into (service, category) scores so
the workflow converges even with no LLM. When an LLM is available it can refine
the ranking, but the stop decision is always re-checked deterministically so the
"repair only with sufficient evidence" rule cannot be talked around by the model.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from . import prompts
from .llm_json import call_tool_json
from .schemas import Hypothesis, RCAReport, EvidenceItem

# Stop thresholds (also stated in the prompt; enforced here regardless).
CONFIDENCE_STOP = 0.70
MIN_INDEPENDENT_EVIDENCE = 2

_KNOWN_CATEGORIES = (
    "cpu-stress", "memory-stress", "network-delay", "network-loss",
    "pod-fail", "disk-io", "pod-config-error",
)

def _rank_key(h: "Hypothesis"):
    """Rank hypotheses: a concrete, repairable known category (chaos / config)
    outranks a vague LLM category (e.g. "service") even at slightly lower
    confidence, so grounded deterministic detections stay authoritative."""
    known = 1 if h.failure_category in _KNOWN_CATEGORIES else 0
    return (known, h.confidence)


# Repair hints keyed by category (derived from observed evidence, not oracle).
_REPAIR_HINT = {
    "cpu-stress": "Remove the injected CPU StressChaos targeting the service.",
    "memory-stress": "Remove the injected memory StressChaos targeting the service.",
    "network-delay": "Remove the injected NetworkChaos (delay) targeting the service.",
    "network-loss": "Remove the injected NetworkChaos (loss) targeting the service.",
    "pod-fail": "Remove the injected PodChaos so the pod can recover.",
    "disk-io": "Remove the injected IOChaos targeting the service volume.",
    "pod-config-error": "Restore the deployment's under-provisioned resource limits.",
}


def _category_from_evidence(ev: EvidenceItem) -> Optional[str]:
    text = f"{ev.finding} {ev.raw_excerpt}".lower()
    for cat in _KNOWN_CATEGORIES:
        if cat in text:
            return cat
    # heuristics for un-named signals
    if ev.modality == "config" and "limit" in text:
        return "pod-config-error"
    if "stresschaos" in text:
        return "cpu-stress" if "cpu" in text else ("memory-stress" if "memory" in text else "cpu-stress")
    if "networkchaos" in text:
        return "network-delay" if "delay" in text else ("network-loss" if "loss" in text else "network-delay")
    if "podchaos" in text:
        return "pod-fail"
    if "iochaos" in text:
        return "disk-io"
    return None


def _aggregate(evidence: List[EvidenceItem]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    scores: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for ev in evidence:
        svc = (ev.service or "").strip()
        cat = _category_from_evidence(ev) or ""
        if not svc or not cat:
            continue
        key = (svc, cat)
        bucket = scores.setdefault(key, {
            "max_conf": 0.0, "supporting": [], "sources": set(), "modalities": set(),
        })
        bucket["max_conf"] = max(bucket["max_conf"], ev.confidence)
        if ev.finding:
            bucket["supporting"].append(ev.finding)
        bucket["sources"].add(ev.source or "")
        bucket["modalities"].add(ev.modality or "")
    return scores


def _independent_count(bucket: Dict[str, Any]) -> int:
    # Independent == distinct (source or modality). Use the larger set.
    return max(len(bucket.get("sources") or set()), len(bucket.get("modalities") or set()))


def _deterministic_hypotheses(evidence: List[EvidenceItem]) -> List[Hypothesis]:
    scores = _aggregate(evidence)
    hyps: List[Hypothesis] = []
    for (svc, cat), bucket in scores.items():
        indep = _independent_count(bucket)
        # Confidence blends the strongest single signal with corroboration.
        conf = min(0.99, bucket["max_conf"] * (1.0 + 0.1 * max(0, indep - 1)))
        repairability = "repairable" if conf >= CONFIDENCE_STOP and indep >= MIN_INDEPENDENT_EVIDENCE \
            else "needs_more_evidence"
        hyps.append(Hypothesis(
            service=svc,
            failure_category=cat,
            mechanism=_REPAIR_HINT.get(cat, "see supporting evidence"),
            confidence=round(conf, 3),
            supporting_evidence=bucket["supporting"][:6],
            contradicting_evidence=[],
            missing_evidence=[] if repairability == "repairable" else ["independent corroboration"],
            repairability=repairability,
        ))
    hyps.sort(key=_rank_key, reverse=True)
    return hyps


def _merge_llm_hypotheses(det: List[Hypothesis], llm_raw: Any) -> List[Hypothesis]:
    """Prefer deterministic high-confidence chaos/config hypotheses; let the LLM
    contribute additional ones and refine ordering, but never erase a strong
    evidence-backed deterministic hypothesis."""
    if not isinstance(llm_raw, dict):
        return det
    llm_hyps = [Hypothesis.from_dict(h) for h in (llm_raw.get("hypotheses") or [])]
    by_key = {(h.service, h.failure_category): h for h in det}
    for lh in llm_hyps:
        key = (lh.service, lh.failure_category)
        if key in by_key:
            # keep the higher confidence of the two
            if lh.confidence > by_key[key].confidence:
                by_key[key].confidence = lh.confidence
            if lh.mechanism and not by_key[key].mechanism:
                by_key[key].mechanism = lh.mechanism
        else:
            by_key[key] = lh
    merged = list(by_key.values())
    merged.sort(key=_rank_key, reverse=True)
    return merged


def update_hypotheses(plan: Any, evidence_history: List[EvidenceItem], *,
                      current_hypotheses: Optional[List[Hypothesis]] = None,
                      use_llm: bool = True, oracle_hint: Optional[Dict[str, str]] = None,
                      chat_api: Any = None) -> List[Hypothesis]:
    det = _deterministic_hypotheses(evidence_history)
    if not use_llm:
        return det or (current_hypotheses or [])

    plan_json = plan.to_dict() if hasattr(plan, "to_dict") else plan
    messages = prompts.build_hypothesis_messages(
        plan_json=plan_json,
        evidence_history_json=[e.to_dict() for e in evidence_history],
        current_hypotheses_json=[h.to_dict() for h in (current_hypotheses or det)],
        oracle_hint=oracle_hint,
    )
    raw = call_tool_json(messages, prompts.EMIT_HYPOTHESES_TOOL, fallback=None, chat_api=chat_api)
    merged = _merge_llm_hypotheses(det, raw)
    return merged or det or (current_hypotheses or [])


def should_stop_and_repair(hypotheses: List[Hypothesis],
                           evidence_history: List[EvidenceItem]) -> Tuple[bool, str]:
    if not hypotheses:
        return False, "no hypotheses yet"
    top = hypotheses[0]
    if top.confidence < CONFIDENCE_STOP:
        return False, f"top confidence {top.confidence:.2f} < {CONFIDENCE_STOP}"

    # Safety-critical missing evidence blocks repair (e.g. unknown target service).
    if not top.service:
        return False, "top hypothesis has no identified target service"

    # Independent evidence is counted over items concerning the top hypothesis'
    # SERVICE, by distinct (source or modality). At least one of them must also
    # identify the failure category, so we never repair on service alone.
    supporting = [ev for ev in evidence_history if (ev.service or "") == top.service]
    sources = {ev.source or "" for ev in supporting}
    modalities = {ev.modality or "" for ev in supporting}
    indep = max(len(sources), len(modalities))
    has_category = any(_category_from_evidence(ev) == top.failure_category for ev in supporting)
    if indep < MIN_INDEPENDENT_EVIDENCE:
        return False, f"only {indep} independent evidence item(s) for service '{top.service}'"
    if not has_category:
        return False, "no evidence item identifies the failure category for the top hypothesis"
    safety_missing = [m for m in top.missing_evidence
                      if any(tok in m.lower() for tok in ("service", "target", "field", "namespace"))]
    if safety_missing:
        return False, f"safety-critical missing evidence: {safety_missing}"

    return True, (f"top hypothesis service='{top.service}' category='{top.failure_category}' "
                  f"confidence={top.confidence:.2f} with {indep} independent evidence items")


def build_rca_report(hypotheses: List[Hypothesis], evidence_history: List[EvidenceItem], *,
                     exhausted: bool = False) -> RCAReport:
    """Synthesize the RCA report from the top hypothesis (best-effort if exhausted)."""
    # de-duplicate evidence by (source, finding)
    seen = set()
    unique_ev: List[EvidenceItem] = []
    for ev in evidence_history:
        key = (ev.source, ev.finding)
        if key not in seen:
            seen.add(key)
            unique_ev.append(ev)

    if not hypotheses:
        return RCAReport(
            root_cause_service="",
            failure_category="",
            mechanism="",
            confidence=0.0,
            evidence=unique_ev,
            uncertainty="No hypothesis could be formed from the collected evidence.",
            repair_hints=[],
        )

    top = hypotheses[0]
    uncertainty = ""
    if exhausted and top.confidence < CONFIDENCE_STOP:
        uncertainty = (f"RCA step budget exhausted; best-effort conclusion at "
                       f"confidence {top.confidence:.2f}. Consider WaitAndVerify.")
    elif top.confidence < CONFIDENCE_STOP:
        uncertainty = f"Low confidence ({top.confidence:.2f})."

    hints = list(top.repairability == "repairable" and top.missing_evidence == [] and [] or [])
    hint = _REPAIR_HINT.get(top.failure_category)
    if hint:
        hints.append(hint)

    return RCAReport(
        root_cause_service=top.service,
        failure_category=top.failure_category,
        mechanism=top.mechanism,
        confidence=top.confidence,
        evidence=unique_ev,
        uncertainty=uncertainty,
        repair_hints=hints,
    )
