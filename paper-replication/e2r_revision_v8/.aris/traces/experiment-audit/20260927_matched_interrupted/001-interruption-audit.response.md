# Experiment Audit Report — interrupted matched CPU block

**Date:** 2026-09-27 (Asia/Tokyo). **Auditor:** fresh GPT-6-Astra ultra, same-family, provisional. **Scope:** `experiments/e2r/runs/recovery_matched_frontend_cpu_v1` and its frozen protocol, measurement code, immutable baseline, code-review addenda and current analysis/tracker.

## Overall verdict: WARN

**Integrity status: warn. Matched comparison: invalid. Complete matched pairs: 0 of 1 planned.** No fabricated, self-normalized or mismatched saved result was found. The no-action arm is a valid complete standalone trajectory under the frozen criteria. The typed arm records a successful deletion but no measured recovery outcome. Neither PASS nor FAIL may replace its null oracle.

The audit used only local artifact reads and offline arithmetic/pure replay. It made no external, network, Kubernetes, Ansible or experiment invocation and changed no source/result artifact. It created only audit/trace outputs. **328 deterministic checks passed with zero mismatches**: 212 independent baseline/control comparisons and 116 typed/accounting/binding checks. The semantic verdict remains same-family/provisional; these arithmetic checks are deterministic evidence, not a completed experimental comparison.

## Exact accounting

| Quantity | Audited value |
|---|---:|
| Planned episodes / started episodes | 2 / 2 |
| Injection attempts / confirmed injections | 2 / 2 |
| Unknown injection outcomes | 0 |
| Repair-process invocations / confirmed mutations | 1 / 1 |
| Unknown repair-process outcomes | 0 |
| Completed episode trajectories | 1 control; 0 treatment |
| Completed saved evaluation windows | control 3/3; typed 0/3 |
| Saved oracle statuses | `["FAIL", null]` |
| Complete matched pairs | 0/1 |
| Saved block workload latency observations | 750 control + 300 typed = 1,050 |
| Separate calibration observations | 900 across 6 windows |

The original `block.json:15` counts only one episode because the block aggregator saves after `run_episode` returns (`scripts/e2r/run_recovery_matched_block.py:406`). The second episode file proves another started attempt, injection and repair. The interruption sidecar correctly reconciles those counts (`block.interruption.json:10`). The read-only Ansible preflight is not a repair invocation; the control's final cleanup is not a treatment repair. No denominator may silently exclude the interrupted typed attempt. One measured FAIL and one unobserved outcome do not define a two-arm recovery rate.

The 1,050 figure counts retained root-path workload latencies with aggregate attempts/successes. It is **not** a known total of requests issued before interruption: unpersisted/in-flight treatment evaluation requests are unknown, and canary requests are separate. Empty treatment arrays mean zero completed saved windows, not proof that evaluation never began (`episodes/01_typed_cleanup.json:558`; `episodes/01_typed_cleanup.interruption.json:8`).

## Recomputed measurements

All 13 saved windows (6 baseline, 5 control, 2 typed pre/fault) retain 150 finite nonnegative latencies and 150 recorded successes, so error rate is 0 in every saved window. All saved canary booleans are true. The nearest-rank p95 is the 143rd ordered latency in each 150-sample window. Throughput is recorded successes divided by recorded monotonic elapsed time; mean latency below is audit-derived.

| Window | N | Mean ms | p95 ms | Success rps | Elapsed s |
|---|---:|---:|---:|---:|---:|
| no_action-pre | 150 | 15.676435 | 16.786580 | 4.997438265 | 30.015378 |
| no_action-fault-validation | 150 | 878.729336 | 1359.609749 | 4.876480285 | 30.759891 |
| no_action-evaluation-0 | 150 | 946.397058 | 1500.684631 | 4.839861194 | 30.992624 |
| no_action-evaluation-1 | 150 | 904.345512 | 1501.261685 | 4.875425808 | 30.766543 |
| no_action-evaluation-2 | 150 | 964.091992 | 1399.665209 | 4.781646496 | 31.369948 |
| typed_cleanup-pre | 150 | 20.247441 | 19.501911 | 4.997199076 | 30.016815 |
| typed_cleanup-fault-validation | 150 | 962.815984 | 1399.259328 | 4.726677502 | 31.734765 |

The calibration aggregate reconstructs to 900 samples, mean **18.112473890910 ms**, pooled p95 **18.263094127178 ms**, median per-window throughput **4.997163960150 rps**, and error 0. Frozen business thresholds are p95 ≤ **21.915712952614 ms**, throughput ≥ **3.997731168120 rps**, error ≤ **0.01**, and a true canary (`scripts/e2r/run_recovery_baseline.py:118`; `rca_bench/recovery_oracle.py:122`). Both fresh pre-health gates pass; both fault windows are measurable and degraded through p95. All three control evaluation windows fail only the latency component.

Full precision, minima/maxima, all latency-count checks, calibration rows, timestamp ordering and wall/monotonic differences are in `recompute_noaction.json` and `recompute_typed_and_accounting.json`. The observed wall/monotonic window durations agree within microseconds. Recorded clock readings and aggregate successes are inputs to recomputation; they are not newly observed facts.

## A. Ground-truth provenance: WARN

This is a real isolated live workload with a deliberately injected known frontend CPU fault. The baseline comes from a separate no-fault run, and raw Chaos objects bind the typed plan to the known injection. No model-generated reference or reference derived from prediction statistics was found. However, `RCAReport` explicitly receives `frontend`, `cpu-stress` and confidence `.99`; this is not an evaluated RCA prediction (`scripts/e2r/run_recovery_matched_block.py:152`). Confidence `.99` is not a calibrated accuracy result. A product GET canary checks availability, not persistent-state integrity (`scripts/e2r/run_recovery_sanity.py:56`).

## B. Score normalization: PASS

Every saved p95, throughput and error metric agrees with raw diagnostics. p95 uses all valid latencies irrespective of HTTP success; missing/invalid latency coverage produces NaN rather than a success-only p95 (`rca_bench/workload_oracle.py:155`). All retained windows have latency count equal to attempts. The frozen 1.2 and 0.8 multipliers use the separate calibration; there is no division by a model's own extrema. No completed window was dropped from the analysis, and the incomplete arm remains explicit.

## C. Result existence and evidentiary completeness: WARN

### Control: complete standalone trajectory

The exact Chaos UID **`7d2f31e8-4d0f-4632-9ff8-f1e2d66c6045`** agrees across the initial object, precondition, three pre-window liveness probes and terminal technical read. Each saved object has the correct kind/name/namespace/frontend selector, 180-second profile, `AllInjected=True` and `AllRecovered=False` (`episodes/00_no_action.json:389`, `:470`, `:1067`, `:1141`). Pure replay also reproduces both arms' plan/YAML safety results and complete precondition capture hashes.

Liveness ages are **61.492148, 93.821197 and 125.822337 s**. Evaluation end ages from wall timestamps are **92.616226, 124.717907 and 157.318813 s**. All start budgets pass. The pretechnical age **157.726545 s** plus the 9-second worst-case read budget is **166.726545 < 170 s**. Technical observation completes at **158.111220 < 170 s**; the saved final observation at **158.117005 < 165 s**; cleanup/absence completion at **158.686443 < 175 s**. These precede the configured 180-second expiry. The three oracle freshness ages are **65.500681, 33.398999 and 0.798094 s**, below 120 s. The final-observation field is saved after technical/oracle work, so it is not identical to the third workload-window end (`scripts/e2r/run_recovery_matched_block.py:265`).

All three raw technical responses validate their expected object identities, scoped context and statuses. They independently recompute **fault_absent=False, resource_restored=True, ready_endpoints=True**: the fault exists; frontend has 1 ready replica of 1; its endpoint points to the recorded frontend pod. Here `resource_restored` means ready replica count, not a CPU-restoration measurement (`rca_bench/workload_oracle.py:280`). The whole saved oracle dictionary reproduces exactly as **FAIL**, no missing checks, valid identity/freshness, and maximum/trailing passing-window counts 0 (`episodes/00_no_action.json:1211`). Joint FAIL is structurally required by fault presence; the independently useful business observation is the three failing p95 windows.

Final exact-object cleanup records rc0; a separate GET records NotFound. Errors are null and the cleanup deadline passes. The post-cleanup snapshot has 12 Running pods, 24 ready containers and zero restarts, matching the block-start UIDs/restart counts and calibration snapshots (`episodes/00_no_action.json:1305`, `:1323`, `:1343`, `:1345`). The treatment starts **30.023693 s** after control completion, satisfying washout. Snapshots support recorded runtime identity stability, not every transient state between them.

### Treatment: deletion observed, recovery outcome absent

The exact UID **`88da17d7-c111-413a-889a-c22ce607c108`** is active in both saved initial and precondition objects; the raw plan evidence, target binding and captured object hash reproduce. The playbook environment is exactly `KUBECONFIG: ${SCOPED_KUBECONFIG}`, and its only shell mutation is `kubectl delete stresschaos e2r-matched-cpu-typed -n e2r-supplement-20260925 --ignore-not-found` (`episodes/01_typed_cleanup.json:469`).

The reviewed venv-local Ansible executable returns **0** and explicitly reports that exact object deleted in that namespace, with failed=0/unreachable=0. Pre-execution age is **62.608846 s**, bounded timeout **30 s**, execution duration **1.211603 s**, completion **63.824475 < 150 s** (`episodes/01_typed_cleanup.json:546`). This substantiates one repair-process invocation and successful mutation. It does not establish subsequent application recovery.

The treatment JSON ends with empty evaluation arrays. It has **no technical package, oracle, cleanup/absence result, post-cleanup runtime or completion timestamp**. Sidecar null outcomes preserve that absence. Do not call this a measured treatment FAIL, a treatment PASS, or a complete pair.

### Late sidecars and immutable sources

The three original SHA-256 values match the review and both sidecar bindings:

- `block.json`: `59f875fffb5ffdbc55f823e617ffa18963323d02aa4e0605f27dcf8b72c5d925`
- `episodes/00_no_action.json`: `ab2ddd9dac089013b11b5e5b97e8a062bb6006985230b1469649113f7c714a0e`
- `episodes/01_typed_cleanup.json`: `bb7f73d828bd44eb5fd90e68ded1796d7bffcb46c79c910473de779b0092f00d`

Protocol/baseline/manifest bindings and reviewed runner/collector/oracle/finalizer fingerprints agree (`refine-logs/FULL_EXPERIMENT_CODE_REVIEW.md:860`, `:923`). Sidecar hashes are `100b1f4d1e28a06e67a7b0eff0e15b0f45c6c1ad8925a998e7181bb07a64daf4` and `5f0e12cb8aad3b33dabf5c4ad0c3dd22a36c2fe3bdd469dfe80f80cb4f0f7006`. Full input hashes are in the JSON manifest.

Both late exact-name Chaos GETs record NotFound. Their captured timestamp is **34,223.325583 s (9 h 30 min 23.326 s)** after the explicitly reconstructed repair wall time (injection wall time + recorded monotonic repair age). This cannot date the interruption or establish absence within the original deadlines.

The late scoped pod GET is **rc1 Unauthorized**. Despite an empty `items` list in stdout, the failed read correctly yields **late_pods=null, late_runtime=null, runtime_matches=null** (`episodes/01_typed_cleanup.interruption.json:60`). The executor reports that the scoped token expired; the preserved stderr establishes authorization failure, not its cause independently. `late_runtime_observation_error=null` means no Python exception was thrown; it does not mean the subprocess succeeded. The earlier executor-reported 12-Ready-pod sentence at code-review line 902 is not raw runtime evidence from the finalizer. Late state cannot fill the missing treatment technical/oracle/cleanup fields.

## D. Dead-code/result-path audit: PASS

Control execution reaches the collector, three raw technical probes and `evaluate_recovery`, and their outputs reproduce (`scripts/e2r/run_recovery_matched_block.py:213`, `:229`, `:253`). The treatment reaches Ansible and saves execution, then initializes empty evaluation arrays (`:190`, `:198`). Later stages are unobserved, not phantom results. The optional `build_business_oracle` callback is not used by this runner and supplies no claimed metric.

## E. Scope, retries and claims: WARN

Scope is one frontend/CPU-stress profile in one namespace, seeded order 20260927, one planned episode per arm, zero complete pairs. The original two episode files are both retained. The runner rejects reused output directories, stops on failed gates, and contains no replacement-episode/retry loop (`scripts/e2r/run_recovery_matched_block.py:350`, `:406`, `:428`). Sidecars explicitly disallow replacement. No hidden retries or successful-only episode filtering were found in this directory, reviewed control flow or analysis; the artifact audit cannot establish absence of unrecorded work elsewhere.

The current analysis states comparison invalid and has no effect size. Tracker R042 records one control only and R043 interrupted/invalid; neither is a completed multi-fault campaign (`refine-logs/EXPERIMENT_TRACKER.md:27`). The examined paper excerpt still labels controlled comparisons as a prospective protocol (`paper/sections/5_validation.tex:3`); no result from this block is presented there as a measured pair. This is not a full audit of unrelated manuscript claims.

## F. Evaluation type

Legacy classification: **self_supervised_proxy**. Precise subtype: **live isolated known-target controlled fault, interrupted matched block**. The HTTP/cluster observations are real, not model-generated synthetic references or simulation. The taxonomy reflects the absence of an independently assessed learned diagnosis ground truth and use of prespecified recovery criteria against calibration. It does not reduce the actual observed workload to a fabricated proxy.

## Warranted claims and action items

1. Supported: in one complete recorded no-action trajectory, all three evaluation p95 values exceeded the frozen limit while the same injected UID reported active at each retained observation; cleanup and recorded runtime identity passed.
2. Supported: the exact scoped typed deletion returned rc0 before the repair deadline in the second episode.
3. Unsupported: a typed recovery PASS/FAIL, a typed-versus-no-action business delta/effect size, causal recovery benefit, population success rate, diagnosis/graph benefit or production/restoration generality.
4. Preserve original sources and sidecars together. Keep statuses `[FAIL, null]`, complete pairs 0, and missing/in-flight treatment evidence explicit. Do not pair the control with v3 sanity, late health or a replacement treatment.
5. Update any downstream status to one audited control plus one interrupted treatment; keep the planned campaign incomplete. This audit does not authorize any restart, rerun, replacement or further live fault action.

Reproduction artifacts: `recompute_noaction.py` / `.json` and `recompute_typed_and_accounting.py` / `.json`. Full request/response and hashes: `.aris/traces/experiment-audit/20260927_matched_interrupted/`.
