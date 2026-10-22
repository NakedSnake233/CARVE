"""Convert a typed RepairPlan into an executable Ansible playbook (YAML).

The deterministic renderer is authoritative whenever every action carries a
concrete ``command`` (the chaos-delete / set-resources commands produced by the
repair planner), because those are exactly the operations that make MicroRemed's
verification pass. The LLM renderer is only a fallback for plans whose actions
lack commands. Either way the output is re-checked by the safety guard before
execution (in the coordinator).

The playbook targets the k3s control-plane host from ``inventory.ini`` and runs
``kubectl`` via ``ansible.builtin.shell`` with the k3s kubeconfig, which avoids a
hard dependency on the ``kubernetes.core`` Ansible collection being installed.
"""

from __future__ import annotations

from typing import Any, List, Optional

from . import prompts
from .llm_json import call_text
from .schemas import RepairPlan, RepairAction
from .cluster_client import ClusterClientContext, DEFAULT_CLUSTER_CONTEXT


def _yaml_comment(text: str) -> str:
    one_line = " ".join(str(text).split())
    return one_line[:200]


def _task_for_action(idx: int, action: RepairAction) -> List[str]:
    lines: List[str] = []
    title = f"{action.action_type} on {action.target_kind or 'target'}/{action.target_name or 'n/a'}"
    lines.append(f"    # evidence/rationale: {_yaml_comment(action.rationale)}")
    lines.append(f"    # expected effect: {_yaml_comment(action.expected_effect)}")
    lines.append(f"    - name: {title}")
    lines.append("      ansible.builtin.shell: |")
    cmd = action.command or f"kubectl get pods -n {action.namespace}"
    lines.append(f"        {cmd}")
    lines.append(f"      register: planrca_step_{idx}")
    lines.append("      ignore_errors: false")
    lines.append(f"    - name: report {title}")
    lines.append("      ansible.builtin.debug:")
    lines.append(f"        var: planrca_step_{idx}.stdout_lines")
    # For config recovery, wait for the rollout to complete.
    if action.action_type == "RecoverConfigError" and action.target_name:
        lines.append(f"    - name: wait for rollout of {action.target_name}")
        lines.append("      ansible.builtin.shell: |")
        lines.append(
            f"        kubectl rollout status deployment/{action.target_name} "
            f"-n {action.namespace} --timeout=120s"
        )
        lines.append("      ignore_errors: false")
    return lines


def render_playbook(
    plan: RepairPlan,
    *,
    client_context: ClusterClientContext = DEFAULT_CLUSTER_CONTEXT,
) -> str:
    """Deterministically render the repair plan to an Ansible playbook string."""
    report = plan.rca_report
    header = [
        "---",
        "# PlanRCAThinkRemed auto-generated remediation playbook",
        f"# RCA: service={report.root_cause_service or '?'} "
        f"category={report.failure_category or '?'} confidence={report.confidence:.2f}",
        f"# uncertainty: {_yaml_comment(report.uncertainty) or 'none'}",
        "- name: PlanRCAThinkRemed remediation",
        "  hosts: all",
        "  gather_facts: false",
        "  environment:",
        f"    KUBECONFIG: {client_context.kubeconfig}",
        "  tasks:",
    ]
    body: List[str] = []
    for idx, action in enumerate(plan.actions):
        body.extend(_task_for_action(idx, action))
    if not body:
        body = [
            "    - name: no-op verify",
            "      ansible.builtin.shell: |",
            "        kubectl get pods -A | head -n 1",
            "      ignore_errors: false",
        ]
    return "\n".join(header + body) + "\n"


def _all_actions_have_commands(plan: RepairPlan) -> bool:
    return bool(plan.actions) and all(bool(a.command) for a in plan.actions)


def generate_playbook(plan: RepairPlan, *, namespace: str, inventory_content: str,
                      use_llm: bool = True, chat_api: Any = None,
                      client_context: ClusterClientContext = DEFAULT_CLUSTER_CONTEXT) -> str:
    """Return executable playbook YAML for ``plan``.

    Deterministic when all actions have commands; otherwise consult the LLM and
    fall back to the deterministic skeleton.
    """
    if _all_actions_have_commands(plan):
        return render_playbook(plan,client_context=client_context)

    if use_llm:
        messages = prompts.build_playbook_messages(
            namespace=namespace, inventory_content=inventory_content,
            repair_plan_json=plan.to_dict(),
        )
        code = call_text(messages, prompts.EMIT_PLAYBOOK_TOOL, key="code", fallback="",
                         chat_api=chat_api)
        if code and code.strip():
            return code

    return render_playbook(plan,client_context=client_context)
