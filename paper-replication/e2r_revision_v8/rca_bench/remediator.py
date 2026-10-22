"""Decoupled remediation baselines — 3 recovery methods × base LLMs.

All methods receive ONLY the RCA prediction (``predicted_service`` + ``predicted_category``),
never the cluster state or oracle, preserving the decoupled contract. This isolates
the **recovery method** from RCA quality.

Supported methods:
1. **ReportDriven** (plan2remed's recovery) — deterministic/LLM-driven typed repair plan
2. **ThinkRemed** — iterative probe-think-fix loop (from MicroRemed/methods/ThinkRemed)
3. **PureLLM** — LLM emits Ansible playbook from RCA report (no typed plan, no probes)

Usage:
    from rca_bench.remediator import get_remediator
    rem = get_remediator("thinkremed", llm_model="gemini-3-flash")
    result = rem.remediate(namespace="...", predicted_service="...", predicted_category="...")
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .measurement import measure


class ReportDrivenRemediator:
    name = "report-driven"

    def __init__(self, use_llm: bool = False, chat_api: Any = None,
                 client_context: Any = None):
        # use_llm=False keeps it deterministic and reproducible; the LLM path is
        # available but still report-only (no cluster probing either way).
        self.use_llm = use_llm
        self.chat_api = chat_api
        if client_context is None:
            from methods.PlanRCAThinkRemed.cluster_client import DEFAULT_CLUSTER_CONTEXT
            client_context=DEFAULT_CLUSTER_CONTEXT
        self.client_context=client_context

    def build(self, *, namespace: str, env: str,
              predicted_service: Optional[str], predicted_category: Optional[str],
              runtime_envs: str = "This microservice system runs on k3s.") -> Dict[str, Any]:
        """Build the repair plan + playbook from the RCA prediction ONLY (no exec,
        no cluster probing). Separated from execution so it is offline-testable."""
        from methods.PlanRCAThinkRemed.schemas import RCAReport
        from methods.PlanRCAThinkRemed import (
            repair_planner_agent, playbook_generator_agent, safety_guard)

        # Report built PURELY from the RCA prediction (no evidence from cluster).
        report = RCAReport(
            root_cause_service=(predicted_service or ""),
            failure_category=(predicted_category or ""),
            mechanism="from RCA report", confidence=0.9, evidence=[], repair_hints=[],
        )
        # evidence_history=[] -> repair_planner falls back to name==category for
        # chaos (NO selector), and targets the predicted service for config-error.
        timings = {}
        with measure(timings, "planning"):
            plan = repair_planner_agent.build_repair_plan(
                rca_report=report, evidence_history=[], runtime_envs=runtime_envs,
                namespace=namespace, env=env, use_llm=self.use_llm, chat_api=self.chat_api)
        action = plan.actions[0].to_dict() if plan.actions else {}
        with measure(timings, "plan_validation"):
            plan_ok, plan_reasons = safety_guard.check_repair_plan(plan, allowed_namespace=namespace)

        try:
            with open("inventory.ini") as fh:
                inventory = fh.read()
        except Exception:
            inventory = "[k3s_control_plane]\nlocalhost ansible_connection=local\n"
        with measure(timings, "rendering"):
            playbook = playbook_generator_agent.generate_playbook(
                plan, namespace=namespace, inventory_content=inventory,
                use_llm=self.use_llm, chat_api=self.chat_api,
                client_context=self.client_context)
        with measure(timings, "playbook_validation"):
            yaml_ok, yaml_reasons = safety_guard.check_playbook_yaml(
                playbook,allowed_namespace=namespace,
                expected_commands=safety_guard.expected_playbook_commands(plan),
                client_context=self.client_context)

        return {
            "timings_seconds": timings,
            "planner_route": ("deterministic" if repair_planner_agent._is_confident_known(report)
                              or not self.use_llm else "llm_with_fallback"),
            "renderer_route": ("deterministic" if playbook_generator_agent._all_actions_have_commands(plan)
                               or not self.use_llm else "llm_with_fallback"),
            "predicted_service": predicted_service,
            "predicted_category": predicted_category,
            "action_type": action.get("action_type"),
            "command": action.get("command"),
            "safety_ok": bool(plan_ok and yaml_ok),
            "safety_reasons": plan_reasons + yaml_reasons,
            "repair_plan": plan.to_dict(),
            "playbook": playbook,
            "executed": False,
            "execution_output": "",
        }

    def remediate(self, *, namespace: str, env: str,
                  predicted_service: Optional[str], predicted_category: Optional[str],
                  runtime_envs: str = "This microservice system runs on k3s.") -> Dict[str, Any]:
        result = self.build(namespace=namespace, env=env, predicted_service=predicted_service,
                            predicted_category=predicted_category, runtime_envs=runtime_envs)
        if not result["safety_ok"]:
            result["execution_output"] = "(skipped: safety rejection)"
            return result
        from methods.PlanRCAThinkRemed import safety_guard
        from methods.PlanRCAThinkRemed.schemas import RepairPlan
        preconditions=safety_guard.run_preconditions(
            RepairPlan.from_dict(result['repair_plan']),client_context=self.client_context)
        result['precondition_diagnostics']=preconditions
        if not preconditions['passed']:
            result['execution_output']='(skipped: preconditions not satisfied)'
            return result
        try:
            from methods.execution_agent import execute_playbook_and_get_response
            with measure(result["timings_seconds"], "execution"):
                status, output = execute_playbook_and_get_response(result["playbook"])
            result["executed"] = bool(status)
            result["execution_output"] = str(output)[:2000]
            if not result["executed"]:
                with measure(result["timings_seconds"], "rollback"):
                    result["rollback"] = safety_guard.execute_rollback(
                        preconditions,client_context=self.client_context)
        except Exception as exc:  # pragma: no cover
            result["execution_output"] = f"(execution error: {exc})"
            with measure(result["timings_seconds"], "rollback"):
                result["rollback"] = safety_guard.execute_rollback(
                    preconditions,client_context=self.client_context)
        return result


class ThinkRemedRemediator:
    """ThinkRemed: iterative probe-think-fix loop (from MicroRemed/methods/ThinkRemed).

    Unlike ReportDriven (which builds a typed plan from the RCA report), ThinkRemed
    lets the LLM iteratively probe the cluster to gather system state, then emit a
    playbook. This is more powerful but also more token-expensive.

    DECOUPLED CONTRACT: Still receives only the RCA prediction; probes are limited
    to reading generic cluster state (no Chaos Mesh objects, no oracle leakage).
    """
    name = "thinkremed"

    def __init__(self, llm_model: Optional[str] = None, chat_api: Any = None):
        """
        Args:
            llm_model: Model name (e.g., "gemini-3-flash", "gpt-5.4-mini"). If None,
                uses the active model from llm_rca_config.json.
            chat_api: Custom chat API instance (for testing). If None, uses the
                standard LLM bridge with the specified model.
        """
        self.llm_model = llm_model
        self.chat_api = chat_api

    def remediate(self, *, namespace: str, env: str = "k3s",
                  predicted_service: Optional[str], predicted_category: Optional[str],
                  runtime_envs: str = "This microservice system runs on k3s.") -> Dict[str, Any]:
        """Run ThinkRemed's iterative remediation loop."""
        try:
            # Import ThinkRemed's coordinator
            import sys, os
            sys.path.insert(0, os.path.expanduser("~/MicroRemed"))
            from methods.ThinkRemed.coordinator import remediate_failure

            # Build minimal root_cause dict (ThinkRemed expects this format)
            root_cause = {
                "service": predicted_service or "unknown",
                "category": predicted_category or "unknown"
            }

            # TODO: if self.llm_model is set, temporarily override the active model
            # in ThinkRemed's LLM config (currently it uses the global chat_api)

            # Run ThinkRemed (returns playbook execution result)
            result = remediate_failure(
                runtime_envs=runtime_envs,
                namespace=namespace,
                root_cause=root_cause,
                failure_category=predicted_category or "unknown"
            )

            return {
                "method": self.name,
                "predicted_service": predicted_service,
                "predicted_category": predicted_category,
                "executed": True,
                "playbook": result.get("playbook", ""),
                "execution_output": result.get("output", str(result))[:2000],
                "thinkremed_metadata": result
            }
        except Exception as exc:
            import traceback
            return {
                "method": self.name,
                "predicted_service": predicted_service,
                "predicted_category": predicted_category,
                "executed": False,
                "error": str(exc),
                "traceback": traceback.format_exc()[:2000]
            }


class PureLLMRemediator:
    """Pure-LLM baseline: ask LLM to emit Ansible playbook from RCA report.

    No typed plan (unlike ReportDriven), no cluster probes (unlike ThinkRemed).
    Just: RCA report → LLM → raw Ansible YAML → execute.

    This tests whether the structured repair planner in ReportDriven adds value
    over a direct LLM → playbook path.
    """
    name = "pure-llm"

    def __init__(self, llm_model: Optional[str] = None, chat_api: Any = None):
        self.llm_model = llm_model
        self.chat_api = chat_api
        if chat_api is None and llm_model:
            # Load LLM config and create chat API
            import json, os
            cfg_path = os.path.join(os.path.dirname(__file__), "..", "llm_rca_config.json")
            cfg = json.load(open(cfg_path))
            model_name = llm_model or cfg.get("active")
            model_cfg = dict(cfg.get("request", {})); model_cfg.update(cfg["models"][model_name])
            # Create a simple chat wrapper (reuse existing LLM bridge if available)
            try:
                from methods.PlanRCAThinkRemed.llm_json import LLMJsonBridge
                self.chat_api = LLMJsonBridge(
                    model=model_cfg["model"],
                    base_url=model_cfg["base_url"],
                    api_key=model_cfg["api_key"],
                    temperature=model_cfg.get("temperature", 0.0)
                ).chat
            except ImportError:
                # Fallback: use the protocol declared by the selected model.
                import urllib.request
                from .llm_transport import build_request, parse_response, session_id
                def simple_chat(messages, **kw):
                    req, _ = build_request(model_cfg, messages,
                                           request_session=session_id("pure-remed"))
                    resp = json.loads(urllib.request.urlopen(req, timeout=120).read())
                    return parse_response(resp, model_cfg.get("api_style", "openai"))["response_text"]
                self.chat_api = simple_chat

    def remediate(self, *, namespace: str, env: str = "k3s",
                  predicted_service: Optional[str], predicted_category: Optional[str],
                  runtime_envs: str = "This microservice system runs on k3s.") -> Dict[str, Any]:
        """Ask LLM to emit playbook from RCA report."""
        prompt = f"""You are a microservice remediation expert. Given an RCA prediction, generate an Ansible playbook to fix the failure.

**RCA Prediction:**
- Root-cause service: {predicted_service or 'unknown'}
- Failure category: {predicted_category or 'unknown'}
- Namespace: {namespace}
- Environment: {runtime_envs}

**Task:** Generate a valid Ansible playbook (YAML) that remediates this failure. Output ONLY the playbook code, no explanation.

Common patterns:
- If category is a Chaos Mesh fault (networkchaos, podchaos, etc.), delete the chaos object: `kubectl delete <kind> {predicted_category} -n {namespace}`
- If category is "pod-config-error", patch the deployment: `kubectl set resources deployment/{predicted_service} -n {namespace} --limits=cpu=500m,memory=512Mi`
- Otherwise, restart the pod: `kubectl rollout restart deployment/{predicted_service} -n {namespace}`

Output the playbook now:"""

        try:
            playbook = self.chat_api([{"role": "user", "content": prompt}])

            # Clean up markdown code fences if present
            import re
            playbook = re.sub(r'^```ya?ml\n', '', playbook, flags=re.MULTILINE)
            playbook = re.sub(r'\n```$', '', playbook)

            # Execute it
            from methods.execution_agent import execute_playbook_and_get_response
            status, output = execute_playbook_and_get_response(playbook)

            return {
                "method": self.name,
                "predicted_service": predicted_service,
                "predicted_category": predicted_category,
                "playbook": playbook,
                "executed": bool(status),
                "execution_output": str(output)[:2000],
                "llm_model": self.llm_model or "default"
            }
        except Exception as exc:
            import traceback
            return {
                "method": self.name,
                "predicted_service": predicted_service,
                "predicted_category": predicted_category,
                "executed": False,
                "error": str(exc),
                "traceback": traceback.format_exc()[:2000]
            }


def get_remediator(method: str = "report-driven", llm_model: Optional[str] = None, **kwargs) -> Any:
    """Factory: return a remediator instance by method name.

    Args:
        method: One of {"report-driven", "thinkremed", "pure-llm"}
        llm_model: Model name for LLM-driven methods (e.g., "gemini-3-flash", "gpt-5.4-mini")
        **kwargs: Passed to the remediator constructor (e.g., use_llm=True for ReportDriven)

    Returns:
        A remediator instance with a ``.remediate(...)`` method.

    Example:
        >>> rem = get_remediator("thinkremed", llm_model="gemini-3-flash")
        >>> result = rem.remediate(namespace="online-boutique", predicted_service="frontend", predicted_category="networkchaos")
    """
    method = method.lower().replace("_", "-")
    if method == "report-driven":
        use_llm = kwargs.get("use_llm", False)
        return ReportDrivenRemediator(use_llm=use_llm)
    elif method == "thinkremed":
        return ThinkRemedRemediator(llm_model=llm_model)
    elif method == "pure-llm":
        return PureLLMRemediator(llm_model=llm_model)
    else:
        raise ValueError(f"Unknown remediation method: {method}. Choose from: report-driven, thinkremed, pure-llm")
