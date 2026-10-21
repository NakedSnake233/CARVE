"""Full-lifecycle ops-evaluation harness.

Per incident: inject a fault → collect the unified multimodal context → run the
RCA algorithm and SCORE it (AC@k / MRR vs the hidden oracle) → optionally
remediate and score that → restore. Reuses MicroRemed's own
injection/health/restore primitives (lazy-imported) so the benchmark is extended,
not rewritten.

The oracle labels (target service, failure type) are used ONLY for scoring; they
are never placed in the context handed to the RCA algorithm.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .interface import BaseRCA, MultimodalContext
from .context_provider import LiveClusterContextProvider
from .metrics import IncidentEval, evaluate, format_report
from .remediator import ReportDrivenRemediator


@dataclass
class IncidentOutcome:
    incident_id: str
    failure_type: str
    target_service: str
    injected: bool
    rca_eval: Optional[IncidentEval] = None
    rca_result: Optional[Dict[str, Any]] = None
    remediated: Optional[bool] = None
    remediation: Optional[Dict[str, Any]] = None
    attempts: Optional[int] = None
    elapsed: float = 0.0
    note: str = ""


class RCAEvalHarness:
    def __init__(self, rca: BaseRCA, *, namespace: str, env: str, manifest_path: str,
                 provider: Optional[LiveClusterContextProvider] = None,
                 remediate: bool = False, remediator: Optional[ReportDrivenRemediator] = None,
                 model: Optional[str] = None, injection_timeout: int = 60,
                 wait_interval: int = 10, settle_seconds: int = 8):
        self.rca = rca
        self.namespace = namespace
        self.env = env
        self.manifest_path = manifest_path
        self.provider = provider or LiveClusterContextProvider()
        self.remediate = remediate
        self.remediator = remediator or ReportDrivenRemediator()
        self.model = model
        self.injection_timeout = injection_timeout
        self.wait_interval = wait_interval
        self.settle_seconds = settle_seconds

    # -- MicroRemed primitives (lazy) -----------------------------------------
    def _mr(self):
        from envs.env import get_random_service
        from chaos.injection import inject_failure, stop_injection
        from chaos import check_status
        from chaos.deployment import restore_by_original_manifest
        return get_random_service, inject_failure, stop_injection, check_status, restore_by_original_manifest

    def run_incident(self, failure_type: str, idx: int = 0) -> IncidentOutcome:
        get_random_service, inject_failure, stop_injection, check_status, restore = self._mr()
        target = get_random_service(self.env, failure_type)
        inc_id = f"{idx}-{failure_type}-{target}"
        t0 = time.time()
        out = IncidentOutcome(incident_id=inc_id, failure_type=failure_type,
                              target_service=target, injected=False)

        # health gate
        if not check_status.check(self.namespace, f"app={target}", "pod-fail"):
            out.note = "target unhealthy pre-injection; skipped"
            return out

        if not inject_failure(failure_type, target, self.namespace):
            out.note = "inject_failure returned False"
            return out

        # wait for the fault to take effect
        injected = False
        start = time.time()
        while time.time() - start < self.injection_timeout:
            if not check_status.check(self.namespace, f"app={target}", failure_type, 5):
                injected = True
                break
            time.sleep(self.wait_interval)
        out.injected = injected
        if not injected:
            stop_injection(failure_type, self.namespace)
            try:
                restore(self.namespace, target, self.manifest_path)
            except Exception:
                pass
            out.note = "injection did not register"
            out.elapsed = time.time() - t0
            return out

        # let telemetry reflect the fault, then collect the unified context
        time.sleep(self.settle_seconds)
        ctx: MultimodalContext = self.provider.collect(self.namespace, self.env)

        # RCA (no oracle) + score (oracle used only here)
        result = self.rca.diagnose(ctx).normalized()
        ranking = result.ranked_services()
        out.rca_result = result.to_dict()
        out.rca_eval = IncidentEval(
            incident_id=inc_id, failure_category=failure_type, true_service=target,
            predicted_ranking=ranking, predicted_category=result.predicted_category,
        )

        # Decoupled remediation: input is ONLY the RCA report (predicted service +
        # category). No oracle, no chaos probing, no selector. Verification below
        # uses the oracle target for SCORING only -> success is RCA-bounded.
        if self.remediate:
            top = result.top()
            rem = self.remediator.remediate(
                namespace=self.namespace, env=self.env,
                predicted_service=(top.service if top else None),
                predicted_category=result.predicted_category)
            out.remediation = {k: rem.get(k) for k in (
                "predicted_service", "predicted_category", "action_type",
                "command", "safety_ok", "executed")}
            time.sleep(self.settle_seconds)
            out.remediated = bool(check_status.check(self.namespace, f"app={target}", failure_type))

        # cleanup
        stop_injection(failure_type, self.namespace)
        try:
            restore(self.namespace, target, self.manifest_path)
        except Exception:
            pass
        time.sleep(3)
        out.elapsed = time.time() - t0
        return out

    def run(self, failure_list: Sequence[str]) -> Dict[str, Any]:
        outcomes: List[IncidentOutcome] = []
        for i, ft in enumerate(failure_list, 1):
            print(f"[rca_bench] incident {i}/{len(failure_list)}: {ft}")
            oc = self.run_incident(ft, idx=i)
            outcomes.append(oc)
            tag = (f"inj={oc.injected}" if not oc.rca_eval else
                   f"rank={oc.rca_eval.rank_of_truth()} top={(oc.rca_eval.predicted_ranking or ['?'])[0]}")
            rem = "" if oc.remediated is None else f" remediated={oc.remediated}"
            print(f"           target={oc.target_service} {tag}{rem} ({oc.elapsed:.0f}s) {oc.note}")

        evals = [oc.rca_eval for oc in outcomes if oc.rca_eval is not None]
        rca_metrics = evaluate(evals)
        rem = [oc for oc in outcomes if oc.remediated is not None]
        rem_success = sum(1 for oc in rem if oc.remediated)
        return {
            "outcomes": outcomes,
            "rca_metrics": rca_metrics,
            "rca_report": format_report(rca_metrics, title=f"RCA metrics [{self.rca.name}]"),
            "n_incidents": len(failure_list),
            "n_injected": sum(1 for oc in outcomes if oc.injected),
            "n_scored": len(evals),
            "remediation": (None if not rem else
                            {"n": len(rem), "success": rem_success,
                             "rate": rem_success / len(rem) if rem else 0.0}),
        }
