"""Explicitly synthetic engineering fixtures, never live intervention evidence."""
from __future__ import annotations

import copy
from dataclasses import asdict

from .contracts import Policy
from .schema import Action, Entity, EvidenceGraph, Fact, Plan, Relation, EFFECTS


def example_bundle(*, capacity=100.0, load=50.0, headroom=20.0, hide_capacity=False, group=0):
    entities = [Entity("front", "service", "Service", "e2r-test", "front", "uid-front", "1"),
                Entity("old", "deployment", "Deployment", "e2r-test", "old", "uid-old", "2"),
                Entity("new", "deployment", "Deployment", "e2r-test", "new", "uid-new", "3"),
                Entity("db", "shared_dependency"), Entity("noise", "deployment")]
    values = {"front": {"dependencies_known": True, "error_rate": 0.9},
              "old": {"dependencies_known": True, "replicas": 1, "ready_replicas": 1,
                      "healthy": True, "hpa_absent": True, "scheduler_headroom": 5,
                      "capacity": None if hide_capacity else capacity, "load": load,
                      "compatible": True, "version_approved": True, "connections_per_replica": 2},
              "new": {"dependencies_known": True, "healthy": False, "replicas": 1},
              "db": {"connection_headroom": headroom}, "noise": {"cpu": float(group % 7)}}
    graph = EvidenceGraph(100.0, entities,
        [Fact(f"{e}:{k}", e, k, v, 95.0, 60.0, "synthetic_fixture", uid=next(n.uid for n in entities if n.id == e))
         for e, fields in values.items() for k, v in fields.items()],
        [Relation("front", "old", "depends_on", 95, 60), Relation("front", "new", "routes_to", 95, 60),
         Relation("old", "db", "competes_for", 95, 60)], ["front"])
    switch = Action("restore_selector", "front", {"option": "stable"}, "old")
    plans = [Plan("no_action", ()), Plan("switch", (switch,)),
             Plan("scale_switch", (Action("scale", "old", {"replicas": 3}), switch)),
             Plan("rollback", (Action("rollback_version", "new", {"option": "stable"}, "old"),))]
    template = {"metadata": {"labels": {"app": "new"}}, "spec": {"containers": [{"name": "app", "image": "example:v1"}]}}
    policy = Policy("e2r-test", ["front", "old", "new"],
                    selector_options={"front": {"stable": {"selector": {"app": "old"}, "destination": "old"}}},
                    rollback_options={"new": {"stable": {"template": template, "destination": "old"}}},
                    max_replicas={"old": 4, "new": 4})
    bundle = {"schema": "e2r_intervention_v1", "graph": graph.to_dict(), "plans": [p.to_dict() for p in plans]}
    return bundle, asdict(policy)


def synthetic_records(groups=8, repeats=5):
    """A declared toy capacity/budget mechanism for testing data flow and gradients."""
    rows = []
    for group in range(groups):
        for condition, factor in (("low", 0.4), ("high", 1.8)):
            load = 40.0 + group * 2
            capacity = load * factor
            headroom = 1.0 if group % 3 == 0 else 20.0
            bundle, _ = example_bundle(capacity=capacity, load=load, headroom=headroom, group=group)
            for arm in ("no_action", "switch", "scale_switch", "rollback"):
                for repeat in range(repeats):
                    multiplier = 3 if arm == "scale_switch" else 1
                    has_route = arm != "no_action"
                    safe = arm != "scale_switch" or headroom >= 4
                    sufficient = capacity * multiplier >= load
                    effects = dict(zip(EFFECTS, (has_route, sufficient, has_route and sufficient and safe,
                                                safe, arm == "rollback")))
                    rows.append({"schema": "e2r_intervention_episode_v1",
                        "episode_id": f"SYNTHETIC-g{group}-{condition}-{arm}-r{repeat}",
                        "group_id": f"config-{group}", "application": "synthetic_fixture",
                        "condition_id": condition, "comparison_group": f"config-{group}",
                        "source": "synthetic", "input": copy.deepcopy(bundle), "chosen_plan": arm,
                        "action_started_at": 101.0,
                        "execution": {"status": "no_action" if arm == "no_action" else "executed", "plan_id": arm,
                                      "backend": "declared_toy_formula; not Kubernetes"},
                        "outcome": {"observed_at": 110.0, "effects": effects},
                        "diagnosis_label": 0,
                        "state_targets": [float(v) for v in effects.values()]})
    return rows
