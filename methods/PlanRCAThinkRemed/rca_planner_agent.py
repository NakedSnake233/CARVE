"""Plan2RCA-style diagnostic planning agent.

Produces an :class:`RCAPlan` with multiple competing hypotheses (service /
resource / network / config / dependency / application levels) and ordered,
modality-tagged diagnostic steps. Uses ``models.llm.chat_api`` via the
:mod:`llm_json` helper, with a deterministic rule-based fallback so the pipeline
never stalls if the LLM is unavailable or returns garbage.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import prompts
from .llm_json import call_tool_json
from .schemas import RCAPlan, Hypothesis


# A deterministic, label-free default plan. The first diagnostic step always
# enumerates active Chaos Mesh experiments and pod health, because those are the
# highest-information read-only probes for this benchmark's fault model.
def _default_diagnostic_steps(namespace: str) -> List[Dict[str, Any]]:
    return [
        {
            "target_modality": "event",
            "command_intent": "List active chaos experiments and pod health to see "
                              "which workload is currently being disturbed.",
            "expected_information_gain": "Identify the disturbed service and the fault family.",
            "stop_condition": "An active chaos resource or an unhealthy pod is found.",
        },
        {
            "target_modality": "resource",
            "command_intent": "Inspect pod status, restarts, readiness and resource saturation.",
            "expected_information_gain": "Distinguish resource exhaustion vs crashloop vs config error.",
            "stop_condition": "Resource/restart anomaly localized to a service.",
        },
        {
            "target_modality": "config",
            "command_intent": "Describe the suspect deployment: resource limits, probes, image, env.",
            "expected_information_gain": "Reveal under-provisioned limits or bad config fields.",
            "stop_condition": "A concrete misconfigured field is identified, or ruled out.",
        },
        {
            "target_modality": "log",
            "command_intent": "Read recent logs of the suspect service for error patterns.",
            "expected_information_gain": "Application-level error signatures.",
            "stop_condition": "Error signature explains the symptom, or logs are clean.",
        },
        {
            "target_modality": "dependency",
            "command_intent": "Check endpoints, DNS and connectivity to upstream dependencies.",
            "expected_information_gain": "Network delay/loss or broken dependency edges.",
            "stop_condition": "Connectivity confirmed healthy or degraded.",
        },
    ]


def _default_hypotheses() -> List[Hypothesis]:
    levels = [
        ("resource", "resource saturation (cpu/memory) on a target service"),
        ("network", "injected network delay/loss on a service's traffic"),
        ("application", "pod failure / crashloop on a target service"),
        ("config", "under-provisioned or invalid deployment configuration"),
        ("dependency", "broken dependency / endpoint / DNS resolution"),
    ]
    out: List[Hypothesis] = []
    for category, mechanism in levels:
        out.append(Hypothesis(
            service="",
            failure_category=category,
            mechanism=mechanism,
            confidence=0.2,
            missing_evidence=["which service is affected", "direct evidence of the mechanism"],
            repairability="needs_more_evidence",
        ))
    return out


def build_default_plan(namespace: str, incident_context: str = "") -> RCAPlan:
    return RCAPlan(
        incident_summary=incident_context or "Microservice incident detected; root cause unknown.",
        candidate_hypotheses=_default_hypotheses(),
        diagnostic_steps=_default_diagnostic_steps(namespace),
        stop_condition="Top hypothesis confidence >= 0.70 with >=2 independent evidence items.",
    )


def build_rca_plan(*, runtime_envs: str, namespace: str, env: str,
                   incident_context: str,
                   examples: Optional[List[Dict[str, Any]]] = None,
                   oracle_hint: Optional[Dict[str, str]] = None,
                   chat_api: Any = None) -> RCAPlan:
    """Return an RCAPlan from the LLM, falling back to a deterministic plan."""
    default = build_default_plan(namespace, incident_context)
    messages = prompts.build_rca_planner_messages(
        runtime_envs=runtime_envs, namespace=namespace, env=env,
        incident_context=incident_context, examples=examples, oracle_hint=oracle_hint,
    )
    raw = call_tool_json(messages, prompts.EMIT_RCA_PLAN_TOOL, fallback=None, chat_api=chat_api)
    if not isinstance(raw, dict):
        return default

    plan = RCAPlan.from_dict(raw)
    # Guard against an empty/degenerate LLM plan -- keep the model's content but
    # ensure we always have at least the high-value default probes.
    if not plan.diagnostic_steps:
        plan.diagnostic_steps = default.diagnostic_steps
    if not plan.candidate_hypotheses:
        plan.candidate_hypotheses = default.candidate_hypotheses
    if not plan.incident_summary:
        plan.incident_summary = default.incident_summary
    if not plan.stop_condition:
        plan.stop_condition = default.stop_condition
    return plan
