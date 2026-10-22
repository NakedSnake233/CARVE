"""Prompt templates and tool (function-call) JSON schemas for PlanRCAThinkRemed.

Design rules baked in here:

* **Oracle-hidden by default.** None of the message builders accept the hidden
  ``root_cause`` / ``failure_category`` labels. An optional ``oracle_hint`` may
  be injected *only* when the coordinator is run with ``use_oracle_rca=True``
  (ablation / upper-bound debugging); it is clearly fenced as DEBUG context.
* **Structured, not prose.** Every agent is asked to answer by calling exactly
  one tool whose arguments are compact JSON. We never ask for private
  chain-of-thought -- only concise evidence summaries, hypotheses, and rationale.
* **Repair only when evidence is sufficient**, prefer minimal / reversible /
  low-blast-radius actions, and never emit destructive operations.

Pure module: stdlib only, no MicroRemed imports, so it is unit-testable offline.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .schemas import MODALITIES, ACTION_TYPES

# ---------------------------------------------------------------------------
# Shared policy text (kept short to control token cost).
# ---------------------------------------------------------------------------

SAFETY_POLICY = (
    "SAFETY POLICY:\n"
    "- Repair ONLY when evidence is sufficient; otherwise gather more evidence "
    "or emit a WaitAndVerify action.\n"
    "- Prefer minimal, reversible, low-blast-radius actions.\n"
    "- A generic restart must NOT be your first strategy; fix the actual cause.\n"
    "- NEVER propose: deleting a namespace, deleting PVC/PV, wiping a database, "
    "cluster-wide restarts, node reboot, deleting all pods, disabling the service "
    "mesh, deleting CRDs, or touching kube-system.\n"
)

NO_COT = (
    "Do not reveal step-by-step private reasoning. Provide only concise findings, "
    "hypotheses, and short rationale. Respond by calling the provided tool with "
    "compact JSON only."
)

PROBE_POLICY = (
    "Probes must be READ-ONLY (kubectl get/describe/logs/top/rollout status, or "
    "kubectl exec ... -- curl/wget/nslookup/dig/cat /etc/resolv.conf). They must "
    "never mutate the system (no apply/patch/scale/delete/rollout restart). "
    "Active Chaos Mesh experiments are observable with read-only commands such as "
    "`kubectl get stresschaos,networkchaos,podchaos,iochaos -n <ns> -o wide`."
)


def _oracle_block(oracle_hint: Optional[Dict[str, str]]) -> str:
    """Render the optional ablation-only oracle context, or empty string.

    Only the coordinator's ``use_oracle_rca=True`` path passes ``oracle_hint``.
    By default this returns "" so hidden labels never reach the model.
    """
    if not oracle_hint:
        return ""
    rc = oracle_hint.get("root_cause", "")
    fc = oracle_hint.get("failure_category", "")
    return (
        "\n[DEBUG ORACLE CONTEXT — ABLATION ONLY, normally hidden]\n"
        f"oracle_root_cause_service={rc}\noracle_failure_category={fc}\n"
    )


def _json(obj: Any, limit: int = 6000) -> str:
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        s = str(obj)
    return s if len(s) <= limit else s[:limit] + " ...<truncated>"


def _examples_block(examples: Optional[List[Dict[str, Any]]]) -> str:
    if not examples:
        return ""
    lines = [
        "\nHISTORICAL CLOUD-OPSBENCH EXAMPLES (reference patterns only — these are "
        "PAST incidents, NOT the current case; do not copy their labels):"
    ]
    for i, ex in enumerate(examples, 1):
        lines.append(
            f"  [{i}] category={ex.get('fault_category','?')} "
            f"symptom={_json(ex.get('symptom_summary',''),200)} "
            f"useful_tools={_json(ex.get('useful_tools',[]),200)} "
            f"pattern={_json(ex.get('evidence_pattern',''),200)}"
        )
    return "\n".join(lines)


# ===========================================================================
# Tool (function-calling) schemas. chat_api(prompts, tools) returns the call;
# we read tool_calls[0]["function"]["arguments"] as JSON.
# ===========================================================================

SELECT_PROBES_TOOL = {
    "type": "function",
    "function": {
        "name": "select_probes",
        "description": "Choose the next read-only diagnostic probe command(s) to run.",
        "parameters": {
            "type": "object",
            "properties": {
                "cmds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Read-only shell/kubectl commands to execute next.",
                },
                "intent": {"type": "string", "description": "What information these probes should yield."},
                "target_modality": {"type": "string", "enum": list(MODALITIES)},
            },
            "required": ["cmds"],
        },
    },
}

EMIT_RCA_PLAN_TOOL = {
    "type": "function",
    "function": {
        "name": "emit_rca_plan",
        "description": "Emit a structured RCA diagnostic plan as compact JSON.",
        "parameters": {
            "type": "object",
            "properties": {
                "incident_summary": {"type": "string"},
                "candidate_hypotheses": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "service": {"type": "string"},
                            "failure_category": {"type": "string"},
                            "mechanism": {"type": "string"},
                            "confidence": {"type": "number"},
                            "missing_evidence": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
                "diagnostic_steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "target_modality": {"type": "string", "enum": list(MODALITIES)},
                            "command_intent": {"type": "string"},
                            "expected_information_gain": {"type": "string"},
                            "stop_condition": {"type": "string"},
                        },
                    },
                },
                "stop_condition": {"type": "string"},
            },
            "required": ["incident_summary", "candidate_hypotheses", "diagnostic_steps"],
        },
    },
}

EMIT_EVIDENCE_TOOL = {
    "type": "function",
    "function": {
        "name": "emit_evidence",
        "description": "Summarize probe output into structured evidence items.",
        "parameters": {
            "type": "object",
            "properties": {
                "evidence": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "source": {"type": "string"},
                            "command": {"type": "string"},
                            "service": {"type": "string"},
                            "modality": {"type": "string", "enum": list(MODALITIES)},
                            "finding": {"type": "string"},
                            "raw_excerpt": {"type": "string"},
                            "confidence": {"type": "number"},
                        },
                        "required": ["finding"],
                    },
                },
                "suggested_next_steps": {"type": "array", "items": {"type": "string"}},
                "suspicion_notes": {"type": "string"},
            },
            "required": ["evidence"],
        },
    },
}

EMIT_HYPOTHESES_TOOL = {
    "type": "function",
    "function": {
        "name": "emit_hypotheses",
        "description": "Emit the updated ranked hypotheses and a stop/continue decision.",
        "parameters": {
            "type": "object",
            "properties": {
                "hypotheses": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "service": {"type": "string"},
                            "failure_category": {"type": "string"},
                            "mechanism": {"type": "string"},
                            "confidence": {"type": "number"},
                            "supporting_evidence": {"type": "array", "items": {"type": "string"}},
                            "contradicting_evidence": {"type": "array", "items": {"type": "string"}},
                            "missing_evidence": {"type": "array", "items": {"type": "string"}},
                            "repairability": {
                                "type": "string",
                                "enum": ["unknown", "repairable", "unsafe", "needs_more_evidence"],
                            },
                        },
                        "required": ["service", "confidence"],
                    },
                },
                "ready_to_repair": {"type": "boolean"},
                "reason": {"type": "string"},
            },
            "required": ["hypotheses", "ready_to_repair"],
        },
    },
}

EMIT_REPAIR_PLAN_TOOL = {
    "type": "function",
    "function": {
        "name": "emit_repair_plan",
        "description": "Emit a typed, low-risk repair plan as compact JSON.",
        "parameters": {
            "type": "object",
            "properties": {
                "actions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action_type": {"type": "string", "enum": list(ACTION_TYPES)},
                            "namespace": {"type": "string"},
                            "target_kind": {"type": "string"},
                            "target_name": {"type": "string"},
                            "patch": {"type": "object"},
                            "command": {"type": "string"},
                            "rationale": {"type": "string"},
                            "expected_effect": {"type": "string"},
                            "rollback_hint": {"type": "string"},
                            "evidence_refs": {"type": "array", "items": {"type": "string"}},
                            "preconditions": {"type": "array", "items": {"type": "string"}},
                            "risk": {"type": "string", "enum": ["low", "medium", "high"]},
                        },
                        "required": ["action_type", "target_name", "rationale", "evidence_refs", "preconditions"],
                    },
                },
                "verification_plan": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["actions"],
        },
    },
}

EMIT_PLAYBOOK_TOOL = {
    "type": "function",
    "function": {
        "name": "emit_playbook",
        "description": "Emit the final executable Ansible playbook YAML.",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Complete Ansible playbook runnable via "
                                   "`ansible-playbook -i inventory.ini remediation.yml`.",
                }
            },
            "required": ["code"],
        },
    },
}


# ===========================================================================
# Message builders. Each returns a list[{role, content}] for chat_api.
# ===========================================================================

def build_rca_planner_messages(*, runtime_envs: str, namespace: str, env: str,
                               incident_context: str,
                               examples: Optional[List[Dict[str, Any]]] = None,
                               oracle_hint: Optional[Dict[str, str]] = None) -> List[Dict[str, str]]:
    system = (
        "You are a senior SRE performing root cause analysis on a Kubernetes "
        "microservice system. Produce a Plan2RCA-style diagnostic plan with "
        "MULTIPLE competing hypotheses across these levels: service, resource, "
        "network, config, dependency, application. Each diagnostic step must "
        "specify a target modality, a command intent, the expected information "
        "gain, and a stop condition.\n" + NO_COT + "\n" + PROBE_POLICY
    )
    user = (
        f"ENVIRONMENT: {env} (namespace={namespace})\n"
        f"RUNTIME: {runtime_envs}\n"
        f"INCIDENT CONTEXT (observed, label-free):\n{incident_context}\n"
        f"{_examples_block(examples)}"
        f"{_oracle_block(oracle_hint)}\n"
        "Call emit_rca_plan with your plan."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def build_probe_selector_messages(*, namespace: str, hypotheses_json: Any,
                                  diagnostic_step: Any, recent_evidence_json: Any,
                                  oracle_hint: Optional[Dict[str, str]] = None) -> List[Dict[str, str]]:
    system = (
        "You are selecting the next READ-ONLY diagnostic probe(s) for an ongoing "
        "RCA. Choose commands that best discriminate between the current "
        "hypotheses and fill their missing_evidence.\n" + PROBE_POLICY + "\n" + NO_COT
    )
    user = (
        f"NAMESPACE: {namespace}\n"
        f"CURRENT HYPOTHESES: {_json(hypotheses_json, 2500)}\n"
        f"CURRENT DIAGNOSTIC STEP: {_json(diagnostic_step, 1200)}\n"
        f"RECENT EVIDENCE: {_json(recent_evidence_json, 2500)}"
        f"{_oracle_block(oracle_hint)}\n"
        "Call select_probes with 1-4 read-only commands."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def build_evidence_messages(*, agent_role: str, namespace: str, hypotheses_json: Any,
                            diagnostic_step: Any, probe_output: str,
                            oracle_hint: Optional[Dict[str, str]] = None) -> List[Dict[str, str]]:
    system = (
        f"You are the {agent_role} evidence agent in a multi-agent RCA workflow. "
        "Convert raw probe output into structured, factual evidence items. Do not "
        "speculate beyond what the output supports. Tag each item with the correct "
        "modality and the service it concerns.\n" + NO_COT
    )
    user = (
        f"NAMESPACE: {namespace}\n"
        f"FOCUS HYPOTHESES: {_json(hypotheses_json, 2000)}\n"
        f"DIAGNOSTIC STEP: {_json(diagnostic_step, 800)}\n"
        f"PROBE OUTPUT:\n{probe_output[:6000]}"
        f"{_oracle_block(oracle_hint)}\n"
        "Call emit_evidence."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def build_hypothesis_messages(*, plan_json: Any, evidence_history_json: Any,
                              current_hypotheses_json: Any,
                              oracle_hint: Optional[Dict[str, str]] = None) -> List[Dict[str, str]]:
    system = (
        "You maintain the ranked hypothesis set for an RCA. Raise a hypothesis' "
        "confidence when evidence directly identifies a target config/resource/"
        "service; lower it when evidence contradicts. Decide ready_to_repair=true "
        "ONLY when the top hypothesis has confidence >= 0.70, at least two "
        "independent supporting evidence items, and no safety-critical "
        "missing_evidence.\n" + NO_COT
    )
    user = (
        f"RCA PLAN: {_json(plan_json, 2000)}\n"
        f"CURRENT HYPOTHESES: {_json(current_hypotheses_json, 2500)}\n"
        f"EVIDENCE HISTORY: {_json(evidence_history_json, 6000)}"
        f"{_oracle_block(oracle_hint)}\n"
        "Call emit_hypotheses."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def build_repair_planner_messages(*, runtime_envs: str, namespace: str, env: str,
                                  rca_report_json: Any, evidence_history_json: Any,
                                  oracle_hint: Optional[Dict[str, str]] = None) -> List[Dict[str, str]]:
    system = (
        "You are an SRE generating a TYPED repair plan from an RCA report. Use only "
        f"these action_type values: {', '.join(ACTION_TYPES)}.\n"
        "Guidance:\n"
        "- For injected resource/network/pod/io chaos that you observed as an active "
        "Chaos Mesh experiment, remove that specific injected fault "
        "(RemoveInjectedStress / RecoverNetworkFault / RecoverPodFailure) by deleting "
        "that single named chaos resource.\n"
        "- For a config error, emit an exact patch ONLY for the field your evidence "
        "shows is wrong (e.g. restore under-provisioned resource limits).\n"
        "- If evidence is insufficient, return a single WaitAndVerify action instead "
        "of inventing a risky patch.\n" + SAFETY_POLICY + NO_COT
    )
    user = (
        f"ENVIRONMENT: {env} (namespace={namespace})\n"
        f"RUNTIME: {runtime_envs}\n"
        f"RCA REPORT: {_json(rca_report_json, 3000)}\n"
        f"EVIDENCE HISTORY: {_json(evidence_history_json, 4000)}"
        f"{_oracle_block(oracle_hint)}\n"
        "Call emit_repair_plan with minimal, reversible actions scoped to "
        f"namespace {namespace}."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def build_playbook_messages(*, namespace: str, inventory_content: str,
                            repair_plan_json: Any) -> List[Dict[str, str]]:
    system = (
        "You convert a typed repair plan into a single executable Ansible playbook "
        "(YAML only). It must run via `ansible-playbook -i inventory.ini "
        "remediation.yml`. PREFER `ansible.builtin.shell: kubectl ...` tasks (most "
        "portable; the kubernetes.core collection may be absent). Include "
        "rollout/status verification tasks when relevant and a comment referencing "
        "the evidence for each task. Keep it minimal. Emit NO destructive actions.\n"
        + SAFETY_POLICY
    )
    user = (
        f"NAMESPACE: {namespace}\n"
        f"inventory.ini:\n{inventory_content}\n"
        f"REPAIR PLAN: {_json(repair_plan_json, 4000)}\n"
        "Call emit_playbook with the full YAML in `code`."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def build_reflection_messages(*, namespace: str, repair_plan_json: Any,
                              execution_output: str, verification_json: Any,
                              oracle_hint: Optional[Dict[str, str]] = None) -> List[Dict[str, str]]:
    system = (
        "A remediation attempt did not resolve the incident. Briefly diagnose why "
        "from the execution output and verification feedback, then emit a REVISED "
        "typed repair plan. Keep actions minimal, reversible, and safe.\n"
        + SAFETY_POLICY + NO_COT
    )
    user = (
        f"NAMESPACE: {namespace}\n"
        f"PREVIOUS REPAIR PLAN: {_json(repair_plan_json, 2500)}\n"
        f"EXECUTION OUTPUT:\n{execution_output[:3000]}\n"
        f"VERIFICATION FEEDBACK: {_json(verification_json, 1500)}"
        f"{_oracle_block(oracle_hint)}\n"
        "Call emit_repair_plan with a safer/more effective plan."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
