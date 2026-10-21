"""Bridge to MicroRemed's verification logic.

This is the ONLY module that is allowed to read the hidden oracle labels
(``root_cause`` / ``failure_category``). The benchmark's ground-truth health
check, ``chaos.check_status.check(namespace, f"app={root_cause}", failure_category)``,
is identical to what MicroRemed's own ThinkRemed/SoloGen verification uses, so
the comparison stays fair.

On top of the oracle status we run the RepairPlan's own (read-only) verification
probes to produce human-readable feedback for the reflection step.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .safe_probe_agent import run_probes


def verify_with_oracle(namespace: str, root_cause: str, failure_category: str) -> bool:
    """Return MicroRemed's ground-truth recovery status for this incident.

    NOTE: ``root_cause`` / ``failure_category`` are oracle labels. They are used
    here only to call MicroRemed's status checker for metrics/verification -- they
    are never forwarded into any LLM prompt.
    """
    try:
        from chaos import check_status  # MicroRemed runtime dependency
    except Exception as exc:
        print(f"[PlanRCAThinkRemed] WARN: chaos.check_status unavailable ({exc}); "
              f"cannot run oracle verification")
        return False
    try:
        return bool(check_status.check(
            namespace=namespace,
            label=f"app={root_cause}",
            type=failure_category,
        ))
    except Exception as exc:
        print(f"[PlanRCAThinkRemed] WARN: oracle verification raised {exc}")
        return False


def _run_probe_plan(verification_plan: Optional[List[str]], timeout: int = 15) -> List[str]:
    feedback: List[str] = []
    for result in run_probes(verification_plan or [], timeout=timeout):
        out = result.get("output", "")
        # keep feedback compact
        feedback.append(out.strip()[:500])
    return feedback


def _infer_failure_reason(probe_feedback: List[str], failure_category: str) -> str:
    joined = "\n".join(probe_feedback).lower()
    if any(k in joined for k in ("stresschaos", "networkchaos", "podchaos", "iochaos")):
        return "An injected chaos experiment still appears to be active."
    if "crashloopbackoff" in joined or "notready" in joined or "0/1" in joined:
        return "Target pods are still not Ready."
    return f"Recovery check for '{failure_category}' did not pass."


def run_verification(*, namespace: str, root_cause: str, failure_category: str,
                     verification_plan: Optional[List[str]] = None,
                     wait_reme_time: int = 10) -> Dict[str, Any]:
    """Wait, then return structured verification feedback.

    Returns::

        {"success": bool, "oracle_status": bool,
         "probe_feedback": list[str], "failure_reason": str}
    """
    if wait_reme_time and wait_reme_time > 0:
        try:
            time.sleep(wait_reme_time)
        except Exception:
            pass

    oracle_status = verify_with_oracle(namespace, root_cause, failure_category)
    probe_feedback = _run_probe_plan(verification_plan)
    failure_reason = "" if oracle_status else _infer_failure_reason(probe_feedback, failure_category)

    return {
        "success": bool(oracle_status),
        "oracle_status": bool(oracle_status),
        "probe_feedback": probe_feedback,
        "failure_reason": failure_reason,
    }
