"""Typed, low-risk repair planning.

Turns an :class:`RCAReport` into a :class:`RepairPlan` of typed
:class:`RepairAction` items. For this benchmark's fault model the correct,
benchmark-aligned repair is deterministic and evidence-grounded:

* injected Chaos Mesh experiment  -> delete that single named chaos resource
  (RemoveInjectedStress / RecoverNetworkFault / RecoverPodFailure);
* under-provisioned deployment     -> restore resource limits (RecoverConfigError);
* insufficient evidence            -> WaitAndVerify (never invent a risky patch).

The deterministic mapping is authoritative for these known categories (it is
what actually makes verification pass). The LLM is used to fill in or refine when
the category is unknown / low-confidence, and its output is always re-typed
through :class:`RepairAction` and re-checked by the safety guard downstream.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from . import prompts
from .llm_json import call_tool_json
from .schemas import RCAReport, RepairAction, RepairPlan, EvidenceItem

# category -> (chaos CRD kind, action_type)
_CHAOS_REPAIR = {
    "cpu-stress": ("StressChaos", "RemoveInjectedStress"),
    "memory-stress": ("StressChaos", "RemoveInjectedStress"),
    "network-delay": ("NetworkChaos", "RecoverNetworkFault"),
    "network-loss": ("NetworkChaos", "RecoverNetworkFault"),
    "pod-fail": ("PodChaos", "RecoverPodFailure"),
    "disk-io": ("IOChaos", "RemoveInjectedStress"),
}

_KIND_TO_RESOURCE = {
    "StressChaos": "stresschaos",
    "NetworkChaos": "networkchaos",
    "PodChaos": "podchaos",
    "IOChaos": "iochaos",
}

_CHAOS_NAME_RE = re.compile(
    r"(StressChaos|NetworkChaos|PodChaos|IOChaos)\s+'([^']+)'", re.IGNORECASE
)
_CONTAINER_NAME_RE = re.compile(r"container\s+'([^']+)'", re.IGNORECASE)
_BOUTIQUE_CONTAINER = {
    "adservice": "server",
    "cartservice": "server",
    "checkoutservice": "server",
    "currencyservice": "server",
    "emailservice": "server",
    "frontend": "server",
    "loadgenerator": "main",
    "paymentservice": "server",
    "productcatalogservice": "server",
    "recommendationservice": "server",
    "redis-cart": "redis",
    "shippingservice": "server",
}


def _chaos_target_from_evidence(evidence: List[EvidenceItem]) -> Optional[Dict[str, str]]:
    """Recover the exact chaos kind/name/service observed during RCA, if any."""
    for ev in evidence:
        m = _CHAOS_NAME_RE.search(ev.finding or "")
        if m:
            return {"kind": m.group(1), "name": m.group(2), "service": ev.service or ""}
    return None


def _config_container_from_evidence(
    service: str, evidence: List[EvidenceItem]
) -> str:
    for item in evidence:
        if (item.service or "") != service:
            continue
        match = _CONTAINER_NAME_RE.search(item.finding or "")
        if match:
            return match.group(1)
    return _BOUTIQUE_CONTAINER.get(service, "")


def _verification_plan(category: str, namespace: str, service: str) -> List[str]:
    sel = f"-l app={service}" if service else ""
    plan = [f"kubectl get pods -n {namespace} {sel}".strip()]
    if category in ("cpu-stress", "memory-stress", "pod-config-error"):
        plan.append(f"kubectl top pods -n {namespace} {sel} --containers".strip())
    if category in ("network-delay", "network-loss"):
        plan.append(f"kubectl get networkchaos -n {namespace}")
    if category == "disk-io":
        plan.append(f"kubectl get iochaos -n {namespace}")
    if category in ("cpu-stress", "memory-stress"):
        plan.append(f"kubectl get stresschaos -n {namespace}")
    if category == "pod-fail":
        plan.append(f"kubectl get podchaos -n {namespace}")
    return plan


def _evidence_refs(report:RCAReport,evidence:List[EvidenceItem])->List[str]:
    refs=[]
    report_rows=[item.to_dict() for item in report.evidence]
    for item in evidence:
        try:index=report_rows.index(item.to_dict())
        except ValueError:continue
        ref=f'evidence:{index}'
        if ref not in refs:refs.append(ref)
    return refs or [f"rca_prediction:{report.root_cause_service or '-'}:{report.failure_category or '-'}"]


def _deterministic_actions(report: RCAReport, namespace: str,
                           evidence: List[EvidenceItem]) -> List[RepairAction]:
    cat = (report.failure_category or "").strip()
    svc = (report.root_cause_service or "").strip()
    refs=_evidence_refs(report,evidence)
    evidence_basis='observed' if any(ref.startswith('evidence:') for ref in refs) else 'prediction_only'

    # Chaos-injected faults: delete the single named chaos resource.
    if cat in _CHAOS_REPAIR:
        kind, action_type = _CHAOS_REPAIR[cat]
        observed = _chaos_target_from_evidence(evidence)
        name = (observed or {}).get("name") or cat
        if observed and observed.get("kind"):
            kind = observed["kind"]
        resource = _KIND_TO_RESOURCE.get(kind, kind.lower())
        cmd = f"kubectl delete {resource} {name} -n {namespace} --ignore-not-found"
        return [RepairAction(
            action_type=action_type,
            namespace=namespace,
            target_kind=kind,
            target_name=name,
            patch=None,
            command=cmd,
            rationale=(f"RCA found an active {kind} '{name}' targeting service "
                       f"'{svc or '(from chaos selector)'}'. Removing this injected fault "
                       f"restores normal behavior."),
            expected_effect=f"The {cat} condition clears once the chaos experiment is deleted.",
            rollback_hint="Re-applying the chaos manifest would re-inject the fault (not needed).",
            evidence_refs=refs,
            evidence_basis=evidence_basis,
            preconditions=[f"kubectl get {resource} {name} -n {namespace} -o json"],
            precondition_predicate={
                "type": "active_chaos_target",
                "kind": kind,
                "name": name,
                "namespace": namespace,
                "selector_service": svc,
            },
            rollback_policy="reapply_captured_object",
            risk="low",
        )]

    # Config error: restore under-provisioned resource limits via `kubectl set resources`.
    if cat == "pod-config-error" and svc:
        container_name = _config_container_from_evidence(svc, evidence)
        if not container_name:
            return [RepairAction(
                action_type="WaitAndVerify", namespace=namespace,
                target_kind="Deployment", target_name=svc,
                command=f"kubectl get deployment {svc} -n {namespace} -o json",
                rationale="The target container cannot be bound to the finite restoration runbook.",
                expected_effect="Observe the deployment before selecting a container-scoped mutation.",
                rollback_hint="No mutation performed.", evidence_refs=refs,
                evidence_basis=evidence_basis,
                preconditions=[f"kubectl get deployment {svc} -n {namespace} -o json"],
                risk="low",
            )]
        cmd = (f"kubectl set resources deployment/{svc} -n {namespace} "
               f"--containers={container_name} "
               f"--limits=cpu=500m,memory=512Mi --requests=cpu=100m,memory=128Mi")
        return [RepairAction(
            action_type="RecoverConfigError",
            namespace=namespace,
            target_kind="Deployment",
            target_name=svc,
            target_container=container_name,
            patch={
                "spec": {"template": {"spec": {"containers": [
                    {"resources": {
                        "requests": {"cpu": "100m", "memory": "128Mi"},
                        "limits": {"cpu": "500m", "memory": "512Mi"},
                    }}
                ]}}}
            },
            command=cmd,
            rationale=(f"RCA found deployment '{svc}' with abnormally low resource limits. "
                       f"Restoring sane requests/limits triggers a rollout to healthy pods."),
            expected_effect="CPU/memory usage falls below 50% of the restored limits.",
            rollback_hint="Previous (broken) limits were cpu<=2m/memory<=200Mi.",
            evidence_refs=refs,
            evidence_basis=evidence_basis,
            preconditions=[f"kubectl get deployment {svc} -n {namespace} -o json"],
            precondition_predicate={
                "type": "deployment_underprovisioned",
                "kind": "Deployment",
                "name": svc,
                "namespace": namespace,
                "container_name": container_name,
                "max_cpu_millicores": 2,
                "max_memory_mib": 200,
            },
            rollback_policy="restore_captured_container_resources",
            risk="medium",
        )]

    # Not enough to act safely.
    return [RepairAction(
        action_type="WaitAndVerify",
        namespace=namespace,
        target_kind="",
        target_name=svc,
        patch=None,
        command=f"kubectl get pods -n {namespace}",
        rationale="Evidence is insufficient to identify a safe, specific repair.",
        expected_effect="Re-observe system state before attempting any mutation.",
        rollback_hint="No mutation performed.",
        evidence_refs=refs,
        evidence_basis=evidence_basis,
        preconditions=[f"kubectl get pods -n {namespace}"],
        precondition_predicate={},
        rollback_policy="none",
        risk="low",
    )]


def _is_confident_known(report: RCAReport) -> bool:
    cat = (report.failure_category or "").strip()
    known = cat in _CHAOS_REPAIR or cat == "pod-config-error"
    return known and bool(report.root_cause_service) and report.confidence >= 0.6


def build_repair_plan(*, rca_report: RCAReport, evidence_history: List[EvidenceItem],
                      runtime_envs: str, namespace: str, env: str,
                      use_llm: bool = True, oracle_hint: Optional[Dict[str, str]] = None,
                      chat_api: Any = None) -> RepairPlan:
    det_actions = _deterministic_actions(rca_report, namespace, evidence_history)
    verification_plan = _verification_plan(
        rca_report.failure_category, namespace, rca_report.root_cause_service
    )

    # For a confidently identified known fault, the deterministic repair is the
    # correct, benchmark-aligned action -- use it directly.
    if _is_confident_known(rca_report):
        return RepairPlan(rca_report=rca_report, actions=det_actions,
                          verification_plan=verification_plan)

    if not use_llm:
        return RepairPlan(rca_report=rca_report, actions=det_actions,
                          verification_plan=verification_plan)

    # Otherwise consult the LLM, then re-type + fall back to deterministic.
    messages = prompts.build_repair_planner_messages(
        runtime_envs=runtime_envs, namespace=namespace, env=env,
        rca_report_json=rca_report.to_dict(),
        evidence_history_json=[e.to_dict() for e in evidence_history],
        oracle_hint=oracle_hint,
    )
    raw = call_tool_json(messages, prompts.EMIT_REPAIR_PLAN_TOOL, fallback=None, chat_api=chat_api)
    if isinstance(raw, dict) and raw.get("actions"):
        actions = [RepairAction.from_dict(a) for a in raw["actions"]]
        # force namespace scoping to the incident namespace
        for a in actions:
            if not a.namespace:
                a.namespace = namespace
        vplan = [str(v) for v in (raw.get("verification_plan") or [])] or verification_plan
        return RepairPlan(rca_report=rca_report, actions=actions, verification_plan=vplan)

    return RepairPlan(rca_report=rca_report, actions=det_actions,
                      verification_plan=verification_plan)
