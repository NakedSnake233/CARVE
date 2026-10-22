# M4 baseline and runtime independent checks

Deterministic consistency: **PASS**. Overall scoped review: **WARN / same-family provisional**, because static evidence and a no-fault pilot do not establish recovery efficacy or authenticate historical execution. No numerical or saved-identity mismatch was found.

Only audit artifacts were written. No network, Kubernetes/API, GPU, fault, repair or benchmark action was performed.

## Metric recomputation

Each p95 is the nearest-rank order statistic `sorted(latencies)[ceil(0.95*N)-1]`. Per-window throughput is successes/elapsed seconds; baseline throughput is the median of those six values. Pooled p95 uses all 900 latencies. These agree with current formula code at `scripts/e2r/run_recovery_baseline.py:118` and `:123`.

| Run/window | Count | Recomputed p95 ms | Recomputed throughput rps | Error rate | Raw evidence |
|---|---:|---:|---:|---:|---|
| v1/baseline-0 | 150 | 17.989250365645 | 4.997328442080 | 0.0 | `experiments/e2r/runs/recovery_baseline_v1/baseline.json:107` |
| v1/baseline-1 | 150 | 16.900299116969 | 4.997065630290 | 0.0 | `experiments/e2r/runs/recovery_baseline_v1/baseline.json:268` |
| v1/baseline-2 | 150 | 17.157181166112 | 4.997205189581 | 0.0 | `experiments/e2r/runs/recovery_baseline_v1/baseline.json:429` |
| v1/baseline-3 | 150 | 16.987927258015 | 4.997266239212 | 0.0 | `experiments/e2r/runs/recovery_baseline_v1/baseline.json:590` |
| v1/baseline-4 | 150 | 16.992858611047 | 4.997304665268 | 0.0 | `experiments/e2r/runs/recovery_baseline_v1/baseline.json:751` |
| v1/baseline-5 | 150 | 17.747113946825 | 4.997146873925 | 0.0 | `experiments/e2r/runs/recovery_baseline_v1/baseline.json:912` |
| **v1/pooled** | **900** | **17.336041200906** | **4.997235714397 (median)** | **0.0** | `experiments/e2r/runs/recovery_baseline_v1/baseline.json:22` |
| v2/baseline-0 | 150 | 18.263094127178 | 4.997063653630 | 0.0 | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:107` |
| v2/baseline-1 | 150 | 18.072161823511 | 4.997126865430 | 0.0 | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:268` |
| v2/baseline-2 | 150 | 18.602617084980 | 4.997210703600 | 0.0 | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:429` |
| v2/baseline-3 | 150 | 18.550334032625 | 4.997201054870 | 0.0 | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:590` |
| v2/baseline-4 | 150 | 18.489612266421 | 4.997016130464 | 0.0 | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:751` |
| v2/baseline-5 | 150 | 17.430427949876 | 4.997695617962 | 0.0 | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:912` |
| **v2/pooled** | **900** | **18.263094127178** | **4.997163960150 (median)** | **0.0** | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:22` |

All 12 arrays contain exactly 150 finite nonnegative values. Every stored sample count, p95, throughput and error rate matches independent recomputation exactly. Both calibrations record six successful product canaries; canary responses are not separately preserved.

For clarity, pooled active-window throughput would be v1=4.997219505043 rps, v2=4.997218994277 rps. This is a different aggregate from the reported median, not a mismatch.

## v2 pre/post runtime identity

The two saved snapshots contain 12 deployments, 12 pods, and 12 services each. All 36 corresponding full object dictionaries are identical. Each pod is Running and Ready, and all 24 regular containers are ready/running with restartCount=0. Pod UIDs, containerIDs and imageIDs are identical pre/post. Full identities and exact container field references are in the JSON report.

| App | Pod UID | Pre UID evidence | Post UID evidence |
|---|---|---|---|
| adservice | `0a838b53-19c2-46d1-84ba-c5493c844049` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:3474` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:10304` |
| cartservice | `ea668207-2bc9-427e-81eb-65936ac18de1` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:3796` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:10626` |
| checkoutservice | `5f6095c7-c767-4ea2-be60-0654deb3fb4b` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:4108` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:10938` |
| currencyservice | `6b24d2dc-9c05-4596-b0a7-d87cc9dbfcc1` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:4446` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:11276` |
| emailservice | `daa97f92-8676-479e-8bfa-b03220dab29f` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:4775` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:11605` |
| frontend | `ca664d5b-d3a9-48c6-98f4-d33b5c363176` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:5092` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:11922` |
| loadgenerator | `81d34579-b1da-4832-9c08-450b030501a1` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:5457` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:12287` |
| paymentservice | `413458b5-ce2d-4e62-b3c2-2db54905509a` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:5816` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:12646` |
| productcatalogservice | `3b33831b-857c-4086-95e7-00e729fa91fe` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:6144` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:12974` |
| recommendationservice | `ea052333-47ae-4696-a2d7-cf367cc68123` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:6458` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:13288` |
| redis-cart | `7e898c0b-5717-4864-bfca-f9052100e6cd` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:6780` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:13610` |
| shippingservice | `443cee0a-2082-42a9-bbee-c82c9fe390a8` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:7068` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:13898` |

The saved `runtime_identity_stable_across_calibration=true` flag at `experiments/e2r/runs/recovery_baseline_v2/baseline.json:14732` is therefore supported by the retained snapshots. v1 has no corresponding post-calibration snapshot.

## Manifest and v1→v2 changes

All deployment and service names/namespaces match the YAML. All 12 deployment UIDs and all 12 service UIDs remain unchanged between runs; service specs and ClusterIPs also remain unchanged. Every v2 last-applied deployment/service spec equals the parsed YAML spec exactly, including full pod templates. Every v1→v2 deployment spec has exactly one scalar change: sidecar-busybox command `sleep 3600` → `sleep 2147483647`. No other deployment spec field differs.

| Deployment | v1 command | v2 command | YAML command | v2 last-applied annotation |
|---|---|---|---|---|
| adservice | `experiments/e2r/runs/recovery_baseline_v1/baseline.json:1191` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:1191` | `experiments/e2r/isolated_boutique.yaml:1240` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:1082` |
| cartservice | `experiments/e2r/runs/recovery_baseline_v1/baseline.json:1381` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:1381` | `experiments/e2r/isolated_boutique.yaml:790` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:1272` |
| checkoutservice | `experiments/e2r/runs/recovery_baseline_v1/baseline.json:1597` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:1597` | `experiments/e2r/isolated_boutique.yaml:278` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:1462` |
| currencyservice | `experiments/e2r/runs/recovery_baseline_v1/baseline.json:1794` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:1794` | `experiments/e2r/isolated_boutique.yaml:975` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:1678` |
| emailservice | `experiments/e2r/runs/recovery_baseline_v1/baseline.json:1986` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:1986` | `experiments/e2r/isolated_boutique.yaml:175` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:1875` |
| frontend | `experiments/e2r/runs/recovery_baseline_v1/baseline.json:2229` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:2229` | `experiments/e2r/isolated_boutique.yaml:495` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:2067` |
| loadgenerator | `experiments/e2r/runs/recovery_baseline_v1/baseline.json:2394` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:2394` | `experiments/e2r/isolated_boutique.yaml:896` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:2310` |
| paymentservice | `experiments/e2r/runs/recovery_baseline_v1/baseline.json:2620` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:2620` | `experiments/e2r/isolated_boutique.yaml:605` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:2505` |
| productcatalogservice | `experiments/e2r/runs/recovery_baseline_v1/baseline.json:2812` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:2812` | `experiments/e2r/isolated_boutique.yaml:697` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:2701` |
| recommendationservice | `experiments/e2r/runs/recovery_baseline_v1/baseline.json:3012` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:3012` | `experiments/e2r/isolated_boutique.yaml:376` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:2893` |
| redis-cart | `experiments/e2r/runs/recovery_baseline_v1/baseline.json:3180` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:3180` | `experiments/e2r/isolated_boutique.yaml:1146` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:3093` |
| shippingservice | `experiments/e2r/runs/recovery_baseline_v1/baseline.json:3366` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:3366` | `experiments/e2r/isolated_boutique.yaml:1067` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:3255` |

All deployment generations and revisions advance from 1 to 2. All 12 pod UIDs and 24 corresponding regular-container IDs change, while corresponding imageIDs remain identical. New pods were created between 2026-09-26 15:46:11 and 15:51:45 UTC, after v1 ended (14:07:34.828962 UTC) and before v2 began (15:55:16.889436 UTC). This supports a rollout-compatible runtime reset, not runtime continuity across versions. Intent itself needs the change log or operational command records outside the assigned inputs.

| App | v1 UID evidence | v2 UID evidence | v2 creation evidence |
|---|---|---|---|
| adservice | `experiments/e2r/runs/recovery_baseline_v1/baseline.json:3474` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:3474` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:3454` |
| cartservice | `experiments/e2r/runs/recovery_baseline_v1/baseline.json:3796` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:3796` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:3776` |
| checkoutservice | `experiments/e2r/runs/recovery_baseline_v1/baseline.json:4108` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:4108` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:4088` |
| currencyservice | `experiments/e2r/runs/recovery_baseline_v1/baseline.json:4446` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:4446` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:4426` |
| emailservice | `experiments/e2r/runs/recovery_baseline_v1/baseline.json:4775` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:4775` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:4755` |
| frontend | `experiments/e2r/runs/recovery_baseline_v1/baseline.json:5092` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:5092` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:5072` |
| loadgenerator | `experiments/e2r/runs/recovery_baseline_v1/baseline.json:5457` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:5457` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:5437` |
| paymentservice | `experiments/e2r/runs/recovery_baseline_v1/baseline.json:5816` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:5816` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:5796` |
| productcatalogservice | `experiments/e2r/runs/recovery_baseline_v1/baseline.json:6144` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:6144` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:6124` |
| recommendationservice | `experiments/e2r/runs/recovery_baseline_v1/baseline.json:6458` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:6458` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:6438` |
| redis-cart | `experiments/e2r/runs/recovery_baseline_v1/baseline.json:6780` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:6780` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:6760` |
| shippingservice | `experiments/e2r/runs/recovery_baseline_v1/baseline.json:7068` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:7068` | `experiments/e2r/runs/recovery_baseline_v2/baseline.json:7048` |

Service identity/spec evidence is recorded per service in the JSON; representative frontend evidence: `experiments/e2r/runs/recovery_baseline_v1/baseline.json:7601`, `experiments/e2r/runs/recovery_baseline_v2/baseline.json:7601`, `experiments/e2r/runs/recovery_baseline_v2/baseline.json:14431`, `experiments/e2r/isolated_boutique.yaml:519`.

## No-fault scope and limits

Both files explicitly record `evaluation_type=live_no_fault_baseline_only`, `fault_injected=false`, and `repair_executed=false` at lines 3–6. Their limits are at `experiments/e2r/runs/recovery_baseline_v1/baseline.json:7902`, `experiments/e2r/runs/recovery_baseline_v2/baseline.json:14733`. The manifest workload is USERS=10 at `experiments/e2r/isolated_boutique.yaml:873`; run workload definitions begin at line 10 in each baseline. Environment namespace, source and intended scope are at `experiments/e2r/isolated_environment.json:2`, `:4`, and `:13`.

- This is a read-only static audit of saved artifacts, not live Kubernetes or HTTP verification. No network, Kubernetes/API, GPU or experiment execution was performed.
- Raw latencies support deterministic recomputation, but request outcomes, HTTP bodies and per-request timestamps are not preserved; successful request counts and canary booleans remain recorded observations.
- The workload consists of six 30-second measurement windows per calibration with 150 scheduled requests each, alongside the manifest loadgenerator USERS=10. It supports baseline calibration for this isolated workload, not fault/repair efficacy, recovery rate, repeated-seed robustness, or production performance.
- Baseline throughput is median(window successes/elapsed_seconds), not total successes divided by summed active duration or the overall wall-clock span. Both baseline p95 values use all 900 raw latency samples with nearest-rank ceil(0.95*N)-1 indexing.
- v1 has no saved cluster_snapshot_after or runtime-stability field. Its single snapshot cannot establish runtime stability across calibration. v2 does retain pre/post snapshots, which are structurally identical in all 36 deployment/pod/service objects.
- v1→v2 pod/container identities must not be treated as continuous. The observed sidecar-only specification change, generation/revision increment, and replacement timing are consistent with deliberate rollout; operator intent is not independently established by these four experiment inputs.
- Current YAML matches v2 last-applied deployment/service specs; last-applied annotations and current code do not by themselves prove the exact historical execution path or authenticate the saved records.
- isolated_environment.json is high-level metadata and does not enumerate the subsequent sidecar sleep hardening. Its claim that existing online-boutique is unchanged cannot be verified without corresponding snapshots of that separate namespace.
- This same-family review remains provisional for semantic integrity judgments; deterministic file consistency checks have passed.

## Input hashes

| Input | SHA-256 |
|---|---|
| `experiments/e2r/isolated_boutique.yaml` | `sha256:9150029373d00e9882b83922071d69c12bcd36b04af5cab16ea90a2caa0882bb` |
| `experiments/e2r/isolated_environment.json` | `sha256:e609874865aac726703127483c822450071e6e04979676fecb09908fd84122f6` |
| `experiments/e2r/runs/recovery_baseline_v1/baseline.json` | `sha256:b9c4c39b77b6640390746b77167ab5f69f2dca72598b59399b150d4dc9c563bf` |
| `experiments/e2r/runs/recovery_baseline_v2/baseline.json` | `sha256:3957fa90acce446bd248c6e493c2374307454cc96d4644b99ec60fc04b84642d` |
| `scripts/e2r/run_recovery_baseline.py` | `sha256:080ec0a903b3dffc3f5378c23aed469f78c43f2166acb0c0393088ebe7c1c296` |

All inputs were rehashed after verification and were unchanged.

## Supplemental intent and init-container evidence

The review record explicitly declares an intentional benchmark setup rollout at `refine-logs/FULL_EXPERIMENT_CODE_REVIEW.md:686`, requires fresh identities/pre-post checks at `:687`, a new no-fault baseline at `:688`, and an application/Redis state reset at `:691`. This corroborates the observed replacement pattern as deliberate preparation; it is documentary evidence, not external execution attestation. Redis `emptyDir` is declared at `experiments/e2r/isolated_boutique.yaml:1158`, so these records cannot support persistent-state preservation across versions. No causal claim is made that sidecar restarts explain the failed sanity-v2 latency.

The sole loadgenerator init container also retains the same containerID across v2 pre/post, records ready=true, restartCount=0, and Completed/exitCode=0: `experiments/e2r/runs/recovery_baseline_v2/baseline.json:5736`, `experiments/e2r/runs/recovery_baseline_v2/baseline.json:12566`.

Supplementary input hash: `refine-logs/FULL_EXPERIMENT_CODE_REVIEW.md` → `sha256:297593d8f41a51b5a6e8e03b49314e2fa0535601cb1b93e51c5b8e2a895c3173`.
