"""RCA interface + adapter contract tests on a synthetic context (no cluster)."""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from rca_bench.interface import MultimodalContext, ServiceMetrics, ServiceLogs, RCAResult, RootCauseCandidate  # noqa: E402
from rca_bench.adapters import MetricHeuristicRCA, RandomRCA, PlanRCARCA, get_rca  # noqa: E402


def _ctx_cpu():
    # B is CPU-saturated -> should be the top suspect
    return MultimodalContext(
        namespace="online-boutique", env="online-boutique",
        services=["A", "B", "C"],
        metrics={
            "A": ServiceMetrics("A", cpu_ratio=0.05, mem_ratio=0.1, restarts=0, ready_pods=1, total_pods=1),
            "B": ServiceMetrics("B", cpu_ratio=0.95, mem_ratio=0.2, restarts=0, ready_pods=1, total_pods=1),
            "C": ServiceMetrics("C", cpu_ratio=0.07, mem_ratio=0.1, restarts=0, ready_pods=1, total_pods=1),
        },
        logs={s: ServiceLogs(s) for s in ("A", "B", "C")},
    )


def test_result_normalization_and_accessors():
    r = RCAResult(ranking=[RootCauseCandidate("A", 0.2), RootCauseCandidate("B", 0.9),
                           RootCauseCandidate("B", 0.5)])
    assert r.ranked_services() == ["B", "A"]      # sorted desc + deduped
    assert r.top().service == "B"
    assert isinstance(json.dumps(r.to_dict()), str)


def test_metric_heuristic_picks_cpu_service():
    res = MetricHeuristicRCA().diagnose(_ctx_cpu()).normalized()
    assert res.ranked_services()[0] == "B"
    assert res.predicted_category == "cpu-stress"


def test_random_is_deterministic_and_complete():
    ctx = _ctx_cpu()
    a = RandomRCA().diagnose(ctx).ranked_services()
    b = RandomRCA().diagnose(ctx).ranked_services()
    assert a == b                      # reproducible
    assert set(a) == set(ctx.services) # ranks every candidate


def test_planrca_detects_config_error_from_context():
    deploy_json = {"kind": "List", "items": [
        {"kind": "Deployment", "metadata": {"name": "A"},
         "spec": {"template": {"spec": {"containers": [
             {"name": "server", "resources": {"limits": {"cpu": "2m", "memory": "200Mi"}}}]}}}},
        {"kind": "Deployment", "metadata": {"name": "B"},
         "spec": {"template": {"spec": {"containers": [
             {"name": "server", "resources": {"limits": {"cpu": "300m", "memory": "256Mi"}}}]}}}},
    ]}
    ctx = MultimodalContext(
        namespace="online-boutique", env="online-boutique", services=["A", "B"],
        metrics={"A": ServiceMetrics("A", cpu_limit_millicores=2),
                 "B": ServiceMetrics("B", cpu_limit_millicores=300)},
        raw={"deploy_json": deploy_json},
    )
    res = PlanRCARCA().diagnose(ctx).normalized()
    assert res.ranked_services()[0] == "A"
    assert res.predicted_category == "pod-config-error"


def test_planrca_reads_chaos_when_exposed():
    chaos_json = {"kind": "List", "items": [
        {"kind": "StressChaos", "metadata": {"name": "cpu-stress"},
         "spec": {"selector": {"labelSelectors": {"app": "C"}}, "stressors": {"cpu": {"workers": 10}}}}]}
    ctx = MultimodalContext(
        namespace="online-boutique", env="online-boutique", services=["A", "B", "C"],
        metrics={s: ServiceMetrics(s) for s in ("A", "B", "C")},
        raw={"deploy_json": {"items": []}, "chaos_json": chaos_json})
    res = PlanRCARCA().diagnose(ctx).normalized()
    assert res.ranked_services()[0] == "C"   # chaos selector names the culprit


def test_category_inference_prioritizes_stress_over_not_ready():
    # A CPU/mem-starved pod ALSO goes not-ready; category must reflect the
    # specific stress, not be mislabeled pod-fail.
    from rca_bench.adapters import _infer_category
    cpu = MultimodalContext("ns", "online-boutique", ["A"],
        metrics={"A": ServiceMetrics("A", cpu_ratio=0.9, mem_ratio=0.2, not_ready=1)})
    mem = MultimodalContext("ns", "online-boutique", ["A"],
        metrics={"A": ServiceMetrics("A", cpu_ratio=0.1, mem_ratio=0.8, not_ready=1)})
    podfail = MultimodalContext("ns", "online-boutique", ["A"],
        metrics={"A": ServiceMetrics("A", cpu_ratio=0.05, mem_ratio=0.05, not_ready=1, restarts=20)})
    cfg = MultimodalContext("ns", "online-boutique", ["A"],
        metrics={"A": ServiceMetrics("A", cpu_limit_millicores=2, not_ready=1)})
    assert _infer_category(cpu, "A") == "cpu-stress"
    assert _infer_category(mem, "A") == "memory-stress"
    assert _infer_category(podfail, "A") == "pod-fail"
    assert _infer_category(cfg, "A") == "pod-config-error"


def test_registry():
    assert isinstance(get_rca("planrca"), PlanRCARCA)
    assert isinstance(get_rca("metric"), MetricHeuristicRCA)
    try:
        get_rca("nope"); assert False
    except ValueError:
        pass


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(); print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)} RCA-interface tests passed.")


if __name__ == "__main__":
    _run_all()
