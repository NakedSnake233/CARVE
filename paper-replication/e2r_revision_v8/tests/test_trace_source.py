"""JaegerTraceSource edge aggregation + trace-based RCA (offline, mock traces)."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from rca_bench.trace_sources import (  # noqa: E402
    aggregate_edges, trace_latency_by_service, _callee_from_client_op, _span_is_error)
from rca_bench.interface import MultimodalContext, ServiceMetrics  # noqa: E402
from rca_bench.adapters import _infer_category, MetricHeuristicRCA  # noqa: E402


def _trace(spans, processes):
    return {"processes": processes, "spans": spans}


def _client_trace(op, dur_us, tags=None):
    """A frontend-only trace (Online Boutique v0.3.8 reality): the callee emits NO
    server span; the callee is encoded in the frontend client span's op name."""
    procs = {"p1": {"serviceName": "frontend"}}
    root = {"spanID": "r", "processID": "p1", "operationName": "Recv./", "duration": dur_us + 1000,
            "references": [], "tags": []}
    client = {"spanID": "c", "processID": "p1", "operationName": op, "duration": dur_us,
              "references": [{"refType": "CHILD_OF", "spanID": "r"}], "tags": tags or []}
    return [_trace([root, client], procs)]


def _mock_traces(callee_latency_us, callee_error=False):
    # frontend(p1) --CHILD_OF--> productcatalogservice(p2) span with given latency
    procs = {"p1": {"serviceName": "frontend"}, "p2": {"serviceName": "productcatalogservice"}}
    parent = {"spanID": "a", "processID": "p1", "operationName": "GET /", "duration": 5000,
              "references": [], "tags": []}
    child_tags = [{"key": "error", "value": "true"}] if callee_error else []
    child = {"spanID": "b", "processID": "p2", "operationName": "Recv.", "duration": callee_latency_us,
             "references": [{"refType": "CHILD_OF", "spanID": "a"}], "tags": child_tags}
    return [_trace([parent, child], procs)]


def test_aggregate_edges_latency():
    edges = aggregate_edges(_mock_traces(1_200_000))   # 1.2s callee
    assert len(edges) == 1
    e = edges[0]
    assert e.src == "frontend" and e.dst == "productcatalogservice"
    assert e.p99_ms is not None and e.p99_ms >= 1000     # 1.2s -> 1200ms
    assert e.error_count == 0


def test_aggregate_edges_errors():
    edges = aggregate_edges(_mock_traces(20_000, callee_error=True))
    assert edges[0].error_count == 1 and edges[0].call_count == 1


def test_trace_latency_by_service():
    sig = trace_latency_by_service(aggregate_edges(_mock_traces(1_200_000)))
    assert "productcatalogservice" in sig
    assert sig["productcatalogservice"]["latency_ms"] >= 1000


def test_trace_signal_drives_network_category():
    # high inbound trace latency -> network-delay; high inbound errors -> network-loss
    ctx_delay = MultimodalContext("ns", "online-boutique", ["productcatalogservice", "frontend"],
        metrics={"productcatalogservice": ServiceMetrics("productcatalogservice", trace_latency_ms=1200.0),
                 "frontend": ServiceMetrics("frontend", trace_latency_ms=18.0)})
    assert _infer_category(ctx_delay, "productcatalogservice") == "network-delay"
    assert MetricHeuristicRCA().diagnose(ctx_delay).ranked_services()[0] == "productcatalogservice"

    ctx_loss = MultimodalContext("ns", "online-boutique", ["productcatalogservice"],
        metrics={"productcatalogservice": ServiceMetrics("productcatalogservice", trace_error_pct=45.0)})
    assert _infer_category(ctx_loss, "productcatalogservice") == "network-loss"


def test_callee_from_client_op_mapping():
    # OpenCensus client-span op names -> k8s service names (lowercased <Word>Service)
    assert _callee_from_client_op("Sent.hipstershop.ProductCatalogService.ListProducts") == "productcatalogservice"
    assert _callee_from_client_op("Sent.hipstershop.CurrencyService.Convert") == "currencyservice"
    assert _callee_from_client_op("Sent.hipstershop.CartService.GetCart") == "cartservice"
    assert _callee_from_client_op("Sent.hipstershop.AdService.GetAds") == "adservice"
    # server/root spans are not caller-side -> None
    assert _callee_from_client_op("Recv./") is None
    assert _callee_from_client_op("Recv.hipstershop.AdService.GetAds") is None


def test_client_span_edge_latency():
    # frontend client span to productcatalog at 1.2s, NO callee server span
    edges = aggregate_edges(_client_trace("Sent.hipstershop.ProductCatalogService.ListProducts", 1_200_000))
    e = [x for x in edges if x.dst == "productcatalogservice"]
    assert len(e) == 1 and e[0].src == "frontend"
    assert e[0].p99_ms is not None and e[0].p99_ms >= 1000
    sig = trace_latency_by_service(edges)
    assert sig["productcatalogservice"]["latency_ms"] >= 1000


def test_client_span_grpc_error():
    # gRPC DeadlineExceeded (status.code=4) on a client span -> counted as error
    assert _span_is_error([{"key": "status.code", "value": 4}]) is True
    assert _span_is_error([{"key": "status.code", "value": 0}]) is False
    edges = aggregate_edges(_client_trace(
        "Sent.hipstershop.CartService.GetCart", 50_000, tags=[{"key": "status.code", "value": 14}]))
    e = [x for x in edges if x.dst == "cartservice"][0]
    assert e.error_count == 1 and e.call_count == 1
    assert trace_latency_by_service(edges)["cartservice"]["error_pct"] == 100.0


def test_client_span_drives_network_category():
    # high inbound trace latency from client spans -> network-delay on that callee
    edges = aggregate_edges(_client_trace("Sent.hipstershop.ShippingService.GetQuote", 1_500_000))
    sig = trace_latency_by_service(edges)
    ctx = MultimodalContext("ns", "online-boutique", ["shippingservice", "frontend"],
        metrics={"shippingservice": ServiceMetrics("shippingservice", trace_latency_ms=sig["shippingservice"]["latency_ms"]),
                 "frontend": ServiceMetrics("frontend", trace_latency_ms=9.0)})
    assert _infer_category(ctx, "shippingservice") == "network-delay"


def test_loss_with_retransmit_latency_classified_as_loss():
    # Live regression: 50% packet loss inflates trace latency to ~8.5s via TCP
    # retransmits with 0 gRPC errors. loss_pct (the discriminative signal) must
    # win over the high trace latency -> network-loss, NOT network-delay.
    ctx = MultimodalContext("ns", "online-boutique", ["currencyservice"],
        metrics={"currencyservice": ServiceMetrics(
            "currencyservice", trace_latency_ms=8500.0, trace_error_pct=0.0, loss_pct=50.0, rtt_ms=241.0)})
    assert _infer_category(ctx, "currencyservice") == "network-loss"


def test_pure_delay_no_loss_still_classified_as_delay():
    # Pure tc-netem delay drops no packets -> loss_pct~0; high latency -> network-delay.
    ctx = MultimodalContext("ns", "online-boutique", ["productcatalogservice"],
        metrics={"productcatalogservice": ServiceMetrics(
            "productcatalogservice", trace_latency_ms=1000.9, trace_error_pct=0.0, loss_pct=0.0, rtt_ms=1289.0)})
    assert _infer_category(ctx, "productcatalogservice") == "network-delay"


def test_cluster_overload_not_mislabeled_delay():
    # Whole-fleet high inbound trace latency (cluster overload / residual chaos not yet
    # recovered) must NOT read as network-delay. Regression for v3 rounds 1-2: every
    # frontend-callee crossed the absolute 600ms threshold, so the latency check fired
    # before the not_ready check and masked pod-fail/disk/cpu as network-delay
    # (pod-fail cat-acc collapsed to 0.0). Fleet-relative gate fixes this.
    svcs = ["frontend", "cartservice", "currencyservice", "productcatalogservice",
            "checkoutservice", "shippingservice", "adservice", "emailservice"]
    metrics = {s: ServiceMetrics(s, trace_latency_ms=5000.0) for s in svcs}
    metrics["emailservice"] = ServiceMetrics("emailservice", trace_latency_ms=5000.0, not_ready=1)
    ctx = MultimodalContext("ns", "online-boutique", svcs, metrics=metrics)
    # a slow-but-healthy service is NOT network-delay when the whole fleet is slow
    assert _infer_category(ctx, "cartservice") != "network-delay"
    # the killed pod is still correctly pod-fail (latency no longer short-circuits it)
    assert _infer_category(ctx, "emailservice") == "pod-fail"


def test_single_edge_delay_amid_load_still_detected():
    # One service standing out >=3x above an (already elevated) fleet baseline is a
    # genuine delay fault and must still be detected; a baseline-level service is not.
    svcs = ["frontend", "cartservice", "currencyservice", "productcatalogservice"]
    metrics = {s: ServiceMetrics(s, trace_latency_ms=700.0) for s in svcs}   # baseline ~700ms
    metrics["currencyservice"] = ServiceMetrics("currencyservice", trace_latency_ms=3000.0)
    ctx = MultimodalContext("ns", "online-boutique", svcs, metrics=metrics)
    assert _infer_category(ctx, "currencyservice") == "network-delay"   # 3000 >= 3 x 700
    assert _infer_category(ctx, "cartservice") != "network-delay"       # 700 is just load


def test_multitier_backend_edges_and_unknown_service_filtered():
    # v4 / OTel reality (boutique v0.10.1): each service emits its OWN Sent.* client
    # spans in its own trace, so querying checkoutservice yields checkout->payment and
    # checkout->email edges -- payment/email had NO trace coverage in v3 (frontend-only).
    # Spans from the OTel default process name "unknown_service:*" (before
    # OTEL_SERVICE_NAME is applied) must be dropped, not turned into junk edges.
    procs = {"p1": {"serviceName": "checkoutservice"},
             "p2": {"serviceName": "unknown_service:server"}}
    spans = [
        {"spanID": "a", "processID": "p1", "operationName": "Recv.hipstershop.CheckoutService.PlaceOrder",
         "duration": 50000, "references": [], "tags": []},
        {"spanID": "b", "processID": "p1", "operationName": "Sent.hipstershop.PaymentService.Charge",
         "duration": 3000, "references": [{"refType": "CHILD_OF", "spanID": "a"}], "tags": []},
        {"spanID": "c", "processID": "p1", "operationName": "Sent.hipstershop.EmailService.SendOrderConfirmation",
         "duration": 2000, "references": [{"refType": "CHILD_OF", "spanID": "a"}], "tags": []},
        {"spanID": "d", "processID": "p2", "operationName": "Sent.hipstershop.CurrencyService.Convert",
         "duration": 1000, "references": [], "tags": []},   # junk: unknown_service caller
    ]
    edges = aggregate_edges([{"processes": procs, "spans": spans}])
    pairs = {(e.src, e.dst) for e in edges}
    assert ("checkoutservice", "paymentservice") in pairs
    assert ("checkoutservice", "emailservice") in pairs
    assert not any("unknown_service" in e.src or "unknown_service" in e.dst for e in edges)


# --- cascade / blame-propagation (v4 multi-tier reality) ---------------------
# A dead/slow backend emits no error spans of its OWN; the error/latency surfaces
# on the inbound edges of its CALLER. The caller must not be blamed -- blame flows
# to the deepest faulty node. checkoutservice -> paymentservice is a known edge.
_CHECKOUT_CALLS_PAYMENT = [("checkoutservice", "paymentservice")]


def test_cascade_victim_not_blamed_podfail():
    # pod-fail on payment: payment not_ready + 0 error spans (it's dead); the caller
    # checkout shows high inbound trace_error/latency because ITS call to payment
    # failed. checkout must NOT be read as its own network-loss, and payment (the
    # true root) must outrank it.
    svcs = ["frontend", "checkoutservice", "paymentservice"]
    metrics = {
        "frontend": ServiceMetrics("frontend"),
        "checkoutservice": ServiceMetrics("checkoutservice", trace_error_pct=57.9,
                                          trace_latency_ms=820.0, loss_pct=10.0, rtt_ms=314.0),
        "paymentservice": ServiceMetrics("paymentservice", not_ready=1, trace_error_pct=0.0),
    }
    ctx = MultimodalContext("ns", "online-boutique", svcs, metrics=metrics,
                            topology=_CHECKOUT_CALLS_PAYMENT)
    assert _infer_category(ctx, "checkoutservice") != "network-loss"   # victim, not root
    assert _infer_category(ctx, "paymentservice") == "pod-fail"
    assert MetricHeuristicRCA().diagnose(ctx).ranked_services()[0] == "paymentservice"


def test_cascade_victim_not_blamed_disk():
    # disk-io on payment (slow, callers time out): caller flagged via trace_error must
    # yield to payment's own disk signal.
    svcs = ["checkoutservice", "paymentservice"]
    metrics = {
        "checkoutservice": ServiceMetrics("checkoutservice", trace_error_pct=40.0, loss_pct=10.0),
        "paymentservice": ServiceMetrics("paymentservice", disk_write_ms=8000.0, trace_error_pct=0.0),
    }
    ctx = MultimodalContext("ns", "online-boutique", svcs, metrics=metrics,
                            topology=_CHECKOUT_CALLS_PAYMENT)
    assert _infer_category(ctx, "checkoutservice") != "network-loss"
    assert _infer_category(ctx, "paymentservice") == "disk-io"
    assert MetricHeuristicRCA().diagnose(ctx).ranked_services()[0] == "paymentservice"


def test_true_network_loss_on_leaf_still_detected():
    # Genuine packet loss on payment (a leaf -> no failed callee): its OWN ping
    # loss_pct must still classify it network-loss, and the trace-error cascade onto
    # its caller must be suppressed so the root wins.
    svcs = ["checkoutservice", "paymentservice"]
    metrics = {
        "checkoutservice": ServiceMetrics("checkoutservice", trace_error_pct=45.0, loss_pct=10.0),
        "paymentservice": ServiceMetrics("paymentservice", loss_pct=50.0, trace_error_pct=30.0),
    }
    ctx = MultimodalContext("ns", "online-boutique", svcs, metrics=metrics,
                            topology=_CHECKOUT_CALLS_PAYMENT)
    assert _infer_category(ctx, "paymentservice") == "network-loss"     # own ping loss, unsuppressed
    assert _infer_category(ctx, "checkoutservice") != "network-loss"    # victim of payment's loss
    assert MetricHeuristicRCA().diagnose(ctx).ranked_services()[0] == "paymentservice"


def test_cascade_victim_not_blamed_delay():
    # delay on payment (leaf): payment's own inbound latency -> network-delay; the
    # caller's cascaded latency must be suppressed so payment outranks it. Needs a
    # realistic fleet (most services at baseline) so the fleet median stays low and
    # the genuine root clears the fleet-relative gate.
    svcs = ["frontend", "cartservice", "currencyservice", "adservice",
            "checkoutservice", "paymentservice"]
    metrics = {s: ServiceMetrics(s, trace_latency_ms=2.0) for s in svcs}   # baseline ~2ms
    metrics["checkoutservice"] = ServiceMetrics("checkoutservice", trace_latency_ms=2000.0, rtt_ms=300.0)
    metrics["paymentservice"] = ServiceMetrics("paymentservice", trace_latency_ms=3000.0, loss_pct=0.0)
    ctx = MultimodalContext("ns", "online-boutique", svcs, metrics=metrics,
                            topology=_CHECKOUT_CALLS_PAYMENT)
    assert _infer_category(ctx, "paymentservice") == "network-delay"
    assert _infer_category(ctx, "checkoutservice") != "network-delay"   # cascaded, not own
    assert MetricHeuristicRCA().diagnose(ctx).ranked_services()[0] == "paymentservice"


def test_loss_storm_not_mislabeled_and_root_not_buried():
    # Under benchmark load the external ping path (8.8.8.8) drops packets fleet-wide,
    # so MANY pods read loss_pct ~30% at once. That must NOT each become a network-loss
    # "root" and bury the true fault (regression: the dead pod ranked #8 under ~7
    # spurious-loss services). Fleet-relative gate fixes this.
    svcs = ["frontend", "cartservice", "currencyservice", "productcatalogservice",
            "adservice", "shippingservice", "emailservice", "checkoutservice"]
    metrics = {s: ServiceMetrics(s, loss_pct=30.0) for s in svcs}            # storm
    metrics["checkoutservice"] = ServiceMetrics("checkoutservice", not_ready=1, loss_pct=30.0)
    ctx = MultimodalContext("ns", "online-boutique", svcs, metrics=metrics)
    assert _infer_category(ctx, "cartservice") != "network-loss"            # storm, not a fault
    assert _infer_category(ctx, "checkoutservice") == "pod-fail"            # true root survives
    assert MetricHeuristicRCA().diagnose(ctx).ranked_services()[0] == "checkoutservice"


def test_loss_storm_median_in_teens_does_not_bury_root():
    # The storm that bit calibration v1: ~a third of the fleet spikes to 22-28% while
    # the rest stay low, so the MEDIAN sits ~15% -- below a 20% activation floor, so the
    # relative gate never fired and the 22-28% pods buried the true disk root (rank #6).
    # The 35% floor + always-relative gate must keep the disk root on top.
    loss = {"s1": 5.0, "s2": 8.0, "s3": 10.0, "s4": 12.0, "s5": 15.0, "s6": 18.0, "s7": 12.0,
            "st1": 22.0, "st2": 25.0, "st3": 28.0, "st4": 24.0}
    metrics = {k: ServiceMetrics(k, loss_pct=v) for k, v in loss.items()}
    metrics["disksvc"] = ServiceMetrics("disksvc", disk_write_ms=8000.0, loss_pct=10.0)
    ctx = MultimodalContext("ns", "online-boutique", list(metrics), metrics=metrics)
    assert _infer_category(ctx, "st3") != "network-loss"          # 28% storm pod, not a fault
    assert _infer_category(ctx, "disksvc") == "disk-io"
    assert MetricHeuristicRCA().diagnose(ctx).ranked_services()[0] == "disksvc"


def test_single_injected_loss_amid_calm_fleet_still_detected():
    svcs = ["frontend", "cartservice", "currencyservice", "productcatalogservice"]
    metrics = {s: ServiceMetrics(s, loss_pct=10.0) for s in svcs}            # baseline
    metrics["currencyservice"] = ServiceMetrics("currencyservice", loss_pct=50.0)
    ctx = MultimodalContext("ns", "online-boutique", svcs, metrics=metrics)
    assert _infer_category(ctx, "currencyservice") == "network-loss"        # 50% stands out
    assert _infer_category(ctx, "cartservice") != "network-loss"
    assert MetricHeuristicRCA().diagnose(ctx).ranked_services()[0] == "currencyservice"


def test_single_injected_loss_amid_storm_still_detected():
    # even mid-storm a genuine 50% injection clears the 2x-median bar; a storm-level
    # service does not.
    svcs = ["frontend", "cartservice", "currencyservice", "productcatalogservice", "adservice"]
    metrics = {s: ServiceMetrics(s, loss_pct=22.0) for s in svcs}            # storm baseline ~22%
    metrics["currencyservice"] = ServiceMetrics("currencyservice", loss_pct=50.0)
    ctx = MultimodalContext("ns", "online-boutique", svcs, metrics=metrics)
    assert _infer_category(ctx, "currencyservice") == "network-loss"        # 50 >= 2 x 22
    assert _infer_category(ctx, "cartservice") != "network-loss"            # 22 < 2 x 22
    assert MetricHeuristicRCA().diagnose(ctx).ranked_services()[0] == "currencyservice"


def test_disk_io_with_incidental_packet_loss_stays_disk():
    # disk stress can intermittently inflate the pod's OWN egress ping loss_pct above
    # the 20% threshold (observed 30%); the direct dd-write measure must win so the
    # incident is not mislabeled network-loss.
    svcs = ["paymentservice"]
    metrics = {"paymentservice": ServiceMetrics("paymentservice", disk_write_ms=8000.0, loss_pct=30.0)}
    ctx = MultimodalContext("ns", "online-boutique", svcs, metrics=metrics)
    assert _infer_category(ctx, "paymentservice") == "disk-io"


def test_genuine_loss_with_baseline_disk_still_loss():
    # the reorder must NOT swallow real packet loss: baseline disk (~200ms) < threshold.
    svcs = ["currencyservice"]
    metrics = {"currencyservice": ServiceMetrics("currencyservice", loss_pct=50.0, disk_write_ms=200.0)}
    ctx = MultimodalContext("ns", "online-boutique", svcs, metrics=metrics)
    assert _infer_category(ctx, "currencyservice") == "network-loss"


def test_empty_traces_no_crash():
    assert aggregate_edges([]) == []
    assert trace_latency_by_service([]) == {}


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(); print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)} trace-source tests passed.")


if __name__ == "__main__":
    _run_all()
