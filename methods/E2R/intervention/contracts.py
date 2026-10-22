"""Trusted action semantics and three-valued preconditions, independent of learning."""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field

from .schema import Action, EvidenceGraph, Plan, Witness, number, strict


@dataclass
class Policy:
    namespace: str
    allowed_targets: list[str]
    selector_options: dict = field(default_factory=dict)
    rollback_options: dict = field(default_factory=dict)
    max_replicas: dict[str, int] = field(default_factory=dict)
    max_steps: int = 3
    capacity_margin: float = 1.0
    max_harm_probability: float = 0.05
    min_success_probability: float = 0.8
    min_support: int = 5
    min_utility: float = -1000000000.0

    def __post_init__(self):
        if (not self.namespace or not 1 <= self.max_steps <= 3
                or not number(self.capacity_margin) or self.capacity_margin < 1
                or not 0 <= self.max_harm_probability <= 1
                or not 0 <= self.min_success_probability <= 1 or self.min_support < 1):
            raise ValueError("invalid intervention policy")
        if any(type(v) is not int or v < 1 for v in self.max_replicas.values()):
            raise ValueError("replica bounds must be positive integers")
        if not number(self.min_utility):
            raise ValueError("utility threshold must be finite")
        for catalog in (self.selector_options, self.rollback_options):
            for options in catalog.values():
                destinations = [option.get("destination") for option in options.values()]
                if None in destinations or len(destinations) != len(set(destinations)):
                    raise ValueError("each approved configuration needs a distinct observed destination entity")

    @classmethod
    def from_dict(cls, value):
        return strict(cls, value)


def action_option(action: Action, policy: Policy) -> dict | None:
    options = policy.selector_options if action.operation == "restore_selector" else policy.rollback_options
    return options.get(action.target, {}).get(action.parameters.get("option"))


def required_variables(graph: EvidenceGraph, action: Action) -> set[tuple[str, str]]:
    target = action.target
    out = {(target, "dependencies_known")}
    if action.operation == "scale":
        out |= {(target, k) for k in ("replicas", "hpa_absent", "scheduler_headroom")}
    else:
        destination = action.destination or target
        out |= {(destination, k) for k in ("healthy", "capacity", "load")}
        if action.operation == "rollback_version":
            out |= {(destination, "compatible"), (destination, "version_approved")}
    if action.operation == "scale":
        for r in graph.known_relations():
            if r.source == target and r.kind in ("depends_on", "competes_for"):
                out |= {(r.target, "connection_headroom"), (target, "connections_per_replica")}
    return out


def check_plan(graph: EvidenceGraph, plan: Plan, policy: Policy, *, hypothetical=False) -> list[Witness]:
    checks = []
    closure = graph.closure(graph.goals)
    prior_scaled = set()
    if len(plan.actions) > policy.max_steps:
        return [Witness(-1, "plan_length", "violated")]
    for index, action in enumerate(plan.actions):
        node = graph.entity(action.target)
        kind = "Service" if action.operation == "restore_selector" else "Deployment"
        scoped = (node.namespace == policy.namespace and action.target in policy.allowed_targets
                  and action.target in closure and node.kind == kind and bool(node.uid))
        checks.append(Witness(index, "target_scope_and_identity", "pass" if scoped else "violated"))
        option = action_option(action, policy)
        if action.operation == "scale":
            approved = action.parameters["replicas"] <= policy.max_replicas.get(action.target, 0)
        else:
            approved = bool(option and option.get("destination") == action.destination)
        checks.append(Witness(index, "approved_parameters", "pass" if approved else "violated"))
        relevant = {action.target, action.destination}
        for relation in graph.known_relations():
            if relation.kind in ("depends_on", "competes_for", "constrains") and (
                    relation.source in relevant or relation.target in relevant):
                fresh = relation.validity == "present" and relation.observed_at <= graph.at <= relation.observed_at + relation.ttl
                checks.append(Witness(index, "dependency_freshness:" + relation.source + ":" + relation.target,
                    "pass" if fresh else "unknown", tuple(relation.evidence),
                    ((action.target, "dependencies_known"),) if not fresh else ()))
        required = required_variables(graph, action)
        values = {}
        for entity, key in sorted(required):
            fact = graph.latest(entity, key)
            value = graph.read(entity, key, hypothetical=hypothetical)
            values[(entity, key)] = value
            checks.append(Witness(index, f"known:{entity}:{key}", "unknown" if value is None else "pass",
                                  (fact.id,) if fact else (), ((entity, key),) if value is None else ()))

        def predicate(name, keys, evaluate, deferred=False):
            vals = [values.get(k) for k in keys]
            refs = tuple(f.id for k in keys if (f := graph.latest(*k)) is not None)
            if any(v is None for v in vals):
                status = "unknown"
            else:
                status = "pass" if evaluate(*vals) else "violated"
            if deferred and status != "pass":
                status = "deferred"
            checks.append(Witness(index, name, status, refs,
                                  tuple(k for k, v in zip(keys, vals) if v is None)))

        predicate("dependency_inventory", [(action.target, "dependencies_known")], lambda v: v is True)
        if action.operation == "scale":
            desired = action.parameters["replicas"]
            predicate("no_hpa_conflict", [(action.target, "hpa_absent")], lambda v: v is True)
            predicate("scale_up_only", [(action.target, "replicas")], lambda v: number(v) and desired >= v)
            predicate("scheduling_budget", [(action.target, "replicas"), (action.target, "scheduler_headroom")],
                      lambda current, free: number(current) and number(free) and free >= desired - current)
            for r in graph.known_relations():
                if r.source == action.target and r.kind in ("depends_on", "competes_for"):
                    predicate("shared_budget:" + r.target,
                              [(action.target, "replicas"), (action.target, "connections_per_replica"),
                               (r.target, "connection_headroom")],
                              lambda current, each, free: all(number(x) and x >= 0 for x in (current, each, free))
                              and free >= max(0, desired - current) * each)
            prior_scaled.add(action.target)
        else:
            destination = action.destination or action.target
            predicate("destination_healthy", [(destination, "healthy")], lambda v: v is True)
            predicate("destination_capacity", [(destination, "capacity"), (destination, "load")],
                      lambda capacity, load: all(number(v) and v >= 0 for v in (capacity, load))
                      and capacity >= policy.capacity_margin * load,
                      deferred=destination in prior_scaled)
            if action.operation == "rollback_version":
                for key in ("compatible", "version_approved"):
                    predicate(key, [(destination, key)], lambda v: v is True)
    return checks


def feasible(checks: list[Witness], *, allow_deferred=True) -> bool:
    allowed = ("pass", "deferred") if allow_deferred else ("pass",)
    return all(c.status in allowed for c in checks)


def mandatory_structure(graph: EvidenceGraph, plan: Plan) -> tuple[set[str], set[tuple[str, str]]]:
    nodes = set(graph.goals)
    variables = set()
    for action in plan.actions:
        nodes.add(action.target)
        if action.destination:
            nodes.add(action.destination)
        variables |= required_variables(graph, action)
    nodes |= {entity for entity, _ in variables}
    # All incident constraint-factor members remain in the action's safety skeleton.
    while True:
        expanded = nodes | {member for relation in graph.known_relations()
                            if relation.kind in ("constrains", "competes_for")
                            and (relation.source in nodes or relation.target in nodes)
                            for member in (relation.source, relation.target)}
        if expanded == nodes:
            break
        nodes = expanded
    return nodes, variables


def field_value(obj: dict, path: str):
    result = obj
    for part in path.strip("/").split("/"):
        result = result[part.replace("~1", "/").replace("~0", "~")]
    return result


@dataclass
class Mutation:
    entity: str
    kind: str
    namespace: str
    name: str
    uid: str
    path: str
    before: object
    after: object
    patch: list[dict]


def compile_mutation(action: Action, graph: EvidenceGraph, policy: Policy, live: dict) -> Mutation:
    """Compile one exact-object write. API acceptance is not behavioral recovery."""
    node = graph.entity(action.target)
    meta = live.get("metadata", {})
    expected_kind = "Service" if action.operation == "restore_selector" else "Deployment"
    if (action.target not in policy.allowed_targets or action.target not in graph.closure(graph.goals)
            or node.namespace != policy.namespace or node.kind != expected_kind
            or live.get("kind") != expected_kind or meta.get("namespace") != node.namespace
            or meta.get("name") != node.name or not node.uid or meta.get("uid") != node.uid
            or not meta.get("resourceVersion")):
        raise ValueError("live target identity/scope mismatch")
    option = action_option(action, policy)
    if action.operation == "scale":
        path, after = "/spec/replicas", action.parameters["replicas"]
        if not live["spec"]["replicas"] <= after <= policy.max_replicas.get(action.target, 0):
            raise ValueError("replica write is not an approved scale-up")
    elif action.operation == "restore_selector":
        if not option or option.get("destination") != action.destination:
            raise ValueError("selector is not approved")
        path, after = "/spec/selector", option["selector"]
        if not isinstance(after, dict) or not after or any(not isinstance(k, str) or not isinstance(v, str) for k, v in after.items()):
            raise ValueError("selector must be a nonempty string mapping")
    else:
        if not option or option.get("destination") != action.destination:
            raise ValueError("rollback template is not approved")
        path, after = "/spec/template", option["template"]
        labels = after.get("metadata", {}).get("labels", {})
        selector = live["spec"].get("selector", {})
        if selector.get("matchExpressions") or not selector.get("matchLabels") or any(
                labels.get(k) != v for k, v in selector["matchLabels"].items()):
            raise ValueError("rollback template must preserve the deployment selector")
        if not after.get("spec", {}).get("containers"):
            raise ValueError("rollback needs a complete approved pod template")
    before = copy.deepcopy(field_value(live, path))
    after = copy.deepcopy(after)
    patch = [{"op": "test", "path": "/metadata/uid", "value": node.uid},
             {"op": "test", "path": "/metadata/resourceVersion", "value": meta["resourceVersion"]},
             {"op": "test", "path": path, "value": before},
             {"op": "replace", "path": path, "value": after}]
    return Mutation(action.target, node.kind, node.namespace, node.name, node.uid, path, before, after, patch)


def compensation(mutation: Mutation, live: dict) -> list[dict]:
    meta = live.get("metadata", {})
    if (meta.get("uid") != mutation.uid or live.get("kind") != mutation.kind
            or meta.get("namespace") != mutation.namespace or meta.get("name") != mutation.name
            or not meta.get("resourceVersion") or field_value(live, mutation.path) != mutation.after):
        raise ValueError("compensation would overwrite an unrelated change")
    return [{"op": "test", "path": "/metadata/uid", "value": mutation.uid},
            {"op": "test", "path": "/metadata/resourceVersion", "value": meta["resourceVersion"]},
            {"op": "test", "path": mutation.path, "value": mutation.after},
            {"op": "replace", "path": mutation.path, "value": mutation.before}]


def checks_dict(checks):
    return [asdict(c) for c in checks]
