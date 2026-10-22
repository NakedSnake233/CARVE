# B2/B3 independent experiment audit

**Overall verdict: WARN. No actual integrity BLOCK found.**

**Date:** 2026-09-26. **Auditor:** fresh GPT-6-Astra ultra reviewer `/root/b2_b3_result_audit`, with fresh GPT-6-Astra ultra generalization/scaling subreviewer. `review_independence: same-family`; `acceptance_status: provisional`. Deterministic equality checks are direct evidence; semantic acceptance remains provisional. No network, API, SSH, GPU, Kubernetes or retraining was used for this audit. Implementation and experiment results were not edited by the reviewer.

| Component | Verdict | Artifact checks | Permitted interpretation |
|---|---|---|---|
| Qwen formal + disjoint pilot/combined view | WARN | identities, prompts, labels, responses, metrics/CIs and concatenation PASS | bounded single-model offline comparison; negative for LLM patch + GNN superiority |
| Six generalization runs | WARN | splits, labels, plans, checkpoints and reported arithmetic PASS | fixed-base held-out-scenario and source-application transfer only |
| CPU/GPU scaling | WARN | all raw-to-summary timing arithmetic PASS | recorded implementation overhead of replicated synthetic fixtures |
| Multi-root proxy | PASS within declared simulation-only scope | all 114 compositions and 342 rankings PASS | GT-conditioned synthetic two-root sensitivity only |

The report covers final files named below and the raw evidence referenced by them. It does not certify older paper tables, cross-model compatibility, recovery, live concurrent faults, production applicability, external billing or historical execution authenticity beyond retained artifacts.

## A. Ground-truth provenance — PASS with explicit proxy separation

Qwen and generalization use recorded dataset labels from each source incident's `ground_truth.json`, not predictions. `methods/E2R/data.py:183–193` discovers labels separately; `run_m2_formal.py:64–72` writes separate frozen model inputs and evaluator labels. Independently verified all 116 Qwen/test labels against source GT and metadata and rebuilt all 116 model observation snapshots exactly from raw topology/telemetry. The generalization subaudit separately checked all 798 manifest GT service labels.

Observation construction uses pre/during operational fields and diagnosis cutoffs (`methods/E2R/data.py:37–50`, `61–180`). All accepted timestamps remain at/before cutoff; an absent maximum denotes no accepted telemetry. Qwen's actual 696 sent prompt arrays regenerate exactly from the frozen observations. Forbidden GT/directory/scenario keys and original source-directory IDs are absent. `methods/E2R/llm_pilot.py:66–120` gives catalog-direct and proposal identical facts/catalog; raw-direct omits catalog. Future/excluded-row coverage counts are removed from LLM evidence. No post-recovery state is used.

The multi-root construction deliberately uses evaluator roots to choose the overlaid service. This is disclosed in `run_multi_root_proxy.py:185–193`, `278–293` and `multi_root_proxy_v1/results.json:17–19`; it is not an unbiased blind multi-fault benchmark. Labels are not embedded as tensor features, but label-conditioned input construction is an essential qualifier.

## B. Score normalization — PASS

AC@k is a true-service rank indicator and MRR its reciprocal; failures contribute zero. Seeds are averaged inside generation, generations inside incident, and incidents receive equal weight (`analyze_m2_formal.py:8–22`, `47–61`). All means, 5,000-draw incident bootstrap endpoints, and all four paired AC@1 contrasts reproduce for every reported all/application group in the formal, pilot and combined analyses. These are incident bootstrap intervals without a multiple-comparison correction; they do not establish robustness over new fault families.

Proxy Recall@k divides retrieved roots by two per composition, ExactSet@2 uses exact set equality, and AP divides precision-at-relevant-ranks by two (`run_multi_root_proxy.py:198–220`). Every saved row and aggregate recomputes. No metric denominator is derived from a model's own maximum, minimum or mean. Input `log1p` and graph-neighborhood averaging are feature/model transformations, not score normalization.

## C. Result existence, identities and arithmetic — PASS; provenance/reporting WARN

### Qwen campaign and scorer

Formal coverage is exactly **108 incidents × 2 generations × 3 routes = 648 starts and 648 finishes**; every expected journal identity occurs once, start precedes finish, and attempt numbers are sequential. All 648 provider transports succeeded; 476 outputs passed strict route validation. Each raw response envelope, normalized response text/usage/model/ID, regenerated validation result, sent prompt and recorded setting was verified. The formal model/settings are `qwen3.8-flash`, Anthropic-style nonstreaming request, temperature 0, max_tokens 2000, thinking disabled. Validation failures/abstentions are not deleted. Provider-side model weights or billing cannot be attested by a returned alias.

The formal scoring file has exactly **2,916** `(incident, arm, generation, seed)` identities: **2,666 ranked / 250 failed**. Expected full candidate permutations, labels, true-service ranks, strict acceptance, fallback flags, direct-response rankings and failed rows all match source evidence. Failed proposals yield null no-fallback ranks and the frozen rule-patch rank in fallback. Code: `score_m2_formal.py:43–76`, `79–117`; `analyze_m2_formal.py:31–43`.

Formal checkpoint hashes match the three current M1 patch checkpoints, which load with seeds 42/43/44 and `shared_observation_v1`. Hashes are `c4895cf287eb11dae65267fdf0e6c53d7053680ec651e3a7066e75baf58889f5`, `5b1486bf9bd8e33d8569170ecc9d79344409e0647062fc6c801ad5661c36b467`, and `ce72f2987e5e9514eea28f56c76d2c50e67445dc8746b79006071fa95ecbfb80`.

The successful eight-incident pilot has 48 calls and 216 score rows; its cases are disjoint from the 108 formal cases. The combined view is **exact concatenation**, covers exactly the original 116 test IDs, and contains **696 calls; 3,132 score rows, 2,863 ranked / 269 failed**. No arithmetic reweighting or sample selection occurred during combination. It is a descriptive pilot-plus-formal aggregate, not 116 untouched prospective formal incidents. The formal subset independently supports the same negative conclusion. The pilot predates checkpoint-hash recording; `m2_qwen38_combined_scores_v1/results.json` now records hashes per shard with pilot hashes null, correctly avoiding a retrospective pilot hash attestation. CPU behavioral replay supports consistency with the current checkpoints, but cannot prove historical bytes.

| Combined arm | AC@1 | AC@3 | AC@5 | MRR |
|---|---:|---:|---:|---:|
| reference | 0.364943 | 0.623563 | 0.781609 | 0.526643 |
| runtime | 0.416667 | 0.683908 | 0.795977 | 0.568137 |
| rule_patch | 0.821839 | 0.933908 | 0.948276 | 0.880542 |
| llm_patch_no_fallback | 0.642241 | 0.777299 | 0.803161 | 0.713857 |
| llm_patch_with_fallback | 0.695402 | 0.856322 | 0.889368 | 0.781998 |
| proposal_rank_no_gnn | 0.771552 | 0.844828 | 0.849138 | 0.809655 |
| raw_direct_rank | 0.366379 | 0.405172 | 0.431034 | 0.392321 |
| catalog_direct_rank | 0.793103 | 0.814655 | 0.827586 | 0.806936 |

Combined no-fallback LLM patch + GNN minus catalog-direct is **−15.0862 percentage points**, incident bootstrap 95% interval **[−23.5632, −6.8930]**; versus rule-patch it is **−17.9598 points**, interval **[−24.5726, −12.0690]**. Formal-only counterparts are **−12.9630** and **−16.0494 points**. Thus the current evidence contradicts a claim that the LLM patch + GNN component is necessary for better ranking in this setting. Raw-direct's lower unconditional score also includes greater schema/abstention failure; it is not a pure conditional ranking-quality comparison.

CPU PyTorch 2.14.0+cpu replay evaluated all **1,740 distinct GNN scoring computations**, verifying accepted duplicated no-fallback/fallback rankings are identical, plus every direct row. Saved Qwen scores used CUDA PyTorch 2.11.0+cu130. **681** full ranking differences (formal 627, pilot 54) are near-tie reorderings; **102** true-rank changes occur only at ranks ≥12 and preserve every AC@1/3/5. Maximum saved-order adjacent score inversion under CPU is **3.58e−7**. Saved MRR arithmetic exactly reproduces from saved rankings; cross-device full rankings/MRR are not bitwise guaranteed. Detailed differences remain in `cpu_replay_differences.json`.

### Request expenditure, caching and latency

| Shard | Calls | Uncached input field | Output | Cache-read input | Cache-inclusive input+output volume |
|---|---:|---:|---:|---:|---:|
| Failed pilot v1 | 3 | 14,456 | 6,000 | 22,528 | 42,984 |
| Successful pilot v2 | 48 | 141,146 | 17,789 | 551,424 | 710,359 |
| Formal | 648 | 1,863,990 | 242,302 | 7,461,888 | 9,568,180 |
| Analyzed combined | 696 | 2,005,136 | 260,091 | 8,013,312 | 10,278,539 |

All reported cache-creation counts are zero. Total Qwen expenditure including the earlier failed v1 sanity is **699 calls**, **2,019,592 input + 266,091 output + 8,035,840 cache-read tokens = 10,321,523 cache-inclusive token volume**. The failed v1 has retained journal usage but predates raw envelope retention; its three calls remain expenditure, not scored evaluation cases. Monetary provider cost is absent for all attempts and must remain **unknown**, not zero. Cache-inclusive volume is not a price estimate; token classes may have different billing rules.

The original v1 analyses report normalized `total_tokens = input_tokens + output_tokens`, omitting cache reads from the summary even though journals preserve them (`rca_bench/llm_transport.py:113–122`; original `analyze_m2_formal.py:69–72`). The formal normalized total is 2,106,292, and combined 2,265,227; these cannot be presented as complete token volume. Reviewer evidence `qwen_supplement_verification.json` supplies every numeric usage field.

**Corrected final accounting verified:** `m2_qwen38_formal_analysis_v2` and `m2_qwen38_combined_analysis_v2` preserve all already-verified metrics, paired intervals, scope and request statistics exactly, and add independently correct raw usage fields and cache-inclusive volume (`analyze_m2_formal.py:65–83`). These v2 artifacts supersede v1 for reporting. `m2_expenditure_v3/expenditure.json` additionally accounts for three DeepSeek failed transports and three GPT Luna compatibility calls: **705 attempts total, 702 reported usage rows, 2,315,241 normalized tokens and 10,351,081 cache-inclusive reported token volume**. All five source journals and each per-run/total field were independently checked; the v3 change correctly marks DeepSeek usage as null/unknown, so reported totals are not proof of zero unreported usage. Monetary cost remains null. Compatibility calls were inspected for accounting only, not as a performance replication or model-robustness study. This is the subtotal for the five listed current campaigns; eight separately retained legacy Gemini/GPT54/Claude35 gateway failures are outside that subtotal and this audit scope. Cache omission is resolved in these final artifacts; retained v1 files are superseded.

Formal request-time sum/mean/p95 are **4,509.6492 / 6.95934 / 11.26878 seconds**. Combined values are **4,847.1211 / 6.96425 / 11.26871 seconds**. Every request duration is finite/nonnegative; start/finish wall timestamps agree with elapsed durations within their one-second timestamp resolution. All analysis sums/means/linear-interpolated p95 values reproduce. These are client HTTP-attempt elapsed times, not independent provider compute time or a full end-to-end recovery pipeline. Saved graph-rank timing starts after host-to-device movement and includes CPU score copy/sorting (`score_m2_formal.py:16–21`); it is not pure GPU kernel time. Accepted fallback/nonfallback rows reuse one timed computation, so their times must not be summed as separate executions.

### Multi-root composition and metric verification

All **116 test sources** are partitioned into **57 disjoint pairs** (29 Boutique, 28 TrainTicket; 114 used incidents) plus leftovers `case00597` and `case00539`. Every pair has two distinct services from the same application; 55 pairs have different scenarios and two share a scenario. Both orientations are reported, yielding **114 compositions × 3 seeds = 342 score rows**, not 342 independent source incidents. Pair order and leftovers regenerate exactly from the saved seed.

Rebuilt all 114 compositions and all 342 predictions on CPU; **rankings are exact**. Across 228 pre/during phases: 636 imported pod rows, 504 remapped endpoint IPs, two preserved base-root pods needed by other services' endpoints, and **zero dangling endpoints or ambiguous valid IP identities**. All pod names and valid IPs are unique per phase. Two original pending pods carry the placeholder `unknown`; neither is an endpoint, and the placeholder is correctly excluded from valid IP identities. **227 unchanged imported pod identities** remain stable across phases. Deterministic remapping depends on pair/source pod identity and original IP, not phase (`run_multi_root_proxy.py:76–150`).

Directed call relations are deduplicated and take the **maximum** source-window count, independently verified for all phases; 1,606 duplicate relation rows collapse (`run_multi_root_proxy.py:54–69`). The original source-local windows are retained under an explicit synthetic/no-shared-clock policy (`:175–193`). There is no simultaneity or causal interaction evidence. Service-local overlay is asymmetric; both orientations preserve that limitation.

Across the six seed/orientation cells, Recall@2 is **0.78947–0.82456**, Recall@5 **0.90351–0.93860**, ExactSet@2 **0.57895–0.64912**, and MAP **0.87282–0.89090**. These are sensitivity results from a GT-conditioned artificial composition, not real concurrent-fault accuracy or recovery. `simulation_only` is an appropriate declared classification; the plan's generic `synthetic_proxy` wording must not be confused with real GT performance.

## D. Executed metric paths / dead code — PASS

All reported Qwen metric, bootstrap, validation, direct-rank and fallback branches have corresponding saved rows and independently recomputed values. All proxy metric helpers are called in the runner and have matching raw and aggregate outputs (`run_multi_root_proxy.py:198–220`, `296–323`). The generalization/scaling subaudit checked invoked model, loss, plan and timing paths. No claimed value was found only in unexecuted metric code. This establishes consistency of current code and retained artifacts, not a historical executable attestation.

## E. Scope — WARN

Only one actual LLM model alias is covered; two generations at temperature zero and three fixed scorer seeds are repeated measurements within an incident. No cross-model robustness, independent unknown fault operation, causal recovery or production claim is supported. The combined view includes the protocol-check pilot and the formal shard; keep both visible. The fixed source data contain four injected topology scenarios from two testbed applications. Generalization uses constrained held-out subsets, scaling one replicated fixture, and multi-root GT-conditioned synthetic compositions.

The tracker initially had stale RUNNING/BLOCKED/QUEUED/TODO entries, but the final reread of `refine-logs/EXPERIMENT_TRACKER.md:17–24` correctly marks B2/B3 artifacts completed with audit pending and their negative/scoped results. Audit status can now be attached; no result file was missing. Retain the pilot hash and scaling hardware provenance qualifiers in tracker notes. Nothing in this audit clears live fault/repair gates.

## F. Evaluation classification

- Qwen and generalization: `real_gt`, meaning recorded injected-fault testbed service-ranking labels; not production or human causal adjudication.
- Scaling: `simulation_only`, no accuracy target; synthetic workload overhead.
- Multi-root: `simulation_only`, GT-conditioned composition; no physical shared cutoff or actual multi-fault execution.

## Generalization/scaling independent subaudit

# Independent forensic review: generalization and synthetic scaling

Date: 2026-09-26. Reviewer: fresh delegated Codex reviewer, **same-family / provisional**, not a different-family independent certification. The reviewer read primary scripts, saved outputs, snapshots, raw service-label files and checkpoints directly. Shared `experiment-integrity.md` and `reviewer-independence.md` were read. No network, API, SSH, GPU, Kubernetes, training or experiment execution was used. Only new files in this audit directory were written.

**Generalization: WARN. Scaling: WARN. No BLOCK-level contradiction found in the inspected evidence.** The saved arithmetic and split identities pass; historical content provenance, numerical ties and claim scope require qualifications below. A zero-length `checks_failed` array is a set of bounded checks, not an unconditional integrity clearance.

## Reproducible evidence

Run from repository root:

```sh
.venv-e2r/bin/python .aris/traces/experiment-audit/20260926_b2_b3/generalization_scaling/forensic_audit.py
```

Artifacts: `forensic_audit.py` is reviewer-authored; `forensic_output.json` contains detailed verification and all CPU/GPU ranking differences; `forensic_stdout.json` contains its captured summary; `sha256_inventory.json` pins 4,649 inspected files at audit time; `input_fingerprints.json` pins all 798 saved observations and their reconstructed patch tensor inputs. Inference uses CPU only and inspected production graph/model/loss code. Split reconstruction, ranking-derived metrics and timing arithmetic are independently implemented. The script does not retrain or benchmark elapsed performance.

## Generalization: tests that pass

1. **All six split files match their frozen declarations and independently reconstructed restrictions of the M1 base split.** The base split reconstructs exactly from sorted app/scenario groups with shuffle seed 42 and floor-15% validation/test allocations: 566/116/116 out of 798 cases. Within each run, train/validation/test IDs are unique and disjoint; source directories are unique across all 798 manifest records. LOFO excludes the held-out scenario from both training and validation and tests only that scenario's original test subset. Cross-application runs use source-app training/validation and target-app original test subsets. Evidence: `scripts/e2r/make_generalization_splits.py:19`, `scripts/e2r/make_generalization_splits.py:24`, `scripts/e2r/run_graph_experiment.py:15`, `scripts/e2r/run_graph_experiment.py:29`.

2. **No direct label input or exact saved-input reuse across these split boundaries was found.** Inspected tensor features use the 25-dimensional observable type/telemetry/deployment/endpoint vector and fixed edge weights; target/scenario enter supervised loss and metric calculation, not this vector or graph planner. Both cross-app directions have zero candidate-service identity overlap. No exact observation-byte hash or ordered patch-tensor signature overlaps between training and test; no exact patch-tensor signature overlap between training and validation. This is an exact-input test, not a graph-isomorphism or near-duplicate/template-independence proof. Evidence: `methods/E2R/data.py:37`, `methods/E2R/data.py:61`, `methods/E2R/data.py:183`, `methods/E2R/graph.py:52`, `methods/E2R/graph.py:150`, `methods/E2R/graph.py:171`, `scripts/e2r/run_graph_experiment.py:68`.

3. **The label path is dataset-provided, not model-generated.** All 798 manifest `true_service` values match their original `ground_truth.json` service labels, and each belongs to its observation candidate set. This check verifies label-file identity, not the causal validity of every underlying injected incident. The offline generalization evaluation is appropriately `real_gt` with controlled-dataset scope. Evidence: `methods/E2R/data.py:183`, `experiments/e2r/runs/generalization_analysis_v1/analysis.json:3`.

4. **Every saved prediction has exact identity coverage and auditable arithmetic.** Verified 2,784 prediction rows = 232 run-specific test entries × 3 seeds × 4 variants; all `(case_id, seed, variant)` keys occur once, match split IDs and manifest metadata, point to the expected checkpoint, contain each candidate once, and have a rank equal to the true service's position in the ranking. Recomputed all 72 seed/variant summaries, plus patch per-seed metrics, means, sample standard deviations and training-time sums in the analysis. All match. No rescaling or self-normalized scores occur. The 232 test entries represent the same 116 unique base-test incidents evaluated once in the LOFO family and once in the cross-app family; they are not 232 independent incidents. Evidence: `scripts/e2r/run_graph_experiment.py:155`, `scripts/e2r/run_graph_experiment.py:166`, `scripts/e2r/analyze_generalization.py:15`.

5. **All 2,960 saved plans exactly regenerate from their current observations. All 18 checkpoint files load, have finite weights, distinct hashes and the declared architecture/configuration.** Config: patch training, 25 input features, GraphSAGE hidden 128, two layers, dropout 0.1, Adam learning rate 0.003, weight decay 1e-5, batch size one, CE plus coefficient-one margin ranking loss, maximum 200 epochs and validation patience 30. Seed list is 42/43/44 for each run. Validation chooses the checkpoint without test metrics. Every recorded stop is 30 epochs after the best validation epoch; actual run lengths are 36–130 epochs. CPU checkpoint validation losses reproduce the recorded best losses within 5.88e-8 absolute. Evidence: `scripts/remote/generalization_worker.py:50`, `scripts/e2r/run_graph_experiment.py:127`, `scripts/e2r/run_graph_experiment.py:140`, `scripts/e2r/run_graph_experiment.py:152`, `${DYRCA_ROOT}/src/dyrca_rca/models/config.py:13`, `${DYRCA_ROOT}/src/dyrca_rca/models/losses.py:56`, `${DYRCA_ROOT}/src/dyrca_rca/models/graphsage.py:45`.

| Run | Train / validation / test | Saved patch AC@1 | Saved patch MRR | CPU patch MRR |
|---|---:|---:|---:|---:|
| LOFO T1 | 425 / 87 / 29 | 0.344828 | 0.509377 | 0.509377 |
| LOFO T2 | 460 / 94 / 22 | 0.484848 | 0.659848 | 0.659848 |
| LOFO T3 | 425 / 87 / 29 | 0.149425 | 0.363055 | 0.362965 |
| LOFO T4 | 388 / 80 / 36 | 0.138889 | 0.346296 | 0.346296 |
| Boutique → TrainTicket | 282 / 59 / 57 | 0.415205 | 0.500561 | 0.500561 |
| TrainTicket → Boutique | 284 / 57 / 59 | 0.305085 | 0.444613 | 0.445178 |

Saved report evidence: `experiments/e2r/runs/generalization_analysis_v1/REPORT.md:5`. The ± values there are sample standard deviations over three training seeds, not confidence intervals over independent dataset splits.

## Generalization warnings and narrow claim

**G1 — Numerical ties prevent exact ranking replay.** CPU PyTorch 2.14.0 reconstructs 499 full rankings differently from the saved GPU PyTorch 2.11.0 results. Of these, 70 change the true-service rank across all variants; six are patch rows. All saved orders are compatible with the CPU scores within a relative tolerance of 1e-5; the largest adjacent score inversion is 1.91e-6 absolute. All patch AC@1/3/5 means remain unchanged; patch MRR differs in T3 and TrainTicket→Boutique as tabulated. This supports numerical near ties as the explanation, but saved logits are absent, so exact GPU score-level verification is unavailable. Sorting uses exact float order then service name, while GPU deterministic algorithms were disabled. Do not claim bitwise or all-rank CPU reproduction. Evidence: `scripts/e2r/run_graph_experiment.py:159`, `scripts/e2r/run_graph_experiment.py:162`, `experiments/e2r/runs/generalization_lofo_T3_v1/results.json:6`, `experiments/e2r/runs/generalization_lofo_T3_v1/results.json:10`; detailed differing rows in `forensic_output.json` under `checkpoint_replay_differences`.

**G2 — Historical input/source identity is not cryptographically bound to the runs.** The run results save a remote absolute manifest path and runtime properties, but no per-run source commit, source hash, observation hash, checkpoint hash or prediction hash receipt. Checkpoints themselves save configuration, not input identity. Audit-time hashes plus successful CPU replay strongly support current local artifact consistency; they do not prove that every remote training byte was the current local byte. No local generalization worker logs/status were present in the inspected local generalization artifacts. Evidence: `scripts/e2r/run_graph_experiment.py:152`, `scripts/e2r/run_graph_experiment.py:175`, `experiments/e2r/runs/generalization_lofo_T3_v1/results.json:12`.

**G3 — Restricted generalization, not broad robustness.** LOFO uses only 22–36 test incidents per held-out scenario from one fixed split. Neither LOFO nor the two app directions supplies new production environments or independent fault families. The held-out condition is a scenario label among existing controlled templates. Training on patch and testing alternative graph variants is a fixed-trained-weights intervention, not separately trained alternative-method baselines. T3/T4 top-1 accuracy is only about 0.15/0.14. Evidence: `experiments/e2r/runs/generalization_analysis_v1/REPORT.md:7`, `experiments/e2r/runs/generalization_analysis_v1/REPORT.md:14`, `scripts/e2r/run_graph_experiment.py:154`.

**G4 — Cross-app transfer also changes telemetry availability.** Independently observed: all 400 Boutique observations have metric summaries; all 398 TrainTicket observations have empty per-service metric summaries. Thus app and available telemetry are confounded, although the feature schema and architecture are shared. A zero standard deviation in TrainTicket→Boutique top-k accuracy is repeated seed agreement on the same 59 test cases, not proof of robustness. Evidence: `experiments/e2r/runs/full_data_v2/manifest.json:19171`, `methods/E2R/graph.py:155`, `experiments/e2r/runs/generalization_analysis_v1/REPORT.md:10`.

**Warranted claim:** “On this controlled recorded dataset, a patch-trained GraphSAGE model trained with three seeds achieved the tabulated localization scores when the designated scenario or application was excluded from training and validation. LOFO evaluates only the scenario portion of one fixed test split; cross-app transfer includes different telemetry availability. Saved-ranking metrics are arithmetically verified, with minor CPU/GPU near-tie MRR differences.” This evidence does not establish open-world generalization, LLM benefit or recovery effectiveness.

## Scaling: tests that pass

1. **Raw measurements exist and arithmetic is correct.** Both CPU and GPU files contain exactly 200 rows: 5 candidate counts (13, 65, 130, 260, 520) × 4 telemetry expansion factors (1, 2, 4, 8) × 10 repetitions. Each condition has repetition IDs 1–10 once, and all five recorded stage durations are finite and positive. Independently recomputed all stage medians/order-statistic p95 values and all comparison pipeline sums, medians, p95 values and CPU/GPU ratios; no mismatch. Evidence: `scripts/e2r/benchmark_scaling.py:69`, `scripts/e2r/benchmark_scaling.py:79`, `scripts/e2r/analyze_scaling.py:28`.

2. **Synthetic fixture dimensions reconstruct exactly.** All 20 CPU/GPU conditions match the locally regenerated node, directed-edge, catalog-operation and serialized-byte counts from base case `case00001`. Largest condition: 520 candidates, 1,040 nodes, 2,080 directed edges, 120 catalog operations and 5,564,303 serialized bytes. At every candidate count, the reconstructed graph tensors are identical across all four telemetry factors: extra keys increase serialized volume and provide no new model information. Evidence: `scripts/e2r/benchmark_scaling.py:13`, `scripts/e2r/benchmark_scaling.py:42`, `methods/E2R/graph.py:157`.

3. **The designated existing M1 seed-42 checkpoint exists and matches its prior checkpoint hash receipt.** Current SHA-256 is `c4895cf287eb11dae65267fdf0e6c53d7053680ec651e3a7066e75baf58889f5`; all three M1 checkpoints match the existing receipt. The benchmark source loads `model_seed42.pt` from its CLI checkpoint directory. This proves the local designated artifact exists, not which checkpoint directory was historically passed to the scaling run, since that CLI argument was not saved. Evidence: `scripts/e2r/benchmark_scaling.py:66`, `experiments/e2r/checkpoint_hashes_gpu_full_4090d.json:2`.

4. **The report itself correctly limits evaluation to simulation-only overhead.** At 520 candidates, saved inference medians are approximately 2.49–2.73 ms CPU and 0.82–0.87 ms GPU, while the sum-of-stages pipeline medians are 188.7–254.7 ms CPU and 332.4–412.7 ms GPU. CPU/GPU pipeline ratios are 0.568–0.702 over all conditions: GPU-labelled runs do not have lower whole-pipeline medians. Reported GPU peak allocated memory is 13,611,008 bytes = 12.98 MiB. This is PyTorch peak allocated memory across the benchmark process, not total VRAM or a per-condition maximum. Evidence: `experiments/e2r/runs/scaling_analysis_v1/REPORT.md:21`, `experiments/e2r/runs/scaling_analysis_v1/REPORT.md:26`, `scripts/e2r/benchmark_scaling.py:99`.

## Scaling warnings and narrow claim

**S1 — Hardware/environment attribution is insufficient.** CPU output identifies only `cpu` and PyTorch 2.14.0+cpu; GPU output identifies only `cuda:0` and PyTorch 2.11.0+cu130. Neither stores CPU model, host, effective thread count, GPU model, driver, measurement timestamp or code/input/checkpoint hashes. The generalization runs separately record RTX 4090 D, but scaling-specific hardware identity cannot be established from these outputs. Imported runner code sets thread environment defaults only when absent; the benchmark does not persist the actual thread configuration. Consequently ratios compare recorded runs under different, incompletely described environments; they are not a controlled causal estimate of GPU acceleration. Evidence: `experiments/e2r/runs/scaling_cpu_v1/results.json:4`, `experiments/e2r/runs/scaling_gpu_v1/results.json:4`, `scripts/e2r/run_graph_experiment.py:8`, `scripts/e2r/benchmark_scaling.py:97`.

**S2 — “p95” is a lower order statistic from only ten adjacent repetitions.** The implemented rule is `floor((n-1)*0.95)`, so n=10 selects the ninth smallest observation. This is a definable lower-quantile convention and its arithmetic matches, but no convention is disclosed in the output report; it is not a stable tail-latency estimate. One discarded warm-up repetition precedes the ten consecutive retained repetitions; there are no independently restarted host trials or uncertainty intervals. Evidence: `scripts/e2r/benchmark_scaling.py:53`, `scripts/e2r/benchmark_scaling.py:72`, `scripts/e2r/analyze_scaling.py:10`.

**S3 — Pipeline timer is the sum of specific measured segments, with repeated construction work.** It excludes fixture replication, disk input, telemetry collection, model loading, LLM generation and recovery. `execution_seconds` calls `graphs_for`, which rebuilds the catalog and reference/runtime graphs in addition to executing the patch, although an earlier catalog stage was already timed. A synchronization is also placed outside the immediately preceding tensor timer. The reported number is valid for these five measured code segments; it is not a single contiguous end-to-end service latency or a minimum optimized pipeline. Evidence: `scripts/e2r/benchmark_scaling.py:65`, `scripts/e2r/benchmark_scaling.py:71`, `scripts/e2r/benchmark_scaling.py:73`, `methods/E2R/graph.py:193`, `scripts/e2r/analyze_scaling.py:32`.

**S4 — Replication is not independent topology/information scaling.** The graphs are disconnected renamed copies of one recorded graph; the base observation is itself a single Boutique T1 case. Added telemetry keys are ignored by the fixed feature extractor. No new candidate diversity, hard dependencies, topology families, accuracy outcomes or recovery outcomes are evaluated. Both source code and existing report disclose this honestly; keep that limitation attached to every reused performance number. Evidence: `scripts/e2r/benchmark_scaling.py:19`, `scripts/e2r/benchmark_scaling.py:46`, `scripts/e2r/benchmark_scaling.py:100`, `experiments/e2r/runs/scaling_analysis_v1/REPORT.md:28`.

**Warranted claim:** “On 20 synthetic fixtures formed by replicating one recorded graph, the saved CPU/GPU environment runs show the reported implementation segment timings over ten measurements each. Telemetry expansion increases serialized bytes without changing model inputs; the largest fixture contains 520 candidates. These are observed overhead measurements, not production scalability, independent-information scaling, recovery throughput or a controlled hardware speedup claim.”

## Remaining limits of this audit

- Same-family provisional review, not the cross-family separation requested by the strict shared integrity protocol.
- No historical remote code checkout or run-time hash receipt; audit hashes pin current artifacts only.
- No retraining, GPU replay, timing rerun or verification of causal injection correctness. Checkpoint validation-loss agreement and score-order tolerance provide consistency evidence, not proof of the historical training process.
- Exact byte/tensor overlap testing does not establish independence of controlled templates or exclude graph-isomorphic/near-duplicate observations.
- No requested production artifact was changed.


## Action items and claim impact

1. Carry the cache-inclusive accounting correction and unknown monetary cost into any report; retain failed-pilot expenditure separately.
2. Retain per-shard hash provenance and CPU/GPU near-tie reproducibility limits. Do not retroactively describe pilot/scaling/generalization execution bytes as historically attested by audit-time hashes.
3. Present generalization as the exact fixed-base held-out protocol, and scaling as a one-fixture synthetic overhead microbenchmark with its timing/hardware limitations.
4. Keep GT-conditioned construction, both proxy orientations, source-local clocks and the simulation-only label adjacent to multi-root numbers.
5. Attach this bounded verdict to the now-corrected tracker; do not turn it into live execution approval.

**One warranted narrow statement:** On the fixed 116-incident Qwen pilot-plus-formal offline view, with failures retained, LLM-selected GraphPatch + frozen GNN without fallback achieved AC@1 **64.22%**, below catalog-informed direct ranking **79.31%** and rule-patch + the same GNN **82.18%**; the untouched 108-incident formal subset shows the same direction. The current experiment supports this bounded negative component result, not general LLM/GraphPatch superiority or production recovery.

Claim map: bounded Qwen negative comparison **supported**; LLM patch/GNN necessity or superiority **unsupported**; broad unseen-fault/cross-app robustness **unsupported**; exact six constrained transfer results **supported with qualifiers**; synthetic scaling overhead **supported with qualifiers**; blind/live concurrent-fault or production recovery **unsupported**; GT-conditioned synthetic two-root sensitivity **supported with qualifiers**.

Forensic evidence resides in `.aris/traces/experiment-audit/20260926_b2_b3/`: reviewer-authored verifier scripts, detailed JSON outputs, CPU ranking differences, all audited input hashes, and full reviewer request/response metadata. These checks are a bounded integrity assessment, not a guarantee against every possible provenance failure.
