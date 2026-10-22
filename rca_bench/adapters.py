"""Concrete RCA algorithms implementing :class:`BaseRCA`.

* ``MetricHeuristicRCA`` — a real telemetry-only baseline: scores each service
  from the unified context (CPU/mem saturation, restarts, not-ready pods, log
  errors, k8s warnings, under-provisioned limits) and infers a category.
* ``PlanRCARCA`` — PlanRCAThinkRemed's deterministic detection applied to the
  SAME context: reuses ``detect_active_chaos`` (only when the harness exposes
  injection artifacts) and ``detect_underprovisioned_config`` (always), then the
  metric heuristic for the rest. This is the method under test.
* ``RandomRCA`` — a reproducible random-permutation lower bound.

All consume only the :class:`MultimodalContext` (no oracle, no extra probing).
"""

from __future__ import annotations

import json
import zlib
from typing import Dict, List, Optional

from .interface import (BaseRCA, MultimodalContext, RCAResult, RootCauseCandidate,
                        ServiceMetrics)

# weights for the telemetry anomaly score. NOTE: pod restartCount is CUMULATIVE
# (not incident-scoped), so it is downweighted; `not_ready` is the incident-
# specific pod-health signal and carries the pod-fail discrimination. net/disk
# are active-probe symptoms (strong, localize network/disk faults).
_W = {"cpu": 1.0, "mem": 1.0, "restart": 0.15, "not_ready": 1.0, "log": 0.3,
      "event": 0.4, "net": 3.0, "disk": 3.0}

# active-probe thresholds (injected: delay +1000ms, loss 50%, disk 5s/IO).
# RTT baseline to 8.8.8.8 is ~250ms on this network, so 600ms cleanly separates
# the +1000ms delay; disk uses a large value set by a timed-out dd probe.
# loss FLOOR is 35% (not 20%): the egress ping to 8.8.8.8 is noisy under cluster
# load and spikes ~half the fleet to 20-30% (baseline ~10%), which a 20% floor read
# as a storm of false "network-loss" that buried real pod-fail/disk roots. A genuine
# tc-netem 50% injection reads ~50% (well clear of 35), so 35 sits between the storm
# ceiling (~30) and the injection (~50); the fleet-relative gate is the second line.
_RTT_MS, _LOSS_PCT, _DISK_MS = 600.0, 35.0, 1500.0
# distributed-trace thresholds (preferred over ping: real inter-service latency,
# baseline ~ms; +1000ms delay -> edge p99 >> 600ms; loss -> high inbound errors).
_TRACE_LAT_MS, _TRACE_ERR_PCT = 600.0, 20.0
# A network-delay fault makes ONE service's inbound trace latency stand out. Cluster-
# wide latency (overload / thrash, e.g. residual chaos not yet recovered) lifts EVERY
# edge together and must NOT read as a delay fault: otherwise, under load, every
# frontend-callee crosses the absolute threshold, floods predictions with
# "network-delay", and -- because `not_ready` is checked AFTER the latency heuristic --
# even masks pod-fail / disk / cpu as network-delay (the v3 rounds-1&2 regression). So
# require BOTH an absolute floor AND -- only when the fleet baseline is itself elevated
# -- standing out >= _TRACE_REL_FACTOR x the fleet median.
_TRACE_REL_FACTOR = 3.0
# The SAME flood hits egress ping loss_pct: the external ping target (8.8.8.8) drops
# packets fleet-wide when the cluster is under benchmark load (loadgen + rapid
# kubectl-exec), lifting MANY pods past the 20% absolute floor at once -> a storm of
# false "network-loss" that buries the true pod-fail/disk root (observed: the dead
# pod ranked #8 under ~7 spurious-loss services). A genuine tc-netem loss spikes ONE
# pod far above that baseline, so gate loss the same way (factor tuned lower than
# trace's 3x: injected 50% vs ~10% baseline / ~20-30% under-load spurious).
_LOSS_REL_FACTOR = 2.0


def _trace_latency_anomalous(ctx: MultimodalContext, m: Optional["ServiceMetrics"]) -> bool:
    """True iff ``m``'s inbound trace latency is high *and* anomalous relative to the
    fleet (so cluster-wide slowness does not masquerade as a single-service delay)."""
    if m is None or m.trace_latency_ms is None or m.trace_latency_ms < _TRACE_LAT_MS:
        return False
    vals = sorted(mm.trace_latency_ms for mm in ctx.metrics.values()
                  if mm.trace_latency_ms is not None)
    if len(vals) >= 3:
        median = vals[len(vals) // 2]
        if median >= _TRACE_LAT_MS and m.trace_latency_ms < _TRACE_REL_FACTOR * median:
            return False   # whole fleet is slow -> overload, not a single-edge delay
    return True


def _loss_anomalous(ctx: MultimodalContext, m: Optional["ServiceMetrics"]) -> bool:
    """True iff ``m``'s egress packet loss is high *and* anomalous relative to the
    fleet, so a load-induced fleet-wide ping-loss storm does not masquerade as a
    single-service packet-loss fault. Mirror of :func:`_trace_latency_anomalous`."""
    if m is None or m.loss_pct is None or m.loss_pct < _LOSS_PCT:
        return False
    vals = sorted(mm.loss_pct for mm in ctx.metrics.values() if mm.loss_pct is not None)
    if len(vals) >= 3:
        median = vals[len(vals) // 2]
        # ALWAYS relative to the live median (floored at the absolute _LOSS_PCT via the
        # early return). The external ping baseline (~10%) sits just under the floor, so
        # a load storm lifts the MEDIAN only into the teens (rarely past 20) while
        # spiking ~half the fleet past 20% -- an activation condition of median>=_LOSS_PCT
        # would almost never fire and let the storm through (observed: a no-op gate, disk
        # pod buried at rank #6). Effective bar = max(_LOSS_PCT, _LOSS_REL_FACTOR*median):
        # a genuine 50% injection clears it; storm-level 20-30% pods do not.
        if m.loss_pct < _LOSS_REL_FACTOR * median:
            return False
    return True


def _callees(ctx: MultimodalContext, svc: str) -> set:
    """Services that ``svc`` calls (from the merged trace/static call graph)."""
    out = {dst for (src, dst) in ctx.topology if src == svc}
    out |= {e.dst for e in ctx.traces if e.src == svc}
    out.discard(svc)
    return out


def _downstream_failure(ctx: MultimodalContext, svc: str) -> bool:
    """True iff a service ``svc`` CALLS is itself failed/anomalous.

    Multi-tier tracing surfaces a failure at the first *living* ancestor: when a
    backend dies (pod-fail) or stalls (disk/cpu/mem), it emits no error spans of
    its own, so the error/latency lands on the inbound edges of its *callers*. A
    caller is then a cascade VICTIM, not a network-fault root -- its trace-based
    signal must not be read as its own fault. We detect that by checking whether
    any callee is itself anomalous, so blame propagates to the deepest faulty
    node along the call graph. Uses only observed health + the app topology (no
    oracle / chaos selector). Pure ping signals (rtt/loss) are the caller's OWN
    measurements and are *not* gated by this."""
    for c in _callees(ctx, svc):
        cm = ctx.metrics.get(c)
        if cm is None:
            continue
        if cm.not_ready:
            return True
        if (cm.cpu_ratio or 0.0) >= 0.5 or (cm.mem_ratio or 0.0) >= 0.5:
            return True
        if cm.cpu_limit_millicores is not None and cm.cpu_limit_millicores <= 5:
            return True
        if cm.disk_write_ms is not None and cm.disk_write_ms >= _DISK_MS:
            return True
        if _loss_anomalous(ctx, cm):
            return True
        if cm.trace_error_pct is not None and cm.trace_error_pct >= _TRACE_ERR_PCT:
            return True
        if _trace_latency_anomalous(ctx, cm):
            return True
    return False


def _anomaly_scores(ctx: MultimodalContext) -> Dict[str, float]:
    scores: Dict[str, float] = {s: 0.0 for s in ctx.services}
    max_restart = max([m.restarts for m in ctx.metrics.values()] + [1])
    max_log = max([l.error_count for l in ctx.logs.values()] + [1])
    ev_by_svc: Dict[str, int] = {}
    for e in ctx.k8s_events:
        obj = e.get("object") or ""
        for s in ctx.services:
            if obj.startswith(s):
                ev_by_svc[s] = ev_by_svc.get(s, 0) + int(e.get("count") or 1)
    max_ev = max(list(ev_by_svc.values()) + [1])
    for s in ctx.services:
        m = ctx.metrics.get(s)
        sc = 0.0
        if m:
            if m.cpu_ratio is not None:
                sc += _W["cpu"] * min(m.cpu_ratio, 2.0)
            if m.mem_ratio is not None:
                sc += _W["mem"] * min(m.mem_ratio, 2.0)
            sc += _W["restart"] * (m.restarts / max_restart)
            sc += _W["not_ready"] * (1.0 if m.not_ready else 0.0)
            if ((m.trace_error_pct is not None and m.trace_error_pct >= _TRACE_ERR_PCT) or
                    _trace_latency_anomalous(ctx, m)) and not _downstream_failure(ctx, s):
                sc += _W["net"]   # trace error/latency = own fault ONLY if no callee failed
            if _loss_anomalous(ctx, m):     # fleet-relative: not a load-wide loss storm
                sc += _W["net"]
            if m.rtt_ms is not None and m.rtt_ms >= _RTT_MS:
                sc += _W["net"]
            if m.disk_write_ms is not None and m.disk_write_ms >= _DISK_MS:
                sc += _W["disk"]
        log = ctx.logs.get(s)
        if log:
            sc += _W["log"] * (log.error_count / max_log)
        sc += _W["event"] * (ev_by_svc.get(s, 0) / max_ev)
        scores[s] = sc
    return scores


def _infer_category(ctx: MultimodalContext, svc: str) -> Optional[str]:
    # Order matters: the SPECIFIC stress signals (cpu/mem saturation, tiny limit)
    # take priority over generic pod-health, because a CPU/mem-starved pod also
    # goes not-ready -- so checking not_ready first would mislabel stress as
    # pod-fail. (restartCount is cumulative -> not used for categorization.)
    m = ctx.metrics.get(svc)
    if m:
        if m.cpu_limit_millicores is not None and m.cpu_limit_millicores <= 5:
            return "pod-config-error"
        cpu_r, mem_r = (m.cpu_ratio or 0.0), (m.mem_ratio or 0.0)
        if mem_r >= 0.5 and mem_r >= cpu_r:
            return "memory-stress"
        if cpu_r >= 0.5:
            return "cpu-stress"
        # Disk before the network checks: a timed dd write is a DIRECT disk measure,
        # whereas disk stress can intermittently inflate this pod's egress ping
        # loss_pct (observed 30% vs 10% baseline) -- a side effect that would else
        # mislabel disk-io as network-loss. A real packet-loss fault leaves disk
        # writes at baseline (~200ms < threshold), so this stays correct for loss.
        if m.disk_write_ms is not None and m.disk_write_ms >= _DISK_MS:
            return "disk-io"
        # Network-loss indicators (app-level errors OR measured packet loss) are
        # checked BEFORE latency: 50% packet loss inflates trace latency via TCP
        # retransmits (observed ~8.5s with 0 gRPC errors), so a latency-first check
        # mislabels loss as delay. Packet loss is the discriminative signal -- pure
        # tc-netem delay drops no packets (loss_pct~0), so this stays correct for
        # delay. Distributed-trace error% preferred; ping loss_pct as fallback.
        # A caller whose inbound edges error because a downstream callee is itself
        # failed is a cascade VICTIM -- skip the trace-error -> network-loss read so
        # blame stays on the deeper faulty node (its own ping loss_pct below still
        # catches a genuine packet-loss fault on THIS service).
        cascade = _downstream_failure(ctx, svc)
        if m.trace_error_pct is not None and m.trace_error_pct >= _TRACE_ERR_PCT and not cascade:
            return "network-loss"
        if _loss_anomalous(ctx, m):
            return "network-loss"
        # delay = high latency WITHOUT loss (trace inter-service p99 preferred, ping rtt
        # fallback). The trace check is fleet-relative (see _trace_latency_anomalous):
        # cluster-wide latency must NOT be read as a single-service delay, else it
        # short-circuits the pod-fail / disk checks below (rounds-1&2 v3 regression).
        if _trace_latency_anomalous(ctx, m) and not cascade:
            return "network-delay"
        if m.rtt_ms is not None and m.rtt_ms >= _RTT_MS:
            return "network-delay"
        if m.not_ready:
            return "pod-fail"
    log = ctx.logs.get(svc)
    if log and log.error_count > 0:
        return "network-loss"   # last-resort when no probe data: connection errors
    return None


class MetricHeuristicRCA(BaseRCA):
    name = "metric"

    def diagnose(self, ctx: MultimodalContext) -> RCAResult:
        scores = _anomaly_scores(ctx)
        ranking = [RootCauseCandidate(service=s, score=round(sc, 4),
                                      failure_category=_infer_category(ctx, s))
                   for s, sc in scores.items()]
        ranking.sort(key=lambda c: c.score, reverse=True)
        top = ranking[0] if ranking else None
        return RCAResult(ranking=ranking, method=self.name,
                         predicted_category=(top.failure_category if top else None))


class PlanRCARCA(BaseRCA):
    """PlanRCAThinkRemed's deterministic RCA over the unified context."""
    name = "planrca"

    def diagnose(self, ctx: MultimodalContext) -> RCAResult:
        try:
            from methods.PlanRCAThinkRemed.evidence_agents import (
                detect_active_chaos, detect_underprovisioned_config)
        except Exception:
            detect_active_chaos = detect_underprovisioned_config = None

        scores = _anomaly_scores(ctx)
        category: Dict[str, str] = {}

        # 1) config-error: under-provisioned deployments (always-on, telemetry-fair)
        if detect_underprovisioned_config is not None:
            deploy_blob = json.dumps(ctx.raw.get("deploy_json") or {})
            for ev in detect_underprovisioned_config(deploy_blob):
                if ev.service:
                    scores[ev.service] = max(scores.get(ev.service, 0.0), 5.0)
                    category[ev.service] = "pod-config-error"

        # 2) injection artifacts (only if the harness chose to expose them)
        if detect_active_chaos is not None and ctx.raw.get("chaos_json"):
            chaos_blob = json.dumps(ctx.raw.get("chaos_json"))
            for ev in detect_active_chaos(chaos_blob):
                if ev.service:
                    scores[ev.service] = max(scores.get(ev.service, 0.0), 10.0)

        ranking: List[RootCauseCandidate] = []
        for s, sc in scores.items():
            cat = category.get(s) or _infer_category(ctx, s)
            ranking.append(RootCauseCandidate(service=s, score=round(sc, 4), failure_category=cat))
        ranking.sort(key=lambda c: c.score, reverse=True)
        top = ranking[0] if ranking else None
        return RCAResult(ranking=ranking, method=self.name,
                         predicted_category=(top.failure_category if top else None))


class RandomRCA(BaseRCA):
    name = "random"

    def diagnose(self, ctx: MultimodalContext) -> RCAResult:
        import random
        seed = zlib.crc32(("|".join(ctx.services) + ctx.namespace).encode())
        rng = random.Random(seed)
        svcs = list(ctx.services)
        rng.shuffle(svcs)
        ranking = [RootCauseCandidate(service=s, score=float(len(svcs) - i))
                   for i, s in enumerate(svcs)]
        return RCAResult(ranking=ranking, method=self.name)


from .llm_rca import LLMRCA  # noqa: E402  (after deterministic helpers are defined)

RCA_REGISTRY = {
    "planrca": PlanRCARCA,
    "metric": MetricHeuristicRCA,
    "random": RandomRCA,
    "llmrca": LLMRCA,
}


def get_rca(name: str) -> BaseRCA:
    key = (name or "planrca").strip().lower()
    if key not in RCA_REGISTRY:
        raise ValueError(f"unknown rca method '{name}'; choose from {sorted(RCA_REGISTRY)}")
    return RCA_REGISTRY[key]()
