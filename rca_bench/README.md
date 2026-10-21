# rca_bench — full-lifecycle ops evaluation for MicroRemed

Extends MicroRemed from *remediation-only* to **RCA + remediation**:

- a **pluggable RCA algorithm interface** (`interface.BaseRCA`),
- a **unified multimodal context** (metrics / logs / traces / k8s-state / topology)
  supplied to every algorithm through one spec (`interface.MultimodalContext`),
- **RCA metrics** AC@k and MRR (+ failure-category accuracy) (`metrics`),
- an **eval harness** that runs inject → collect → diagnose(scored) → remediate(scored) → restore (`harness`).

Standard library only. `interface` / `metrics` / `topology` / `adapters` import
with no cluster and no LLM (offline-testable); cluster access and the
PlanRCAThinkRemed detectors are lazy.

## 1. Plug in an RCA algorithm

```python
from rca_bench.interface import BaseRCA, MultimodalContext, RCAResult, RootCauseCandidate

class MyRCA(BaseRCA):
    name = "my-rca"
    def diagnose(self, ctx: MultimodalContext) -> RCAResult:
        # rank every candidate service from the telemetry; NO oracle access
        ranking = [RootCauseCandidate(service=s, score=my_score(ctx, s),
                                      failure_category=my_category(ctx, s))
                   for s in ctx.services]
        return RCAResult(ranking=ranking, predicted_category=...)
```

Register it in `adapters.RCA_REGISTRY` (or pass an instance to the harness) and
select with `--rca-method my-rca`.

## 2. The unified input spec: `MultimodalContext`

| field | modality | source |
|---|---|---|
| `services` | — | candidate root-cause set (deployments / `app` labels) |
| `metrics[svc]` → `ServiceMetrics` | metrics | `kubectl top` + pod status: `cpu/mem_ratio`, `restarts`, `not_ready`, limits |
| `logs[svc]` → `ServiceLogs` | logs | `kubectl logs`: `error_count`, samples |
| `traces` → `list[TraceEdge]` | traces | pluggable `TraceSource` (Jaeger/OTel); empty if none |
| `topology` → `list[(caller,callee)]` | topology | from traces, else known static graph |
| `k8s_events` | k8s_state | `kubectl get events` (Warnings) |
| `raw` | — | escape hatch (`deploy_json`, and `chaos_json` only in artifact-exposed mode) |

Accessors: `get_metrics(svc)`, `get_logs(svc)`, `downstream_of(svc)`, `upstream_of(svc)`, `candidate_services()`.

**Fairness.** The context carries **no oracle labels**; `incident_summary` is a
generic alert. By default the harness **hides the injection artifacts** (Chaos
Mesh CRDs) so RCA is a genuine telemetry task, not "read the chaos object's
name". Deployment resource limits *are* exposed (a config-error legitimately
manifests as an abnormally low `cpu` limit). Pass `--expose-artifacts` to expose
the chaos CRDs for an upper-bound comparison.

## 3. Metrics

- **AC@k** (Accuracy@k): fraction of incidents whose true root-cause service is
  within the top-`k` predictions. Reported for k ∈ {1,3,5}.
- **MRR**: mean of `1/rank` of the true service (0 if absent).
- **category accuracy**: predicted vs true `failure_category`.
- All also broken down **per failure category**.

## 4. Built-in algorithms

| `--rca-method` | description |
|---|---|
| `planrca` | PlanRCAThinkRemed's deterministic detection over the context: under-provisioned-config detection (always) + chaos-selector (only if exposed) + metric/restart/log heuristics. |
| `metric` | telemetry-only baseline: anomaly score from cpu/mem ratio, restarts, not-ready, log errors, warnings. |
| `random` | reproducible random-permutation lower bound. |

## 5. Run

```bash
conda activate microremed
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
cd ${ARTIFACT_ROOT}

# RCA-only evaluation (fast): score AC@k / MRR over the easy set
python -m rca_bench.cli \
  --namespace online-boutique --env online-boutique \
  --manifest-path ${MICROREMED_ROOT}/envs/source-config/online-boutique-config.yaml \
  --experiment-path ${MICROREMED_ROOT}/experiments/easy.txt \
  --rca-method planrca --out conversations/rca_eval_planrca.json

# full lifecycle: also run + score remediation
python -m rca_bench.cli ... --remediate

# upper bound: expose the chaos CRDs to RCA
python -m rca_bench.cli ... --expose-artifacts
```

Output: a metrics table (overall + per-category AC@1/3/5, MRR, category-acc),
plus optional remediation success rate, and a per-incident JSON (`--out`).

## 6. Notes / limitations

- **Telemetry observability of each fault**: cpu/mem-stress and pod-config-error
  are strongly localizable from `metrics` alone; pod-fail from restarts/not-ready
  + events; **network-delay/loss are hard from metrics alone** and benefit from a
  real `TraceSource` (latency/error edges) — wire one via `LiveClusterContextProvider(trace_source=...)`.
- The RCA score reflects diagnosis from the *standardized context*; it is separate
  from PlanRCAThinkRemed's remediation success (which may additionally read the
  live chaos object during its own repair phase).
- `tests/test_rca_metrics.py`, `tests/test_rca_interface.py` cover the metrics and
  the interface/adapters offline (no cluster).
