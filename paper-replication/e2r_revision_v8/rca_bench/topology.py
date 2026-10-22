"""Service dependency topology sources.

Topology is part of the multimodal context. When distributed tracing is
available the caller->callee edges can be derived from traces; otherwise we fall
back to a known static call graph for the supported demo systems.

Pure stdlib; pluggable via :class:`TopologySource`.
"""

from __future__ import annotations

import abc
from typing import List, Optional, Tuple

# Known Online Boutique call graph (caller -> callee).
ONLINE_BOUTIQUE_TOPOLOGY: List[Tuple[str, str]] = [
    ("loadgenerator", "frontend"),
    ("frontend", "adservice"),
    ("frontend", "cartservice"),
    ("frontend", "checkoutservice"),
    ("frontend", "currencyservice"),
    ("frontend", "productcatalogservice"),
    ("frontend", "recommendationservice"),
    ("frontend", "shippingservice"),
    ("checkoutservice", "cartservice"),
    ("checkoutservice", "currencyservice"),
    ("checkoutservice", "productcatalogservice"),
    ("checkoutservice", "shippingservice"),
    ("checkoutservice", "emailservice"),
    ("checkoutservice", "paymentservice"),
    ("recommendationservice", "productcatalogservice"),
    ("cartservice", "redis-cart"),
]

_ENV_TOPOLOGY = {
    "online-boutique": ONLINE_BOUTIQUE_TOPOLOGY,
    "boutique": ONLINE_BOUTIQUE_TOPOLOGY,
}


class TopologySource(abc.ABC):
    @abc.abstractmethod
    def edges(self, namespace: str, env: str) -> List[Tuple[str, str]]:
        ...


class StaticTopologySource(TopologySource):
    """Return a known static call graph for the env (empty if unknown)."""

    def edges(self, namespace: str, env: str) -> List[Tuple[str, str]]:
        return list(_ENV_TOPOLOGY.get((env or "").strip().lower(), []))


def derive_topology_from_traces(trace_edges) -> List[Tuple[str, str]]:
    """Build a (caller, callee) edge list from aggregated trace edges."""
    seen = set()
    out: List[Tuple[str, str]] = []
    for e in trace_edges or []:
        key = (e.src, e.dst)
        if e.src and e.dst and key not in seen:
            seen.add(key)
            out.append(key)
    return out
