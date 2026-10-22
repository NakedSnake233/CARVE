from dataclasses import replace

import pytest

from methods.E2R.intervention.schema import Action, Entity, EvidenceGraph, Fact, Plan, Relation
from methods.E2R.intervention.contracts import (Policy, check_plan, compile_mutation, compensation,
                                               feasible, mandatory_structure)
from methods.E2R.intervention.verification import VerificationProtocol, business_windows, verification_package
from rca_bench.recovery_oracle import WorkloadWindow


def example():
    entities = [Entity("front", "service", "Service", "test", "front", "uid-front", "1"),
                Entity("old", "deployment", "Deployment", "test", "old", "uid-old", "2"),
                Entity("db", "shared_dependency")]
    values = {"front": {"dependencies_known": True}, "old": {"dependencies_known": True,
              "replicas": 1, "hpa_absent": True, "scheduler_headroom": 5,
              "healthy": True, "capacity": 100, "load": 50, "connections_per_replica": 2},
              "db": {"connection_headroom": 10}}
    facts = [Fact(f"{e}:{k}", e, k, v, 90, 30, "probe", uid=next(n.uid for n in entities if n.id == e))
             for e, vals in values.items() for k, v in vals.items()]
    graph = EvidenceGraph(100, entities, facts,
        [Relation("front", "old", "routes_to", 90, 30), Relation("old", "db", "competes_for", 90, 30)], ["front"])
    policy = Policy("test", ["front", "old"],
                    selector_options={"front": {"previous": {"selector": {"version": "v1"}, "destination": "old"}}},
                    max_replicas={"old": 4})
    return graph, policy


def test_auxiliary_healthy_target_is_allowed_by_dependency_closure():
    graph, policy = example()
    plan = Plan("scale-old", (Action("scale", "old", {"replicas": 3}),))
    assert feasible(check_plan(graph, plan, policy))
    graph.relations = []
    assert not feasible(check_plan(graph, plan, policy))


def test_unknown_dependency_and_shared_budget_cannot_be_pruned():
    graph, policy = example()
    plan = Plan("scale", (Action("scale", "old", {"replicas": 4}),))
    nodes, variables = mandatory_structure(graph, plan)
    assert "db" in nodes and ("db", "connection_headroom") in variables
    graph.facts = [f for f in graph.facts if f.entity != "db"]
    checks = check_plan(graph, plan, policy)
    assert not feasible(checks)
    assert any(("db", "connection_headroom") in c.missing_variables for c in checks)


def test_conditional_write_and_compensation_do_not_overwrite_other_changes():
    graph, policy = example()
    live = {"kind": "Service", "metadata": {"namespace": "test", "name": "front", "uid": "uid-front",
            "resourceVersion": "88"}, "spec": {"selector": {"version": "v2"}}}
    action = Action("restore_selector", "front", {"option": "previous"}, "old")
    mutation = compile_mutation(action, graph, policy, live)
    assert mutation.patch[1]["value"] == "88"
    assert mutation.patch[-1]["path"] == "/spec/selector"
    live["spec"]["selector"] = mutation.after
    assert compensation(mutation, live)[-1]["value"] == {"version": "v2"}
    live["spec"]["selector"] = {"version": "v3"}
    with pytest.raises(ValueError, match="unrelated"):
        compensation(mutation, live)
    live["metadata"]["uid"] = "replacement"
    with pytest.raises(ValueError, match="identity"):
        compile_mutation(action, graph, policy, live)


def test_capacity_after_scale_is_deferred_and_must_be_rechecked():
    graph, policy = example()
    graph.facts = [replace(f, value=150) if f.variable == "load" else f for f in graph.facts]
    switch = Action("restore_selector", "front", {"option": "previous"}, "old")
    assert not feasible(check_plan(graph, Plan("switch", (switch,)), policy))
    checks = check_plan(graph, Plan("prepare-switch", (Action("scale", "old", {"replicas": 3}), switch)), policy)
    assert feasible(checks) and any(c.status == "deferred" for c in checks)


def window(identity, start, end):
    return WorkloadWindow(30, 10, 0, 20, True, identity, "transaction", start, end)


def test_mitigation_and_defect_elimination_are_separate():
    business = business_windows(window("base", 0, 10), [window(f"p{i}", 20+i*5, 25+i*5) for i in range(3)],
                                completed_at=15, evaluated_at=36, protocol=VerificationProtocol())
    package = verification_package(field_status="pass", controller_status="pass", business=business,
                                   non_target={"other": business}, mechanism_status="fail")
    assert package["mitigation"] == "pass" and package["fault_elimination"] == "fail"
    missing = verification_package(field_status="pass", controller_status="unknown", business=business,
                                   non_target={}, mechanism_status="unknown")
    assert missing["mitigation"] == "unknown"


def test_replayed_or_pre_action_windows_cannot_verify_recovery():
    base = window("base", 0, 10)
    post = window("reused", 20, 25)
    result = business_windows(base, [post, post, post], completed_at=15, evaluated_at=30,
                              protocol=VerificationProtocol())
    assert result["status"] == "unknown"
