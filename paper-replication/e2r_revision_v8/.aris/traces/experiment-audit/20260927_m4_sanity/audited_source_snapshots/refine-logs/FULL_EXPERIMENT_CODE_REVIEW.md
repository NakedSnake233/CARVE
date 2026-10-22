# E2R full-experiment pre-deployment code review

Date: 2026-09-26. Reviewer: fresh delegated reviewer `/root/full_experiment_code_review`.

`review_independence: same-family`  
`acceptance_status: provisional`

This is a reviewer-authored **launch-gate review**, not a result audit or a live-recovery approval. The reviewer read the requested design, review, plan, implementation, tests, frozen M1 artifacts and `review_inventory_v1` directly. No API/network/SSH/GPU/Kubernetes calls, training, experiment-data edits or implementation edits were made by the reviewer. The executor fixed issues during the review; the verdict below applies to the final re-read files fingerprinted at the end. Synthetic HTTP responses were supplied through mocks, never through a provider. Existing checkpoints were read/scored on CPU by synthetic-campaign contract tests; these outputs are not research evidence.

## Final start-now verdict

| Gate | Verdict | Meaning |
|---|---|---|
| A: reference/runtime/node-only M1 smoke and full queue | **PASS, provisional** | Start through `reviewer_full_worker.py` using the unchanged 798-case manifest and new output directories. The actual GPU must still meet the worker's idle checks; this review did not query GPU state. |
| B: Qwen three-route eight-case pilot | **PASS, provisional** | Start the three-request sanity within the explicit 48-attempt campaign. Continue only if all three are accepted. This is permission to collect the bounded pilot, not evidence of provider compatibility or a positive result. |
| B: later frozen scoring | **Functional contract PASS; retain artifact gate** | Synthetic scoring/analysis runs pass and current audited checkpoint identities are verified below. Use exactly those three checkpoints and the frozen campaign files. The scorer checks seed/training metadata but does not itself pin checkpoint content hashes; verify the listed hashes before deployment/scoring or add that check. |
| C: local namespace/command/YAML safety checks | **PASS for the bounded syntactic guard only** | The reproduced escapes are rejected and canonical plans pass. This does **not** satisfy the full B4 evidence/precondition/rollback/oracle contract or authorize live cluster mutation. |

There is no remaining reproduced blocker to A or the bounded B request-collection start in the reviewed final files. No live C2/recovery claim follows from these gates.

## Confirmed launch properties

### A — matched graph training

- Recomputed `split_cases(..., seed=42)` equals the stored split exactly: **566 train / 116 validation / 116 test**. The manifest has 798 unique case IDs, observation paths and source directories; split IDs cover those 798 once.
- `training_variant` selects both the training tensors and validation tensors. The same architecture (128 hidden units, two layers, dropout 0.1), Adam settings (0.003, weight decay 1e-5), loss and patience 30 are used. Test labels do not choose the checkpoint.
- The full worker requests seeds 42/43/44 for each of reference, runtime and node-only. The three audited patch checkpoints are reused; no extra patch retraining is silently substituted. Each newly trained frozen object evaluates all four requested input variants.
- Node-only intentionally retains patch nodes, shared features and candidate services while providing empty edge tensors. The upstream GraphSAGE layer supplies zero neighbor messages for that case. This is the no-neighbor-message control specified in the research review. The stale dated-plan phrase about executing only node operations was corrected by the executor; it is not the implemented control.
- The observation builder uses allowlisted pre/during topology and timestamp-filtered telemetry. Labels, injection metadata, post snapshots and old plan caches are outside the feature path. All 798 saved observations have matching case IDs and `max_used_timestamp <= cutoff` when present. Existing label/post-state mutation tests pass.
- Current M1 predictions have **1,044 unique and exactly expected `(case, seed, variant)` identities**, complete candidate permutations and recomputed ranks matching recorded truth. No successful-only materialization filtering is allowed: one materialization failure aborts the run.
- The worker holds an exclusive file lock, refuses existing stage evidence/output directories and uses a new process group for each child. It samples GPU memory/use until two successive samples have `<500 MiB` and `<5%` utilization. A pure mocked sequence `[idle, busy, idle, idle]` produced consecutive counts `[1, 0, 1, 2]`.
- This no-overwrite/concurrency pass is for the **worker entry point**. The lower-level trainer alone only rejects a completed `results.json`; it is not an independent safe-resume interface for a partially populated directory.

### B — factual parity, request ceiling and scoring

- All **798** snapshots produced identical `evidence` objects for proposal, raw-direct and catalog-direct. Proposal and catalog-direct had identical supported-operation catalogs; raw-direct excluded the catalog. Coverage fields derived by scanning excluded/future records are removed from the prompt.
- Deterministic pilot selection with seed 20260926 produces one case for every application/scenario stratum: `case00418`, `case00290`, `case00250`, `case00499`, `case00005`, `case00105`, `case00742`, `case00673`. Eight cases × two generations × three routes is **48 attempts**, of which the first three are sanity attempts. The sanity is included in the 48, not additional.
- The explicit ceiling must equal the full scheduled count. A campaign lock excludes concurrent writers. Start events are appended/flushed/fsynced before calls. There is one HTTP attempt, no redirect retry, no model retry and no automatic model switch. Any unfinished start stops continuation; completed failures consume the ceiling.
- The final runner saves complete label-free observations in `frozen_inputs.json` and evaluator labels separately in `evaluator_labels.json`. Source changes are compared against those files on resume and scoring. The reviewer reproduced the old drift bug with a mocked three-request sanity, changed CPU telemetry from 1 to 999, and resumed. Before the fix all six requests completed under an unchanged campaign manifest. On the final files the same change raises `Frozen campaign input mismatch: frozen_inputs.json` before another mock call.
- Qwen's Messages envelope, OpenAI Chat Completions and Responses envelopes are separately tested. The recorded settings match the serialized model/token/temperature fields; Responses correctly does not claim an unsent temperature. Returned model ID and available provider usage are retained. No assertion of real provider compatibility was made here.
- Acceptance checks require an explicit Boolean abstention, a complete unique candidate permutation, supported unique operation IDs, prerequisites and successful execution without executor warnings. Proposal ranking and graph acceptance share the same strict proposal gate. Invalid or abstained responses cannot become no-fallback successes.
- Scoring revalidates accepted responses, preserves failed rows with `rank=None`, and creates a separate rule-fallback arm. Analysis requires the exact expected prediction identity set, recomputes ranked rows, treats failed ranks as zero and aggregates within incident before confidence intervals. The synthetic formal integration test exercises scoring and analysis with all eight arms.
- Missing provider usage/cost remains missing; cost is not fabricated from an alias. Raw protocol usage preserves cache fields when returned. The current summary is not yet the complete per-route cost/latency report requested for the paper (see below).

### C — bounded execution guard

The final combined plan-plus-YAML path binds the command to its structured namespace and target, limits mutation argv to canonical chaos deletion or the fixed resource-restoration template, rejects unsupported flags, and restricts shell characters. YAML must parse as one play with the fixed execution environment and only permitted play/task fields. Shell commands must match the validated typed plan exactly and contain no internal newline. Debug tasks can only reference already registered deterministic shell output. Both the coordinator and report-driven remediator call the combined checks before execution.

The reviewer independently rechecked these synthetic cases after the fixes:

| Synthetic mutation | Final outcome |
|---|---|
| Extra chaos target after the named target | Rejected |
| Alternative kubectl context/config or privilege flags | Rejected |
| Second command separated by newline | Rejected |
| Background `&` command or shell variable/glob expansion | Rejected |
| Unchecked `pre_tasks` / `post_tasks` | Rejected |
| Altered KUBECONFIG/PATH execution environment | Rejected |
| Debug `lookup('pipe', ...)` expression | Rejected |
| Changed rendered target or task condition | Rejected by regression tests |
| Canonical typed chaos plan and renderer output | Accepted |

No payload was executed. The newline, background-command and debug-lookup cases had passed earlier revisions and were actual defects, not hypothetical suggestions. The final suite also incorporates targeted regressions. An intermediate test failure caused by an old safe-playbook fixture lacking the new required field was corrected; the final suite is green.

## Work still required before later claims/live work

These are not reasons to postpone the approved A/B bounded starts:

1. **Checkpoint provenance:** `score_m2_formal.py` currently accepts a checkpoint with matching seed and `training_variant='patch'`, without checking its bytes, source split or feature-policy identity. The actual three files inspected here are correct. Pin/verify their content hashes when transferring/scoring; do not treat matching filenames/metadata alone as the audited-checkpoint identity. M1 matrix analysis similarly trusts serialized ranks instead of independently checking every candidate/rank against the manifest; current frozen rows were checked by this reviewer, and new rows need that artifact check before claims.
2. **Complete analysis:** M1 matrix reporting lacks the requested per-application/scenario/changed-input strata and repeated-condition primary inference; it currently provides an incident-bootstrap sensitivity result. M2 counts attempts by route but pools token/cost/latency totals, does not aggregate preserved cache-token fields, and does not yet report all semantic/fallback/operation/effective-graph-equality summaries. These are later analysis requirements; keep the raw files. An Anthropic-shaped synthetic usage object containing cache counters retains them in the journal path but the analyzer's totals loop excludes them.
3. **Modality scope:** `no_trace_features` keeps saved mixed-source call edges and explicitly discloses that choice. It cannot be reported as removal of traces plus trace-derived edges. Source attribution is absent from the saved compact schema. This does not block the matched-training launch.
4. **Full recovery contract remains unimplemented:** the current schemas still do not enforce evidence IDs, current-state preconditions, executable rollback or three-valued independently measured business oracles. Category-named chaos deletion is cleanup, and the report-only remediator constructs an evidence-empty report. The local syntax/namespace gate is not the 60-fixture B4 contract study and cannot establish recovery safety or application recovery. Keep live mutation stopped until those separately specified gates are implemented and reviewed.
5. **Inventory/claim boundaries:** `review_inventory_v1` correctly separates the 798 topology cases from the historical 760 records, reports 398 naturally topology-only TrainTicket cases and labels historical workload unknown. Its historical table audit still has three impossible mABC top-k rows; this is an unresolved paper-evidence gate, not a reason to alter the new experiment or those original cells.
6. **Prompt/code freeze:** frozen observations now prevent data drift. Preserve this reviewed code version across sanity, continuation and scoring; started request messages remain the authoritative serialized prompt record. The campaign does not independently fingerprint source-code/prompt-builder changes.

## Validation and artifact identity

Final local command:

```text
PYTHONDONTWRITEBYTECODE=1 .venv-e2r/bin/python -m pytest tests/e2r tests/test_llm_transport.py tests/test_safety_guard.py tests/test_pipeline_deterministic.py -q -p no:cacheprovider
```

**40 tests passed.** Additional reviewer-authored pure checks confirmed source freeze rejection, the GPU-idle state machine, nine adversarial guard mutations plus canonical acceptance, full-corpus route factual parity, fixed split equality and existing M1 row/candidate/rank integrity. No trained model or experimental outcome was created.

Verified audited M1 checkpoint SHA-256 values:

```text
model_seed42.pt  c4895cf287eb11dae65267fdf0e6c53d7053680ec651e3a7066e75baf58889f5
model_seed43.pt  5b1486bf9bd8e33d8569170ecc9d79344409e0647062fc6c801ad5661c36b467
model_seed44.pt  ce72f2987e5e9514eea28f56c76d2c50e67445dc8746b79006071fa95ecbfb80
```

All three report seed 42/43/44 respectively, 25 input features, patch training, `shared_observation_v1`, unit structural edge weights and the same 128×2 fixed configuration.

Reviewed final source fingerprints (SHA-256):

```text
40172a3f1514e33f0d382adf5916da1787ec30a1bd4489da8acfac3cb3e81a57  scripts/e2r/run_graph_experiment.py
6c5b94c7326ed533112aa36c41247cd710dfe9843438795fc625c3e55decac1c  scripts/remote/reviewer_full_worker.py
a74277b886803d65b7d2a62ec5e097711bfe86845a79704d32150104f045c7e2  scripts/e2r/run_m2_formal.py
188d78b66b0e56bb9ad39d7bde14e123b8abf15eadfff0456c796d5654fe63d6  scripts/e2r/score_m2_formal.py
daaa2af3791175b5b1f28cab4bad2fd85f9b37d4664727dd1a92a24476b468ce  scripts/e2r/analyze_m2_formal.py
33910c4f0bd3498ac07cb3dd98b6c648e608aa24e50c2d43faee56dde7bbce1d  scripts/e2r/analyze_m1_matrix.py
a6c1ff4f985c1fad056a7b2acc39803772604785ff46ce5794d6ab3fd0c411f9  methods/E2R/graph.py
87bc01b366dd16f35923197ab43b21cdd357ff478e29c813d2997656f75200d1  methods/E2R/llm_pilot.py
9d86328e1224d9ce0207234f76371601d12ba86e1a77d1e3f8ede1678f10d336  methods/E2R/data.py
1be8b4105f9cd78c81f3f7804704ce4b38fe8b91a816465d3f6904f044402a3d  rca_bench/llm_transport.py
b366cc42e9f06cce5a0335a57c3bf0892a488ce6fec4a457d8950dbf48bf36fa  methods/PlanRCAThinkRemed/safety_guard.py
4c558e4e581f67d260cc6c4749d6401cda44ef59cc8f6a33d4f459774cdd35e5  methods/PlanRCAThinkRemed/playbook_generator_agent.py
```

## Addendum — Qwen bounded-output compatibility rescue

Date: 2026-09-26. Same fresh reviewer; same-family provisional review. No network/API/GPU/SSH/Kubernetes action was taken by the reviewer in this addendum.

**Verdict: the minimal rescue passes the local code gate for a separately recorded v2 sanity campaign.** A and the bounded C verdicts above remain unchanged; their reviewed implementation fingerprints still match. B is still conditional on the new three-route live sanity passing. This change does not establish that the gateway honors `thinking` or that internal thinking caused the v1 empty output.

Read-only inspection of `m2_qwen38_pilot_formal_v1` confirms three completed transport-successful attempts, one per route, all reporting `qwen3.8-flash`, all with `output_tokens=2000`, empty normalized response text and strict acceptance zero. Recorded totals are **14,456 input tokens and 6,000 output tokens**. There are no unknown-outcome requests. The old responses were not stored as full envelopes, so their block types/stop reason cannot be reconstructed from those records. Thinking exhausting the bounded output is a plausible explanation, not a verified diagnosis. The old status file says `in_progress`; the strict-sanity continuation gate nevertheless prevents further requests. Treat it as a stopped, incomplete compatibility campaign, not a completed experiment with 45 failures.

The reviewed change is bounded:

- Both Qwen private configs now supply `thinking: {type: disabled}`. The transport includes it only for the Anthropic Messages route. `request_settings` records exactly the same object as the outgoing request body, keeping the setting identical for all three experiment routes. OpenAI/Responses bodies are unaffected.
- The old v1 campaign settings lack `thinking`. Attempting to use the new configuration in that directory therefore encounters the existing immutable-campaign mismatch check before any request. Preserve v1 and use a distinct v2 directory; no automatic retry was added.
- `call_once` now retains the parsed provider response envelope for both normal and malformed envelopes, alongside normalized response text, model and usage. Request headers/configuration are not copied into that field. The strict output validator still rejects empty text even if transport and envelope parsing succeed; raw thinking blocks are not converted into candidate rankings.
- The 2000-token output limit, temperature 0, one-call transport, concurrency lock, exact scheduled ceiling and strict three-route sanity gate remain intact. A full fresh v2 eight-case/two-generation campaign has 48 possible attempts, including its three sanity attempts. Alongside the three spent v1 calls that means **up to 51 Qwen formal-pilot-series attempts**; v1 usage/failures must remain in cumulative expenditure. A new output directory does not erase those calls or provide automatic budget authorization.

Validation: **10 focused transport/M2 unit tests passed.** A separate mock-only response fixture verified that the recorded and serialized thinking settings match, a `stop_reason=max_tokens` thinking-only envelope is retained verbatim, normalized output remains empty, output usage is preserved, strict acceptance remains false, and the synthetic API key is absent from the recorded result. No live success is claimed.

Updated SHA-256 fingerprints for the rescue:

```text
fc3ab2eda4c9f08c5104cc354745bd921897729641c1ba8feee2d0f4ef3b2be1  rca_bench/llm_transport.py
b30f2d358bdae6f3d0ffe6b756c45d6d961f983b9eb09afbfad4c6fd57cc7985  methods/E2R/llm_pilot.py
c4f83c759dcd411a8a9b5446a16502afd0d94ed2fb88eb11c0984bb1e06d12bd  tests/test_llm_transport.py
6b7e42a66699fcbf71b3b7c9554cc4b25db50058b0c4161eb022f44a06091274  tests/e2r/test_m2_llm.py
```

Read-only v1 request-journal fingerprint at addendum review: `36804f8440abd57d9b3e1ffa96639a2144dfc6fe040236776be10376cf26dde9`.

## Addendum — explicit generalization splits and operation-group deletion

Date: 2026-09-26. Reviewer `/root/full_experiment_code_review`; same-family provisional review. **PASS for the specified next GPU wave**, with the existing artifact/claim limits below. This addendum covers `explicit_split`, `make_generalization_splits.py`, `generalization_worker.py`, `run_operation_ablation.py`, and the new split/dependency tests. Only local reads, CPU unit tests and mocked execution were used; no GPU query, SSH, training, API or cluster action occurred.

### Explicit split and generalization queue

All six saved split files exactly equal the declared filters of the audited M1 base split, including list order:

| Run | Train | Validation | Test | Materialized union |
|---|---:|---:|---:|---:|
| LOFO T1 | 425 | 87 | 29 | 541 |
| LOFO T2 | 460 | 94 | 22 | 576 |
| LOFO T3 | 425 | 87 | 29 | 541 |
| LOFO T4 | 388 | 80 | 36 | 504 |
| Boutique → TrainTicket | 282 | 59 | 57 | 398 |
| TrainTicket → Boutique | 284 | 57 | 59 | 400 |

`explicit_split` requires exactly nonempty train/val/test lists, rejects duplicate IDs, cross-partition overlap and unknown IDs, preserves each declared list's order, and returns only the selected union for materialization. Reviewer fixtures confirmed duplicate, overlap, unknown, empty and extra-key rejection. The trainer replaces its full `cases` collection with that union before the graph-building loop and disallows combining explicit splits with `--limit`. Thus unused base cases are neither materialized nor trained. The actual manifest's case IDs are unique, as already verified above.

LOFO removes the held-out scenario from both base training and base validation, then tests only the corresponding **base-test subset**. Cross-app uses source-app base train/validation and target-app base test. Training and validation remain patch-only with the unchanged fixed architecture/loss/optimizer/stopping rule and seeds 42/43/44. The worker launches six runs, hence 18 model fits, and the default evaluation retains reference/runtime/patch/node-only inputs. Each run saves its exact split, per-seed checkpoint, run metadata and per-case predictions in a separately named directory.

A fully mocked worker run confirmed all six commands have the right split/output names, patch training, seeds 42/43/44, physical-GPU selection via `CUDA_VISIBLE_DEVICES`, child `cuda:0`, and `start_new_session=True`. The initial mocked idle sequence `[idle,busy,idle,idle]` yielded consecutive counts `[1,0,1,2]`; each later stage required two fresh idle samples. A second invocation rejected existing evidence before launching a child; a held file lock rejected a concurrent generalization worker. A mocked child exit code 2 stopped after one stage and recorded FAILED. A mocked stop sent SIGTERM to the child's process group and exited with STOPPED/143.

No overwrite/concurrency defect was found for this worker entry point. Its lock is specific to the generalization queue, not a global GPU reservation shared by every worker in the repository; stage the next wave after other GPU queues finish rather than allowing two pending queue workers to compete for the same idle GPU. The lower-level trainer's partial-directory limitation noted above remains unchanged.

**Claim boundary:** this new intended protocol is a filtered base-test generalization study, not the earlier design review's all-199-T3 held-out population. T3's test denominator here is 29, not 199. The patch-only LOFO fits also do not supply the earlier proposed matched runtime-training comparison. Report this exact protocol and its denominators; there is no split leakage from these choices.

### Frozen operation-group deletion

The operation modes are correctly scoped: full catalog; drop `ALIGN_NODE`; drop `ADD_BLACKHOLE_NODE` plus `ADD_TRACE_MISMATCH_EDGE`; and drop `SPLIT_NODE`. `selected_ids` repeatedly removes selected operations whose prerequisites are absent until reaching a fixed point, then returns canonical catalog order. In addition to the new unit test, the reviewer checked a reversed-order three-operation dependency chain: dropping its root removed both direct and transitive dependents while preserving an unrelated operation. Full mode retained all IDs in catalog order.

Plans pass through the same `select_plan` and warning-rejecting `execute` path before tensorization. An execution failure is collected and causes the entire run to fail before any completed-results files are emitted; it cannot silently shorten the success denominator. No model is retrained by this script. All three actual M1 checkpoint content hashes still equal the audited hashes listed earlier in this report.

A mock-only end-to-end operation run with 116 synthetic case IDs and fake models produced **1,392 unique exact `(case_id, mode, seed)` identities: 348 rows per mode = 116 incidents × 3 seeds**. A repeat invocation preserved existing results and rejected overwrite. Injecting one execution failure prevented success output entirely. These temporary synthetic results were not added to any research-result directory.

The script relies on being given the audited base split and checkpoint directory; it does not itself enforce a test size of 116, reject repeated test IDs, or validate each checkpoint's seed/content hash. This addendum's pass is for the exact verified 116-case split and three audited checkpoints, not arbitrary interchangeable inputs. Preserve the supplied split/checkpoint identity with the output and audit exact rows before claims, as required by the earlier artifact gate. `cases_changed_from_full_selection` measures changed operation selection counts, not necessarily changed effective graph tensors or rankings.

**Validation:** all **8** tests in `tests/e2r/test_observation.py` passed, plus the reviewer-authored invalid-split, actual-split equality, mocked worker lifecycle, transitive dependency, exact-row, failure-abort and no-overwrite checks described above. No concrete launch blocker remains for this scoped wave.

Reviewed source SHA-256 fingerprints:

```text
5dfbad3b44dc4fb714258b9b195bc962736bca48a16a7b7ce8b7485d240c67cb  scripts/e2r/run_graph_experiment.py
ee5f18b7507d46006fb15611efc6ff6df595e4c0f3b1e03b4362babf42844e08  scripts/e2r/make_generalization_splits.py
8d59ae31ff739c862e5ab5e6d5451be28d1aceec69a29975ed634de28f77a54d  scripts/remote/generalization_worker.py
a2d55925bca8d7121a4671cb0b2c7b50d75d0d1635442c7887f87fa02db5a861  scripts/e2r/run_operation_ablation.py
ff73c9ff10fa60584ac340c67b50c8101e38adbdee9a7d9047c7980fd92d8b41  tests/e2r/test_observation.py
```

## Addendum — recovery evidence, preconditions and three-valued oracle extension

Date: 2026-09-26. Fresh review by `/root/full_experiment_code_review`; same-family provisional. **Verdict: BLOCK the extended C recovery-contract gate.** The earlier A/B start verdicts are unaffected. The existing syntactic namespace/command/YAML protections remain useful, but this extension does not yet make evidence/precondition/post-state acceptance reliable. No API/network/cluster/GPU call, real probe or repair was made. All failure reproductions used in-memory fixtures and a mocked `safe_probe_agent.run_probes`.

The reviewer read the modified schemas, deterministic planner, safety guard, coordinator, report-driven remediator, `safe_probe_agent.py`, `verification_bridge.py`, `rca_bench/recovery_oracle.py`, and relevant tests. The requested targeted test command passed **29 tests**:

```text
PYTHONDONTWRITEBYTECODE=1 .venv-e2r/bin/python -m pytest tests/test_safety_guard.py tests/test_pipeline_deterministic.py tests/test_remediator.py tests/test_recovery_oracle.py -q -p no:cacheprovider
```

Passing these tests does not cover the reproduced counterexamples below.

### C-R1 — precondition execution fails open (blocker)

`run_preconditions` treats absence of a few textual error substrings as PASS. It does not require exact result coverage, nonempty parseable observation output or a successful process result. For a mutating plan with a nonempty precondition list, the following mocked returns all produced **PASS**:

- `[]` — no precondition result at all;
- one `allowed=True` result with empty output;
- `allowed=True` with `STDERR: The connection to the server localhost:8080 was refused - did you specify the right host or port?`;
- a successfully printed unrelated/healthy resource object.

The first three directly contradict the promised missing/failed measurement gate. `safe_probe_agent` records permission to run (`allowed`), not command success; its string wrapper discards the numeric return code. The coordinator and report-driven remediator correctly skip execution when `passed=False`, but these false PASS results bypass that new branch.

**Minimum fix:** return structured process status, timeout and stdout/stderr from the precondition runner; require one matching result per requested command and an explicit successful exit. Missing/truncated/unparseable output, transport/authentication/connection errors and timeouts must be UNKNOWN/non-success. Do not infer success by absence of error words. Keep the existing execution skip when any required precondition is non-PASS. Add fixtures for the exact cases above.

The precondition runner also inherits the probe runner's kubeconfig/context, while the generated play explicitly sets `/etc/rancher/k3s/k3s.yaml`. Bind precondition reads and mutation execution to the same declared cluster/client configuration; matching only a namespace string cannot establish that both refer to the same cluster.

### C-R2 — evidence and preconditions are not bound to the mutation (blocker for the evidence-bound contract)

The reviewer constructed a canonical `RecoverConfigError` action targeting `deployment/cartservice` in `e2r-review-synthetic`, with:

```text
evidence_refs = ['nonexistent-reference']
preconditions = ['kubectl get deployment unrelated -n e2r-review-synthetic']
rollback_hint = 'x'
expected_effect = 'x'
```

`check_repair_plan(..., allowed_namespace='e2r-review-synthetic')` returned **`(True, [])`**. The namespace is checked, but the precondition resource/name is not bound to the action target. Any nonempty evidence/rollback/effect text satisfies the new presence checks. There is no lookup proving an evidence reference exists or concerns this action, and no predicate verifying a captured current state. Consequently a successful `kubectl get` on a healthy deployment is sufficient even when the supposed underprovisioning is absent or the plan is stale.

The deterministic planner's fallback `rca_prediction:<service>:<category>` is a reference to a diagnosis, not independently observed fault evidence. This is acceptable metadata for the explicitly diagnosis-only template arm, but it cannot be counted as implementing the evidence-bound arm. The deterministic config rollback still describes assumed old limits instead of storing the actual prior values or an executable inverse.

**Minimum fix:** for the currently implemented bounded primitives, resolve references against the supplied report/evidence context and distinguish prediction-only references from observed support. Require the precondition to read the exact action resource/name/namespace, parse that object and check the declared current-state predicate before mutation. Capture the actual approved old fields and a target-bound rollback representation; arbitrary descriptive strings are not a rollback contract. Wrong-target, nonexistent-reference, healthy/stale-state and unavailable-rollback fixtures must fail. This need not expand the action space.

### C-R3 — post-repair oracle can PASS invalid or missing measurements (blocker)

Reviewer-created `WorkloadWindow` fixtures reproduced these outcomes:

| Fixture | Actual result | Required handling |
|---|---|---|
| Technical values `{'fault_absent': 0, 'resource_restored': 'false', 'ready_endpoints': []}` with otherwise good windows | PASS | Reject invalid Boolean measurements / UNKNOWN; never PASS |
| Baseline `samples=0` with otherwise plausible numbers | PASS | Missing calibration must be UNKNOWN/non-success |
| Baseline p95 = infinity; post p95 and throughput = infinity | PASS | Non-finite measurements must be UNKNOWN/non-success |
| `required_windows=0`, no post windows, technical checks true | PASS | Reject invalid configuration; no zero-window recovery |
| Required state canary missing (`None`) in all windows | FAIL | Missing required measurement is UNKNOWN/non-success |

The technical validator tests only identity with `None` and `False`; other values slip through. Numeric fields have no finite/type/domain validation and baseline sample sufficiency is not checked. The window type also contains no timestamp, interval or source identity, so the function cannot distinguish three fresh nonoverlapping observations from repeated/stale samples; such assurance would currently have to come from a caller, and there is no production caller yet.

**Minimum fix:** validate required positive window/sample configuration; require actual Boolean technical values; validate finite, physically valid numeric measurements and sufficient healthy baseline samples. Treat missing required canary data as UNKNOWN. Record and validate the window timing/source information required to establish freshness/order and three distinct consecutive observation windows, either in this function or in the collecting caller. Extend tests with the exact invalid-input fixtures above before assigning a local oracle PASS verdict.

### C-R4 — new oracle is not yet the runtime success gate (integration blocker)

Repository reference search found `evaluate_recovery`/`WorkloadWindow` only in their module and unit tests. The coordinator still calls `verification_bridge.run_verification`, whose `success` is solely the legacy Boolean Chaos checker result; that result still sets the final trace's `success=True`. No workload-window or three-valued technical check participates in this path. The report-driven remediator reports execution status, not independent application recovery.

**Minimum fix:** before the new recovery campaign, connect its measurement collector and success field to the reviewed three-valued oracle, or explicitly label the existing Boolean result as legacy technical-check success and keep application-recovery success unavailable. Missing workload observations must never inherit the old Boolean success. Add a mocked integration fixture where the legacy checker returns true but workload observations are missing/failed; application-recovery success must remain false/UNKNOWN.

### What this extension does correctly

The new schema fields survive `from_dict`/serialization. Deterministic actions populate precondition commands using the intended action namespace and target. The normal coordinator/report-driven paths validate the plan/YAML before invoking preconditions, retain precondition diagnostics and avoid action execution when the returned gate is non-PASS. Existing namespace/target mismatch, shell/YAML escape and three-valued basic fixtures still pass. The necessary changes are to the acceptance semantics and their measurement integration, not to expand the model, taxonomy or baseline work.

This addendum supersedes any interpretation of the earlier bounded syntactic C PASS as approval of the full evidence/precondition/business-recovery contract. Keep live mutation and new C2 claims stopped until the reproduced blockers are fixed and rechecked.

Reviewed extension fingerprints (SHA-256):

```text
100a191b2a4b2619bb4b63b3769d9933e9c1a200cc05bb69a0c7889a634ee94f  methods/PlanRCAThinkRemed/schemas.py
54818c576434be889328a2b25331a9ea25af7d572353670b74b83efdeaecc949  methods/PlanRCAThinkRemed/repair_planner_agent.py
117917b91e05b02c4cf6e6a1ee6fbbf5a6ef2b82814857921cc880db0016951e  methods/PlanRCAThinkRemed/safety_guard.py
8de3d2b50fb1d1e09c385043fbae7e6d10c87918e6c71e62a858985b7998227c  methods/PlanRCAThinkRemed/safe_probe_agent.py
128f4cc04784ef0bb750b1f7221c1c408ceaccdf3a7b368ee22caae7b20dfd4b  methods/PlanRCAThinkRemed/coordinator.py
d05e84474313b7414ff7262cd3a1ceceae54b103b17168a89919e0ef6d55702c  methods/PlanRCAThinkRemed/verification_bridge.py
d6649387f800330c2f11006be229008a8d2b7bdc9db216169efa212fe3cdd444  rca_bench/remediator.py
a6f799fe7e04eaf048ad7de907007a70c0a14f49ce427e115c2edb4478fbb2e6  rca_bench/recovery_oracle.py
5ea75ce8475d9f37257d0b449f07474be6b1f7e6b04c2d084ddefaea2e747b98  tests/test_recovery_oracle.py
58c9897f65b3ee3e5b41fb246739c58b5749d90d1f07b7f5b4c04cd357788fe5  tests/test_safety_guard.py
```

## Addendum — targeted C recheck after counterexample fixes

Date: 2026-09-26. Same reviewer and provisional independence status. **Final C verdict for this revision: BLOCK remains.** The recheck was limited to the previous counterexample families and mocked coordinator integration; no new live operation or external call was performed. All **31** targeted tests passed.

### Corrected items

- The original arbitrary-reference / wrong-precondition-target / one-character rollback-and-effect plan is now rejected.
- Missing precondition result rows and a literally empty `output` field now return UNKNOWN.
- Every previously reproduced oracle invalid-input fixture now fails closed: non-Boolean technical values, zero baseline samples, non-finite measurements and missing required canaries return UNKNOWN; `required_windows=0` raises ValueError.
- A fully mocked coordinator with a successful legacy checker produced UNKNOWN/non-success when the business callback was absent or returned UNKNOWN, FAIL/non-success when it returned FAIL, and PASS/success only when the business callback returned PASS. No actual RCA/model/probe/playbook/checker was run. This closes the prior specific legacy-checker-only success path, although the real collector/callback still needs its separate live-data implementation and validation.

### Remaining reproduced precondition blocker

The new target-name test scans the entire `output` string. The actual `safe_probe_agent` wrapper includes the original command, so a target-bound command supplies the target's name even if its response is empty, failed or unrelated. Replaying the **same prior failed/missing/unrelated measurement fixtures in the existing runner's native string format** still gives PASS:

```text
command:kubectl get deployment cartservice -n e2r-review-synthetic -o json
response:
```

```text
command:kubectl get deployment cartservice -n e2r-review-synthetic -o json
response:STDOUT:

STDERR:
The connection to the server localhost:8080 was refused - did you specify the right host or port?
```

```text
command:kubectl get deployment cartservice -n e2r-review-synthetic -o json
response:{"metadata":{"name":"unrelated"},"status":{"readyReplicas":1}}
```

All three returned **`status='PASS', passed=True`** under mocked `run_probes`. The standard kubectl error above also does not contain the newly searched contiguous phrase `connection refused`. These are the earlier C-R1/C-R2 measurement failures, not a new requested scope. Since both execution callers trust `passed`, the extension still permits mutation without a successful observation of its actual target.

**Minimum correction before another C pass:** preserve the process return code/timeout and actual stdout separately from the echoed command; require successful execution and a nonempty, parseable expected object. Compare returned object kind/name/namespace against the action and evaluate its precondition predicate. Exact coverage must include command/result identity, not only list length. Do not use the command echo or error-text denylist as evidence of a current target state. Use the three native-wrapper fixtures above in the regression tests.

The prior evidence/current-state/rollback limitation also remains only partially addressed. Canonical target-bound read commands are an improvement, but service-name substring matching does not resolve evidence references, a successful resource read does not test the claimed faulty/stale state, and a 12-character minimum on descriptions does not capture prior resource fields or an executable rollback. These unchanged limitations remain part of C-R2; the earlier source/client-configuration and window-freshness obligations also remain open. They are not prerequisites for A/B offline work, but prevent promoting the extended guard to the full evidence-bound recovery contract.

Reviewed recheck fingerprints (SHA-256):

```text
65ca3a746e4c8d6dcd6822d5b23b1bd5459dc47948564f8bf75864a9f0b24ca1  methods/PlanRCAThinkRemed/safety_guard.py
8de3d2b50fb1d1e09c385043fbae7e6d10c87918e6c71e62a858985b7998227c  methods/PlanRCAThinkRemed/safe_probe_agent.py
4b10dfa5c936504692eaea525288e406cc7a0b7a1b4eb595a67509ad3619aad9  methods/PlanRCAThinkRemed/coordinator.py
b12bfdb6ff94dba9c1ab4fae9dde71d60ca38c969462eb146f3e7838382ebaca  rca_bench/recovery_oracle.py
961dd7bdc5e1ae8991977c17326a9daf475e702b06850fd6a6ff28cb3019f1e5  tests/test_safety_guard.py
2169759a2605520e3669abf670a20096a3d9444277cf9d30eadb32e9e13c3632  tests/test_recovery_oracle.py
```

## Addendum — native-response and evidence-resolution fixes verified

Date: 2026-09-26. Same reviewer, same-family provisional. **Targeted local C regression gate: PASS.** This recheck closes the three native-wrapper false-PASS reproductions and the tested unresolved/mismatched evidence-reference cases. It does not extend the scope to live execution or the still-open full B4 contracts. No live probe/API/cluster/GPU operation occurred.

The reviewer replayed the previous strings verbatim, including `command:<canonical target get>`, the first `response:` delimiter, and the original response content:

| Prior native response | Rechecked result |
|---|---|
| Empty response body | UNKNOWN, `passed=False` |
| Standard `The connection to the server localhost:8080 was refused ...` stderr | UNKNOWN, `passed=False` |
| JSON object named `unrelated` while the echoed command targets `cartservice` | UNKNOWN, `passed=False` |

The implementation now checks the response body rather than the echoed command; the demonstrated echo-induced target-binding failure is closed.

The evidence recheck also confirmed:

- arbitrary `nonexistent-reference` and out-of-range `evidence:99` reject as unresolved;
- `evidence:0` resolving to an unrelated service rejects;
- `evidence:0` resolving to a matching report evidence item accepts;
- the deterministic planner maps that actual report item to `evidence:0`.

All **32** tests in the same safety/pipeline/remediator/oracle suite passed. The previously verified strict oracle input checks and coordinator business-callback requirement retain their earlier scoped verdict; this turn did not repeat or broaden their independent review.

**Current acceptance boundary:** local syntax/namespace, these precondition-response regressions, tested evidence-reference resolution, and the prior oracle/callback regressions pass. The full evidence-bound/live C gate remains unaccepted until the already recorded obligations are completed: actual command-success and command/result identity, a shared probe/mutation client context, parsing and checking the declared current-state predicate rather than resource-name presence alone, captured prior fields and executable rollback, and fresh/distinct workload-window measurement. This patch does not claim those capabilities, and this review does not invent an expanded test scope. A/B offline verdicts remain unchanged.

Reviewed fix SHA-256 fingerprints:

```text
1d7c8f0f9cce5bd0a5000bcf916f38a6aa24fe60a20235a704821b1de7048189  methods/PlanRCAThinkRemed/safety_guard.py
12eeaa6ce85d1b6acbe33aab02ee7a9572ba36470ba94dbfe57cf0f9d459976f  methods/PlanRCAThinkRemed/repair_planner_agent.py
71ebb0d2a9ad993b2184694c0de6b346af32a4aaa1f85a0097cc5112e9afbc93  tests/test_safety_guard.py
```

## Addendum — full bounded B4 local contract and baseline-preparation re-review

Date: 2026-09-26. Reviewer `/root/full_experiment_code_review`; `review_independence: same-family`, `acceptance_status: provisional`.

This was a fresh direct review of the current cluster client, safe probe runner, guard, schemas, planner, renderer, coordinator, report-driven remediator, recovery/workload oracles and requested tests. The isolated deployment manifest was also inspected for the requested preparation verdict. The existing MicroRemed execution-agent wrapper was read to verify how its success flag is derived. The executor fixed defects found during this pass; the verdict applies to the final fingerprinted revision below. The reviewer made **no network/HTTP, SSH, GPU, Kubernetes, Ansible, fault-injection or repair call** and made no implementation edits. Process/socket/HTTP operations in checks were replaced by mocks or supplied request functions.

### Final verdicts

| Requested gate | Verdict | Exact scope |
|---|---|---|
| (a) Local recovery-contract gate | **PASS, provisional** | The implemented container resource-restoration and target-bound Chaos-cleanup primitives, plus WaitAndVerify, under the declared default client context. The reproduced blockers from this review are fixed; this is a local contract result, not demonstrated live recovery. |
| (b) Namespace dry-run/deploy and one no-fault workload baseline | **PASS, provisional** | Proceed with server-side dry-run of the reviewed `isolated_boutique.yaml`, deploy only its declared `e2r-supplement-20260925` resources if that gate passes, and collect one bounded, independently identified healthy baseline measurement. Run no fault injection or remediation in this preparation step. |
| Fault injection and live repair | **BLOCKED pending runtime gates** | The namespace/client/permission checks, actual health and workload calibration, frozen live protocol, and one reviewed restoration/rollback sanity have not been performed by this reviewer. Local mock results cannot substitute for them. |

The preparation PASS is deliberately separate from live repair. A healthy baseline is useful evidence even while the fault/repair gate is closed. This clearance does not assert that the cluster currently has capacity, that the namespace is healthy, or that a server-side dry-run has succeeded.

### Prior blockers independently replayed

**Process, command and client identity.** Safe probes now use argv with `shell=False`, set the shared kubeconfig environment and retain return code, timeout, stdout, stderr, exact command and client-context metadata. The reviewer replayed missing rows, a wrong command, a different client context, unavailable/nonzero process status, timeout, empty stdout, invalid JSON and a wrong returned target. They return UNKNOWN/non-success. A currently healthy resource, replacing the previously underprovisioned state, returns FAIL. The valid target fixture passes and produces a capture. The unused named-context field that was only logged rather than executed was removed; this verdict is for the supported kubeconfig-based context.

**Evidence and current-state predicates.** Indexed evidence references resolve against the report; explicit diagnosis-only references are checked against the report and tagged `prediction_only`, while actual referenced observations are tagged `observed`. The distinction is preserved rather than presenting a prediction as an observed measurement. The guard binds each implemented mutation to its canonical JSON read and declared predicate. The resource predicate selects one named container and tests its observed CPU/memory limits; the chaos predicate checks object kind/name/namespace, non-terminating state and the diagnosed service selector. The earlier malformed/incorrect-target/healthy-state cases no longer pass solely on text presence.

**Captured inverse and failed execution.** The guard captures actual pre-action fields. During this review the first rollback implementation used strategic merge, which left newly introduced request fields behind when they had been absent originally. The final implementation emits JSON Patch with a test of the indexed container name and replacement of the complete captured resources object (or removal when appropriate). The reviewer replayed the absent-original-requests fixture and applied the emitted test/replace semantics to a local post-mutation object: it returned exactly to the captured object without changing the other container.

The first reviewed renderer also used `ignore_errors: true`, while the MicroRemed wrapper reports success from the Ansible process exit code. That could hide failed actions/rollouts from rollback. Final action and rollout tasks use `ignore_errors: false`; the guard rejects an altered playbook that enables ignoring. Mocked coordinator execution failure invokes rollback exactly once, skips legacy/business success evaluation and leaves recovery non-success. Both the coordinator and report-driven path retain the captured rollback diagnostics. Actual cluster acceptance of an inverse is still a runtime sanity requirement.

**Technical post-state identity.** During this review, wrong-context/wrong-target synthetic probe responses initially produced all three technical checks as true. The final `_json_probe` requires one exact command/result, the declared client context, explicit allowed/not-timed-out state, an integer process return code, and the expected JSON kind/name/namespace. Replaying wrong object identities and wrong contexts now leaves technical checks unknown. Hidden evaluator labels are not supplied to these measured technical predicates.

**Workload windows and callback integration.** Repeated IDs, cross-source observations, overlapping windows, windows beginning before repair completion, and stale measurements return UNKNOWN. An initial bug let an old three-window success prefix survive a fresh failing window. The final oracle requires a fresh **trailing** successful streak; the reviewer replayed passing windows ending at time 50 followed by a failing window at 990–999 and evaluation at 1000, and success was rejected. Missing/nonfinite measurements and missing required state canaries remain non-success.

The mocked coordinator returns UNKNOWN without a business callback, FAIL for a failing callback and PASS only when both its legacy technical check and business result pass. It supplies the actual execution-completion timestamp and declared client context to the callback. A failed execution cannot obtain a business PASS. The supplied business callback collects distinct timed workload windows and technical measurements before evaluating them.

**Baseline attribution and sample denominators.** The first port-forward readiness test could mistake a preexisting local listener for its own process. The final collector rejects an occupied local port before spawning and requires the kubectl process's forwarding-ready evidence before connection success counts. Reviewer mocks verified the occupied-port rejection and the positive process-readiness path without opening a socket. Nineteen invalid latencies plus one valid latency previously yielded a 20-sample finite p95; replay now preserves all 20 attempts, reports only one valid latency and emits nonfinite p95, making that window unmeasurable.

### Preparation boundary and exact remaining runtime gates

The parsed manifest declares one experiment namespace; other objects are scoped to it. It includes workload/executor service accounts, a namespace Role/RoleBinding, network policies, quota and ClusterIP application services. Workloads use the experiment workload account with token automount disabled. The reviewed deployment is a separate benchmark instance, not an update to `online-boutique`.

Before fault injection/live repair, retain these concrete gates:

1. Complete server-side validation and establish the actual experiment namespace/resource identity, deployed health, endpoints, image identities and resource headroom. Preserve existing user-owned namespaces and campaigns. A local manifest review is not a runtime deployment result.
2. Verify that preconditions, execution, rollback and measured technical checks reach the same actual cluster and intended isolated namespace using the declared credentials. Creating a namespace Role does not itself cause the current kubeconfig-backed executor to use that scoped identity. Effective permissions and the intended execution host/client must be checked before live repair.
3. Freeze and collect the healthy workload calibration and the live observation schedule. The default collector is a bounded request-count collector; its defaults do not establish the earlier planned 180-second calibration/30-second workload-window protocol. Record endpoint/source, request count, concurrency, duration, sample sufficiency, offered workload and baseline tolerances. One preliminary healthy measurement is not an application-recovery result.
4. Run the separately gated one-case restoration/rollback sanity and verify the actual captured inverse, state canary where required, and a fresh independent post-state verdict before any expanded fault campaign. Failed/UNKNOWN observations must remain non-success. The implemented local action scope here is resource restoration plus chaos cleanup; selector restoration or any additional live primitive needs its own implementation/review or an explicit narrower campaign scope.

These are outstanding execution/protocol gates, not newly alleged local regressions. There is no remaining reproduced local blocker from the requested counterexample set in the final revision. Neither the preparation PASS nor the local PASS grants a production-safety or open-ended-repair claim.

### Validation record and fingerprints

Final command:

```text
PYTHONDONTWRITEBYTECODE=1 .venv-e2r/bin/python -m pytest tests/test_safety_guard.py tests/test_recovery_oracle.py tests/test_workload_oracle.py tests/test_pipeline_deterministic.py tests/test_remediator.py -q -p no:cacheprovider
```

**40 tests passed.** Independent pure/mock checks additionally covered structured preconditions, exact inverse application on a local JSON object, execution-failure rollback, callback arguments/outcomes, technical-check identity, stale-prefix rejection, incomplete-latency handling and port-forward ownership/readiness. None of their synthetic outputs were recorded as research or live-recovery results.

Reviewed final SHA-256 fingerprints:

```text
a8004f9961c417ae9a2e4aa587f90aeb4f8c663c125db081d6bf9e31ae520fca  methods/PlanRCAThinkRemed/cluster_client.py
fe1663114ce0f1ab1eaee20cb66bc410de60939862aa1318e88a513b99cc1874  methods/PlanRCAThinkRemed/safe_probe_agent.py
392a1f54dadd41f36bd7d6d36c9462fe36838f99efb4255d3237a163efa1a3fc  methods/PlanRCAThinkRemed/safety_guard.py
6b3db2fe8664b50e84f66b0aadc5aa8758b6a6d316e689a5611d3f868dd7e721  methods/PlanRCAThinkRemed/schemas.py
892fc9f94cc068079b3df75168e8ed4508e0d67b960e941e7adae82062c61fc3  methods/PlanRCAThinkRemed/repair_planner_agent.py
1fc410be8f9c823e1b3672f2dd753029a3732135a43b12cee939ae21b8c14def  methods/PlanRCAThinkRemed/playbook_generator_agent.py
005052da154cf3ffd81972a17b6807970610e386a4c8a09ee1056a21695e6f96  methods/PlanRCAThinkRemed/coordinator.py
6e2ac9775e726c5a36adc225cf8a6f11ec2efecd0363095ce476fffd512e9dc4  rca_bench/recovery_oracle.py
2434c4585645d8a81bc5170d28e8a4b74811912c347f820863823664848da002  rca_bench/workload_oracle.py
a8352f5d31eba3624136f0e2862ae0f0883c68561dfa4df6dbe53cf6826bcd0c  rca_bench/remediator.py
d8025d89a951d0c78f1cd9cf92f2fec4ecb9320b778dd6d4c19ff400acf249a4  tests/test_safety_guard.py
fc53fad6376bc97b69cc58c5340216fe012b9f9dcd8e830b15798fcebdeafba5  tests/test_recovery_oracle.py
145a329684714e3c7fb496d789066f648f68ecd0eb9d181a4cd38af8c0a8fc57  tests/test_workload_oracle.py
d46bdd6cd51a0fa1fce409530c97284face5809c0f5be1431b1bb15e494dfad4  tests/test_pipeline_deterministic.py
7567b7f249ee7d8dcb858d75e4b5c749a83463b832b46780dd07ac0c83f553cf  tests/test_remediator.py
9f8f64c143e78763ad5371f0648a1b1eb0897ad6f886106a0da0a81866e0d576  experiments/e2r/isolated_boutique.yaml
```

## Addendum — no-fault baseline runner and synthetic multi-root proxy

Date: 2026-09-26. Fresh targeted local review by `/root/full_experiment_code_review`, same-family provisional. No network/Kubernetes/GPU/API calls, training, or implementation edits were made. Cluster/HTTP/port-forward behavior was mocked. Existing source observations were read without modification.

| Script gate | Verdict | Blocking issue |
|---|---|---|
| `run_recovery_baseline.py` | **BLOCK for this runner revision** | Namespace is unrestricted despite the approved isolated-only boundary; an all-invalid latency window set crashes before preserving the failure artifact. |
| `run_multi_root_proxy.py` | **BLOCK before scoring** | Composition creates new dangling endpoints, conflicting IP ownership and duplicated dependency relations; its retained time/coverage metadata does not describe the composite. |

The earlier approval to prepare the isolated namespace and obtain one bounded no-fault measurement is unchanged. These are implementation corrections needed for these particular runner entry points.

### Baseline runner

The **current duration-mode revision** was reviewed: defaults are 30-second warmup, six 30-second windows, target 5 requests/s and five workers. It imports the reviewed unused-port/forwarding-readiness wrapper, sets the declared cluster client environment, makes only `kubectl get`/port-forward plus bounded HTTP reads, and calls no fault, repair or mutation API. Output directories are exclusive; existing outputs are not overwritten. Raw attempt/success/valid-latency counts and latency values are retained when the normal artifact write is reached.

Two mock-only main-path checks reproduced the blockers:

1. Passing `--namespace online-boutique` succeeds and writes `status=complete` when supplied successful synthetic reads/windows. The script neither restricts the namespace nor verifies the returned deployment names/namespaces against the reviewed isolated manifest; it checks only that there are 12 deployments and available replicas are at least desired replicas. **Minimum fix:** enforce the approved namespace before any external read/traffic, validate exact expected deployment identities and positive replica/availability requirements. Do not silently redirect the baseline to a user-owned namespace.
2. Six synthetic windows with zero valid latency samples make `all_latencies=[]`; the aggregate p95 indexing raises `IndexError` before `baseline.json` is written. The immutable directory remains but the collected failure evidence is absent. **Minimum fix:** make empty/incomplete aggregate latency coverage explicitly unmeasurable, write `failed_calibration` with all collected raw diagnostics and missing aggregate p95, then exit unsuccessfully. Do not turn the shortened finite-latency subset into an accepted calibration.

The duration collector's scheduling unit test passes. The runner must still report actual elapsed/achieved load: scheduled offered-rate settings are not proof that every request was issued on schedule under saturation. Root/product HTTP availability is the measured scope; the product GET canary does not establish persistent cart/business-state preservation. Those are reporting boundaries, not new requests to run faults.

### Synthetic proxy

Pairing on the actual frozen test set is deterministic and disjoint: **29 Boutique pairs plus 28 TrainTicket pairs, with one leftover case per application**. Thus 114 source incidents enter 57 pairs and two remain explicitly listed as leftovers. Both orientations and all three scorer seeds imply 342 expected prediction rows. Pair construction uses application/scenario/truth to require different roots; source test IDs remain separate from training. The metric fixture confirms root recall divides by `2*n`, ExactSet@2 by `n`, and AP uses the two relevant roots. Orientations are reported separately rather than counted as independent incidents. Checkpoint seed/patch metadata is checked and content hashes are saved.

The proxy does not directly place `true_service` or root-label fields in tensors. However, ground truth explicitly selects the service state copied into each synthetic observation; this is **ground-truth-conditioned synthetic construction**, not label-free evidence generation. State that in the result metadata/limitations. The present simulation-only/live-interaction/production claim ceiling is appropriate.

The actual composition is not yet structurally valid:

- All **10** observed dangling-endpoint phase views are newly introduced, not inherited from either source: three Boutique and seven TrainTicket. For example, overlaying `case00418` onto `case00673` leaves `recommendationservice` pointing to an IP absent from the merged pod inventory.
- Pair **`case00749` / `case00148`** gives IP `10.244.2.253` to both `ts-preserve-service` and `ts-travel-service` in the during view, in both orientations. `graph.py` builds an IP→pod dictionary, so row order then arbitrarily determines endpoint ownership.
- Call edges are deduplicated by their complete JSON object. Different `count` values therefore retain repeated `(source,target)` relations. This occurs in **98 Boutique phase/orientation views**; `case00004` / `case00227` has 14 repeated during relations. The advertised deduplicated union should use relation identity with an explicit count-aggregation rule.
- **57 orientations** import an overlay whose source cutoff is later than the base cutoff retained in `window`. Summing all source coverage also counts observations not actually copied. Independently recorded source phases may be aligned for a synthetic experiment, but the composite must not present inherited base timestamps/counts as a real unified diagnosis window.

**Minimum fix before scoring:** construct source-safe pod/IP identities and consistently remap every affected endpoint reference; enforce no newly introduced dangling endpoints or conflicting ownership; deduplicate relations by endpoints/type, not their counters; record both source windows and an explicit synthetic phase-alignment policy and honest composition coverage. Preserve both orientations, pair/leftover IDs and the simulation-only ceiling. Do not repair this by dropping invalid pairs after seeing scores.

### Validation record

Eight focused proxy/workload tests passed, including the new duration-scheduling test. Independent checks verified actual pair coverage, new-versus-source endpoint inconsistencies, IP collisions, duplicate relations, time-window mismatch, metric denominators, and mocked baseline scope/failure behavior. No model scoring or experimental result was produced by this review.

Reviewed SHA-256 fingerprints:

```text
953577dabe17b933f44dbda9d5dd575228d41c932420a0e9cb42dae6c784d546  scripts/e2r/run_recovery_baseline.py
12f788298199b4e3c95d47d13bd53fb1562d1547522be3522b3ffd13ad74c0e3  rca_bench/workload_oracle.py
308c3f42b652f1921eae3473c224aa6972587ea0034fc56cec98128f9a4661ce  scripts/e2r/run_multi_root_proxy.py
d854a810a1de1c7e28c0e67d4e75e0c814ac3aaafc8086825eafd0462b679ec4  tests/e2r/test_multi_root_proxy.py
768f8db6e0893c26c98f123f95a24ab1472bb36cb7be3d7c64e817babd641731  tests/test_workload_oracle.py
```

## Addendum — baseline/proxy final superseding recheck

Date: 2026-09-26. Same fresh reviewer; same-family provisional. **This addendum supersedes the baseline/proxy BLOCK verdicts above for the final files fingerprinted below.** No external calls, model scoring, GPU operation or implementation edit was performed; checks used pure composition and mocked baseline execution.

| Gate | Final verdict |
|---|---|
| `run_recovery_baseline.py` | **PASS, provisional**, for one immutable no-fault measurement in `e2r-supplement-20260925` |
| `run_multi_root_proxy.py` | **PASS, provisional**, for the declared evaluator-conditioned simulation-only proxy with the audited fixed split/checkpoints |

**Baseline:** the reviewer replayed the earlier main-path fixtures. A different namespace rejects before any kubectl call. Incorrect deployment name/namespace, nonpositive replicas and unready containers reject before port-forwarding. The healthy mock completes six duration-mode windows. The all-invalid-latency mock exits 2 but preserves `failed_calibration`, all six raw diagnostic records and null aggregate p95. The scope remains baseline-only: no fault or repair code is called, and existing output directories are refused. The prior forwarding readiness/unused-port protections remain in force.

**Proxy:** independent replay confirms 57 disjoint pairs (29 Boutique, 28 TrainTicket), both orientations, and two explicit leftovers: `case00597` and `case00539`. All **114** composites pass pure graph materialization with **zero ambiguous IP owners, zero dangling endpoints and zero duplicate directed relations**; **1,606** duplicate source relation rows are collapsed under the declared maximum-count rule. Source windows are separately retained and no common physical cutoff is claimed.

During this recheck an intermediate fix used phase-dependent synthetic pod identities and invented ADD_NODE/ALIGN_NODE for an unchanged overlaid pod. The final mapping is keyed by pair/source pod identity, and IP mapping also includes the source IP. The reviewer replayed that exact unchanged-pod counterexample: name/IP now match across phases and no false ADD_NODE/ALIGN_NODE is produced. The new regression covers this defect.

**Validation:** all **10** proxy/workload tests pass, plus the independent baseline fixtures and full 114-composition audit. The three current M1 checkpoint content hashes still match the audited hashes already recorded in this report. Expected proxy scoring coverage is **342 rows = 57 pairs × 2 orientations × 3 seeds**; no scores were generated by this review.

Retain the claim limits: evaluator truth selects the overlaid service during synthetic construction; this is disclosed and is not evidence of label-free incident generation. Both orientations remain correlated views of the same pair. The result can support only synthetic sensitivity, not real concurrent faults, causal fault interaction, recovery or production generalization. The merged `coverage` counters sum whole-source counts and are not validated exact counts of the partly retained composite telemetry; do not report them as such. They do not enter this fixed GNN scoring path. The separate fault-injection/live-repair runtime gates remain closed as described in the B4 review.

Final reviewed SHA-256 fingerprints:

```text
953577dabe17b933f44dbda9d5dd575228d41c932420a0e9cb42dae6c784d546  scripts/e2r/run_recovery_baseline.py
12f788298199b4e3c95d47d13bd53fb1562d1547522be3522b3ffd13ad74c0e3  rca_bench/workload_oracle.py
31604d523f6b56b14f9224a0e75da210fffc64ef181cd0f5aa40da8dc9787419  scripts/e2r/run_multi_root_proxy.py
cd44e5a7c0c97b148c9c5d26a98d59398fc910911aa36c5fcafbb3217555c1a2  tests/e2r/test_multi_root_proxy.py
768f8db6e0893c26c98f123f95a24ab1472bb36cb7be3d7c64e817babd641731  tests/test_workload_oracle.py
```

## Addendum — exact one-case pre-live sanity gate

Date: 2026-09-26. Reviewer `/root/full_experiment_code_review`; same-family provisional. **Verdict: BLOCK the one live sanity on the current revision until the two narrow issues below are fixed. The expanded campaign remains BLOCKED.** No external request, Kubernetes/Ansible action, fault injection or GPU operation was performed by the reviewer.

The supplied runtime facts were considered as executor-provided evidence: exact isolated namespace, 12 ready deployments/12 pods, and effective scoped-SA permissions with user-namespace and namespace-deletion denial. Independently reading the saved baseline confirms 900 samples, zero error, approximately 4.9972 rps, p95 17.336 ms, six windows and successful canaries. Its SHA-256 is `b9c4c39b77b6640390746b77167ab5f69f2dca72598b59399b150d4dc9c563bf`. The existing scoped credential file's local mode is 0600; its token was not read or printed.

### Verified protections and corrected findings

The case is hard-bound to namespace `e2r-supplement-20260925`, service `frontend` and StressChaos `e2r-sanity-cpu`, with a 180-second duration. Admin credentials perform the declared initial injection and final cleanup; scoped credentials are passed into the typed plan's read, YAML environment/validation, preconditions, mutation, rollback and technical observation paths. The namespace Role's new `pods/portforward` grant is namespace-scoped. The runner refuses a reused output directory and unapproved baseline/credential paths; it records baseline content identity and has no request/model retry or automatic second incident.

Earlier issues found during this review were corrected: missing/nonfinite fault observations no longer count as measurable degradation; the evidence item now derives from an exact scoped live-object GET rather than the injection manifest; cleanup failure cannot remain `complete_pass`; delete cleanup and the absence GET are independent attempts; token files are created atomically with O_EXCL and mode0600 rather than writing first and chmod later.

Eight fully mocked main-path scenarios exercised the real planner, renderer, guard and technical-oracle integration with a fake subprocess backend: nominal success, NaN fault latency, missing fault canary, no measurable degradation, wrong observed target, Ansible failure, cleanup failure and cleanup timeout. Outcomes were respectively complete success or fail-closed as intended. Execution failure invoked captured rollback and cleanup. The final cleanup block was separately replayed after the independent-attempt fix: delete timeout still ran the absence GET, saved its evidence and forced `failed_cleanup`. No real process was launched.

**43 targeted contract tests passed.** An additional synthetic credential-creator test confirmed O_EXCL/0600 creation and absence of the synthetic token from stdout.

### Two remaining issues

1. **RBAC denial exit convention is rejected.** Both creator and sanity currently require return code 0 for stdout `yes` and stdout `no`. A mock explicit denial with stdout `no` and exit1 is rejected as `RBAC query failed`; exit0/no passes. Normal kubectl denial can use exit1, so the intended negative-permission gate must recognize explicit denial separately from a query error. Accept only the intended explicit success/denial tuples (for example yes/0 and no/1), and continue to reject errors/empty output. The creator currently deletes its just-created credential on this normal-denial-shaped result. This is a compatibility failure that stops the run before injection, not an unsafe allow.
2. **Repair can begin after the fault's 180-second automatic expiry.** The collector schedules 150 requests per fault window but waits for queued requests to finish; a severely degraded frontend can stretch that window beyond its 30-second target. The runner does not record/enforce an injection deadline before the observed-object/precondition/repair stages. A StressChaos object can remain after its active duration ends; kind/name/selector alone is not evidence that its stress is still active. Thus a late deletion can be followed by natural recovery and still receive the sanity's PASS. Record injection time and an explicit pre-repair age/deadline bound, reject a window/repair that reaches auto-expiry, and bound execution within that eligibility interval or independently confirm active fault state. Preserve the failed attempt and clean it up; do not retry it as part of this authorization.

Once these two checks are corrected and locally replayed, the intended clearance is only **one known-target isolated CPU-stress contract sanity**, not a causal recovery benchmark, a diagnosis-accuracy experiment, or authorization for an expanded fault campaign. The independent oracle/cleanup result from that actual run must still be reviewed before expansion.

Reviewed revision fingerprints:

```text
c51adbab5c16620e1775516d4da67eea86458db66287a2c1f95ee11c1a0c86d1  scripts/e2r/create_scoped_kubeconfig.py
5feae3eb6439b9dd782697b9d8fc2d0203b9ccacd540d0a3beb7e8832653b170  scripts/e2r/run_recovery_sanity.py
a8004f9961c417ae9a2e4aa587f90aeb4f8c663c125db081d6bf9e31ae520fca  methods/PlanRCAThinkRemed/cluster_client.py
a025ddec725a19c1f958d41f26f537857ac17d0cb1bb7ff1ae1be63705bf4b38  methods/PlanRCAThinkRemed/playbook_generator_agent.py
d0ac1b465cd3d1d2eff9ecd33936246e5bfc2647199d5ff201d0292cb640233f  methods/PlanRCAThinkRemed/safety_guard.py
c649fc21720d98b9cce59c62d2d66dbc6c1f02f69a2f0f3da48137365b20487b  methods/PlanRCAThinkRemed/coordinator.py
afcf97ae1ac619aed045e10112a339a1971485119193898ff29699dcdd952357  rca_bench/remediator.py
12f788298199b4e3c95d47d13bd53fb1562d1547522be3522b3ffd13ad74c0e3  rca_bench/workload_oracle.py
0c1cff3c781b283cecd020185b9d89605f605b928dec77b8e04783de644632b5  experiments/e2r/isolated_boutique.yaml
```

## Addendum — RBAC convention fixed; fault-age bound needs execution coverage

Date: 2026-09-26. Same-family provisional targeted recheck; no external action. **The exact one-live-sanity gate remains BLOCKED on the remaining timing issue. Expanded runs remain BLOCKED.**

The recorded runtime convention is now correctly implemented in both scripts: `(yes, 0)` permits and `(no, 1)` denies. Local credential-creator mocks accept the explicit denial and reject `(no, 0)`, empty output and error text. Atomic 0600 creation and token non-disclosure remain intact. The corrected `auth can-i delete namespaces` query removes the earlier resource/name argument error. The RBAC blocker is closed.

The new age check is useful but does not yet enforce the claimed attribution margin. It runs **before** another precondition subprocess (up to 15 seconds) and an Ansible subprocess still allowed **120 seconds**. An allowed age of 119 seconds plus 14 seconds for the precondition and 50 seconds for Ansible reaches 183 seconds; neither subprocess exceeds its timeout, yet the repair can finish after the 180-second automatic expiry and receive `complete_pass`. In addition, the age origin is injection-command completion, which can lag actual fault creation.

**Minimum remaining correction:** anchor the conservative lifetime at injection request start; recheck eligibility immediately before Ansible; cap execution to the remaining attribution interval with a margin; and reject a successful process exit if its completion crosses that interval. Preserve the attempt and perform the existing independent cleanup/absence checks. This is the remainder of the previously reported auto-expiry issue, not a new experimental requirement.

The prior 43 passing contract tests and mocked cleanup/error paths remain applicable; this turn's new executed checks were the explicit RBAC tuple fixtures. No live sanity has been performed or approved by this addendum.

Revision fingerprints:

```text
325ae4e6372ac6ef4e333f1e7cc6741c818ae5e6793edbf45822b3e99dd2c8f2  scripts/e2r/create_scoped_kubeconfig.py
91401a1d413e6388625b0d7236f8dedfe51d6e61a92c556173324dd8d6ca4eb4  scripts/e2r/run_recovery_sanity.py
```

## Addendum — exact one-case live sanity clearance

Date: **2026-09-27** (Asia/Tokyo). Reviewer `/root/full_experiment_code_review`; same-family provisional. **PASS for exactly one live sanity execution of the final fingerprinted runner.** This supersedes the preceding one-case timing/RBAC BLOCK verdicts. **The expanded fault/recovery campaign remains BLOCKED.**

The clearance is limited to `scripts/e2r/run_recovery_sanity.py` with the approved immutable baseline, reviewed `${SCOPED_KUBECONFIG}`, one fresh output directory, namespace **`e2r-supplement-20260925`**, service **`frontend`**, and exact StressChaos name **`e2r-sanity-cpu`**. The declared CPU stress is 10 workers/load100 with a 180-second auto-duration. No automatic second case, retry, wider target selection or expanded campaign is cleared. Any gate failure consumes this sanity attempt and requires result review before further live fault work.

The reviewer made no external/API/Kubernetes/Ansible/GPU call. Existing runtime health/RBAC facts supplied by the executor and the locally verified baseline/credential-mode evidence from the earlier addendum remain the runtime basis. The baseline's approved SHA-256 remains `b9c4c39b77b6640390746b77167ab5f69f2dca72598b59399b150d4dc9c563bf`.

### Final deadline fix replay

Fault age now starts conservatively at the injection request, so apply latency consumes the budget. The early 120-second eligibility guard remains. After scoped preconditions, the runner recomputes age, derives the remaining time to a **150-second completion deadline**, caps Ansible to `min(30, floor(remaining))`, and refuses to start with less than five seconds. It records completion age and rejects even return code zero at or beyond 150 seconds, leaving a 30-second margin before the fault's declared automatic expiry.

Seven full main-path mock scenarios exercised the real planner, scoped renderer/guard, preconditions and oracle with fake process/time/workload backends:

| Scenario | Observed result |
|---|---|
| Normal case: execute at fault age60s, finish61s | `complete_pass`; timeout30s |
| Eligibility age exactly120s | Abort before Ansible |
| 20s injection latency plus slow fault window: age139s | Abort before Ansible; apply latency included |
| Eligibility119s + preconditions14s; finish149.5s | `complete_pass`; timeout reduced to17s |
| Same start, return code0 at age150s | Failed attribution gate, never PASS |
| Preconditions leave only4s | Abort without Ansible |
| Execution exceeds remaining17s | Timeout/non-success |

**Every scenario attempted cleanup and verified absence in the mocked final path.** The earlier independent cleanup-timeout replay also remains valid: an exception deleting the object does not suppress the subsequent absence GET, and any cleanup/probe error forces `failed_cleanup` with saved evidence.

The explicit RBAC convention remains `(yes,0)` for permission and `(no,1)` for denial; malformed/error responses reject. The credential creator uses atomic exclusive 0600 creation and does not log token material. Live evidence is obtained through the scoped exact-object read; invalid/unmeasurable fault windows cannot count as degradation. The post-state result requires the independent technical checks and three fresh 30-second workload windows. A nonzero Ansible result or an unsuccessful/UNKNOWN oracle cannot become a successful sanity.

Final verification: **43 targeted tests passed**, plus the seven timing main-path mocks and the prior scoped-context, invalid-observation, wrong-target, execution-failure and cleanup fixtures. No actual recovery outcome has been claimed by this review.

This is a known-target contract/instrumentation sanity, not a diagnosis-accuracy or causal recovery benchmark. Before expansion, inspect the actual trace for valid initial degradation, scoped repair timing, oracle verdict, cleanup absence and any state/health regression. Preserve a negative or failed attempt; do not replace it with an unreported rerun.

Final clearance fingerprints (SHA-256):

```text
22317a6830f8876fbad802a27b65b47e5f26ecef9c6303c6a971c7e78c22799b  scripts/e2r/run_recovery_sanity.py
325ae4e6372ac6ef4e333f1e7cc6741c818ae5e6793edbf45822b3e99dd2c8f2  scripts/e2r/create_scoped_kubeconfig.py
a8004f9961c417ae9a2e4aa587f90aeb4f8c663c125db081d6bf9e31ae520fca  methods/PlanRCAThinkRemed/cluster_client.py
a025ddec725a19c1f958d41f26f537857ac17d0cb1bb7ff1ae1be63705bf4b38  methods/PlanRCAThinkRemed/playbook_generator_agent.py
d0ac1b465cd3d1d2eff9ecd33936246e5bfc2647199d5ff201d0292cb640233f  methods/PlanRCAThinkRemed/safety_guard.py
c649fc21720d98b9cce59c62d2d66dbc6c1f02f69a2f0f3da48137365b20487b  methods/PlanRCAThinkRemed/coordinator.py
afcf97ae1ac619aed045e10112a339a1971485119193898ff29699dcdd952357  rca_bench/remediator.py
12f788298199b4e3c95d47d13bd53fb1562d1547522be3522b3ffd13ad74c0e3  rca_bench/workload_oracle.py
6e2ac9775e726c5a36adc225cf8a6f11ec2efecd0363095ce476fffd512e9dc4  rca_bench/recovery_oracle.py
0c1cff3c781b283cecd020185b9d89605f605b928dec77b8e04783de644632b5  experiments/e2r/isolated_boutique.yaml
```

## Addendum — failed v1 audit and clearance for one infrastructure-corrected v2

Date: **2026-09-27**. Reviewer `/root/full_experiment_code_review`; same-family provisional. **PASS for exactly one new immutable v2 sanity attempt after the implemented Ansible preflight. Expanded fault/recovery experiments remain BLOCKED.** The v1 failure must remain unchanged and included in attempt/cost accounting. This is a separately reviewed infrastructure correction, not an automatic retry or permission to rerun until success.

### Actual v1 outcome audited

The reviewer directly read `experiments/e2r/runs/recovery_sanity_frontend_cpu_v1/trace.json` and recomputed both workload-window p95 values from their raw finite latency lists. Both windows contain 150 attempts/150 successes:

- Pre-window p95: **17.7979 ms**, with the recorded pre-health gate PASS.
- Fault-window p95: **804.5415 ms**, versus the immutable baseline's **17.3360 ms**; fault observations are measurable and degraded despite zero HTTP errors.

The scoped observed object and capture agree on exact name/namespace/selector. The captured object hash recomputes correctly; live status records `AllInjected=True`, `AllRecovered=False` and an injected frontend/server container. Plan/YAML/precondition gates passed. Execution started at fault age approximately **61.107 s**, well inside the reviewed attribution interval.

Ansible exited1 in **0.0816 s** with `ModuleNotFoundError: No module named 'ansible'` from `${LOCAL_HOME}/.local/bin/ansible-playbook`. Direct inspection confirms that old launcher selects `/usr/bin/python3`. The import fails before Ansible's playbook CLI runs, so the typed repair command did not execute. The trace correctly records `status='failed'` and `repair_executed=False`; there is no post-repair workload oracle result and no recovery success to claim.

The failure branch's scoped rollback returned0 with **`unchanged`**. That is a successful no-op application of the capture, not evidence that an inverse repaired a partially changed system. Admin cleanup then returned0; the independent GET returned1/NotFound, with no cleanup errors and `cleanup_verified_absent=True`. These are directly recorded facts. The subsequent 12-pod/2-of-2 Ready observation is executor-reported; no reviewer cluster call was made. A new pre-injection workload check remains necessary and is already enforced in the runner.

The complete v1 trace remains SHA-256 **`346a326e1c0cd88dea291932d521415ec182bcc46d54b53a3abff627a4fd1991`**. Its approximately 93.86-second failed episode is preserved; it is not converted into a success or silently removed from denominators.

### Infrastructure correction reviewed

The executor reports no second fault injection and a successful no-fault Ansible preflight (return code0, approximately1.20 s, frontend pod output present). The reviewer independently verified the local installation metadata for **ansible-core 2.21.4**, the venv executable's shebang, and the current implementation. No reviewer Ansible/Kubernetes/network call was performed.

`run_ansible` uses `Path(sys.executable).parent / 'ansible-playbook'`; for the reviewed invocation with `.venv-e2r/bin/python`, this is the absolute executable **`${PROJECT_ROOT}/.venv-e2r/bin/ansible-playbook`**, whose shebang points into that same venv. The failed PATH-resolved launcher is no longer selected. Preflight and mutation use this same helper. Local/remote Ansible temp directories are private 0700 directories under an exclusive temporary directory; the inventory explicitly uses localhost/local connection.

Before any injection, the runner renders and validates a scoped WaitAndVerify playbook containing only `kubectl get pods -n e2r-supplement-20260925`. It requires exit0 and frontend pod output and saves its result in the new trace. This functional preflight exercises the interpreter/import/module/shell/kubeconfig path that failed in v1; it is stronger evidence than a version command alone. Package version was checked locally, and the helper records the actual executable. The same namespace/RBAC, fresh pre-health, fault-measurability, scoped observation, 150-second attribution deadline and independent cleanup/oracle gates remain in place.

Reviewer mocks verified the actual helper's absolute executable, scoped YAML environment, localhost inventory, `shell=False` and 0700 temp paths. Full preflight-gate fixtures show that import failure or missing frontend output abort before port-forward/workload/injection, while a passing preflight reaches only the subsequent read-only setup. **All 43 targeted contract tests pass.** The reviewer did not execute the proposed second fault case.

### Exact new clearance and reporting boundary

One new output directory, conventionally `recovery_sanity_frontend_cpu_v2`, may be used for **one** attempt of the unchanged `frontend` / `e2r-sanity-cpu` / `e2r-supplement-20260925` profile, invoked through the reviewed venv Python. V1 must not be overwritten or counted as a successful remediation. The in-run preflight and pre-health check must pass again; a failed gate is not permission for another automatic attempt. After v2, review the complete actual execution/oracle/cleanup trace before any expansion.

This clearance addresses an executable-environment failure while preserving the same case and protocol. It does not establish diagnosis accuracy, causal recovery benefit or generalization from either sanity attempt.

Final reviewed fingerprints:

```text
efba2fa1859c835a134bbce9b8475bdc2dccb9a1b2c96f1c3f5f84ff8223b97c  scripts/e2r/run_recovery_sanity.py
a025ddec725a19c1f958d41f26f537857ac17d0cb1bb7ff1ae1be63705bf4b38  methods/PlanRCAThinkRemed/playbook_generator_agent.py
d0ac1b465cd3d1d2eff9ecd33936246e5bfc2647199d5ff201d0292cb640233f  methods/PlanRCAThinkRemed/safety_guard.py
12f788298199b4e3c95d47d13bd53fb1562d1547522be3522b3ffd13ad74c0e3  rca_bench/workload_oracle.py
e92b377ecefe0a2490a11002ef3eb8ca5625ba93cb050a349234b05a197ee9b6  .venv-e2r/bin/ansible-playbook
```

## Addendum — v2 pre-health abort; isolated sidecar stabilization and rebaseline

Date: **2026-09-27**. Reviewer `/root/full_experiment_code_review`; same-family provisional. **PASS for the isolated-only stabilization rollout, readiness/stability checks, and one new immutable no-fault baseline. No v3 fault injection or repair is authorized.** A v3 decision must follow inspection of the resulting stability/baseline artifacts; automatic retry and expanded campaigns remain blocked.

### Actual v2 audit

The v2 trace records a successful scoped venv-Ansible preflight, followed by a 150-request pre-window with 150 successes, zero error, approximately 4.9974 rps and a successful product-availability canary. Recomputed raw p95 is **26.1528 ms**. The approved original baseline limit remains **20.8032 ms** (`17.3360 ×1.2`), so `pre_health_gate=False` is correct.

The runner stopped before injection: `fault_injected=False`, `repair_executed=False`, `events=[]`. Cleanup returned0 and the independent GET returned1/NotFound, with `cleanup_verified_absent=True` and no cleanup errors. V2 is therefore a preserved failed pre-health attempt, not a recovery attempt or outcome. Its trace SHA-256 is **`47122d445bcde4e11899fa02161ca0cf48f7d8e77c4bb582f3218f9568a28f23`**.

The saved preflight output independently records 12 Running/2-of-2 Ready pods with two recent restarts each. The executor's additional per-container observation attributes both restarts to each `sidecar-busybox` (`Completed`, exit0), while all main-container restart counts remain 0. Combined with the recorded `sh -c 'sleep 3600'` command, this identifies a concrete source of hourly sidecar termination/restart. It does **not**, by itself, prove that those restarts caused the particular 26.15-ms pre-window p95. Removing known periodic churn is a justified environment correction, not a reason to change or relabel the failed v2 measurement.

### Exact manifest delta verified

The proposed manifest contains exactly 12 `sleep 2147483647` replacements and no remaining `sleep 3600`. Replacing those 12 strings back in memory reproduces the last approved manifest SHA-256 **`0c1cff3c781b283cecd020185b9d89605f605b928dec77b8e04783de644632b5`** byte-for-byte. Comparing each new Deployment template with the baseline snapshot's stored last-applied configuration likewise produces only `/spec/containers/1/command/2` for each of the 12 deployments. No namespace, workload image, application resource, permissions or network-policy change is included in this patch.

The reviewed proposed manifest SHA-256 is **`9150029373d00e9882b83922071d69c12bcd36b04af5cab16ea90a2caa0882bb`**. The executor reports its server-side dry-run passed and that it has not yet been applied. The reviewer made no cluster call and did not apply the manifest.

### Cleared preparation sequence

1. Apply exactly this reviewed manifest change to **`e2r-supplement-20260925` only**. This is an intentional benchmark setup rollout, outside any fault/recovery measurement.
2. Wait for all 12 deployments/pods to complete rollout and become 2-of-2 Ready. Save new pod identities and per-container restart counts; require no additional restarts during the subsequent warmup/calibration period. A new pod's reset counter alone is not evidence of stability—compare its own identity/count before and after the measurement.
3. Run one fresh, separately named immutable no-fault baseline with the same six 30-second windows, offered 5 rps/five workers, warmup and raw-latency retention. Preserve the original baseline and both sanity traces. Save the updated manifest identity and pre/post readiness/restart evidence alongside the new baseline.
4. Inspect every window's achieved load, error, latency/canary and restart evidence before proposing another fault case. Keep the 1.2 multiplier and existing fault/oracle policy; do not relax thresholds merely to admit v3. A new baseline is justified by the changed environment configuration, not by discarding a failed pre-health gate.

Changing a sidecar command replaces the Deployment pod template, so this rollout also restarts the application containers. In particular, the benchmark Redis uses `emptyDir`; this is an environment reset, not evidence of persistent-state preservation. Let the declared workload re-warm before calibration and treat the post-rollout baseline as a new environment version.

**No v3 authorization is granted here.** The sanity runner currently pins the old baseline path; any future proposal to consume the new baseline must identify its immutable path/hash and be reviewed separately. Across the existing records, there are two sanity attempts but only one actual fault injection: v1 failed on execution infrastructure, and v2 stopped before injection. Preserve that distinction in all reporting.

Current reviewed fingerprints:

```text
9150029373d00e9882b83922071d69c12bcd36b04af5cab16ea90a2caa0882bb  experiments/e2r/isolated_boutique.yaml
ad0dec65f7d627774753d7321d0b0d4ccbe41a896c3265db0b7f31ebc20e29c1  scripts/e2r/run_recovery_baseline.py
12f788298199b4e3c95d47d13bd53fb1562d1547522be3522b3ffd13ad74c0e3  rca_bench/workload_oracle.py
efba2fa1859c835a134bbce9b8475bdc2dccb9a1b2c96f1c3f5f84ff8223b97c  scripts/e2r/run_recovery_sanity.py
```

## Addendum — stabilized baseline v2 accepted; one v3 sanity clearance

Date: **2026-09-27**. Reviewer `/root/full_experiment_code_review`; same-family provisional. **PASS for the stabilized baseline and exactly one new v3 sanity using it, after the explicit runner bindings below are implemented and locally checked. Expanded fault/recovery runs remain BLOCKED.** This does not authorize running the current v1-bound script unchanged, automatic retries, threshold relaxation, or replacement of prior outcomes.

The reviewer directly audited `experiments/e2r/runs/recovery_baseline_v2/baseline.json`, its raw windows and both embedded runtime snapshots. No network/Kubernetes/Ansible/GPU call or implementation edit was made.

### Independent artifact checks

- File SHA-256 exactly matches **`3957fa90acce446bd248c6e493c2374307454cc96d4644b99ec60fc04b84642d`**; status is complete, namespace is the declared isolated namespace, and both fault/repair flags are false.
- Six ordered, nonoverlapping windows each contain **150 attempts, 150 successes and 150 finite nonnegative latencies**. Each window's p95 recomputes from its raw values, and throughput recomputes from success count/recorded elapsed time. All error rates are zero and all canaries are true.
- Combined **900-sample p95 = 18.263094127178192 ms**; median window throughput =**4.997163960150042 rps**. Window p95 ranges **17.4304–18.6026 ms**, and achieved load ranges approximately 4.9970–4.9977 rps. The six raw windows each span at least 30 seconds; intervening gaps are retained in timestamps.
- Pre/post snapshots contain the same **12 pod UIDs and container IDs**, with two Ready containers per pod and every restart count zero. The stored `runtime_identity_stable_across_calibration=True` agrees with recomputation.
- All 12 recorded Deployment templates match the current manifest's intended templates through their stored last-applied configuration. Each sidecar runs `sh -c 'sleep 2147483647'`. Manifest SHA-256 matches **`9150029373d00e9882b83922071d69c12bcd36b04af5cab16ea90a2caa0882bb`**.
- Original baseline v1 and sanity v1/v2 hashes still match the earlier audit; none was overwritten.

This supports a stable, no-fault calibration interval after the deliberate setup correction. It does not prove that sidecar restarts caused the earlier p95 excursion or guarantee indefinitely stable latency. The fresh pre-injection health gate remains required.

### Required literal runner changes before the approved v3

1. Set the approved/default baseline path to **`experiments/e2r/runs/recovery_baseline_v2/baseline.json`**. Reject alternate paths, including v1; do not select a baseline dynamically by recency.
2. Add a required content check against **`3957fa90acce446bd248c6e493c2374307454cc96d4644b99ec60fc04b84642d`** immediately after reading the baseline and **before any external command, HTTP call or injection**. Save the approved path and actual digest in the new trace. A wrong digest must stop rather than warn or fall back.
3. Bind/record the stabilized setup to manifest digest **`9150029373d00e9882b83922071d69c12bcd36b04af5cab16ea90a2caa0882bb`**; reject a local manifest mismatch before proceeding. Keep the existing namespace, service, scoped credential, Ansible preflight, measurement profile, cleanup and deadline gates unchanged.
4. Locally test acceptance of the exact new artifact and rejection of the old path, altered baseline bytes and altered manifest bytes before permitting any process/network action. Use a new immutable output directory such as **`recovery_sanity_frontend_cpu_v3`**, preserving both previous sanity records.

The calibration multiplier remains **1.2**, so the new pre-health/recovery p95 ceiling is **21.91571295261383 ms**. This is the same predeclared rule applied to a separately versioned, healthy post-rollout environment, not a relaxation chosen to pass v2 (v2's 26.1528-ms pre-window would still fail). Do not change the fault/degradation thresholds or other protocol settings to improve the outcome.

### Exact authorization boundary

Once those narrow binding checks pass, one v3 attempt may run the unchanged known-target `frontend` / `e2r-sanity-cpu` case in **`e2r-supplement-20260925`**, through the reviewed venv Python and scoped executor. This approval includes its mandatory read-only preflight and fresh pre-health window; any failed gate ends that attempt. There is no approval for another retry or an expanded campaign. Review the actual v3 execution, fresh independent oracle and verified cleanup before proposing any further live fault work.

Keep distinct denominators: the preserved history has two sanity attempts and one actual injection so far. This approval is for one additional attempt, whose injection count depends on its preflight/health gates. No v3 outcome is asserted here.

Artifact/source fingerprints at this audit (the sanity source is still v1-bound before the authorized literal update):

```text
3957fa90acce446bd248c6e493c2374307454cc96d4644b99ec60fc04b84642d  experiments/e2r/runs/recovery_baseline_v2/baseline.json
9150029373d00e9882b83922071d69c12bcd36b04af5cab16ea90a2caa0882bb  experiments/e2r/isolated_boutique.yaml
080ec0a903b3dffc3f5378c23aed469f78c43f2166acb0c0393088ebe7c1c296  scripts/e2r/run_recovery_baseline.py
efba2fa1859c835a134bbce9b8475bdc2dccb9a1b2c96f1c3f5f84ff8223b97c  scripts/e2r/run_recovery_sanity.py
12f788298199b4e3c95d47d13bd53fb1562d1547522be3522b3ffd13ad74c0e3  rca_bench/workload_oracle.py
```

## Addendum — actual v3 sanity accepted; next experimental boundary

Date: **2026-09-27**. Reviewer `/root/full_experiment_code_review`; same-family provisional. **The actual v3 known-target sanity passes this artifact audit. No additional live fault episode or expanded campaign is authorized by this addendum.** The next cleared work is local preparation and review of a bounded matched-control block, described below. No reviewer network/Kubernetes/Ansible/GPU action was performed.

### Independently recomputed v3 evidence

The trace SHA-256 exactly matches **`33cff9667822ef297d508809f57a758a9fb3dc975a757f46dd10ad47b4cad761`**. Its baseline digest equals the approved **`3957fa90acce446bd248c6e493c2374307454cc96d4644b99ec60fc04b84642d`** and its manifest digest equals **`9150029373d00e9882b83922071d69c12bcd36b04af5cab16ea90a2caa0882bb`**; both match the current immutable files.

All five measured windows contain 150 attempts, 150 successes and 150 finite raw latencies, for **750 recorded requests**. The reviewer recomputed each p95 from the raw list and throughput from actual successes/elapsed time:

| Window | p95 ms | Achieved requests/s | Errors |
|---|---:|---:|---:|
| Pre-health |16.9744|4.99732|0|
| Fault |1399.8424|4.75654|0|
| Post0 |16.9428|4.99717|0|
| Post1 |17.1526|4.99722|0|
| Post2 |16.8879|4.99713|0|

Every recorded canary is true. The fault-window duration/throughput is used as recorded rather than normalized to its 30-second target. The pre-health and fault-degradation flags agree with these measurements.

The precondition's raw scoped object, exact frontend selector and captured-object hash validate. Its controller status records `AllInjected=True`, `AllRecovered=False` and desired phase `Run`. Plan/YAML checks passed. The venv-local Ansible output explicitly records deletion of `e2r-sanity-cpu` from the isolated namespace, with return code0, no ignored failures and **1.2995 s** process time.

The exact oracle dictionary recomputes from the approved baseline, recorded post windows, timestamps and technical flags: three distinct, fresh, trailing passing windows and overall PASS. The technical flags (`fault_absent`, `resource_restored`, `ready_endpoints`) are recorded true. Their underlying post-action raw probe responses are not embedded in this trace, so those three flags cannot all be independently reconstructed here; the reviewed collector implementation and additional executor-reported checks support them. Future claim-bearing control runs should save those raw command/context/status/JSON observations.

### Timing and cleanup attribution

Repair completed at fault age **63.9213 s**, below the 150-second conservative deadline. The final post window ended at age **155.9936 s**, leaving **24.0064 s before the 180-second expiry**. The oracle was evaluated at age 156.3836 s and cleanup completed at 156.6462 s, also before expiry. Thus automatic expiration is not a timing explanation for the observed recovery interval in this recorded case.

Admin final cleanup returned0 and the separately recorded GET returned1/NotFound, with no cleanup errors and verified absence. The executor additionally reports an independent post-run NotFound check and 12 Ready pods with zero restarts; the reviewer did not repeat these cluster operations. V1 and v2 trace hashes remain unchanged.

This proves that the scoped observed-evidence → typed deletion → workload/technical verification → cleanup path executed successfully in **one** known-target CPU-stress case. It does not establish a comparative recovery effect without a no-action counterfactual, and it does not test graph-based diagnosis, LLM planning, service-attribution benefit, multiple fault categories or general operational restoration. The product GET canary is an availability check, not persistent-state validation.

### Next scope: prepare one matched block; live execution still gated

**Authorized now:** freeze and implement a small, non-mutating/local-tested protocol for **one matched two-arm block on the same isolated frontend CPU-stress profile**: typed cleanup versus no action, with one fresh episode per arm. This is cleanup attribution, not the full evidence-bound restoration claim. Keep v3 as the sanity record; do not retroactively treat it as a randomized member of the new block.

Before any new live fault is cleared, the concrete plan/runner must provide:

- fixed pair membership/order and a maximum of two fresh fault episodes, no replacement of failed attempts;
- identical healthy setup, baseline/source hashes, workload, observed evidence, injected target/magnitude and initial degradation gates;
- a common observation reference/deadline that stays before automatic expiry in both arms, with overrun/missing measurements treated as UNKNOWN instead of successful recovery;
- an explicit no-action path that performs no repair mutation until the observation horizon ends, while retaining unconditional exact-object cleanup and absence verification;
- raw technical probe evidence alongside workload samples, separate injection/health/action/oracle/cleanup outcomes, and unchanged unconditional attempt denominators;
- local mocked gate coverage and a fresh code review of that control runner before its two-episode live block.

The reviewed artifacts currently contain only an always-repair sanity runner, not this matched-control implementation. Therefore **the next live control block is BLOCKED pending that concrete gate**; broader repetitions, new faults/targets and the full campaign remain blocked as well. This is an explicit boundary, not an instruction to keep running the successful arm.

Preserve the complete setup history: **three sanity attempts, two actual fault injections, one completed typed repair and one PASS**. V1 failed before the repair command, v2 stopped before injection, and v3 passed after stabilization. These are audit counts across setup versions, not an estimated recovery rate for a frozen experiment population.

Audit fingerprints:

```text
346a326e1c0cd88dea291932d521415ec182bcc46d54b53a3abff627a4fd1991  experiments/e2r/runs/recovery_sanity_frontend_cpu_v1/trace.json
47122d445bcde4e11899fa02161ca0cf48f7d8e77c4bb582f3218f9568a28f23  experiments/e2r/runs/recovery_sanity_frontend_cpu_v2/trace.json
33cff9667822ef297d508809f57a758a9fb3dc975a757f46dd10ad47b4cad761  experiments/e2r/runs/recovery_sanity_frontend_cpu_v3/trace.json
3957fa90acce446bd248c6e493c2374307454cc96d4644b99ec60fc04b84642d  experiments/e2r/runs/recovery_baseline_v2/baseline.json
9150029373d00e9882b83922071d69c12bcd36b04af5cab16ea90a2caa0882bb  experiments/e2r/isolated_boutique.yaml
c49e45199958c65284de9e62a4c2bac2a227659d0a46a1ec5f6b73ff0c47a42b  scripts/e2r/run_recovery_sanity.py
6e2ac9775e726c5a36adc225cf8a6f11ec2efecd0363095ce476fffd512e9dc4  rca_bench/recovery_oracle.py
12f788298199b4e3c95d47d13bd53fb1562d1547522be3522b3ffd13ad74c0e3  rca_bench/workload_oracle.py
```
