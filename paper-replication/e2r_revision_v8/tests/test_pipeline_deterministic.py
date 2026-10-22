"""End-to-end deterministic pipeline test (no cluster, no LLM).

Proves the crux of the method: given realistic read-only probe output, the
deterministic backbone localizes the fault and produces the correct,
safety-passing repair playbook -- the operation that actually makes MicroRemed's
verification pass.
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from methods.PlanRCAThinkRemed import (  # noqa: E402
    evidence_agents, hypothesis_agent, repair_planner_agent,
    playbook_generator_agent, safety_guard,
)

NS = "online-boutique"

STRESSCHAOS_JSON = json.dumps({
    "apiVersion": "v1", "kind": "List", "items": [
        {"kind": "StressChaos",
         "metadata": {"name": "cpu-stress", "namespace": NS},
         "spec": {"selector": {"labelSelectors": {"app": "cartservice"}},
                  "stressors": {"cpu": {"workers": 10, "load": 100}}}},
    ],
})

CONFIG_DEPLOY_JSON = json.dumps({
    "kind": "DeploymentList", "items": [
        {"kind": "Deployment", "metadata": {"name": "productcatalogservice", "namespace": NS},
         "spec": {"template": {"spec": {"containers": [
             {"name": "server", "resources": {"limits": {"cpu": "2m", "memory": "200Mi"}}}
         ]}}}},
    ],
})


def test_chaos_detection_yields_service_and_category():
    items = evidence_agents.detect_active_chaos(STRESSCHAOS_JSON)
    assert len(items) == 1
    assert items[0].service == "cartservice"
    assert items[0].confidence >= 0.9
    assert "cpu-stress" in items[0].finding


def test_full_deterministic_repair_for_cpu_stress():
    # 1) evidence: chaos + a corroborating metric item (two independent sources)
    ev = evidence_agents.detect_active_chaos(STRESSCHAOS_JSON)
    ev += [evidence_agents.EvidenceItem(source="metric", modality="metric",
                                        service="cartservice",
                                        finding="cartservice cpu near limit", confidence=0.6)]
    # 2) hypotheses + stop decision
    hyps = hypothesis_agent._deterministic_hypotheses(ev)
    assert hyps and hyps[0].service == "cartservice"
    assert hyps[0].failure_category == "cpu-stress"
    stop, reason = hypothesis_agent.should_stop_and_repair(hyps, ev)
    assert stop, reason
    # 3) RCA report
    report = hypothesis_agent.build_rca_report(hyps, ev)
    assert report.root_cause_service == "cartservice"
    # 4) typed repair plan
    plan = repair_planner_agent.build_repair_plan(
        rca_report=report, evidence_history=ev, runtime_envs="k3s",
        namespace=NS, env=NS, use_llm=False,
    )
    assert plan.actions[0].action_type == "RemoveInjectedStress"
    assert "delete stresschaos cpu-stress" in plan.actions[0].command
    # 5) playbook + safety
    pb = playbook_generator_agent.generate_playbook(
        plan, namespace=NS, inventory_content="[k3s_control_plane]\n", use_llm=False)
    assert "kubectl delete stresschaos cpu-stress" in pb
    assert "ignore_errors: false" in pb and "ignore_errors: true" not in pb
    ok, reasons = safety_guard.check_playbook_yaml(pb)
    assert ok, reasons
    ok2, reasons2 = safety_guard.check_repair_plan(plan, allowed_namespace=NS)
    assert ok2, reasons2


def test_full_deterministic_repair_for_config_error():
    ev = evidence_agents.detect_underprovisioned_config(CONFIG_DEPLOY_JSON)
    ev += [evidence_agents.EvidenceItem(source="pod-status", modality="resource",
                                        service="productcatalogservice",
                                        finding="productcatalogservice pod-config-error suspected",
                                        confidence=0.6)]
    assert ev and ev[0].service == "productcatalogservice"
    hyps = hypothesis_agent._deterministic_hypotheses(ev)
    report = hypothesis_agent.build_rca_report(hyps, ev)
    assert report.failure_category == "pod-config-error"
    plan = repair_planner_agent.build_repair_plan(
        rca_report=report, evidence_history=ev, runtime_envs="k3s",
        namespace=NS, env=NS, use_llm=False)
    assert plan.actions[0].action_type == "RecoverConfigError"
    assert "kubectl set resources deployment/productcatalogservice" in plan.actions[0].command
    pb = playbook_generator_agent.generate_playbook(
        plan, namespace=NS, inventory_content="x", use_llm=False)
    assert "rollout status deployment/productcatalogservice" in pb
    ok, reasons = safety_guard.check_playbook_yaml(pb)
    assert ok, reasons


def test_insufficient_evidence_yields_waitandverify():
    from methods.PlanRCAThinkRemed.schemas import RCAReport
    empty_report = RCAReport()  # no service/category
    plan = repair_planner_agent.build_repair_plan(
        rca_report=empty_report, evidence_history=[], runtime_envs="k3s",
        namespace=NS, env=NS, use_llm=False)
    assert plan.actions[0].action_type == "WaitAndVerify"
    ok, _ = safety_guard.check_playbook_yaml(
        playbook_generator_agent.generate_playbook(plan, namespace=NS,
                                                   inventory_content="x", use_llm=False))
    assert ok


def test_invalid_focus_does_not_break_probes():
    # Regression: an LLM placeholder service like "unknown (likely ...)" must NOT
    # be injected into a `-l app=<svc>` selector (it breaks the shell). It is
    # rejected -> probes go namespace-wide; a valid name is used as-is.
    from methods.PlanRCAThinkRemed.evidence_agents import (
        ConfigEvidenceAgent, _safe_focus, _service_from_pod)
    bad = "unknown (likely one of the online-boutique workloads)"
    assert _safe_focus(bad) is None
    assert _safe_focus("emailservice") == "emailservice"
    assert _safe_focus("redis-cart") == "redis-cart"
    assert _service_from_pod("emailservice-6c4787b559-mnshl") == "emailservice"
    assert _service_from_pod("redis-cart-7866485cb-ml6rv") == "redis-cart"

    agent = ConfigEvidenceAgent()
    cmds_bad = agent.default_probes(NS, _safe_focus(bad))
    assert all("(" not in c and "app=unknown" not in c for c in cmds_bad), cmds_bad
    # always scans ALL deployments (robust to Deployment objects lacking app= labels)
    assert any(c == f"kubectl get deploy -n {NS} -o json" for c in cmds_bad), cmds_bad
    # a valid focus adds a by-NAME query (not `-l app=`, which Deployments may not have)
    cmds_ok = agent.default_probes(NS, _safe_focus("emailservice"))
    assert any(f"deploy emailservice -n {NS}" in c for c in cmds_ok), cmds_ok


def test_config_detection_is_cpu_specific():
    # Healthy services that legitimately cap memory at 128Mi must NOT be flagged;
    # only the absurd injected cpu limit (<=2m) triggers pod-config-error.
    healthy = json.dumps({"kind": "List", "items": [
        {"kind": "Deployment", "metadata": {"name": "emailservice"},
         "spec": {"template": {"spec": {"containers": [
             {"name": "server", "resources": {"limits": {"cpu": "200m", "memory": "128Mi"}}}]}}}}]})
    assert evidence_agents.detect_underprovisioned_config(healthy) == []
    broken = json.dumps({"kind": "List", "items": [
        {"kind": "Deployment", "metadata": {"name": "emailservice"},
         "spec": {"template": {"spec": {"containers": [
             {"name": "server", "resources": {"limits": {"cpu": "2m", "memory": "200Mi"}}}]}}}}]})
    d = evidence_agents.detect_underprovisioned_config(broken)
    assert len(d) == 1 and d[0].service == "emailservice"


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)} deterministic-pipeline tests passed.")


if __name__ == "__main__":
    _run_all()
