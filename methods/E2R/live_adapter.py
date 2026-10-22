"""Frozen E2R graph scorer on paired, oracle-hidden live snapshots.

This is the graph-only transfer arm. It does not call the LLM graph-proposal
stage and must be named separately from full E2R in method comparisons.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from methods.E2R.data import timestamp
from methods.E2R.graph import graphs_for, tensors, use_upstream
from methods.E2R.paths import default_dyrca_root
from rca_bench.interface import RCAResult, RootCauseCandidate


ROOT = Path(__file__).resolve().parents[2]


def _stats(value: float) -> dict:
    return {"mean": float(value), "max": float(value), "p95": float(value), "n": 1}


def observation_from_snapshots(case_dir: str | Path) -> dict:
    case_dir = Path(case_dir)
    pre = json.loads((case_dir / "pre.json").read_text())
    during = json.loads((case_dir / "during.json").read_text())
    pre_topology = json.loads((case_dir / "pre_topology.json").read_text())
    during_topology = json.loads((case_dir / "during_topology.json").read_text())
    if pre["services"] != during["services"]:
        raise ValueError("candidate universe differs between snapshots")
    candidates = list(during["services"])
    observed_metrics = {}
    for service in candidates:
        stats = {}
        for phase, context in (("pre", pre), ("during", during)):
            metric = context.get("metrics", {}).get(service) or {}
            log = context.get("logs", {}).get(service) or {}
            cpu = metric.get("cpu_millicores")
            if cpu is not None:
                stats[f"{phase}:cpu_usage"] = _stats(float(cpu) / 1000.0)
            total, ready = metric.get("total_pods") or 0, metric.get("ready_pods") or 0
            if total:
                stats[f"{phase}:service_up"] = _stats(float(ready) / total)
            latency = metric.get("trace_latency_ms")
            if latency is not None:
                stats[f"{phase}:latency_p99"] = _stats(latency)
                stats[f"{phase}:trace_ms"] = _stats(latency)
            error = metric.get("trace_error_pct")
            if error is not None:
                stats[f"{phase}:trace_error"] = _stats(float(error) / 100.0)
            lines = log.get("total_lines") or 0
            if lines:
                stats[f"{phase}:log_error"] = _stats((log.get("error_count") or 0) / lines)
        observed_metrics[service] = stats

    call_edges = {}
    for phase, context in (("pre", pre), ("during", during)):
        call_edges[phase] = [{"source": src, "target": dst, "count": 1}
                             for src, dst in context.get("topology", [])
                             if src in candidates and dst in candidates]
    start, cutoff = timestamp(pre_topology["timestamp"]), timestamp(during_topology["timestamp"])
    if cutoff <= start:
        raise ValueError("during snapshot must follow pre snapshot")
    return {"case_id": case_dir.name, "candidates": candidates,
            "pre": pre_topology, "during": during_topology,
            "observed_metrics": observed_metrics, "call_edges": call_edges,
            "window": {"start": start, "baseline_end": start, "cutoff": cutoff,
                       "max_used_timestamp": cutoff},
            "coverage": {"pre": pre.get("raw", {}).get("collector_coverage") or {},
                         "during": during.get("raw", {}).get("collector_coverage") or {}}}


class E2RGraphLiveRCA:
    """Evaluate one frozen, patch-trained seed on a new live context bundle."""

    def __init__(self, seed: int = 42, checkpoint: str | Path | None = None):
        use_upstream(default_dyrca_root())
        import torch
        from dyrca_rca.models.graphsage import WeightedGraphSAGE
        self.torch = torch
        self.seed = seed
        self.checkpoint = Path(checkpoint or ROOT / f"experiments/e2r/runs/gpu_full_4090d/model_seed{seed}.pt")
        blob = torch.load(self.checkpoint, map_location="cpu", weights_only=True)
        self.model = WeightedGraphSAGE(blob["in_dim"], hidden_dim=128,
                                       num_layers=2, dropout=0.1)
        self.model.load_state_dict(blob["state_dict"])
        self.model.eval()
        self.last_diagnostics = {"seed": seed, "checkpoint": str(self.checkpoint),
                                 "llm_used": False}

    def diagnose_bundle(self, case_dir: str | Path) -> RCAResult:
        started = time.perf_counter()
        obs = observation_from_snapshots(case_dir)
        variants, catalog, _ = graphs_for(obs)
        item = tensors(variants["patch"], obs)
        with self.torch.no_grad():
            logits = self.model(item["x"], item["edge_index"],
                                edge_weight=item["edge_weight"])
            scores = logits[item["candidate_indices"]].tolist()
        ranking = [RootCauseCandidate(service=service, score=float(score))
                   for service, score in zip(item["candidate_services"], scores)]
        ranking.sort(key=lambda x: (-x.score, x.service))
        self.last_diagnostics = {"seed": self.seed, "checkpoint": str(self.checkpoint),
                                 "llm_used": False, "catalog_operations": len(catalog),
                                 "materialization_and_inference_seconds": time.perf_counter() - started}
        return RCAResult(ranking=ranking, predicted_category=None,
                         method=f"e2r_graph_s{self.seed}")
