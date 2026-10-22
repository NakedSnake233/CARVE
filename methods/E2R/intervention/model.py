"""Role-based, action-conditioned factor-graph encoder using plain PyTorch."""
from __future__ import annotations

import math
import json
from dataclasses import dataclass

import torch
from torch import nn
from torch.nn import functional as F

from .contracts import mandatory_structure
from .schema import EFFECTS, OPERATIONS, ORIGINS, RELATIONS, ROLES, VARIABLES, EvidenceGraph, Plan, number

FEATURES = tuple("role:" + x for x in ROLES) + tuple("variable:" + x for x in VARIABLES) + \
    tuple("origin:" + x for x in ORIGINS) + ("value", "known", "age", "fresh", "goal", "target", "destination", "fact")
EDGE_TYPES = RELATIONS + ("has_fact", "temporal")


def scalar(value):
    return math.copysign(math.log1p(abs(float(value))), float(value)) if number(value) or type(value) is bool else 0.0


@dataclass
class GraphInput:
    x: torch.Tensor
    edges: torch.Tensor
    edge_types: torch.Tensor
    mandatory: torch.Tensor
    action_targets: torch.Tensor
    action_destinations: torch.Tensor
    action_types: torch.Tensor
    action_parameters: torch.Tensor
    node_ids: list[str]


def tensorize(graph: EvidenceGraph, plan: Plan) -> GraphInput:
    mandatory_nodes, required = mandatory_structure(graph, plan)
    entities = sorted(graph.entities, key=lambda e: e.id)
    facts = sorted(graph.facts, key=lambda f: f.id)
    node_ids = ["entity:" + e.id for e in entities] + ["fact:" + f.id for f in facts]
    missing = sorted(k for k in required if graph.latest(*k) is None)
    node_ids += ["unknown:" + entity + ":" + variable for entity, variable in missing]
    lookup = {key: i for i, key in enumerate(node_ids)}
    feature_index = {key: i for i, key in enumerate(FEATURES)}
    rows, mandatory, edges, kinds = [], [], [], []
    targets = {a.target for a in plan.actions}
    destinations = {a.destination for a in plan.actions if a.destination}

    def base(entity):
        row = [0.0] * len(FEATURES)
        row[feature_index["role:" + entity.role]] = 1.0
        row[feature_index["goal"]] = float(entity.id in graph.goals)
        row[feature_index["target"]] = float(entity.id in targets)
        row[feature_index["destination"]] = float(entity.id in destinations)
        return row

    def link(a, b, kind):
        k = EDGE_TYPES.index(kind)
        edges.extend(((a, b), (b, a)))
        kinds.extend((k, k + len(EDGE_TYPES)))

    for entity in entities:
        rows.append(base(entity))
        mandatory.append(entity.id in mandatory_nodes)
    supporting_facts = {f for r in graph.active_relations()
                        if r.source in mandatory_nodes or r.target in mandatory_nodes for f in r.evidence}
    histories = {}
    for fact in facts:
        entity = graph.entity(fact.entity)
        row = base(entity)
        row[feature_index["variable:" + fact.variable]] = 1.0
        row[feature_index["origin:" + fact.origin]] = 1.0
        present = fact.value is not None and fact.validity == "valid"
        row[feature_index["value"]] = scalar(fact.value) if present else 0.0
        row[feature_index["known"]] = float(present)
        row[feature_index["age"]] = math.log1p(max(0.0, graph.at - fact.observed_at))
        row[feature_index["fresh"]] = float(fact.usable(graph.at, entity.uid))
        row[feature_index["fact"]] = 1.0
        rows.append(row)
        latest = graph.latest(fact.entity, fact.variable)
        mandatory.append(fact.id in supporting_facts or
                         (fact.entity, fact.variable) in required and latest is not None and latest.id == fact.id)
        index = lookup["fact:" + fact.id]
        link(lookup["entity:" + fact.entity], index, "has_fact")
        histories.setdefault((fact.entity, fact.variable), []).append((fact.observed_at, fact.id, index))
    for entity, variable in missing:
        row = base(graph.entity(entity))
        row[feature_index["variable:" + variable]] = 1.0
        row[feature_index["fact"]] = 1.0
        rows.append(row)
        mandatory.append(True)
        link(lookup["entity:" + entity], lookup["unknown:" + entity + ":" + variable], "has_fact")
    for history in histories.values():
        ordered = sorted(history)
        for before, after in zip(ordered, ordered[1:]):
            link(before[2], after[2], "temporal")
    for relation in graph.active_relations():
        link(lookup["entity:" + relation.source], lookup["entity:" + relation.target], relation.kind)
    action_targets = [lookup["entity:" + a.target] for a in plan.actions]
    action_destinations = [lookup["entity:" + (a.destination or a.target)] for a in plan.actions]
    return GraphInput(torch.tensor(rows, dtype=torch.float32),
                      torch.tensor(edges, dtype=torch.long).T.contiguous() if edges else torch.empty(2, 0, dtype=torch.long),
                      torch.tensor(kinds, dtype=torch.long), torch.tensor(mandatory, dtype=torch.bool),
                      torch.tensor(action_targets, dtype=torch.long), torch.tensor(action_destinations, dtype=torch.long),
                      torch.tensor([OPERATIONS.index(a.operation) for a in plan.actions], dtype=torch.long),
                      torch.tensor([[scalar(a.parameters.get("replicas", 0))] for a in plan.actions],
                                   dtype=torch.float32).reshape(-1, 1), node_ids)


def plan_signature(graph: EvidenceGraph, plan: Plan) -> str:
    """Exact intervention grouping, separate from the name-free learned features.

    Explicit unique role bindings permit declared cross-application alignment;
    otherwise an entity's input identifier is retained to avoid pooling targets.
    """
    def binding(key):
        e = graph.entity(key)
        return [e.role, e.binding or e.id]
    return json.dumps([{"operation": a.operation, "target": binding(a.target),
                       "destination": binding(a.destination) if a.destination else None,
                       "parameters": a.parameters} for a in plan.actions], sort_keys=True, separators=(",", ":"))


class ActionConditionedGraph(nn.Module):
    def __init__(self, hidden=64, layers=2, variant="graph", diagnosis_classes=8):
        super().__init__()
        if variant not in ("graph", "set", "full", "full_set"):
            raise ValueError("encoder variant must be graph, set, full or full_set")
        self.config = dict(hidden=hidden, layers=layers, variant=variant, diagnosis_classes=diagnosis_classes)
        self.variant = variant
        self.input = nn.Linear(len(FEATURES), hidden)
        self.operation = nn.Embedding(len(OPERATIONS), hidden)
        self.action_input = nn.Linear(hidden * 3 + 1, hidden)
        self.sequence = nn.GRU(hidden, hidden, batch_first=True)
        self.no_action = nn.Parameter(torch.zeros(hidden))
        self.gate = nn.Sequential(nn.Linear(hidden * 2, hidden), nn.ReLU(), nn.Linear(hidden, 1))
        self.relation = nn.Embedding(len(EDGE_TYPES) * 2, hidden)
        self.messages = nn.ModuleList(nn.Linear(hidden * 2, hidden) for _ in range(layers))
        self.norms = nn.ModuleList(nn.LayerNorm(hidden) for _ in range(layers))
        self.pool = nn.Linear(hidden, hidden)
        self.representation = nn.Linear(hidden * 2, hidden)
        self.effect_head = nn.Linear(hidden, len(EFFECTS))
        self.state_head = nn.Linear(hidden, len(EFFECTS))
        self.diagnosis_head = nn.Linear(hidden, diagnosis_classes)

    def forward(self, item: GraphInput):
        h = F.relu(self.input(item.x))
        if len(item.action_types):
            tokens = self.action_input(torch.cat((h[item.action_targets], h[item.action_destinations],
                                      self.operation(item.action_types), item.action_parameters), dim=-1))
            _, query = self.sequence(tokens.unsqueeze(0))
            query = query[0, 0]
        else:
            query = self.no_action
        gates = self.gate(torch.cat((h, query.expand_as(h)), dim=-1)).squeeze(-1).sigmoid()
        gates = torch.where(item.mandatory, torch.ones_like(gates), gates)
        if self.variant in ("full", "full_set"):
            gates = torch.ones_like(gates)
        compact = gates[~item.mandatory].mean() if (~item.mandatory).any() else gates.sum() * 0
        kept = torch.arange(len(h))
        edges, kinds = item.edges, item.edge_types
        if not self.training:
            kept = ((gates >= 0.5) | item.mandatory).nonzero().flatten()
            remap = torch.full((len(h),), -1, dtype=torch.long)
            remap[kept] = torch.arange(len(kept))
            mask = (remap[edges[0]] >= 0) & (remap[edges[1]] >= 0)
            edges, kinds = remap[edges[:, mask]], kinds[mask]
            h, gates = h[kept], gates[kept]
        h = h * gates[:, None]
        for layer, norm in zip(self.messages, self.norms):
            aggregate = torch.zeros_like(h)
            degree = torch.zeros(len(h), 1)
            if self.variant not in ("set", "full_set") and edges.numel():
                source, destination = edges
                weights = (gates[source] * gates[destination])[:, None]
                aggregate.index_add_(0, destination, (h[source] + self.relation(kinds)) * weights)
                degree.index_add_(0, destination, weights)
            h = F.relu(norm(layer(torch.cat((h, aggregate / degree.clamp_min(1)), dim=-1)))) * gates[:, None]
        attention = ((self.pool(h) * query).sum(-1) / math.sqrt(h.shape[-1]) + gates.clamp_min(1e-6).log()).softmax(0)
        pooled = (h * attention[:, None]).sum(0)
        representation = torch.tanh(self.representation(torch.cat((pooled, query))))
        return {"embedding": representation, "logits": self.effect_head(representation),
                "state": self.state_head(representation), "diagnosis": self.diagnosis_head(representation),
                "compact_loss": compact, "gates": gates, "kept_indices": kept,
                "kept_nodes": len(kept), "total_nodes": len(item.x), "kept_edges": edges.shape[1]}
