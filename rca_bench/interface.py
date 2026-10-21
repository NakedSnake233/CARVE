"""The RCA algorithm plug-in interface and the unified multimodal-context spec.

To add a new RCA algorithm to the benchmark you implement exactly one method::

    from rca_bench.interface import BaseRCA, MultimodalContext, RCAResult, RootCauseCandidate

    class MyRCA(BaseRCA):
        name = "my-rca"
        def diagnose(self, ctx: MultimodalContext) -> RCAResult:
            ranking = [RootCauseCandidate(service=s, score=...) for s in ctx.services]
            return RCAResult(ranking=ranking, predicted_category=...)

The harness collects a :class:`MultimodalContext` (the SAME object for every
algorithm — a fair, standardized view of the incident) and scores the returned
:class:`RCAResult` against the hidden oracle with AC@k / MRR.

Pure stdlib; no cluster / LLM imports here so this contract is importable
anywhere (offline tests, CI).
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

# Telemetry modalities a context may carry (a modality may be empty/absent).
MODALITIES = ("metrics", "logs", "traces", "k8s_state", "topology")


# ---------------------------------------------------------------------------
# Per-modality records
# ---------------------------------------------------------------------------

@dataclass
class ServiceMetrics:
    """Resource metrics for one service (aggregated over its pods/containers)."""
    service: str
    cpu_millicores: Optional[float] = None
    cpu_limit_millicores: Optional[float] = None
    cpu_ratio: Optional[float] = None          # usage / limit, in [0, ~]
    mem_mib: Optional[float] = None
    mem_limit_mib: Optional[float] = None
    mem_ratio: Optional[float] = None
    restarts: int = 0
    ready_pods: int = 0
    total_pods: int = 0
    not_ready: int = 0                          # pods Running-but-not-Ready / failing
    # active-probe signals (symptom-based; discriminate network / disk faults that
    # are invisible in CPU/mem). Populated only when collected.
    rtt_ms: Optional[float] = None              # egress ping RTT (network-delay)
    loss_pct: Optional[float] = None            # egress ping packet loss % (network-loss)
    disk_write_ms: Optional[float] = None       # disk write latency (disk-io)
    trace_latency_ms: Optional[float] = None    # max inbound edge p99 from traces (network-delay)
    trace_error_pct: Optional[float] = None     # max inbound edge error-rate from traces (network-loss)
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ServiceLogs:
    """Log summary for one service."""
    service: str
    error_count: int = 0
    total_lines: int = 0
    samples: List[str] = field(default_factory=list)   # a few recent error lines

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TraceEdge:
    """One aggregated call edge (caller -> callee) from distributed tracing."""
    src: str
    dst: str
    call_count: int = 0
    error_count: int = 0
    p50_ms: Optional[float] = None
    p99_ms: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# The unified multimodal context (RCA input spec)
# ---------------------------------------------------------------------------

@dataclass
class MultimodalContext:
    """Standardized, label-free view of an incident handed to every RCA algorithm.

    Fairness: this object carries NO oracle labels. ``incident_summary`` is a
    generic alert string; the algorithm must localize the root cause from the
    telemetry. (By default the harness also hides the injection artifacts — e.g.
    Chaos Mesh CRDs — so RCA is a real telemetry task, not "read the chaos name".)
    """
    namespace: str
    env: str
    services: List[str]                                   # candidate root-cause set
    incident_summary: str = ""                            # label-free alert text
    metrics: Dict[str, ServiceMetrics] = field(default_factory=dict)
    logs: Dict[str, ServiceLogs] = field(default_factory=dict)
    traces: List[TraceEdge] = field(default_factory=list)
    topology: List[Tuple[str, str]] = field(default_factory=list)  # (caller, callee)
    k8s_events: List[Dict[str, Any]] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)     # escape hatch: raw probe text/JSON
    available_modalities: List[str] = field(default_factory=list)

    # -- convenience accessors -------------------------------------------------
    def candidate_services(self) -> List[str]:
        return list(self.services)

    def get_metrics(self, service: str) -> Optional[ServiceMetrics]:
        return self.metrics.get(service)

    def get_logs(self, service: str) -> Optional[ServiceLogs]:
        return self.logs.get(service)

    def downstream_of(self, service: str) -> List[str]:
        return [d for (s, d) in self.topology if s == service]

    def upstream_of(self, service: str) -> List[str]:
        return [s for (s, d) in self.topology if d == service]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "namespace": self.namespace,
            "env": self.env,
            "services": self.services,
            "incident_summary": self.incident_summary,
            "metrics": {k: v.to_dict() for k, v in self.metrics.items()},
            "logs": {k: v.to_dict() for k, v in self.logs.items()},
            "traces": [e.to_dict() for e in self.traces],
            "topology": [list(e) for e in self.topology],
            "k8s_events": self.k8s_events,
            "available_modalities": self.available_modalities,
        }


# ---------------------------------------------------------------------------
# RCA output
# ---------------------------------------------------------------------------

@dataclass
class RootCauseCandidate:
    service: str
    score: float = 0.0
    failure_category: Optional[str] = None
    rationale: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RCAResult:
    """An RCA algorithm's answer: a ranked candidate list + optional category."""
    ranking: List[RootCauseCandidate] = field(default_factory=list)
    predicted_category: Optional[str] = None
    method: str = ""

    def normalized(self) -> "RCAResult":
        """Return a copy with the ranking sorted by score desc and deduped."""
        seen = set()
        ordered: List[RootCauseCandidate] = []
        for c in sorted(self.ranking, key=lambda x: x.score, reverse=True):
            if c.service in seen:
                continue
            seen.add(c.service)
            ordered.append(c)
        return RCAResult(ranking=ordered, predicted_category=self.predicted_category,
                         method=self.method)

    def ranked_services(self) -> List[str]:
        return [c.service for c in self.normalized().ranking]

    def top(self) -> Optional[RootCauseCandidate]:
        n = self.normalized().ranking
        return n[0] if n else None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "method": self.method,
            "predicted_category": self.predicted_category,
            "ranking": [c.to_dict() for c in self.normalized().ranking],
        }


# ---------------------------------------------------------------------------
# The plug-in base class
# ---------------------------------------------------------------------------

class BaseRCA(abc.ABC):
    """Implement :meth:`diagnose` to plug an RCA algorithm into the benchmark."""

    #: short identifier used by --rca-method and in reports
    name: str = "base"

    @abc.abstractmethod
    def diagnose(self, ctx: MultimodalContext) -> RCAResult:
        """Return a ranked root-cause hypothesis from the multimodal context.

        Must NOT access oracle labels. Should be deterministic for a fixed ctx
        where possible, and must never raise on a well-formed context (return a
        best-effort/empty ranking instead).
        """
        raise NotImplementedError

    def __repr__(self) -> str:  # pragma: no cover
        return f"<RCA {self.name}>"
