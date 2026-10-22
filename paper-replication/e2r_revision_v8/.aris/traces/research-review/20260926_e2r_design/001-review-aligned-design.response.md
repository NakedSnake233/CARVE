# E2R reviewer-aligned experiment design audit

Date: 2026-09-26. Reviewer: fresh delegated senior systems/ML reviewer, `/root/review_aligned_design`.

`review_independence: same-family`  
`acceptance_status: provisional`

This is a reviewer-authored design assessment, not an empirical acceptance result. The review read the requested primary artifacts directly, inspected the relevant repair validators and transport implementation, and performed two pure local validation probes. No API requests, remote SSH, GPU execution, training, Kubernetes operations, or code changes were performed. The legacy `RESEARCH_REVIEW.md` is preserved.

## Verdict

**The current experiment plan does not yet answer every concrete reviewer concern.** Its ordering is sensible and M1 has useful, audited evidence. However, M2 remains an eight-incident compatibility pilot; it does not implement the paper’s evidence-bound reranker. M3’s existing category-keyed injection cleanup cannot establish graph-to-repair benefit. M4’s evidence, precondition and post-state contracts are substantially unimplemented. Workload, scalability and failure-boundary tests cannot all remain optional if the revised paper claims to answer the reviews.

The smallest credible revision is a two-claim paper: a controlled diagnostic representation result, followed by a bounded, measured recovery contract. It needs matched training controls, a formal matched-information LLM comparison, a small service-targeted repair study, and explicit boundary tests. Production access is not required. Successful rejection of an unsupported failure answers a scope question; it does not demonstrate recovery of that failure.

## What the existing evidence actually establishes

The new graph corpus and the historical repair corpus are different experiments and must remain distinct:

| Corpus | Verified scope | Interpretation |
|---|---|---|
| Historical five runs | 760 recorded Online Boutique incidents; seven fault categories; deterministic `planrca` | Descriptive localization and recorded checker outcomes; no demonstrated graph/LLM attribution |
| New graph corpus | 798 incidents: 400 Boutique, 398 TrainTicket; T1 HPA-related CPU stress 199, T2 pod replacement 150, T3 selector faults 199, T4 canary faults 250 | Offline injected-service localization; no live recovery result |
| M1 fixed split | 566 train / 116 validation / 116 test; test has 59 Boutique and 57 TrainTicket; seeds 42/43/44 | Same-weight graph-input substitution into patch-trained models |
| Pending M2 | Eight selected held-out incidents, one per application × scenario stratum; two generations; two request routes | Compatibility and route-accounting pilot only |

M1’s seed-mean test AC@1 is 0.8218 for rule-patch inputs, 0.4167 for runtime inputs and 0.3649 for reference inputs. The raw predictions, labels and metrics passed the prior same-family artifact-integrity audit. These are real recorded-injection labels, not model-generated ground truth. However:

- Every scorer was trained and validation-selected on patch graphs. This is not a fair comparison of separately trained reference/runtime methods, and a representation-distribution advantage remains possible.
- All 398 TrainTicket snapshots, including its 57 test cases, lack diagnosis-period telemetry features. They retain topology/replica/endpoint information. Do not describe this as two-application multimodal validation.
- Candidate counts are 13 for Boutique and 65 for TrainTicket. Effective patch graphs contain at most 36/166 nodes and 74/208 directed edges respectively. These are small sparse graphs, not production-scale evidence.
- Fifteen test cases have identical patch/reference inputs; 39 have identical patch/runtime inputs. Report unchanged and changed strata, without dropping unchanged cases from the primary denominator.
- Exact effective numerical inputs recur across training and test for a few TrainTicket cases. The current split is incident-disjoint but does not establish unseen-template generalization.
- The label is the recorded injected service. Existing evidence does not independently establish business degradation for every injection; pod replacement can recover automatically.
- Candidate features are shared, but patch nodes and their type features also change. The result is not an edge-only ablation. Graph tensors discard relation labels and symmetrize edges; node-type features and unit-weight adjacency are the actual scorer inputs. Version nodes inherit service-level summaries, so version-specific fault localization is not established.
- CPU replay preserved all AC@1/3/5 decisions; tiny TrainTicket near-tie changes affected full rankings and MRR by less than 0.000102. Preserve this qualification.

The paper correctly discloses many historical limitations, but disclosure alone does not validate the retained historical comparative tables. The three mABC TrainTicket AC@5 < AC@3 rows remain mathematically inconsistent under a common ranking/denominator. No new E2R experiment repairs those old cells.

## Reviewer-comment coverage matrix

Repeated reviewer questions are mapped to the corresponding concrete concern below. “Partial” means the concern has an explanation or protocol, not completed experimental evidence.

| Reviewer concern | Existing evidence/status | Exact missing run or artifact |
|---|---|---|
| R1: contributions of graph patches, scorer, LLM, rules, fallback and reranking | M1 isolates input substitution; M2 code provides proposal/direct/rule/fallback routes. Partial. | B1 training-condition matrix and node-only control; B2 matched formal comparison. Remove evidence-reranker claim or implement a real same-patch with/without-reranker contrast. |
| R1: same telemetry + same LLM without GraphPatchPlans | Both M2 routes share factual observations, but only proposal receives the informative rule catalog. Pilot unrun. | B2 direct, direct+catalog and proposal routes with identical facts/model/limits; report raw-observation versus derived-rule-information distinction. |
| R1: identical inputs/candidates/probes/topology/safety across baselines | Common provider exists; historical equality unverified. | B0 comparison contract and E2R export bundle; baseline owner supplies consumption/configuration evidence and predictions. |
| R1: unseen, composite and open-ended failures | No empirical coverage; current taxonomy finite. | B3 held-out scenario; B6 composite/stateful/unknown boundary tests with full-recovery and abstention denominators. |
| R1: other fault types, multi-root, concurrency, stateful dependencies, production | New four-scenario graph corpus improves topology coverage but is distinct from old seven-fault data. | B6 controlled concurrent/stateful tests; explicit absence of production validation. Production deployment is not required. |
| R1: fixed 40-second latency overhead | New stage timer and API request timing exist. Historical decomposition unavailable. | B7 wall-clock stage trace including waits, collection, graph/LLM, execution, verification and cleanup. |
| R1: AC@5 < AC@3 | Error documented, values retained. Unresolved. | Original prediction/denominator recomputation by baseline owner; otherwise withdraw those comparisons from evidentiary tables and conclusions, preserving original files in audit archive. |
| R2: LLM calls, inference cost, cheaper alternatives | M2 retains attempts/usage/fallback; no successful new campaign. | B2/B7 per-route calls/tokens/cost/latency, zero-LLM E2R arm, quality-cost frontier and all failed attempts. |
| R2: scaling services and telemetry | Two graph sizes only. | B7 observed-size characterization and controlled offline graph/telemetry scaling; no production-performance claim from synthetic expansion. |
| R2: workload on both applications | Boutique manifest specifies ten users; historical achieved load and TrainTicket workload unknown. | B0 historical unknowns table; B5 frozen fresh workload script/image, operation mix, achieved load and healthy/faulty measurements. |
| R2: benchmark representativeness | Limitations discuss small controlled subjects. | Explicit service/edge/telemetry/concurrency characterization; B6 boundaries; separate topology-only TrainTicket. |
| R2: safety review/post-repair state/evidence references/measurable predicates | Method section gives design contract. Current repair path does not enforce it. | B4 executable contracts and mutation fixtures; B5 real complete trace with PASS/FAIL/UNKNOWN post-state oracle. |
| R2: construction of 760 incidents, per-category counts and injection parameters | Counts reconstruct; current templates known, historical configurations incomplete. | B0 per-record provenance table separating recorded facts from current defaults; B5 every fresh injection’s target, magnitude, duration, affected resources, and verification. |
| R2: precise restart/restore/rebalance/scale semantics; action-to-fault mapping | Current cleanup/resource templates inspectable; scale/rebalance remain design extensions. | B4 action eligibility table. Restrict measured action set to implemented restorative actions; delete unsupported scale/rebalance performance language. |
| R2/R3: empty replication package | Local files now exist, but no verified public anonymous package. | B0/B8 staged complete package plus anonymous access/reproduction check when published; publishing is a separate authorized action. |
| R2/R3: motivation lacks supporting example/statistics | Historical service/category disagreement gives a concrete motivation. | One real event trace showing diagnosis/action/oracle distinction; use 7/7 wrong-service/correct-category and 6/9 WaitAndVerify passes only as descriptive motivation. |
| R3: graph novelty and unclear terminology | Operations and source implementation are now explicit. | B1/B2 effect evidence; concise actual-operation table: input fact → edit → tensor change → candidate mapping. Claim no novelty for GraphSAGE itself. |
| R3: simplistic repairs, applicability and production risk | Scope disclosed, but current guards are insufficient for a claimed execution boundary. | B4 fixes and negative tests; B5 targeted restoration; B6 rejection boundary. No blanket production-safety statement. |
| R3: recent graph RCA / RCA Agent comparison | User owns external baseline work. | Export common inputs/splits/labels-for-evaluator-only/cost policy; obtain at least the user-selected recent graph and agent predictions under that contract. This review does not implement those baselines. |
| R3: domain knowledge and predefined rules | Catalog and deterministic repair mappings exist in code. | B0 catalog of every rule, thresholds, evidence fields, calibration source and whether hand specified; include known taxonomy exposure. |
| R3: unclear/redundant presentation and apparently AI-generated figures | Editable architecture available; old experimental figures unresolved. | B8 replace unexplained terms with actual operations; regenerate evidence plots from exported rows; provide editable figure sources. Do not infer fabrication from appearance. |

## Maximum-two claim map

**C1 — bounded diagnosis claim.** On the declared recorded-injection population, observation-supported graph updates improve held-out injected-service ranking beyond current-topology synchronization under matched observations and controlled scorer training. An LLM-specific improvement is a conditional refinement of C1, permitted only if B2 beats the rule and catalog-informed direct arms with failures included.

**C2 — bounded recovery claim.** Within the declared action and fault scope, evidence-bound, service-targeted restorative plans produce attributable application recovery and reject unmeasurable or unsupported actions under the tested policy. This requires B4–B6; category-keyed injection cleanup alone cannot support C2. Distinguish controlled common-diagnosis repair comparisons from any genuinely connected diagnosis-to-action episode.

| Result | Permitted conclusion |
|---|---|
| Only current M1 remains positive | Patch input benefits a patch-trained scorer on one recurring-condition split. |
| B1 diagonal and same-weight comparisons both positive | Representation benefit survives matched training controls in the tested setting. |
| Patch loses to node-only control | No demonstrated need for graph propagation; revise the mechanism claim. |
| B2 LLM equals or loses to deterministic patch | Keep the rule-based method/cost result; do not claim LLM benefit. |
| Proposal beats direct but not direct+catalog | Benefit may come from supplied rule-derived information, not GraphPatchPlan execution. |
| B5 full and no-action recover equally | No demonstrated causal recovery benefit. Report spontaneous recovery or inadequate fault persistence. |
| Typed repair rejects more bad plans but does not improve recovery | Claim measured admissibility/rejection behavior only, with false rejection and cost. |
| B6 rejects unknown incidents safely | Claim bounded abstention, not unseen-failure recovery. |

Anti-claims: no universal superiority; no production readiness; no general repair of unseen categories; no state-corruption repair; no causal interpretation of historical conditional rates; no full graph-method attribution for old deterministic numbers; no external-baseline superiority without new matched predictions; no three-model result from configuration aliases; no edge-fusion/reranker/rollback result from design prose.

## Minimal MUST execution package

These are proposed new runs, not authorized or executed spending. The existing cross-campaign 40-request ceiling remains binding until explicitly replaced. Full evaluation is a separate budget decision after the bounded pilot.

### B0 — freeze identity, corpus protocol and comparison interface

No API/GPU/cluster work. Produce `protocol.json`, `corpus_manifest.csv`, `rules_and_actions.md`, `comparison_contract.json` and `historical_discrepancies.md`. Preserve all old artifacts and identify each result as historical deterministic, M1 graph, M2 pilot, or new live experiment.

For 760 records, publish counts CPU 130, memory 125, pod failure 95, configuration 70, delay 110, loss 120, disk I/O 110. Mark historical workload/images/per-incident injection settings unknown where not recorded. For 798 cases, publish the four-scenario distribution above, exact split IDs, telemetry availability and candidate sets. Do not combine these corpora into one enlarged performance denominator.

Export to the baseline owner: cutoff-bounded observations and raw-to-feature description; candidates/entity mapping; reference/runtime topology; modality availability and missingness; incident/split/group IDs; training/validation permissions; common ranking schema; timeout/abstention/failure convention; model/backend and request budgets; allowed tool observations; action policy; evaluator-only labels; complete E2R ranks and request/stage logs. Baseline-owned output must report actual consumed modalities, preprocessing differences, training data, model identity, candidates, failures and costs. Equal access is required; identical numerical encodings are not.

### B1 — complete the structural mechanism control

Dataset: existing 798 snapshots and unchanged 566/116/116 split. Same architecture/configuration as M1, seeds 42/43/44, validation-only stopping, no HPO.

Train on reference, runtime and rule-patch graphs separately; evaluate each trained scorer on all three inputs. Reuse the three audited patch-trained checkpoints. This adds six graph-model fits. Add three node-only fits using the same service features, architecture capacity declared, training data and loss, but no neighbor messages. These are internal E2R component controls, not user-owned external baselines.

Primary contrasts: patch-trained/patch-input versus runtime-trained/runtime-input, and patch versus node-only. Also retain the existing same-weight contrasts and show the full 3×3 train/input matrix. Report AC@1 primary, AC@3/5 and MRR secondary; all 116 test incidents remain in every arm. Report per-application, per-scenario, graph-changed/unchanged and feature-coverage strata. Use paired incident differences with seed averaging; bootstrap by repeated injection condition when those conditions recur, with incident bootstrap as sensitivity. Two primary C1 contrast tests use Holm correction; report effect sizes and intervals, not only significance.

One diagnostic operation-group deletion pass on frozen checkpoints is inexpensive: remove identity-alignment, blackhole/mismatch or version-split groups in turn, including dependent operations consistently. This is a sensitivity analysis, not a retrained method comparison. Report invalid deletion configurations explicitly.

### B2 — bounded compatibility pilot, then formal LLM mechanism/cost study

The eight stopped old requests are verified: four Gemini-route failures, two GPT-route HTTP 500 failures, two Claude-route HTTP 500 failures. They remain in total expenditure accounting with unknown usage. The existing current pilot can spend at most 32 further requests: eight fixed cases × two generations × proposal/direct. Use explicit `--cases 8 --generations 2 --hard-request-budget 32`; runner defaults still describe ten cases/40 requests. Require the initial pair to pass strict transport, envelope, complete-ranking and executable-plan checks before continuation. No silent provider hopping, automatic retries or resumption of stopped old routes.

Model for the primary proposed campaign: the currently configured alias `qwen3.8-flash`, one backend for all routes, requested temperature 0, maximum output 2000, timeout 120 seconds. Record the returned model ID and actual request settings; alias presence is not proof of availability or model identity. Generations are repetitions, not independent incidents. Do not invent an API sampling seed where unsupported.

After pilot and prompt freeze, proposed formal population is the remaining 108 fixed test cases: 55 Boutique / 53 TrainTicket, excluding the eight pilot cases. This is a held-out M1 test subset, not a newly collected independent corpus. Run two generations of three routes: direct rank; direct rank with the same rule catalog; proposal selecting catalog operations and ranking. **648 attempted requests maximum** for a complete formal campaign, requiring a new explicit budget. All other arms reuse those responses/checkpoints: reference, runtime, rule patch, accepted proposal + GNN with/without fallback, proposal’s own ranking without GNN, direct, direct+catalog. Seeds 42/43/44 for GNN arms; one fixed primary model.

Keep every scheduled incident in unconditional metrics; invalid/abstained/timed-out rankings contribute zero AC/MRR. Report coverage, semantic acceptance, fallback once per proposal request, selected-operation counts, equality to rule/runtime graphs, provider identity/usage and route latency. Missing or unstarted work must be distinguished from completed failures; an interrupted campaign is incomplete, not successful-only evidence. Pair by incident, average seeds then generations. Predeclare contrasts proposal+GNN versus direct+catalog and versus rule patch; correct within this LLM contrast family. Raw direct comparison remains useful but cannot alone isolate the catalog contribution.

**Scope correction:** `proposal_rank_no_gnn` tests removal of GraphSAGE. It is not the paper’s evidence-bound post-scorer reranker. The smallest package removes that unimplemented component from empirical claims. If the paper retains a reranker contribution, its implementation and identical-accepted-patch ablation become additional MUST work.

### B3 — one real held-out scenario and telemetry boundary

Hold out all T3 selector cases: 199 test incidents. Train/validate only T1/T2/T4; within each of their six application/scenario strata, sort case IDs, shuffle with split seed 42, reserve floor(15%) for validation. This gives **512 train / 87 validation / 199 test**. Keep each originating incident entirely in one split. Train rule-patch and runtime scorers with seeds 42/43/44: six new fits; evaluate both inputs with both training conditions. No tuning on T3 labels.

This tests an unseen training scenario, **not an unseen rule vocabulary**: selector/blackhole rules already exist. Say that explicitly. Separately score the frozen main models on the 116 original tests after removing logs, traces plus trace-derived edges, and all telemetry plus telemetry-derived edges, with missingness flags updated. TrainTicket’s already missing telemetry is not a successful masking test. These are input-loss robustness tests, not substitutes for matched retraining.

### B4 — repair contract and oracle checks before any mutation

Implement only two operational repair primitives for the primary new study: restore approved resource fields on a named deployment/container, and restore an approved service selector. Record old/new fields, current-state precondition, namespace/target, evidence IDs, permitted change, executable rollback and post-action predicate. Known-good values must be available to every repair arm, not extracted from hidden injection metadata. Retain `WaitAndVerify` as explicit abstention. Category-named Chaos deletion belongs to the separate benchmark-cleanup diagnostic block.

Create 60 local labeled fixtures: for each primitive, five valid fixtures and five each of missing evidence, wrong target/namespace binding, stale precondition, unavailable/undersampled verification source, and missing/inapplicable rollback. Evaluate the current type/regex guard and new contract validator on the same fixtures. Expected outcomes are human-specified contracts, not model-generated ground truth. Report invalid acceptance and valid rejection with their separate denominators; any invalid acceptance in the prospective executor is a stop. Add explicit PASS/FAIL/UNKNOWN post-state fixtures for stale, missing and timed-out observations.

These are software-contract checks, never empirical recovery rates. A concrete running example must resolve one observed selector mismatch, its diagnostic patch, selected service, exact restorative action, precondition read, execution, fresh oracle samples and rollback outcome if invoked.

### B5 — recovery attribution plus small service-targeted repair study

Environment: isolated Online Boutique deployment only, after namespace/permission/resource and telemetry checks. No production or live TrainTicket dependency is required.

**B5a: cleanup attribution.** Seven historical fault categories × five diagnostic interventions × three randomized repeat blocks = **105 fresh injection attempts**. Interventions: correct-service/correct-category, wrong-service/correct-category, correct-service/wrong-category, both wrong, no action. Fix wrong-service/category draws and arm order with seeds 42/43/44. Choose eligible target services and all injection parameters before outcomes are observed; disk-sidecar cases require special business-impact labeling. Each arm gets a fresh verified injection, matched observation horizon and independent cleanup. Keep fault persistence through the measurement window; record natural expiry. This is a small mechanistic diagnostic study, not a precise seven-category recovery benchmark. Expected service-independence of category cleanup is a result to test, not a nuisance to remove.

**B5b: operational restoration.** Two fault mechanisms (resource underprovisioning; invalid service selector) × two targets (cartservice; checkoutservice) × five repeats = **20 matched condition blocks**. Three arms per block: evidence-bound restoration; current diagnosis-only report template; no action. Total **60 fresh injection attempts**. Randomize with master seed 20260926; preserve block membership. Use the same predicted service/category and available evidence across the two repair arms, obtained from the frozen E2R diagnosis path and a documented observation-only category adapter. Save this interface explicitly: M2 currently emits service rankings but no repair category or evidence-bearing RCA report. Do not fill that gap with evaluator labels in the main comparison.

The common-diagnosis comparison isolates repair behavior. Report separately any episode that recomputes the entire live pipeline; replaying a common diagnosis is not an independent end-to-end run. Use the same policy and known-good-state access for both repair arms. When the diagnosis is wrong, it remains wrong in both. Do not remove these episodes. Evaluator-controlled diagnoses belong only to B5a.

For all live blocks, maintain both denominators: every scheduled injection attempt, and independently verified fault episodes. Report injection failure, initial-health failure, diagnosis failure, plan rejection, action attempted, command success, verifier UNKNOWN, application recovery and cleanup failure separately. Conditioned-on-verified-fault recovery is permitted alongside all-attempt yield; neither may silently omit failures. The primary C2 contrast is application recovery for evidence-bound restoration versus no action; template contrast is secondary. With only 20 matched blocks, intervals may remain wide: report that instead of promising significance.

### B6 — minimum failure-boundary package

Run after B4/B5 safety and oracle gates, with the frozen method. All cases are controlled tests, not natural production incidents.

- Composite: two fixed service pairs, three repeat seeds 42/43/44, full method versus no action, fresh simultaneous selector faults: **12 episodes**. Use root-set Recall@2, both-roots recovery, partial recovery, collateral mutation and abstention. A single repaired root is not full success. The current single-root interface need not be expanded to claim success; an explicit limitation/abstention result is valid.
- Stateful dependency: reversible redis-cart resource misconfiguration, three repeat seeds, full method versus no action: **6 episodes**. Populate and read back business cart canaries before and after. Report business state separately from readiness. The manifest uses Redis `emptyDir`; do not restart/replace Redis and claim data persistence. This is stateful-dependency exposure, not a durable database or corruption-repair experiment.
- Healthy change: benign deployment replacement and benign replica change, three repeats each: **6 episodes**, diagnosis/decision only, no induced degradation. Measure false repair proposals and abstention. All-fault corpora cannot establish this rate.
- Unsupported/open-ended boundary: **12 offline case fixtures**, four each for unknown mechanism, missing required evidence, and absent verification source. Measure rejection/abstention and unsafe proposal rates. Do not count these as successful unseen repairs. Arbitrary state corruption remains explicitly unsupported; no destructive live corruption experiment is needed.

### B7 — measured workload, latency, cost and scaling

Share instrumentation with every run. Fresh primary workload: the declared Boutique v0.10.1 load generator with 10 users; publish the actual workload code, business-operation mix, think time and achieved request rate. Record resolved deployed image identities, resource settings and topology. A healthy 30-user characterization is a second load point; proceed to fault work there only if it is healthy and a load-robustness claim is desired. Never equate user count with offered/achieved throughput. Historical TrainTicket workload remains unknown; its offline graph result needs no invented workload description.

Before faults, collect 180 seconds of healthy workload and at least 100 business requests, including at least 20 instances of the operation used by the oracle. Define per-operation baseline throughput/error/p95 latency. Verify a fault with the independent technical check and two successive 30-second windows showing the predeclared business symptom (e.g. p95 > 2× baseline or errors exceeding baseline by five percentage points). Faults without measurable business degradation can enter technical-check analysis but not an application-recovery success denominator without disclosure.

After an action, use a fixed 180-second horizon. Example primary recovery predicate: three complete successive 30-second windows with error rate no more than baseline + one percentage point, p95 latency no more than 1.2× baseline, throughput at least 80% of baseline at the same offered load, sufficient samples, ready endpoints and unchanged state canaries. Freeze thresholds using healthy calibration only. Missing/insufficient observations are UNKNOWN and non-success in the primary unconditional metric. Do not run cleanup until this horizon ends or a predeclared safety stop fires. The ground-truth fault checker is a separate secondary oracle, never the sole business oracle.

Record monotonic timestamps for injection, intentional settling, collection/probes, preprocessing/catalog, LLM proposal, validation, graph execution, tensorization/scoring, category mapping, repair planning/validation/rendering, Ansible execution, rollout waiting, post-action observation and cleanup. Report operational response time from diagnosis-ready observation to verdict, and full episode time separately. Parallel collection needs wall time plus resource work; nested totals must not be added twice. Never estimate stage costs by subtracting 40 seconds from historical means.

Per logical incident/route, report attempted/succeeded LLM calls, failures, retries (zero by primary policy), fallbacks, input/output/cache tokens when supplied, usage-missing count, actual model/protocol, p50/p95 latency, CPU/GPU time and measured memory. Dollar cost requires the actual provider billing/rate source and date; missing usage is unknown. Cost per successful localization/recovery is undefined when successes are zero. Show accuracy or recovery versus requests, tokens and latency for rule-only, direct, catalog-direct and proposal routes. Do not charge the two experimental generations as if deployment always needs two calls; distinguish research expenditure from one-pass operational cost.

Scaling has two separate axes. First report observed 798-case distributions of services, nodes, edges, accepted telemetry rows, serialized bytes and prompt tokens. Second use offline replicated graph fixtures at 65/130/260/520 candidates with fixed degree, plus 1×/2×/4×/8× telemetry volume at fixed graph size; ten timed repetitions per condition after warm-up, seeds 42/43/44 for fixture construction. Measure preprocessing, validation, tensorization, inference and peak memory. These fixtures measure overhead only; no accuracy/recovery claims from replicated graphs. Count prompt growth locally; paid scaling calls are NICE, not necessary for this minimum package. Never silently truncate large inputs.

### B8 — result package and manuscript gate

Produce one table of method identity, scope and actual completed evidence; one component table; one recovery/outcome table with denominators; one latency/cost plot; one boundary/scaling table; and one complete incident trace. Preserve negative results. Publish source, environment specification, data/provenance/redistribution instructions, split IDs, frozen checkpoints, raw full rankings, prompt/response/usage logs with secrets removed, injection/workload manifests, oracle observations and regeneration commands. Verify a clean local reproduction first. Once publication is authorized, verify anonymous access to the exact artifact URL. A local directory does not answer the reviewer’s empty-public-package complaint.

## Full run order and stop/go rules

1. **B0 and scope freeze.** Resolve method/result identity and declare the two claims. Stop attributing old numbers to graph/LLM modules immediately; no spending is needed for this correction.
2. **B4 local executor/oracle contracts, B1 runner extensions and B2 matched-catalog route.** Fix the concrete blockers below. Local checks must fail on namespace mismatch, unsupported evidence and UNKNOWN verification. No live mutation before this gate.
3. **B1 seed-42 bounded smoke, then remaining declared seeds.** Stop on leakage, inconsistent candidates, invalid rows or non-finite values. If patch gains disappear under matched training/node-only controls, narrow C1; do not optimize repeatedly on the test set.
4. **Existing B2 two-request sanity, then remaining bounded pilot.** A bad first pair stops. Unknown-outcome requests stop continuation. Existing old failures consume their recorded budget. No model switching to find a positive result.
5. **Freeze formal B2 protocol and obtain a separate explicit budget if proceeding.** Only after measured pilot cost/latency and strict route validity. Then complete the 108-case campaign once; unchanged labels/splits/prompts. A result failing its contrast is negative/inconclusive, not grounds for unreported reruns.
6. **B3 held-out scenario and input-loss analysis; B7 offline scale.** Can run independently of live deployment after code gates. Failed generalization limits C1, not artifact validity.
7. **Healthy isolated deployment and B7 workload/oracle calibration.** Verify namespace-scoped credentials, no overlapping baseline campaign, application health, observation sources and cleanup. Stop on cross-namespace mutation capability in the prospective executor, inadequate capacity, missing oracle samples or unreliable reset.
8. **B5a one complete five-arm block and B5b one three-arm block.** Check fault persistence, fresh resets, common evidence and timing. If no-action recovers before measurement, fix the persistence protocol prospectively or classify that fault as self-recovering; do not retrospectively remove inconvenient episodes.
9. **Complete B5a/B5b as frozen.** Stop the campaign on an out-of-policy mutation, state loss, uncontrolled fault persistence, or inability to restore healthy state. Record the stopped attempt and cause. Ordinary failed repairs remain experimental outcomes.
10. **B6 boundaries, then B8 analysis and claims audit.** No production-safety or open-ended-repair claim. Acceptance remains provisional and depends on the actual outcomes and user-owned matched external comparisons.

## Concrete blockers before spending

| Blocker | Verified code/artifact basis | Minimum resolution |
|---|---|---|
| Claimed evidence reranker absent | `methods/E2R/*` and `scripts/e2r/score_m2.py` score graphs or retain proposal ranking; no evidence-bound post-scorer promotion stage | Rename the no-GNN arm accurately and remove that unmeasured claim; implement only if retaining it as a contribution. |
| Proposal receives extra derived information | `llm_pilot.messages_for` includes `supported_operations` only for proposal | Add direct+catalog control, or explicitly restrict the result to complete-pipeline comparison. |
| Patch-trained substitution mistaken for fair trained comparator | `run_graph_experiment.py` always trains and validates on `patch` | B1 train-condition control; current script needs an explicit training-condition extension. |
| Formal experiment cannot run through current pilot assumptions | Runner enforces ≤40 attempts and two routes; score/analyze scripts assume seven arms, patch checkpoints and exact completed budget | A separately named formal runner/analysis extension with frozen IDs/routes; do not bypass the pilot ceiling by quietly opening campaigns. |
| Live diagnosis-to-repair bridge absent | M2 emits service rankings, not evidence-bearing category/report; `ReportDrivenRemediator.build` constructs evidence=[] and confidence=0.9 | Explicit observation-only category/evidence adapter; log uncertainty. Do not insert ground truth or claim that fabricated confidence is measured confidence. |
| Category cleanup cannot establish service-targeted benefit | Six repair mappings delete a category-named Chaos object; service does not affect the command | Keep cleanup attribution separate; add B5b restorative actions. |
| Structured namespace does not bind executed command | Pure local probe: action namespace `e2r-supplement-20260925`, but command targets `deployment/frontend -n online-boutique`; `check_repair_plan` returned `(True, [])` | Deterministic command rendering from validated structured target/namespace and a common permitted-action check at execution. |
| “YAML validation” is only string scanning | Pure local probe: `check_playbook_yaml('this is not valid yaml: [ ] [ ]')` returned `(True, [])` | Parse and validate actual executable task structure; avoid arbitrary model-authored command execution for the two bounded primitives. |
| Evidence/preconditions/rollback/three-valued oracle not implemented | Repair schemas provide command and descriptive rollback/verification strings; guard checks type/risk/text; verification bridge returns a boolean | Implement B4’s minimal binding and post-action sample evaluation before C2/live safety claims. |
| Isolated manifest lacks the claimed rich observability and full isolation | `ENABLE_TRACING=0`; no Jaeger objects; default service accounts; ResourceQuota but no experiment-specific executor RBAC; Redis uses `emptyDir` | Declare actual modalities, enable/verify tracing only if claimed; scope executor credentials and selectors; coordinate shared node/resource use; protect state canaries. Namespace separation alone does not prevent shared-node resource interference. |
| Probe measurements can be misinterpreted | Context provider uses gated probes; ping target defaults to 8.8.8.8; disk probe writes 10 MiB in a sidecar and records timeout or fixed 200 ms proxy | Freeze probe policy for all arms; log attempts/missingness; call these symptom probes, not business latency or read-only collection. Do not let a sidecar-only disk effect stand in for app failure. |
| Model metadata can overstate effective settings | Current primary config is temperature 0, but campaign logging hardcodes 0 while transport reads config; Responses route omits temperature | Record actual serialized nonsecret request settings. For GPT Responses do not claim temperature 0 unless it is actually sent/supported. |
| Historical table/result identity remains unresolved | `4_reported_experiments.tex` retains impossible mABC top-k cells and unmatched model/plot attribution | Original raw evidence or withdrawal from comparative conclusions; no arithmetic cell repair. |

These findings do not invalidate the prior M1 narrow integrity result. The earlier M2 code review passed the then-bounded pilot implementation; it did not establish live compatibility, full architecture coverage, the later three-route formal design, or repair safety.

## MUST versus NICE, feasibility and budget

**MUST for the two-claim revision:** B0–B8 as scoped above, with the ability to narrow/drop an unsupported claim. A reranker is not mandatory if it is removed from the claimed implemented method. Broad successful unknown-failure recovery is not mandatory; honest boundary evidence is.

**NICE after core results:** repeat the formal LLM study with configured `deepseek-v4.1-flash` under the identical protocol; application-held-out training; a second live application; richer workload sweeps; real route-weight restoration/scale policies; paid LLM scale probes; a true typed evidence reranker; larger multi-root studies. `gpt-5.6-luna` is another configured route, not existing result evidence. No production deployment is required by this package.

Available now without external access: corpus/provenance exports, B1/B3 implementation and local CPU execution, frozen-checkpoint scoring, B4 local fixtures, B7 overhead tests, paper/artifact preparation. Existing M1 three-seed training took approximately 297 seconds total on the recorded 4090D; this is a measured reference, not a promise for new runs. B1 adds nine fits and B3 six; report actual resource time and use a bounded timing pilot rather than booking speculative GPU hours.

Needs network/API budget: successful current 32-attempt pilot; proposed formal 648-attempt campaign; any optional model repeat. Eight old failed attempts remain visible. Live diagnosis calls, if used, must have their own explicit cap and route accounting; deterministic rule-patch live diagnosis is a valid zero-LLM instantiation but must be labeled as such. Do not silently add live calls to the completed offline cost figure.

Needs isolated testbed preparation, not production access: 105 cleanup-attribution attempts, 60 targeted-restoration attempts, 24 live boundary episodes, healthy calibration and any invalid-injection repeats explicitly recorded. Fixed observation windows alone make this several hours of serial cluster work; use the first complete blocks to estimate the actual duration. Fresh attempts must not overlap user-owned baseline injections on shared resources.

Unsupported by current assets: historical workload reconstruction, natural production incidents, production-scale reliability, durable database corruption recovery, live TrainTicket telemetry/recovery and broad open-ended action coverage. State those limits directly. Adding an unobserved production claim would weaken the revision; finishing the controlled contracts and exporting matched evidence is the useful next step.

## Primary evidence read

The review directly read `review/review.txt`; `refine-logs/EXPERIMENT_PLAN.md`, `EXPERIMENT_TRACKER.md`, `M2_CODE_REVIEW.md`; `idea-stage/docs/research_contract.md`; M1 `REPORT.md`, `EXPERIMENT_AUDIT.md`, `analysis.json`; input-audit `REPORT.md`, `summary.json` and per-case rows; paper sections `3_method.tex`, `4_evaluation.tex`, `4_reported_experiments.tex`, `5_validation.tex`, `7_limitations.tex`; E2R `data.py`, `graph.py`, `llm_pilot.py`; `run_graph_experiment.py`, `run_m2_llm.py`, `score_m2.py`, `analyze_m2.py`, `audit_inputs.py`; `rca_bench/remediator.py`, `measurement.py`, `context_provider.py`; the entire parsed isolated Boutique manifest; and `docs/OPENCODE_GO_CONFIG.md`. Additional direct checks used the repair planner/schema/guard/verification bridge, transport implementation, sanitized effective model settings, existing M2 request journals, corpus manifest and representative raw scenario metadata. Secrets were not copied into this report.
