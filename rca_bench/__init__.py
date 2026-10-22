"""rca_bench: a full-lifecycle ops-evaluation layer for the MicroRemed benchmark.

Adds, on top of MicroRemed's remediation evaluation:

* a **pluggable RCA algorithm interface** (:mod:`rca_bench.interface`) — implement
  ``BaseRCA.diagnose(ctx) -> RCAResult`` and you are scored;
* a **unified multimodal context** (logs / traces / metrics / k8s-state / topology)
  supplied to every RCA algorithm through one spec (:class:`MultimodalContext`),
  collected read-only from the live cluster by :mod:`rca_bench.context_provider`;
* **RCA metrics** AC@k and MRR (+ failure-category accuracy) in
  :mod:`rca_bench.metrics`;
* an **eval harness** (:mod:`rca_bench.harness`) that runs the whole lifecycle
  inject → collect → diagnose (scored) → remediate (scored) → restore.

Pure-Python, standard library only. The interface/metrics/topology modules are
importable with no cluster and no LLM (for offline tests). Cluster access and the
PlanRCAThinkRemed adapter are imported lazily.
"""

__all__ = ["interface", "metrics", "topology", "context_provider", "adapters", "harness"]
__version__ = "0.1.0"
