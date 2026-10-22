# M4 preparation and live-sanity experiment audit

**Overall verdict: WARN. No integrity BLOCK for the narrowly qualified known-target sanity statement below.**

**Date:** 2026-09-27 (Asia/Tokyo). **Auditor:** GPT-6-Astra ultra, fresh agent `/root/m4_sanity_audit`. **Assurance:** same-family / provisional. Deterministic arithmetic and record checks passed; semantic acceptance remains provisional.

In the single known-target frontend CPU-stress v3 sanity, scoped typed deletion completed 63.9 seconds after injection and was followed by three workload windows meeting the predeclared thresholds before the 180-second fault expiry; the trace records oracle PASS and independently recorded cleanup absence, while raw technical-probe and contemporaneous pod-runtime evidence are incomplete.

## Scope and method

Read the isolated manifest/environment description, both baseline artifacts, baseline comparison, all three preserved sanity traces, the relevant runner/planner/guard/oracle code, tracker and code-review addenda. No network, Kubernetes, Ansible, API or GPU operation was run, and no implementation/result artifact was changed. Offline pure-function replays use saved probe rows through a mock. **247 deterministic checks pass across 20 windows and 3,000 requests.** Full checks, input hashes and inspected-source snapshots are under `.aris/traces/experiment-audit/20260927_m4_sanity/`.

## A. Ground-truth provenance — WARN

The target service/fault and confidence 0.99 are supplied by the known injection contract; one live Chaos object is the evidence, not a learned RCA output. HTTP observations are measured; no fabricated model-derived reference was found. This supports a known-target contract sanity, not diagnosis accuracy.

The runner hardcodes namespace, service and fault, constructs a 0.99-confidence RCA report from its own known injection object, and uses `use_llm=False` (`scripts/e2r/run_recovery_sanity.py:17`, `:214`, `:220`). The value 0.99 is neither learned diagnostic confidence nor measured accuracy. The live object JSON and target-bound precondition make the single cleanup action inspectable; they do not exercise graph diagnosis. The independent HTTP collector supplies the workload measurements, and the baseline is a separate no-fault interval. No evidence of model-output-derived fake ground truth was found.

## B. Metric arithmetic and normalization — PASS

For every raw window, all 150 attempts have finite, nonnegative latencies; attempts equal samples and valid-latency count, successes equal 150, and the error rate is zero. Nearest-rank p95 is `sorted(latencies)[ceil(0.95*n)-1]`; throughput is `successes/elapsed_seconds`. No failed requests are removed by the collector’s latency expression; in these artifacts all requests succeeded. Canary outcomes are recorded true, but their HTTP bodies/status records are not retained.

The pooled baseline p95 uses all 900 latency values. Baseline throughput is the median of six window throughputs, not 900 divided by the wall-clock span including five inter-window gaps (`scripts/e2r/run_recovery_baseline.py:118`). This declared aggregation reproduces exactly. Source computation is at historical `workload_oracle.py:155` and `rca_bench/recovery_oracle.py:89`.

| Artifact | Window | Requests | p95 ms | Throughput rps | Error rate | Canary |
|---|---|---:|---:|---:|---:|---|
| baseline_v1 | baseline-0 | 150 | 17.989250 | 4.997328 | 0.0 | True |
| baseline_v1 | baseline-1 | 150 | 16.900299 | 4.997066 | 0.0 | True |
| baseline_v1 | baseline-2 | 150 | 17.157181 | 4.997205 | 0.0 | True |
| baseline_v1 | baseline-3 | 150 | 16.987927 | 4.997266 | 0.0 | True |
| baseline_v1 | baseline-4 | 150 | 16.992859 | 4.997305 | 0.0 | True |
| baseline_v1 | baseline-5 | 150 | 17.747114 | 4.997147 | 0.0 | True |
| baseline_v2 | baseline-0 | 150 | 18.263094 | 4.997064 | 0.0 | True |
| baseline_v2 | baseline-1 | 150 | 18.072162 | 4.997127 | 0.0 | True |
| baseline_v2 | baseline-2 | 150 | 18.602617 | 4.997211 | 0.0 | True |
| baseline_v2 | baseline-3 | 150 | 18.550334 | 4.997201 | 0.0 | True |
| baseline_v2 | baseline-4 | 150 | 18.489612 | 4.997016 | 0.0 | True |
| baseline_v2 | baseline-5 | 150 | 17.430428 | 4.997696 | 0.0 | True |
| sanity_v1 | sanity-pre | 150 | 17.797870 | 4.997388 | 0.0 | True |
| sanity_v1 | sanity-fault | 150 | 804.541456 | 4.949053 | 0.0 | True |
| sanity_v2 | sanity-pre | 150 | 26.152759 | 4.997397 | 0.0 | True |
| sanity_v3 | sanity-pre | 150 | 16.974388 | 4.997318 | 0.0 | True |
| sanity_v3 | sanity-fault | 150 | 1399.842357 | 4.756544 | 0.0 | True |
| sanity_v3 | sanity-post-0 | 150 | 16.942839 | 4.997166 | 0.0 | True |
| sanity_v3 | sanity-post-1 | 150 | 17.152582 | 4.997222 | 0.0 | True |
| sanity_v3 | sanity-post-2 | 150 | 16.887876 | 4.997125 | 0.0 | True |

Baseline v1 pooled p95 = **17.33604120090604 ms**, median throughput = **4.9972357143968615 rps**. Baseline v2 pooled p95 = **18.263094127178192 ms**, median throughput = **4.997163960150042 rps**. The comparison’s p95 ratio **1.0534754685645131**, throughput ratio **0.999985641212278**, and zero error-rate delta reproduce exactly (`recovery_baseline_comparison_v1/analysis.json:25`).

The latency ceiling remains 1.2× baseline, and throughput floor remains 0.8× baseline. The v1 ceiling was **20.803249441087246 ms**; v2-calibrated v3 uses **21.91571295261383 ms**. V2’s rejected pre-window **26.152758859097958 ms** would also fail the new ceiling. The baseline comparison is descriptive across wall-clock times and a setup rollout, not evidence that the sidecar change improved latency.

## C. Existence, immutable bindings, runtime evidence — WARN

All requested artifacts exist; every reported window metric and comparison matches its raw inputs. V1/v2 baselines remain no-fault-only (`recovery_baseline_v1/baseline.json:2`, `recovery_baseline_v2/baseline.json:2`). The v3 trace pins the exact baseline and manifest (`recovery_sanity_frontend_cpu_v3/trace.json:35`). Offline validation accepts those bytes and rejects a changed baseline, a changed manifest and the old baseline path (`scripts/e2r/run_recovery_sanity.py:35`).

| Artifact | SHA-256 |
|---|---|
| experiments/e2r/isolated_boutique.yaml | `9150029373d00e9882b83922071d69c12bcd36b04af5cab16ea90a2caa0882bb` |
| experiments/e2r/runs/recovery_baseline_v1/baseline.json | `b9c4c39b77b6640390746b77167ab5f69f2dca72598b59399b150d4dc9c563bf` |
| experiments/e2r/runs/recovery_baseline_v2/baseline.json | `3957fa90acce446bd248c6e493c2374307454cc96d4644b99ec60fc04b84642d` |
| experiments/e2r/runs/recovery_sanity_frontend_cpu_v1/trace.json | `346a326e1c0cd88dea291932d521415ec182bcc46d54b53a3abff627a4fd1991` |
| experiments/e2r/runs/recovery_sanity_frontend_cpu_v2/trace.json | `47122d445bcde4e11899fa02161ca0cf48f7d8e77c4bb582f3218f9568a28f23` |
| experiments/e2r/runs/recovery_sanity_frontend_cpu_v3/trace.json | `33cff9667822ef297d508809f57a758a9fb3dc975a757f46dd10ad47b4cad761` |

Baseline v2 embeds matching pre/post snapshots: 12 pod UIDs and container IDs remain stable, 24 containers are Ready, all restart counts are zero, and the deployment/service configurations agree with the approved manifest (`recovery_baseline_v2/baseline.json:1072`, `:7902`, `:14732`). Baseline v1 has only its initial snapshot. Between environment versions, every Deployment changes only the sidecar sleep from 3600 to 2147483647 in last-applied specs; replacing the 12 strings in current manifest bytes recovers the prior manifest fingerprint. All pod UIDs change across that planned rollout; this is a reset, not persistent-state preservation. Redis uses `emptyDir`. The review explicitly describes this reset (`refine-logs/FULL_EXPERIMENT_CODE_REVIEW.md:666`, `:691`).

**V3 provenance gap:** `technical_checks` contains only three Boolean values (`recovery_sanity_frontend_cpu_v3/trace.json:1079`). The scoped raw StressChaos/deployment/endpoints observations used to derive them are absent. The saved cleanup GET independently proves exact-object absence later (`:1189`), but cannot reconstruct those earlier scoped technical probes. No contemporaneous pre/post v3 pod-runtime snapshots were saved. The executor reported an immediate post-run health check but confirmed it was not persisted; this audit does not promote it to verified runtime evidence or substitute a late snapshot.

The original collector source was reconstructed after the run by reversing the prospective evidence-recording patch; its verified digest exactly matches the review-pinned **12f788298199b4e3c95d47d13bd53fb1562d1547522be3522b3ffd13ad74c0e3** (`refine-logs/FULL_EXPERIMENT_CODE_REVIEW.md:743`). It is saved as `historical_source/workload_oracle.py` in the audit trace. Current source digest **84a2229a1d5a8f344fbec3ba2ad696ee43ded3854054d2bbacdf5c4ef7e2d1a9** adds prospective raw-probe returns; it is explicitly excluded from v3 execution provenance. The trace itself does not embed all execution-time source hashes, so a review-pinned source reconstruction is weaker provenance than an immutable launch snapshot.

## D. Live path, target binding and execution — PASS within scope

The relevant functions are actually called (`scripts/e2r/run_recovery_sanity.py:161`, `:220`, `:232`, `:244`, `:260`). Saved plans regenerate exactly from their saved reports; rendered YAML is byte-identical; plan/YAML validation and precondition replay reproduce their saved PASS results. The optional `build_business_oracle` callback is not used by this runner and contributes no result.

For both injected episodes, the evidence object equals the subsequent raw precondition object, including the same UID. Kind/name/namespace match **StressChaos/e2r-sanity-cpu/e2r-supplement-20260925**, selector is exactly `app=frontend`, and the captured manifest is mode one, 180 seconds, CPU 10 workers/load100. The actual controller status has Selected/AllInjected true, AllRecovered/Paused false, one Injected container record and no recovery count. Capture hashes and sanitized rollback objects reproduce (`recovery_sanity_frontend_cpu_v1/trace.json:459`, `recovery_sanity_frontend_cpu_v3/trace.json:470`).

The typed command, YAML shell task and precondition use the exact same target. YAML declares the scoped kubeconfig; injection and final cleanup declare admin context. The stored four RBAC answers are consistent with the intended scope, but raw authorization responses are not retained. The helper’s general “active_chaos_target” predicate checks object identity/selector/deletion state, not AllInjected status (`methods/PlanRCAThinkRemed/safety_guard.py:251`); the actual trace has active status and conservative timing, so this does not invalidate this episode or establish a general active-fault verifier.

## Attempts, failures, cleanup and denominators

| Attempt | Injection | Typed mutation completed | Oracle | Classification |
|---|---|---|---|---|
| v1 | Yes | No | Absent | Execution infrastructure failure |
| v2 | No | No | Absent | Pre-health rejection |
| v3 | Yes | Yes | Recorded PASS | One known-target contract sanity |

**V1:** pre p95 **17.7979 ms**, fault p95 **804.5415 ms**. The repair process returned 1 after **0.081566 s** because `${LOCAL_HOME}/.local/bin/ansible-playbook` could not import `ansible`. This occurs before the playbook CLI/mutation, so `repair_executed=False` and no post-repair oracle is correct (`recovery_sanity_frontend_cpu_v1/trace.json:537`). Rollback’s apply returned “unchanged” and is retained; it is not another injected episode or a retry of successful repair (`:546`). Cleanup is not evidence of repaired application state.

**V2:** scoped Ansible preflight succeeded, then the pre-window failed the unchanged health rule. It has no injection event, no repair execution and no oracle (`recovery_sanity_frontend_cpu_v2/trace.json:11`, `:204`). It is one attempted sanity but zero injected fault episodes.

**V3:** the Ansible executable in the reviewed venv returned 0 and stdout reports the exact StressChaos deletion (`recovery_sanity_frontend_cpu_v3/trace.json:548`). The oracle result is not inferred from process exit; three post-windows and recorded technical predicates participate separately.

All three attempts retain cleanup return 0, independent exact-target GET return 1/NotFound, no cleanup exceptions and `cleanup_verified_absent=True` (v1 `:570`, v2 `:207`, v3 `:1171`). The runner executes one injection path and no automatic retry. The code-review addenda preserve separate v2 and v3 decisions (`refine-logs/FULL_EXPERIMENT_CODE_REVIEW.md:623`, `:668`, `:706`); there is no observed successful-only filtering in the supplied series. An audit of saved artifacts cannot prove that no unrecorded external attempts ever occurred.

**Denominators:** two no-fault calibrations; three sanity attempts; one pre-injection rejection; two actual injected episodes; two repair-process invocations, of which only one reached/completed typed mutation; one recorded oracle PASS and one injected episode without a post-oracle owing to infrastructure failure. These changed setup/code versions are not exchangeable trials. Do not report 100% recovery or turn 1/2 or 1/3 into a comparative success-rate estimate.

## Timing, freshness and natural expiry — PASS for this trace

All 20 window intervals are ordered and nonoverlapping within their respective runs, with elapsed time agreeing with wall-clock duration. V1/v2 use the older baseline with a fresh pre-health check; v3 baseline ends **339.2851 seconds** before its pre-window starts. Post-window freshness is separate from baseline age.

V3 injection request starts at **1790438671.357962**; application completes **0.231765 seconds** later. Fault measurement starts after the required 30-second settling delay. Pre-repair age is **62.4902 seconds**, pre-execution age **62.6204 seconds**, remaining attribution budget **87.3796 seconds**, and Ansible timeout **30 seconds**. The named deletion completes at **63.9213 seconds**, before the **150-second** deadline and **180-second** declared expiry (`recovery_sanity_frontend_cpu_v3/trace.json:229`, `:404`, `:548`). Wall and monotonic ages agree within 0.000012 seconds.

The three post-window end ages are **93.9389**, **124.9662**, and **155.9936 seconds**; all precede the earliest conservative expiry. Their ages at oracle evaluation are **62.4446**, **31.4174**, and **0.3900 seconds**, all below 120 seconds. Unique IDs, source identity, post-repair ordering, sample sufficiency and all thresholds pass, and pure oracle replay is an exact dictionary match (`recovery_sanity_frontend_cpu_v3/trace.json:559`, `:1084`; `rca_bench/recovery_oracle.py:80`).

Thus declared automatic expiry cannot explain this recorded pre-expiry PASS. That temporal check does not estimate a causal treatment effect: a matched no-action episode is absent, and spontaneous workload changes remain possible.

## E. Scope and claim impact — WARN

The tracker correctly keeps R043 at SANITY_V3_PASS_REVIEW_PENDING and the no-action/factorial studies blocked (`refine-logs/EXPERIMENT_TRACKER.md:27`). This audit completes evidence checking for this small sanity series, not those studies. It does not establish learned RCA accuracy, evidence-bound reranking, graph-to-repair benefit, superiority to direct repair, comprehensive safety, cross-service/fault generalization, or persistent-state integrity. The HTTP root check reads only the first 1024 bytes, and the product canary tests availability/status, not cart/payment correctness or state preservation (`scripts/e2r/run_recovery_sanity.py:56`; historical `workload_oracle.py:112`).

## F. Evaluation type

Precise classification: **controlled_live_known_target_contract** with measured HTTP workload and separate no-fault calibration. Under the skill’s limited taxonomy this is recorded as **self_supervised_proxy**, because no learned diagnosis is evaluated against independent labels; it is not simulated or model-generated data. The known injector target is valid for checking this action contract but must not be represented as independently inferred RCA ground truth.

## Required reporting and future evidence improvements

- Preserve all attempts, failures and environment versions with the denominator split above.
- Persist timestamped raw technical/RBAC/canary observations and pre/post pod UID/container ID/restart/readiness snapshots in prospective runs; later observations cannot fill v3’s missing evidence.
- Freeze the complete source hashes and approved artifact identities with each future launch.
- Use a predeclared matched comparison with repetitions before asserting causal or comparative recovery benefit.

**One warranted statement:** In the single known-target frontend CPU-stress v3 sanity, scoped typed deletion completed 63.9 seconds after injection and was followed by three workload windows meeting the predeclared thresholds before the 180-second fault expiry; the trace records oracle PASS and independently recorded cleanup absence, while raw technical-probe and contemporaneous pod-runtime evidence are incomplete.
