"""PlanRCAThinkRemed coordinator -- the unified RCA + auto-remediation workflow.

Pipeline per episode::

    incident context (label-free)
      -> Cloud-OpsBench few-shot retrieval (optional)
      -> Plan2RCA-style diagnostic plan
      -> multi-agent evidence collection loop (safe, read-only probes)
      -> evidence-grounded hypothesis update + stop decision
      -> RCA report
      -> typed repair plan -> Ansible playbook
      -> safety check -> execute (MicroRemed execution agent)
      -> oracle verification -> reflect + retry on failure

Fairness: ``root_cause`` and ``failure_category`` are MicroRemed oracle labels.
They are NEVER placed in any LLM prompt unless ``use_oracle_rca=True`` (ablation
only). They are used only by :mod:`verification_bridge` for status verification.

This module returns ``(conversation_messages, attempts)`` exactly like
ThinkRemed/SoloGen, and additionally writes a structured debug trace.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from . import prompts
from . import evidence_agents
from . import hypothesis_agent
from . import rca_planner_agent
from . import repair_planner_agent
from . import playbook_generator_agent
from . import safety_guard
from . import verification_bridge
from . import cloudops_retriever
from .logging_utils import ConversationLog, save_trace, estimate_token_count
from .schemas import Hypothesis, RCAReport, to_dict
from .cluster_client import ClusterClientContext,DEFAULT_CLUSTER_CONTEXT

DEFAULT_CLOUDOPS_INDEX = "data/cloudops_rca_index.jsonl"


def _load_chat_api():
    """Return ``models.llm.chat_api`` or ``None`` (deterministic-only fallback)."""
    try:
        from models.llm import chat_api
        return chat_api
    except Exception as exc:
        print(f"[PlanRCAThinkRemed] INFO: models.llm.chat_api unavailable ({exc}); "
              f"running in deterministic (no-LLM) mode.")
        return None


def _read_inventory() -> str:
    try:
        with open("inventory.ini", "r", encoding="utf-8") as fh:
            return fh.read()
    except Exception:
        return "[k3s_control_plane]\n"


def _incident_context(runtime_envs: str, namespace: str, env: str) -> str:
    # Deliberately label-free: the method only knows an alert fired somewhere in
    # this namespace. It must localize the root cause itself.
    return (
        f"An alert indicates degraded health for one or more services in "
        f"namespace '{namespace}' (env '{env}'). Runtime: {runtime_envs} "
        f"Identify the affected service and the underlying fault from live, "
        f"read-only observation."
    )


def _summarize_hypotheses(hyps: List[Hypothesis], n: int = 3) -> str:
    parts = []
    for h in hyps[:n]:
        parts.append(f"{h.service or '?'}/{h.failure_category or '?'}={h.confidence:.2f}")
    return ", ".join(parts) if parts else "(none)"


def remediate_failure(
    runtime_envs: str,
    namespace: str,
    root_cause: str,
    failure_category: str,
    *,
    model: Optional[str] = None,
    use_oracle_rca: bool = False,
    cloudopsbench_root: Optional[str] = None,
    enable_cloudops_rag: bool = False,
    rca_fewshot_k: int = 3,
    max_rca_steps: int = 6,
    max_remediation_trials: int = 2,
    wait_reme_time: int = 10,
    save_debug_dir: Optional[str] = None,
    env: Optional[str] = None,
    save_path: Optional[str] = None,
    cloudops_index_path: Optional[str] = None,
    business_oracle: Any = None,
    cluster_client_context: ClusterClientContext = DEFAULT_CLUSTER_CONTEXT,
    **_ignored: Any,
) -> Tuple[List[Dict[str, str]], int]:
    env = (env or namespace or "").strip()
    chat_api = _load_chat_api()
    use_llm = chat_api is not None

    # Oracle hint is ONLY constructed when explicitly enabled (ablation/debug).
    # By default it stays None so hidden labels never reach a prompt.
    oracle_hint: Optional[Dict[str, str]] = None
    if use_oracle_rca:
        oracle_hint = {"root_cause": root_cause, "failure_category": failure_category}

    convo = ConversationLog()
    convo.system(
        "PlanRCAThinkRemed: unified RCA + auto-remediation SRE workflow. "
        f"namespace={namespace} env={env} model={model or 'config'} "
        f"oracle_hidden={not use_oracle_rca} llm={'on' if use_llm else 'off(deterministic)'}."
    )

    incident_context = _incident_context(runtime_envs, namespace, env)
    convo.user(f"Incident: {incident_context}")

    # --- Cloud-OpsBench RAG (historical examples only; never current-case labels)
    cloudops_examples: List[Dict[str, Any]] = []
    if enable_cloudops_rag:
        try:
            cloudops_examples = cloudops_retriever.retrieve_examples(
                query=incident_context, env=env, k=rca_fewshot_k,
                cloudopsbench_root=cloudopsbench_root,
                index_path=cloudops_index_path or DEFAULT_CLOUDOPS_INDEX,
            )
        except Exception as exc:
            print(f"[PlanRCAThinkRemed] WARN: cloudops retrieval failed: {exc}")
            cloudops_examples = []
        convo.assistant(f"Retrieved {len(cloudops_examples)} Cloud-OpsBench reference example(s).")

    # --- RCA plan
    plan = rca_planner_agent.build_rca_plan(
        runtime_envs=runtime_envs, namespace=namespace, env=env,
        incident_context=incident_context, examples=cloudops_examples,
        oracle_hint=oracle_hint, chat_api=chat_api,
    )
    convo.assistant(
        f"RCA plan: {plan.incident_summary} | {len(plan.candidate_hypotheses)} hypotheses, "
        f"{len(plan.diagnostic_steps)} diagnostic steps."
    )

    # --- Multi-agent evidence collection loop
    evidence_history: List[Any] = []
    hypotheses: List[Hypothesis] = list(plan.candidate_hypotheses)
    hypothesis_history: List[List[Dict[str, Any]]] = []
    evidence_log: List[Dict[str, Any]] = []
    stop_reason = "max_rca_steps=0 (RCA disabled)" if max_rca_steps <= 0 else ""

    # Guaranteed deterministic sweep (independent of the LLM plan AND immune to
    # LLM outages): ALWAYS probe for active chaos experiments and for
    # under-provisioned deployments. These are the two highest-signal detectors
    # for this benchmark's fault model; relying on the LLM to schedule a
    # config/event step is fragile (a degraded plan or a 502 can skip it).
    swept_stop = False
    if max_rca_steps > 0:
        for sweep_role in ("resource", "config"):
            agent = evidence_agents.AGENT_REGISTRY[sweep_role]()
            try:
                res = agent.collect(
                    namespace=namespace, hypotheses=[h.to_dict() for h in hypotheses],
                    diagnostic_step={"target_modality": sweep_role,
                                     "command_intent": "deterministic baseline sweep"},
                    recent_evidence=[], focus_service=None, use_llm=False,
                    oracle_hint=oracle_hint, chat_api=chat_api,
                )
            except Exception as exc:  # pragma: no cover - defensive
                print(f"[PlanRCAThinkRemed] sweep {sweep_role} failed: {exc}")
                continue
            sev = res.get("evidence", [])
            evidence_history.extend(sev)
            evidence_log.append({"step": f"sweep:{sweep_role}", "agent": res.get("agent"),
                                 "commands": res.get("commands"),
                                 "evidence": [to_dict(e) for e in sev], "suspicion_notes": ""})
            convo.assistant(f"[sweep:{sweep_role}] collected {len(sev)} evidence item(s).")
        hypotheses = hypothesis_agent.update_hypotheses(
            plan, evidence_history, current_hypotheses=hypotheses, use_llm=use_llm,
            oracle_hint=oracle_hint, chat_api=chat_api,
        )
        hypothesis_history.append([h.to_dict() for h in hypotheses[:5]])
        convo.assistant(f"[sweep] top hypotheses: {_summarize_hypotheses(hypotheses)}")
        swept_stop, stop_reason = hypothesis_agent.should_stop_and_repair(hypotheses, evidence_history)
        if swept_stop:
            convo.assistant(f"[sweep] sufficient evidence -> {stop_reason}")

    steps = plan.diagnostic_steps or []
    for i in range(max(0, max_rca_steps)):
        if swept_stop:
            break
        step = steps[i] if i < len(steps) else {"target_modality": "resource",
                                                "command_intent": "re-check top suspect"}
        focus_service = hypotheses[0].service if hypotheses and hypotheses[0].service else None
        agent = evidence_agents.agent_for_modality(step.get("target_modality", "resource"))

        result = agent.collect(
            namespace=namespace, hypotheses=[h.to_dict() for h in hypotheses],
            diagnostic_step=step, recent_evidence=[to_dict(e) for e in evidence_history[-5:]],
            focus_service=focus_service, use_llm=use_llm, oracle_hint=oracle_hint,
            chat_api=chat_api,
        )
        step_evidence = result.get("evidence", [])
        evidence_history.extend(step_evidence)
        evidence_log.append({
            "step": i, "agent": result.get("agent"), "commands": result.get("commands"),
            "evidence": [to_dict(e) for e in step_evidence],
            "suspicion_notes": result.get("suspicion_notes", ""),
        })
        convo.assistant(
            f"[RCA step {i}] {result.get('agent')} agent collected {len(step_evidence)} "
            f"evidence item(s)."
        )

        hypotheses = hypothesis_agent.update_hypotheses(
            plan, evidence_history, current_hypotheses=hypotheses, use_llm=use_llm,
            oracle_hint=oracle_hint, chat_api=chat_api,
        )
        hypothesis_history.append([h.to_dict() for h in hypotheses[:5]])
        convo.assistant(f"[RCA step {i}] top hypotheses: {_summarize_hypotheses(hypotheses)}")

        stop, stop_reason = hypothesis_agent.should_stop_and_repair(hypotheses, evidence_history)
        if stop:
            convo.assistant(f"[RCA] sufficient evidence -> {stop_reason}")
            break

    # Ablation/upper-bound: optionally seed a decisive oracle-backed hypothesis.
    exhausted = bool(max_rca_steps) and not hypothesis_agent.should_stop_and_repair(
        hypotheses, evidence_history)[0]
    if use_oracle_rca:
        oracle_hyp = Hypothesis(
            service=root_cause, failure_category=failure_category,
            mechanism="oracle-provided (ablation/upper-bound only)", confidence=0.99,
            supporting_evidence=["oracle label (ablation)"], repairability="repairable",
        )
        hypotheses = [oracle_hyp] + [h for h in hypotheses
                                     if (h.service, h.failure_category) !=
                                     (root_cause, failure_category)]
        exhausted = False

    rca_report: RCAReport = hypothesis_agent.build_rca_report(
        hypotheses, evidence_history, exhausted=exhausted
    )
    convo.assistant(
        f"RCA report: root_cause_service='{rca_report.root_cause_service}' "
        f"category='{rca_report.failure_category}' confidence={rca_report.confidence:.2f}. "
        f"{rca_report.uncertainty}"
    )

    # --- Repair loop (plan -> playbook -> safety -> execute -> verify -> reflect)
    inventory_content = _read_inventory()
    repair_plan = repair_planner_agent.build_repair_plan(
        rca_report=rca_report, evidence_history=evidence_history,
        runtime_envs=runtime_envs, namespace=namespace, env=env,
        use_llm=use_llm, oracle_hint=oracle_hint, chat_api=chat_api,
    )

    trace: Dict[str, Any] = {
        "method": "PlanRCAThinkRemed",
        "oracle_hidden": not use_oracle_rca,
        "namespace": namespace,
        "env": env,
        "model": model,
        "llm_enabled": use_llm,
        "rca_plan": plan.to_dict(),
        "cloudops_examples": cloudops_examples,
        "evidence_history": evidence_log,
        "hypothesis_history": hypothesis_history,
        "rca_report": rca_report.to_dict(),
        "stop_reason": stop_reason,
        "attempts_detail": [],
    }

    attempts = 0
    success = False
    last_execution_output = ""
    last_verification: Dict[str, Any] = {}
    final_playbook = ""
    last_safety_check: Dict[str, Any] = {"ok": True, "reasons": []}

    max_trials = max(1, max_remediation_trials)
    for trial in range(max_trials):
        # Generate playbook for the current repair plan.
        playbook = playbook_generator_agent.generate_playbook(
            repair_plan, namespace=namespace, inventory_content=inventory_content,
            use_llm=use_llm, chat_api=chat_api,
            client_context=cluster_client_context,
        )
        final_playbook = playbook

        # Safety: typed plan + rendered YAML.
        plan_ok, plan_reasons = safety_guard.check_repair_plan(repair_plan, allowed_namespace=namespace)
        yaml_ok, yaml_reasons = safety_guard.check_playbook_yaml(
            playbook,allowed_namespace=namespace,
            expected_commands=safety_guard.expected_playbook_commands(repair_plan),
            client_context=cluster_client_context)
        safety_reasons = plan_reasons + yaml_reasons
        safety_ok = plan_ok and yaml_ok
        last_safety_check = {"ok": safety_ok, "reasons": safety_reasons}

        attempt_record: Dict[str, Any] = {
            "trial": trial,
            "repair_plan": repair_plan.to_dict(),
            "playbook": playbook,
            "safety_check": {"ok": safety_ok, "reasons": safety_reasons},
        }

        if not safety_ok:
            convo.assistant(f"[safety] repair REJECTED: {safety_reasons}. Requesting a safer plan.")
            attempt_record["execution_output"] = "(skipped: safety rejection)"
            attempt_record["verification"] = {}
            trace["attempts_detail"].append(attempt_record)
            attempts += 1
            # Reflect toward a safer plan and retry within budget.
            repair_plan = _reflect(
                namespace=namespace, repair_plan=repair_plan,
                execution_output="SAFETY REJECTION: " + "; ".join(safety_reasons),
                verification={"success": False, "failure_reason": "unsafe plan"},
                rca_report=rca_report, evidence_history=evidence_history,
                runtime_envs=runtime_envs, env=env, use_llm=use_llm,
                oracle_hint=oracle_hint, chat_api=chat_api,
            )
            continue

        preconditions=safety_guard.run_preconditions(
            repair_plan,client_context=cluster_client_context)
        attempt_record['preconditions']=preconditions
        if not preconditions['passed']:
            convo.assistant(f"[precondition] repair BLOCKED: {preconditions['status']}")
            attempt_record["execution_output"]="(skipped: preconditions not satisfied)"
            attempt_record["verification"]={}
            trace["attempts_detail"].append(attempt_record);attempts+=1
            break

        convo.assistant(f"[execute] running playbook (trial {trial}).")
        execution = _execute(playbook)
        execution_completed_at = time.time()
        last_execution_output = execution["output"]
        attempt_record["execution"] = execution
        attempt_record["execution_output"] = last_execution_output[:4000]
        convo.assistant(f"[execute] output: {last_execution_output[:600]}")

        if not execution["success"]:
            rollback = safety_guard.execute_rollback(
                preconditions,client_context=cluster_client_context)
            attempt_record["rollback"] = rollback
            last_verification = {
                "status": "UNKNOWN",
                "success": False,
                "failure_reason": "repair execution did not report success",
                "business_oracle": {
                    "status": "UNKNOWN", "success": False,
                    "reason": "business verification skipped after execution failure",
                },
            }
            attempt_record["verification"] = last_verification
            trace["attempts_detail"].append(attempt_record)
            attempts += 1
            break

        last_verification = verification_bridge.run_verification(
            namespace=namespace, root_cause=root_cause, failure_category=failure_category,
            verification_plan=repair_plan.verification_plan, wait_reme_time=wait_reme_time,
        )
        if callable(business_oracle):
            try:
                business_result=business_oracle(
                    repair_completed_at=execution_completed_at,
                    client_context=cluster_client_context,
                )
            except Exception as exc:
                business_result={'status':'UNKNOWN','success':False,
                                 'reason':f'independent business oracle failed: {type(exc).__name__}'}
        else:
            business_result={'status':'UNKNOWN','success':False,
                             'reason':'independent business oracle not supplied'}
        last_verification['business_oracle']=business_result
        last_verification['legacy_oracle_status']=last_verification.get('oracle_status')
        last_verification['status']='PASS' if last_verification.get('oracle_status') is True and business_result.get('status')=='PASS' else ('UNKNOWN' if business_result.get('status')=='UNKNOWN' else 'FAIL')
        last_verification['success']=last_verification['status']=='PASS'
        attempt_record["verification"] = last_verification
        trace["attempts_detail"].append(attempt_record)
        attempts += 1

        convo.assistant(
            f"[verify] success={last_verification.get('success')} "
            f"reason={last_verification.get('failure_reason') or 'resolved'}"
        )

        if last_verification.get("success"):
            success = True
            break

        # Reflect with verification feedback and retry.
        if trial < max_trials - 1:
            repair_plan = _reflect(
                namespace=namespace, repair_plan=repair_plan,
                execution_output=last_execution_output, verification=last_verification,
                rca_report=rca_report, evidence_history=evidence_history,
                runtime_envs=runtime_envs, env=env, use_llm=use_llm,
                oracle_hint=oracle_hint, chat_api=chat_api,
            )

    # --- finalize trace + return
    trace["repair_plan"] = repair_plan.to_dict()
    trace["playbook"] = final_playbook
    trace["safety_check"] = last_safety_check
    trace["execution_output"] = last_execution_output[:4000]
    trace["verification"] = last_verification
    trace["attempts"] = attempts
    trace["success"] = success
    trace["token_estimate"] = estimate_token_count(convo.messages)

    written = save_trace(
        trace, save_debug_dir=save_debug_dir, save_path_fallback=save_path,
        namespace=namespace, env=env,
        episode_tag=f"{rca_report.root_cause_service or 'unknown'}_{rca_report.failure_category or 'na'}",
    )
    if written:
        convo.assistant(f"[trace] structured debug trace saved to {written}")

    return convo.messages, attempts


def _execute(playbook: str) -> Dict[str, Any]:
    """Run the playbook via MicroRemed's execution agent (lazy import)."""
    try:
        from methods.execution_agent import execute_playbook_and_get_response
    except Exception as exc:
        return {"success": False,
                "output": f"(execution agent unavailable: {exc})"}
    try:
        status, output = execute_playbook_and_get_response(playbook)
        return {"success": bool(status), "status": status,
                "output": f"status={status}\n{output}"}
    except Exception as exc:  # pragma: no cover - defensive
        return {"success": False, "output": f"(execution error: {exc})"}


def _reflect(*, namespace, repair_plan, execution_output, verification, rca_report,
            evidence_history, runtime_envs, env, use_llm, oracle_hint, chat_api):
    """Produce a revised repair plan after a failed/rejected attempt."""
    from .llm_json import call_tool_json
    from .schemas import RepairAction, RepairPlan

    if use_llm and chat_api is not None:
        messages = prompts.build_reflection_messages(
            namespace=namespace, repair_plan_json=repair_plan.to_dict(),
            execution_output=execution_output, verification_json=verification,
            oracle_hint=oracle_hint,
        )
        raw = call_tool_json(messages, prompts.EMIT_REPAIR_PLAN_TOOL, fallback=None,
                             chat_api=chat_api)
        if isinstance(raw, dict) and raw.get("actions"):
            actions = [RepairAction.from_dict(a) for a in raw["actions"]]
            for a in actions:
                if not a.namespace:
                    a.namespace = namespace
            vplan = [str(v) for v in (raw.get("verification_plan") or [])] or repair_plan.verification_plan
            return RepairPlan(rca_report=rca_report, actions=actions, verification_plan=vplan)

    # Deterministic re-plan (re-derive from the RCA report).
    return repair_planner_agent.build_repair_plan(
        rca_report=rca_report, evidence_history=evidence_history,
        runtime_envs=runtime_envs, namespace=namespace, env=env,
        use_llm=False, oracle_hint=oracle_hint, chat_api=chat_api,
    )
