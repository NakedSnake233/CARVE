# M1 revised experiment audit

**Overall: WARN. Artifact integrity: PASS. Actual integrity blockers: none found.**

Fresh reviewer `/root/m1_revision_audit`, aided by a separate read-only ablation subauditor. `review_independence: same-family`; `acceptance_status: provisional`. This is a bounded integrity review, not an unconditional paper-acceptance certificate.

The final target is `m1_matrix_v3` plus the Boutique feature-deletion and all-test operation-deletion artifacts. `m1_matrix_v2` is superseded because its condition-bootstrap point estimand was not named; its numbers were recomputed and were not fabricated. Version 3 changes the estimand disclosure and adds independently verified condition-macro point estimates without changing matrix cells, subgroups, raw predictions or earlier contrasts. Inventory v2 corrects GT-use disclosure without changing counts or case rows. Legacy paper tables, M2, generalization runs and recovery are outside this verdict; merely reading the supplied table inventory does not audit those historical numbers.

## A. Ground-truth provenance — PASS

Labels come from each source incident's `ground_truth.json`, not from a model: `methods/E2R/data.py:183–193`. For example `${DYRCA_ROOT}/data/experiments/t1-cartservice-stress2w60p-run198/ground_truth.json:3–7` contains the recorded service, category and timing. All **798** source GT labels and metadata identities match the manifest. All 798 source directories are unique.

All **798 saved observation snapshots were rebuilt exactly** from raw pre/during topology and accepted telemetry. Topology uses an explicit field allowlist (`methods/E2R/data.py:37–50`); metric windows are bounded at `data.py:85–117`, completed traces at `data.py:118–150`, and logs at `data.py:151–180`. Candidate services come from observed services/deployments (`data.py:69–77`). The graph/feature path never consumes GT, original directory names or injected categories (`methods/E2R/graph.py:31–114`, `150–190`). No accepted observation exceeded cutoff.

Dataset preparation also uses GT to check candidate presence and fault-end validity (`scripts/e2r/prepare_data.py:40–44`). This is dataset eligibility, not a model input; the final disclosure now says so (`experiments/e2r/runs/review_inventory_v2/inventory.json:272–277`). **Zero cases were excluded** (`full_data_v2/manifest.json:3–6`). The classification remains `real_gt`: supervised offline ranking on recorded injected-fault testbed observations, not production incidents or human causal adjudication.

## B. Score normalization — PASS

Rank is derived by sorting candidate logits and locating the dataset true service (`scripts/e2r/run_graph_experiment.py:158–165`). AC@1/3/5 use rank indicators, and MRR uses reciprocal rank with the incident count as denominator (`run_graph_experiment.py:166–172`; `analyze_m1_matrix.py:8`). Both deletion scripts use the same definitions (`run_modality_ablation.py:61–67`, `run_operation_ablation.py:61–67`). There is no normalization by the model's maximum, mean or other prediction statistic.

The input `log1p` transform (`methods/E2R/graph.py:155–167`) and GraphSAGE weighted-neighbor mean (`${DYRCA_ROOT}/src/dyrca_rca/models/graphsage.py:25–42`) are feature/model operations, not score inflation.

## C. Existence, counts, checkpoints and reported statistics — PASS

Independently regenerated the stratified split: **566 train / 116 validation / 116 test**, disjoint IDs and source directories; all four runs use exactly the same ID order. Split code is at `scripts/e2r/run_graph_experiment.py:15–26`; final statuses are at `refine-logs/EXPERIMENT_TRACKER.md:9–13`.

| Artifact | Verified prediction rows | Exact scope |
|---|---:|---|
| reference-trained | 1,392 | 116 cases × 3 seeds × 4 eval variants |
| runtime-trained | 1,392 | same |
| reused patch-trained | 1,044 | 116 × 3 × 3; no node-only eval |
| node-only-trained | 1,392 | 116 × 3 × 4 |
| Boutique feature deletion | 885 | 59 cases × 3 seeds × 5 modes |
| operation deletion | 1,392 | 116 cases × 3 seeds × 4 modes |

No missing, extra or duplicate product keys; all saved rankings are permutations of the observed candidate list, labels match GT, and every stored true-service rank matches its ranking. The matrix has **15 cells**, including the complete planned 3×3 core, not 16; the unavailable `patch→node_only` cell is visibly `--` (`m1_matrix_v3/REPORT.md:7`).

All **12 checkpoints** load on CPU and identify the correct seed and training variant. Their common settings are 25 input features, hidden width 128, two layers, dropout 0.1, Adam LR 0.003, weight decay 1e-5, batch size 1, requested 200 epochs, patience 30 and pairwise-loss coefficient 1.0. Source: `run_graph_experiment.py:123–153`; upstream `models/config.py:13–36`, `models/losses.py:56–70`. All histories are finite and sequential; the saved checkpoint's CPU validation loss reproduces its reported minimum within **9.0e-8**. Requested budget is common; realized early-stop epochs differ:

| Train | Seed 42 | Seed 43 | Seed 44 |
|---|---:|---:|---:|
| reference | 126 | 109 | 66 |
| runtime | 151 | 79 | 93 |
| patch | 43 | 47 | 59 |
| node-only | 136 | 182 | 124 |

Every AC/MRR summary, all matrix seed values/means/sample SDs, application/scenario/effective-change subgroup values, paired deltas, **5,000-draw incident bootstrap**, **5,000-draw condition-macro bootstrap**, **10,000-draw sign-flip p-values**, and the actual two-primary Holm values reproduce independently. The reference contrast is not included in that two-primary correction. Actual reported Holm values equal proper step-down values; the generic implementation lacks a cumulative-maximum safeguard for other future p-value orders (`analyze_m1_matrix.py:69–71`), which does not change this run.

| Matched train/eval | AC@1 mean ± sample SD | AC@3 | AC@5 | MRR |
|---|---:|---:|---:|---:|
| reference | 0.7184 ± 0.0179 | 0.8420 | 0.8937 | 0.7885 |
| runtime | 0.7730 ± 0.0326 | 0.8736 | 0.9052 | 0.8281 |
| patch | 0.8218 ± 0.0132 | 0.9339 | 0.9483 | 0.8805 |
| node-only | 0.7241 ± 0.0376 | 0.8333 | 0.8937 | 0.7911 |

Final contrast evidence: `m1_matrix_v3/REPORT.md:12–14`; resampling code: `analyze_m1_matrix.py:54–71`.

| Patch minus | Incident delta | Incident 95% interval | Condition-macro delta | Condition 95% interval | Sign-flip p | Holm p (two primary) |
|---|---:|---|---:|---|---:|---:|
| runtime | +0.048851 | [0.002874, 0.097701] | +0.052721 | [-0.012472, 0.127551] | 0.053395 | 0.053395 |
| reference | +0.103448 | [0.045977, 0.166667] | +0.123696 | [0.036848, 0.219391] | 0.001500 | not included |
| node-only | +0.097701 | [0.048851, 0.149425] | +0.127551 | [0.047619, 0.215434] | 0.000200 | 0.000400 |

Incident-weighted and equally weighted condition-macro effects are **different estimands**, now correctly labeled. Condition groups have 1–6 test incidents. Seeds are averaged within incident before these procedures; 348 seed-case rows must never be described as 348 independent incidents.

## D. Runtime path and deletion semantics — PASS; CPU replay qualification

Training/validation actually select the requested graph arm (`run_graph_experiment.py:126–144`), load the best validation checkpoint (`:146–153`) and score every requested variant with the same frozen object per training arm/seed (`:154–165`). The forward function passes actual edges/weights (`:41–42`). Remote commands and completed stages are recorded in `experiments/e2r/remote/reviewer_full/worker.log:15–25`; all 102 printed full-run epoch checkpoints and each final stdout summary match the saved histories/results. No claimed metric was found only in dead code. Source history cannot be cryptographically attested retrospectively; current source plus saved checkpoint behavior is directly verified.

**Node-only is implemented as claimed:** it keeps patch features, node IDs and candidate indices and supplies empty edge tensors (`run_graph_experiment.py:109–112`). The upstream layer explicitly makes zero neighborhood inputs on an empty edge set, while retaining learned neighbor biases (`models/graphsage.py:25–42`). This is a per-node self-network with no cross-node path; extra noncandidate nodes cannot affect candidate scores. Node-only-trained neighbor-weight magnitudes are approximately 1e-39 and all four evaluation variants give the same stored rankings/metrics, consistent with training on zero neighbor inputs. It is not a separate experiment isolating every kind of added node from existing-node relations.

All ablation aggregates and full-arm rankings match their source patch-trained predictions. All 464 case×mode operation selections execute and respect prerequisites. The recursive closure is valid (`run_operation_ablation.py:20–28`; `methods/E2R/graph.py:100–137`), but this realized dataset requires **zero additional recursive removals** after the selected group is removed.

| Operation arm | Selected operations over 116 cases | Changed cases | AC@1 | MRR |
|---|---:|---:|---:|---:|
| full | 412 | 0 | 0.8218 | 0.8805 |
| drop identity | 390 | 22 | 0.7443 | 0.8325 |
| drop blackhole/mismatch | 386 | 25 | 0.7615 | 0.8188 |
| drop version split | 376 | 36 | 0.5546 | 0.6780 |

`drop_identity` removes 22 `ALIGN_NODE` operations while retaining `ADD_NODE`/`ADD_EDGE`; blackhole/mismatch removes 9 blackholes and 17 mismatch edges across 25 cases; version removes 36 split operations. Evidence: `run_operation_ablation.py:12–17` and `operation_ablation_v1/results.json:10–48`. Thus “identity deletion” must mean alignment-operation deletion, not deletion of all identity information. Failed graph materialization would abort completion rather than silently shrink the denominator (`run_operation_ablation.py:51–60`); actual coverage is complete.

| Boutique feature arm | AC@1 | MRR |
|---|---:|---:|
| full | 0.8927 | 0.9262 |
| no log-error features | 0.8983 | 0.9281 |
| no trace features | 0.8079 | 0.8658 |
| no metric features | 0.5480 | 0.6690 |
| topology only | 0.6045 | 0.7367 |

The feature filter is at `run_modality_ablation.py:15–28`; the matching saved numbers are at `modality_ablation_boutique_v1/results.json:11–59`. Both trace-feature and metric-feature deletions **retain mixed-provenance call edges**; only topology-only deletes those edges. The added `REPORT.md:3–5` discloses this. These are frozen-scorer feature deletions, not fully source-separated modality removal or retrained modality comparison. Log removal slightly improves this observed score; a claim that every modality is necessary is contradicted.

**Numerical replay limit:** recorded runs use CUDA PyTorch 2.11.0+cu130 with deterministic algorithms disabled (`gpu_train_reference_v1/results.json:4–10`); the review uses CPU PyTorch 2.14.0+cpu. All 7,497 predictions were replayed. Every reported AC@1/3/5 is preserved, but exact rankings are not bitwise portable:

| Replay | Full-ranking mismatches | True-service-rank mismatches | Consequence |
|---|---:|---:|---|
| reference-trained, 1,392 rows | 136 | 22 | AC metrics identical |
| runtime-trained, 1,392 rows | 184 | 31 | AC metrics identical |
| patch-trained, 1,044 rows | 408 | 66 | AC metrics identical |
| node-only-trained, 1,392 rows | 252 | 0 | all rank metrics identical |
| modality, 885 rows | 105 | 0 | all rank metrics identical |
| operation, 1,392 rows | 558 | 63 | AC metrics identical |

Matrix true-rank changes are all at ranks ≥12, and the largest absolute per-cell mean MRR change is **0.00010176**; operation per-arm mean MRR changes are 0.00006789–0.00009104. Saved-order adjacent score inversions under CPU are at most **9.54e-7** (operation ≤2.39e-7), consistent with floating-point near ties. Original logits were not saved (`run_graph_experiment.py:159–165`), so this explanation is strongly supported but not a proof of the original CUDA margins. Do not claim exact cross-backend ranking reproduction. Timing sums present in predictions reproduce; materialization wall times and end-to-end latency cannot be independently recovered from these rows.

## E. Scope and claim impact — WARN

- One split, two fault-injection applications, four scenario families, three training seeds. All **49 test app/scenario/service/category groups occur among 70 training groups**. Incident resampling and sign flips do not remove recurring-template dependence, shared-model uncertainty or the need for actual held-out-condition tests. Neither bootstrap establishes unseen-condition performance.
- One source-disjoint train/test pair has an **identical effective patch input but different labels**: train `case00526` (ts-station-service) and test `case00450` (ts-basic-service). This is observable-input aliasing, not an identical labeled training example. It limits identifiability, particularly because TrainTicket has no consumed telemetry features (`input_audit_v1/summary.json:86–93`).
- Patch–runtime per-seed AC@1 deltas are **0, +0.0776, +0.0690**. The sign-flip/Holm p=0.0534 and condition-macro interval crossing zero do not support a 0.05-significant runtime-superiority claim, despite a positive incident percentile interval. The methods test different constructions/assumptions; report both without choosing only the favorable one.
- Effects vary by application and scenario: Boutique patch/runtime 0.8927/0.8249 versus TrainTicket 0.7485/0.7193; Boutique T3 patch is below runtime 0.5778/0.6889 (`m1_matrix_v3/REPORT.md:21–31`). Thirty-nine test inputs have equal effective patch/runtime graphs; 77 differ (`REPORT.md:23–25`). Separately trained models can differ even on equal input graphs.
- Operation and feature deletions reuse full-patch-trained weights, so their drops demonstrate reliance/sensitivity under distribution shift, not the standalone achievable value of each operation after retraining. They do not by themselves complete every proposed existing-node relation control (`EXPERIMENT_PLAN_20260926_184526.md:29`, `:65`).
- No LLM or recovery was used in these M1 outputs (`gpu_full_4090d/results.json:20–21`). No production, open-world, real multi-root, universal modality-necessity or live-repair claim is warranted.

## F. Evaluation classification — real_gt

All three in-scope evaluations rank services against dataset-provided injection labels. Input ablations are controlled transformations of real observations; they do not synthesize reference labels. This classification says where the labels come from, not that the injected service was independently causally adjudicated in production.

## Warranted narrow statement

On one fixed 566/116/116 split of 798 recorded fault-injection incidents from Online Boutique and TrainTicket, three matched-training GraphSAGE fits gave patch AC@1 0.8218, reference 0.7184, runtime 0.7730, and no-message-passing control 0.7241. The patch–runtime gain is descriptive, not significant under the reported paired sign-flip/Holm test. Frozen-patch feature and operation deletions show scorer sensitivity within these recorded recurring conditions; they do not establish unseen-condition, LLM, recovery, or production benefits.

## Required reporting boundaries and evidence trace

Use v3 for the final analysis and inventory v2 for GT-use disclosure; preserve v2/v1 as superseded artifacts. Keep the missing matrix cell explicit, label deletion experiments as frozen-scorer sensitivity, retain both macro and incident uncertainty statements, and disclose the CPU/CUDA near-tie limitation. These are conditions on claim wording, not requests to modify verified raw result files. The current tracker completion statuses are correct; its v1/v2 source pointers refer to older reporting copies.

Reviewer-authored executable checks, detailed mismatch rows, selection IDs, raw-data verification and SHA-256 input inventory are in `.aris/traces/experiment-audit/20260926_m1_revision/`. In particular: `verification.json`, `runtime_details.json`, `ablation_verification.json`, `v3_reporting_verification.json`, and `audited_input_hashes.json`. Only local reads, CPU inference and reviewer trace/report writes were used; no training, GPU, API, network, SSH, cluster action or implementation/result edit was performed by the reviewers.
