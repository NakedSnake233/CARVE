"""Trace sources for the multimodal context.

`JaegerTraceSource` queries a Jaeger query API and aggregates spans into
per-edge (caller→callee) latency (p50/p99) + error counts — the proper, low-jitter
signal for network faults (real inter-service latency vs ping-to-8.8.8.8).

Two edge-extraction paths (both in `aggregate_edges`, so both are offline-testable):

  1. **Caller-side client spans.** OpenCensus/gRPC names client spans
     ``Sent.hipstershop.CurrencyService.Convert``; the ``<...>Service`` token is the
     callee and the span duration is the *round-trip* latency the caller observed.
     Lowercased, ``CurrencyService`` == the k8s service ``currencyservice``. This
     recovers per-callee inbound latency/error even when the callee emits no server
     span — which is the case for Online Boutique v0.3.8 backends (only the frontend
     and adservice emit; the Go backends have no server-side tracing). The frontend's
     client spans therefore cover every service it calls (productcatalog, currency,
     cart, ad, recommendation, shipping, checkout).

     With the OTel-instrumented build (v0.10.1, the "v4" benchmark) EVERY service
     emits its own ``Sent.*`` client spans — in its own trace, since the SUT does not
     propagate trace context across the gRPC boundary — so querying each service and
     extracting its outbound client spans yields the FULL multi-tier call graph:
     checkout→payment/email/shipping/currency/cart and recommendation→productcatalog,
     not just the frontend tier. payment and email, which had no trace coverage in v3
     (frontend never calls them directly), are covered in v4 via checkout's spans.
     Set ``OTEL_SERVICE_NAME`` per service or the OTel SDK reports
     ``unknown_service:<exe>`` — those are dropped via :data:`_SKIP_SERVICES`.
  2. **CHILD_OF server spans.** A real mesh / OpenTelemetry build where the callee
     emits a server span linked to the caller — kept so the same code works if the
     SUT is ever upgraded to full server-side tracing.

Wire it in::

    from rca_bench.trace_sources import JaegerTraceSource
    provider = LiveClusterContextProvider(trace_source=JaegerTraceSource("http://127.0.0.1:30686"))

If no traces are available the source returns ``[]`` and the provider falls back
to the active ping/disk probes (§ context_provider). Pure stdlib (urllib, re).
"""

from __future__ import annotations

import json
import re
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from .interface import TraceEdge
from .context_provider import TraceSource

# A client (caller-side) RPC span is prefixed "Sent." by OpenCensus; the callee is
# the "<Word>Service" token in the operation name.
_SENT_PREFIXES = ("Sent.", "Sent ", "Egress.")
_SERVICE_RE = re.compile(r"([A-Za-z][A-Za-z0-9]*Service)")
_ERR_KEYS = ("error", "otel.status_code")
_GRPC_STATUS_KEYS = ("status.code", "census.status.code", "rpc.grpc.status_code",
                     "grpc.status_code", "rpc.grpc.status_code_value")
_OK_VALUES = ("0", "0.0", "ok", "okay", "")
# Service-name substrings to drop from edges: the trace backend itself, and the
# OTel SDK default ("unknown_service[:<exe>]") emitted before a service.name /
# OTEL_SERVICE_NAME is set. Real boutique services always have clean k8s names.
_SKIP_SERVICES = ("jaeger", "unknown_service")


def _percentile(sorted_vals: List[float], q: float) -> Optional[float]:
    if not sorted_vals:
        return None
    idx = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[idx]


def _span_is_error(tags: Any) -> bool:
    """True if a span's tags mark it as an error (OpenCensus/OTLP/gRPC status)."""
    for t in tags or []:
        k = t.get("key")
        v = str(t.get("value")).lower()
        if k in _ERR_KEYS and v in ("true", "error"):
            return True
        if k in _GRPC_STATUS_KEYS and v not in _OK_VALUES:
            return True
    return False


def _callee_from_client_op(op: str) -> Optional[str]:
    """For a caller-side client span, return the lowercased callee service name
    (``Sent.hipstershop.ProductCatalogService.ListProducts`` -> ``productcatalogservice``);
    return None for server/root spans."""
    if not op or not any(op.startswith(p) for p in _SENT_PREFIXES):
        return None
    m = _SERVICE_RE.search(op)
    return m.group(1).lower() if m else None


def aggregate_edges(traces: List[Dict[str, Any]], skip_substr: Optional[str] = None) -> List[TraceEdge]:
    """Aggregate Jaeger trace JSON into per (caller,callee) edges.

    Pure function (no I/O) so it is offline-testable with fixture traces.
    ``skip_substr`` overrides the default skip list (:data:`_SKIP_SERVICES`).
    """
    skips = (skip_substr,) if skip_substr else _SKIP_SERVICES
    agg: Dict[Tuple[str, str], Dict[str, Any]] = {}

    def _add(src: str, dst: str, dur: float, err: bool) -> None:
        if not src or not dst or src == dst:
            return
        s, d = src.lower(), dst.lower()
        if any(k in s or k in d for k in skips):
            return
        a = agg.setdefault((src, dst), {"d": [], "e": 0, "c": 0})
        a["d"].append(dur)
        a["c"] += 1
        if err:
            a["e"] += 1

    for tr in traces or []:
        procs = tr.get("processes", {}) or {}
        svc_of, dur_of, err_of, op_of = {}, {}, {}, {}
        for sp in tr.get("spans", []) or []:
            sid = sp.get("spanID")
            svc_of[sid] = (procs.get(sp.get("processID", ""), {}) or {}).get("serviceName", "")
            dur_of[sid] = (sp.get("duration", 0) or 0) / 1000.0   # μs -> ms
            err_of[sid] = _span_is_error(sp.get("tags"))
            op_of[sid] = sp.get("operationName", "") or ""
        for sp in tr.get("spans", []) or []:
            sid = sp.get("spanID")
            caller = svc_of.get(sid, "")
            # (1) caller-side client span: callee is encoded in the operation name
            callee = _callee_from_client_op(op_of.get(sid, ""))
            if callee:
                _add(caller, callee, dur_of.get(sid, 0.0), err_of.get(sid, False))
            # (2) server span linked to its caller via CHILD_OF (real mesh / OTel)
            for ref in (sp.get("references") or []):
                if ref.get("refType") != "CHILD_OF":
                    continue
                parent = svc_of.get(ref.get("spanID"), "")
                _add(parent, caller, dur_of.get(sid, 0.0), err_of.get(sid, False))

    edges: List[TraceEdge] = []
    for (src, dst), a in agg.items():
        ds = sorted(a["d"])
        edges.append(TraceEdge(src=src, dst=dst, call_count=a["c"], error_count=a["e"],
                               p50_ms=_percentile(ds, 0.5), p99_ms=_percentile(ds, 0.99)))
    return edges


class JaegerTraceSource(TraceSource):
    def __init__(self, base_url: str = "http://127.0.0.1:30686", *,
                 lookback_min: int = 2, per_service_limit: int = 40, timeout: int = 8):
        self.base_url = base_url.rstrip("/")
        self.lookback = f"{lookback_min}m"
        self.limit = per_service_limit
        self.timeout = timeout

    def _get(self, path: str) -> Dict[str, Any]:
        try:
            with urllib.request.urlopen(self.base_url + path, timeout=self.timeout) as r:
                return json.load(r)
        except Exception:
            return {}

    def edges(self, namespace: str, env: str) -> List[TraceEdge]:
        services = [s for s in (self._get("/api/services").get("data") or [])
                    if "jaeger" not in s.lower()]
        traces: List[Dict[str, Any]] = []
        for svc in services:
            data = self._get(f"/api/traces?service={svc}&limit={self.limit}&lookback={self.lookback}")
            traces.extend(data.get("data") or [])
        return aggregate_edges(traces)


def trace_latency_by_service(edges: List[TraceEdge]) -> Dict[str, Dict[str, float]]:
    """Per-service inbound signal: max p99 latency and max error-rate among edges
    INTO the service (high inbound latency -> network-delay suspect; high inbound
    error-rate -> network-loss suspect)."""
    out: Dict[str, Dict[str, float]] = {}
    for e in edges or []:
        cur = out.setdefault(e.dst, {"latency_ms": 0.0, "error_pct": 0.0})
        if e.p99_ms is not None:
            cur["latency_ms"] = max(cur["latency_ms"], e.p99_ms)
        if e.call_count:
            cur["error_pct"] = max(cur["error_pct"], 100.0 * e.error_count / e.call_count)
    return out
