"""Typed decision inputs. Outcomes and experiment assignments are deliberately separate."""
from __future__ import annotations

import math
import copy
from dataclasses import asdict, dataclass, field
from typing import Any

OPERATIONS = ("restore_selector", "scale", "rollback_version")
ROLES = ("service", "deployment", "pod", "shared_dependency", "factor", "goal")
ORIGINS = ("observed", "configuration", "contract", "learned", "hypothetical")
RELATIONS = ("routes_to", "controls", "depends_on", "competes_for", "constrains", "verifies")
EFFECTS = ("endpoints_ready", "capacity_sufficient", "business_recovered",
           "non_target_safe", "mechanism_eliminated")
VARIABLES = ("replicas", "ready_replicas", "capacity", "load", "connection_headroom",
             "connections_per_replica", "scheduler_headroom", "healthy", "compatible",
             "dependencies_known", "hpa_absent", "endpoints", "error_rate", "latency_ms",
             "cpu", "memory", "version_approved", "selector_matches", "request_rate", "cpu_limit", "latency_budget_ms", "warmup_age_seconds",
             "payload_bytes", "offered_bytes_per_second")


def number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def strict(cls, value: dict):
    if not isinstance(value, dict):
        raise ValueError(f"{cls.__name__} requires an object")
    extra = set(value) - set(cls.__dataclass_fields__)
    if extra:
        raise ValueError(f"unexpected {cls.__name__} fields: {sorted(extra)}")
    return cls(**value)


@dataclass(frozen=True)
class Entity:
    id: str
    role: str
    kind: str = ""
    namespace: str = ""
    name: str = ""
    uid: str = ""
    resource_version: str = ""
    binding: str = ""

    def __post_init__(self):
        if not self.id or self.role not in ROLES:
            raise ValueError("invalid entity identity or structural role")


@dataclass(frozen=True)
class Fact:
    id: str
    entity: str
    variable: str
    value: float | bool | None
    observed_at: float
    ttl: float
    source: str
    origin: str = "observed"
    uid: str = ""
    validity: str = "valid"

    def __post_init__(self):
        if (not self.id or self.variable not in VARIABLES or self.origin not in ORIGINS
                or not self.source or not number(self.observed_at)
                or not number(self.ttl) or self.ttl <= 0
                or self.validity not in ("valid", "unknown", "invalid")):
            raise ValueError("invalid fact metadata")
        if self.value is not None and type(self.value) is not bool and not number(self.value):
            raise ValueError("fact values must be finite numbers, booleans or unknown")

    def usable(self, at: float, uid: str = "", hypothetical: bool = False) -> bool:
        return (self.validity == "valid" and self.value is not None
                and self.observed_at <= at <= self.observed_at + self.ttl
                and (not uid or self.uid == uid)
                and (self.origin in ("observed", "configuration", "contract")
                     or hypothetical and self.origin == "hypothetical"))


@dataclass(frozen=True)
class Relation:
    source: str
    target: str
    kind: str
    observed_at: float
    ttl: float
    origin: str = "observed"
    evidence: tuple[str, ...] = ()
    validity: str = "present"

    def __post_init__(self):
        if (self.kind not in RELATIONS or self.origin not in ORIGINS or self.validity not in ("present", "absent", "unknown")
                or not number(self.observed_at) or not number(self.ttl) or self.ttl <= 0):
            raise ValueError("invalid relation")


@dataclass
class EvidenceGraph:
    at: float
    entities: list[Entity]
    facts: list[Fact]
    relations: list[Relation]
    goals: list[str]

    def __post_init__(self):
        if not number(self.at):
            raise ValueError("decision time must be finite")
        ids = {e.id for e in self.entities}
        fact_ids = {f.id for f in self.facts}
        if len(ids) != len(self.entities) or len(fact_ids) != len(self.facts):
            raise ValueError("duplicate entity/fact identity")
        bindings = [e.binding for e in self.entities if e.binding]
        if len(bindings) != len(set(bindings)):
            raise ValueError("role bindings must identify distinct intervention positions")
        if not self.goals or not set(self.goals) <= ids:
            raise ValueError("business goals must reference entities")
        for f in self.facts:
            if f.entity not in ids or f.observed_at > self.at:
                raise ValueError("unknown entity or future evidence in decision input")
        for r in self.relations:
            if r.source not in ids or r.target not in ids or r.observed_at > self.at:
                raise ValueError("invalid relation endpoints or future relation")
            if not set(r.evidence) <= fact_ids:
                raise ValueError("relation references missing evidence")

    @classmethod
    def from_dict(cls, value: dict) -> "EvidenceGraph":
        allowed = {"at", "entities", "facts", "relations", "goals"}
        if set(value) != allowed:
            raise ValueError("graph schema mismatch; labels/raw outcomes are not graph inputs")
        return cls(value["at"], [strict(Entity, x) for x in value["entities"]],
                   [strict(Fact, x) for x in value["facts"]],
                   [strict(Relation, x) for x in value["relations"]], list(value["goals"]))

    def to_dict(self) -> dict:
        return asdict(self)

    def entity(self, key: str) -> Entity:
        return next(e for e in self.entities if e.id == key)

    def latest(self, entity: str, variable: str) -> Fact | None:
        rows = [f for f in self.facts if f.entity == entity and f.variable == variable]
        if not rows:
            return None
        rows.sort(key=lambda f: (f.observed_at, f.id))
        newest = rows[-1]
        if any(f.observed_at == newest.observed_at and f.value != newest.value for f in rows):
            return None  # Conflicting simultaneous evidence is unknown.
        return newest

    def read(self, entity: str, variable: str, *, hypothetical: bool = False):
        fact = self.latest(entity, variable)
        node = self.entity(entity)
        return fact.value if fact and fact.usable(self.at, node.uid, hypothetical) else None

    def active_relations(self) -> list[Relation]:
        return [r for r in self.relations if r.validity == "present" and r.observed_at <= self.at <= r.observed_at + r.ttl
                and r.origin in ("observed", "configuration", "contract")]

    def known_relations(self) -> list[Relation]:
        """Expired dependencies remain obligations until refreshed, not absent edges."""
        return [r for r in self.relations if r.origin in ("observed", "configuration", "contract")
                and not (r.validity == "absent" and r.observed_at <= self.at <= r.observed_at+r.ttl)]

    def closure(self, starts: list[str]) -> set[str]:
        seen = set(starts)
        while True:
            expanded = seen | {x for r in self.active_relations()
                               if r.source in seen or r.target in seen
                               for x in (r.source, r.target)}
            if expanded == seen:
                return seen
            seen = expanded


@dataclass(frozen=True)
class Action:
    operation: str
    target: str
    parameters: dict = field(default_factory=dict)
    destination: str | None = None

    def __post_init__(self):
        if self.operation not in OPERATIONS or not self.target or not isinstance(self.parameters, dict):
            raise ValueError("unsupported operation or action parameters")
        expected = {"replicas"} if self.operation == "scale" else {"option"}
        if set(self.parameters) != expected:
            raise ValueError("action parameter schema mismatch")
        if self.operation == "scale" and (type(self.parameters["replicas"]) is not int
                                           or self.parameters["replicas"] < 1):
            raise ValueError("replicas must be a positive integer")
        if self.operation != "scale" and not isinstance(self.parameters["option"], str):
            raise ValueError("configuration option must be a trusted catalog key")


@dataclass
class GraphObservation:
    """A trusted read can reveal entities, factors, relations, and explicit absence."""
    entities: list[Entity]
    facts: list[Fact]
    relations: list[Relation]


def merge_observation(graph: EvidenceGraph, observation: GraphObservation, at: float) -> EvidenceGraph:
    entities = {e.id: e for e in graph.entities}
    for e in observation.entities:
        if e.id in entities:
            previous = entities[e.id]
            fields = ("role", "kind", "namespace", "name", "binding")
            if any(getattr(e, key) != getattr(previous, key) for key in fields):
                raise ValueError("observation cannot redefine an existing entity's authorized resource binding")
        entities[e.id] = e
    relation_keys = {(r.source, r.target, r.kind) for r in observation.relations}
    relations = [r for r in graph.relations if (r.source, r.target, r.kind) not in relation_keys]
    fact_ids = {f.id for f in observation.facts}
    facts = [f for f in graph.facts if f.id not in fact_ids] + observation.facts
    return EvidenceGraph(at, list(entities.values()), facts, relations+observation.relations, list(graph.goals))


@dataclass(frozen=True)
class Plan:
    id: str
    actions: tuple[Action, ...]

    def __post_init__(self):
        if not self.id or len(self.actions) > 3:
            raise ValueError("plan must have an identity and at most three actions")

    @classmethod
    def from_dict(cls, value: dict) -> "Plan":
        if set(value) != {"id", "actions"}:
            raise ValueError("plan schema mismatch")
        return cls(value["id"], tuple(strict(Action, x) for x in value["actions"]))

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Witness:
    action: int
    predicate: str
    status: str
    evidence: tuple[str, ...] = ()
    missing_variables: tuple[tuple[str, str], ...] = ()
    observed_deviation: Any = None
    level: str = "operation"

    def __post_init__(self):
        if self.status not in ("pass", "violated", "unknown", "deviation", "deferred"):
            raise ValueError("unknown check status")


def input_bundle(value: dict) -> tuple[EvidenceGraph, list[Plan]]:
    if set(value) != {"schema", "graph", "plans"} or value["schema"] != "e2r_intervention_v1":
        raise ValueError("invalid decision bundle; outcome/assignment must be separate")
    graph = EvidenceGraph.from_dict(value["graph"])
    plans = [Plan.from_dict(x) for x in value["plans"]]
    if not plans or len({p.id for p in plans}) != len(plans):
        raise ValueError("candidate plans must be nonempty and uniquely identified")
    ids = {e.id for e in graph.entities}
    if any(a.target not in ids or a.destination is not None and a.destination not in ids
           for p in plans for a in p.actions):
        raise ValueError("action references an unknown entity")
    options = {}
    for plan in plans:
        for action in plan.actions:
            if action.operation != "scale":
                key = (action.operation, action.target, action.destination)
                previous = options.setdefault(key, action.parameters["option"])
                if previous != action.parameters["option"]:
                    raise ValueError("distinct approved configurations require distinct destination entities")
    return graph, plans
