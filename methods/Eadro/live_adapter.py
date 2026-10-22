"""Strict live-snapshot tensor adapter for the isolated Eadro-style model.

The source model under DY-RCA baselines is a PyTorch reproduction of Eadro's
multimodal encoder and service-dependency locator. This adapter supplies only
pre/during telemetry and pre-fault topology; it never reads post-state or the
hidden target while building features.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import torch


METRIC_NAMES = ["request_rate_in", "request_rate_out", "latency_p50",
                "latency_p99", "cpu_usage", "replica_count", "service_up",
                "response_flags"]
TRACE_NAMES = ["span_count", "error_rate", "avg_duration", "p95_duration"]
LOG_NAMES = ["error_count", "total_count", "error_rate", "has_error"]
TOPOLOGY_NAMES = ["replicas_pre", "endpoint_pre", "static_in_degree",
                  "static_out_degree"]


def build_item(case_dir: str | Path, services: list[str]) -> dict:
    case_dir = Path(case_dir)
    pre = json.loads((case_dir / "pre.json").read_text())
    during = json.loads((case_dir / "during.json").read_text())
    pre_topology = json.loads((case_dir / "pre_topology.json").read_text())
    if pre["services"] != services or during["services"] != services:
        raise ValueError("candidate universe differs from frozen service list")
    count = len(services)
    metrics = np.zeros((count, 2, len(METRIC_NAMES)), dtype=np.float32)
    traces = np.zeros((count, 3, len(TRACE_NAMES)), dtype=np.float32)
    logs = np.zeros((count, len(LOG_NAMES)), dtype=np.float32)
    topology = np.zeros((count, len(TOPOLOGY_NAMES)), dtype=np.float32)
    pre_deploy = {x.get("name"): x for x in pre_topology.get("deployments", [])}
    pre_services = {x.get("name"): x for x in pre_topology.get("services", [])}
    indeg, outdeg = {}, {}
    for source, dest in pre.get("topology", []):
        outdeg[source] = outdeg.get(source, 0) + 1
        indeg[dest] = indeg.get(dest, 0) + 1
    trace_by_dest = {}
    for edge in during.get("traces", []):
        dest = edge.get("dst")
        if dest not in services:
            continue
        agg = trace_by_dest.setdefault(dest, {"count": 0, "errors": 0, "durations": []})
        agg["count"] += int(edge.get("call_count") or 0)
        agg["errors"] += int(edge.get("error_count") or 0)
        if edge.get("p99_ms") is not None:
            agg["durations"].append(float(edge["p99_ms"]))
    for index, service in enumerate(services):
        for phase_index, context in enumerate((pre, during)):
            metric = context.get("metrics", {}).get(service) or {}
            values = metrics[index, phase_index]
            if metric.get("trace_latency_ms") is not None:
                values[3] = float(metric["trace_latency_ms"])
            if metric.get("cpu_millicores") is not None:
                values[4] = float(metric["cpu_millicores"]) / 1000.0
            values[5] = float(metric.get("total_pods") or 0)
            total = metric.get("total_pods") or 0
            values[6] = (float(metric.get("ready_pods") or 0) / total) if total else 0.0
            if metric.get("trace_error_pct") is not None:
                values[7] = float(metric["trace_error_pct"]) / 100.0
        trace = trace_by_dest.get(service) or {}
        calls = trace.get("count") or 0
        durations = trace.get("durations") or []
        traces[index, 1] = [calls, trace.get("errors", 0) / calls if calls else 0.0,
                            sum(durations) / len(durations) if durations else 0.0,
                            max(durations) if durations else 0.0]
        log = during.get("logs", {}).get(service) or {}
        errors, lines = log.get("error_count") or 0, log.get("total_lines") or 0
        logs[index] = [errors, lines, errors / lines if lines else 0.0,
                       1.0 if errors else 0.0]
        dep = pre_deploy.get(service) or {}
        svc = pre_services.get(service) or {}
        topology[index] = [dep.get("replicas") or 0,
                           len(svc.get("endpoint_ips") or []),
                           indeg.get(service, 0), outdeg.get(service, 0)]
    return {"metrics": metrics, "traces": traces, "logs": logs,
            "topology": topology}


def edge_index_from_pre(case_dir: str | Path, services: list[str]) -> torch.Tensor:
    pre = json.loads((Path(case_dir) / "pre.json").read_text())
    lookup = {service: index for index, service in enumerate(services)}
    pairs = {(index, index) for index in range(len(services))}
    for source, dest in pre.get("topology", []):
        if source in lookup and dest in lookup:
            pairs.add((lookup[source], lookup[dest]))
            pairs.add((lookup[dest], lookup[source]))
    return torch.tensor(sorted(pairs), dtype=torch.long).t().contiguous()
