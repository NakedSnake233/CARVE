# Boutique frozen-scorer observation-feature deletion

This artifact deletes saved feature families without retraining. `no_log` removes log-error features; `no_trace_features` removes trace latency/error features; `no_metric_features` removes non-log/non-trace summary features; `topology_only` removes every saved telemetry summary and compact call edge.

The saved compact `call_edges` field does not retain whether an edge came from a metric or a trace. Therefore both `no_trace_features` and `no_metric_features` retain mixed-provenance call edges. These two arms are feature deletions rather than complete source-modality deletions. Only `topology_only` removes those edges. This limitation applies to every reported contrast.
