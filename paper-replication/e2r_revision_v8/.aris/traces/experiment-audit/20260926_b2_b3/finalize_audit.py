"""Package reviewer-authored conclusions and forensic evidence; no result edits."""
from pathlib import Path
import json,hashlib,datetime
ROOT=Path('${PROJECT_ROOT}');TRACE=Path(__file__).resolve().parent;OUT=ROOT/'experiments/e2r/runs/b2_b3_audit_v1';OUT.mkdir(parents=True,exist_ok=True)
v=json.loads((TRACE/'qwen_proxy_verification.json').read_text());supp=json.loads((TRACE/'qwen_supplement_verification.json').read_text())
now=datetime.datetime.now(datetime.timezone.utc).isoformat()
md='''# B2/B3 independent experiment audit

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
'''
for arm,r in v['combined']['all_metrics'].items():md+='| '+arm+' | '+' | '.join(f"{r[m]['mean']:.6f}" for m in ('AC@1','AC@3','AC@5','MRR'))+' |\n'
md+='''
Combined no-fallback LLM patch + GNN minus catalog-direct is **−15.0862 percentage points**, incident bootstrap 95% interval **[−23.5632, −6.8930]**; versus rule-patch it is **−17.9598 points**, interval **[−24.5726, −12.0690]**. Formal-only counterparts are **−12.9630** and **−16.0494 points**. Thus the current evidence contradicts a claim that the LLM patch + GNN component is necessary for better ranking in this setting. Raw-direct's lower unconditional score also includes greater schema/abstention failure; it is not a pure conditional ranking-quality comparison.

CPU PyTorch 2.14.0+cpu replay evaluated all **1,740 distinct GNN scoring computations**, verifying accepted duplicated no-fallback/fallback rankings are identical, plus every direct row. Saved Qwen scores used CUDA PyTorch 2.11.0+cu130. **681** full ranking differences (formal 627, pilot 54) are near-tie reorderings; **102** true-rank changes occur only at ranks ≥12 and preserve every AC@1/3/5. Maximum saved-order adjacent score inversion under CPU is **3.58e−7**. Saved MRR arithmetic exactly reproduces from saved rankings; cross-device full rankings/MRR are not bitwise guaranteed. Detailed differences remain in `cpu_replay_differences.json`.

### Request expenditure, caching and latency

| Shard | Calls | Uncached input field | Output | Cache-read input | Cache-inclusive input+output volume |
|---|---:|---:|---:|---:|---:|
'''
for key,label in [('failed_pilot_v1','Failed pilot v1'),('pilot_v2','Successful pilot v2'),('formal','Formal'),('combined','Analyzed combined')]:
    r=supp[key];u=r['all_numeric_usage_fields'];md+=f"| {label} | {r['attempts']} | {u['input_tokens']:,} | {u['output_tokens']:,} | {u['cache_read_input_tokens']:,} | {r['cache_inclusive_input_and_output_tokens']:,} |\n"
md+='''
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

'''
sub=TRACE/'generalization_scaling/REVIEW.md'
if sub.exists():md+=sub.read_text()+'\n'
else:raise SystemExit('Missing completed generalization/scaling subreport')
md+='''
## Action items and claim impact

1. Carry the cache-inclusive accounting correction and unknown monetary cost into any report; retain failed-pilot expenditure separately.
2. Retain per-shard hash provenance and CPU/GPU near-tie reproducibility limits. Do not retroactively describe pilot/scaling/generalization execution bytes as historically attested by audit-time hashes.
3. Present generalization as the exact fixed-base held-out protocol, and scaling as a one-fixture synthetic overhead microbenchmark with its timing/hardware limitations.
4. Keep GT-conditioned construction, both proxy orientations, source-local clocks and the simulation-only label adjacent to multi-root numbers.
5. Attach this bounded verdict to the now-corrected tracker; do not turn it into live execution approval.

**One warranted narrow statement:** On the fixed 116-incident Qwen pilot-plus-formal offline view, with failures retained, LLM-selected GraphPatch + frozen GNN without fallback achieved AC@1 **64.22%**, below catalog-informed direct ranking **79.31%** and rule-patch + the same GNN **82.18%**; the untouched 108-incident formal subset shows the same direction. The current experiment supports this bounded negative component result, not general LLM/GraphPatch superiority or production recovery.

Claim map: bounded Qwen negative comparison **supported**; LLM patch/GNN necessity or superiority **unsupported**; broad unseen-fault/cross-app robustness **unsupported**; exact six constrained transfer results **supported with qualifiers**; synthetic scaling overhead **supported with qualifiers**; blind/live concurrent-fault or production recovery **unsupported**; GT-conditioned synthetic two-root sensitivity **supported with qualifiers**.

Forensic evidence resides in `.aris/traces/experiment-audit/20260926_b2_b3/`: reviewer-authored verifier scripts, detailed JSON outputs, CPU ranking differences, all audited input hashes, and full reviewer request/response metadata. These checks are a bounded integrity assessment, not a guarantee against every possible provenance failure.
'''
(OUT/'EXPERIMENT_AUDIT.md').write_text(md)
hashes={}
for name in ['qwen_proxy_hashes.json','qwen_supplement_hashes.json','generalization_scaling/sha256_inventory.json','accounting_v2_hashes.json','expenditure_v3_hashes.json']:
    h=json.loads((TRACE/name).read_text())
    if isinstance(h,dict):hashes.update(h)
hashes={k:('sha256:'+v if isinstance(v,str) and not v.startswith('sha256:') else v) for k,v in hashes.items()}
tracker=ROOT/'refine-logs/EXPERIMENT_TRACKER.md'
hashes[str(tracker)]='sha256:'+hashlib.sha256(tracker.read_bytes()).hexdigest()
(TRACE/'audited_input_hashes.json').write_text(json.dumps(hashes,indent=2)+'\n')
data={'audit_skill':'experiment-audit','verdict':'WARN','reason_code':'bounded_scope_and_provenance_limitations','summary':'All inspected numerical identities and arithmetic reproduce. Final v2/v3 accounting corrects cached-token omission and unknown-usage treatment. Scope, historical checkpoint attestation and cross-device near ties require qualifiers. No actual integrity blocker.','audited_input_hashes':hashes,'trace_path':str(TRACE.relative_to(ROOT)),'agent_id':'/root/b2_b3_result_audit','verdict_id':'20260926_b2_b3_result_audit','executor_model':'codex-gpt-6-astra','executor_family':'openai','reviewer_model':'gpt-6-astra','reviewer_family':'openai','reviewer_reasoning':'ultra','review_independence':'same-family','acceptance_status':'provisional','generated_at':now,'date':'2026-09-26','auditor':'gpt-6-astra-ultra','overall_verdict':'warn','integrity_status':'warn','actual_integrity_blockers':[],'checks':{'gt_provenance':{'status':'pass','details':'116 Qwen sources independently rebuilt and labels separated; 798 generalization labels checked; proxy construction disclosed.'},'score_normalization':{'status':'pass','details':'All saved metrics/CIs recompute; no prediction-statistic normalization.'},'result_existence':{'status':'warn','details':'All result files/identities present; tracker corrected and final v2/v3 cached-token accounting verified. Historical pilot/generalization/scaling byte attestations remain unavailable.'},'dead_code':{'status':'pass','details':'Every reported metric path has matching saved and recomputed outputs.'},'scope':{'status':'warn','details':'Single LLM, fixed four-scenario/two-application testbed and synthetic scaling/compositions only.'},'eval_type':{'qwen':'real_gt','generalization':'real_gt','scaling':'simulation_only','multi_root':'simulation_only'}},'components':{'qwen':{'verdict':'WARN','artifact_integrity':'PASS'},'generalization':{'verdict':'WARN','artifact_integrity':'PASS'},'scaling':{'verdict':'WARN','artifact_integrity':'PASS'},'multi_root':{'verdict':'PASS','scope':'GT-conditioned simulation-only'}},'claims':[{'id':'bounded_qwen_negative_component_result','impact':'supported'},{'id':'llm_patch_gnn_superiority','impact':'unsupported'},{'id':'constrained_generalization_results','impact':'needs_qualifier'},{'id':'synthetic_scaling_overhead','impact':'needs_qualifier'},{'id':'synthetic_multi_root_sensitivity','impact':'needs_qualifier'},{'id':'production_or_live_concurrent_fault_recovery','impact':'unsupported'}],'warranted_statement':'On the fixed 116-incident Qwen pilot-plus-formal offline view, with failures retained, no-fallback LLM patch+GNN AC@1 is 64.22%, below catalog-direct 79.31% and rule-patch+GNN 82.18%; the untouched 108-case formal subset agrees in direction.'}
(OUT/'EXPERIMENT_AUDIT.json').write_text(json.dumps(data,indent=2)+'\n')
(TRACE/'001-b2-b3.response.md').write_text(md)
print(json.dumps({'output':str(OUT),'verdict':'WARN','hashes':len(hashes),'report_lines':len(md.splitlines())}))
