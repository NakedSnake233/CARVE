# E2R 完整实验 Tracker

| Run ID | Milestone | Purpose | System / Variant | Split | Metrics | Priority | Status | Notes |
|---|---|---|---|---|---|---|---|---|
| R000 | M0 | review coverage/integrity | comment→evidence matrix | all artifacts | coverage/gates | MUST | DONE_REVIEWED | RESEARCH_REVIEW_20260926_E2R_DESIGN.md |
| R001 | M0 | dataset/workload inventory | 798 current cases | all | counts/volumes/known-unknown | MUST | DONE | review_inventory_v2; historical760 separate |
| R002 | M0 | table validity | original RCA table | historical | AC monotonicity/MRR bounds | MUST | DONE_WARN | mABC TT 3 rows unresolved |
| R010 | M1a | runner sanity | train reference/runtime/node-only | 40 cases, seed42,2ep | finite/coverage | MUST | DONE | all three GPU smoke runs passed |
| R011 | M1b | matched training | train=reference | fixed566/116/116,3seed | AC/MRR/time | MUST | DONE | gpu_train_reference_v1 |
| R012 | M1b | matched training | train=runtime | fixed566/116/116,3seed | AC/MRR/time | MUST | DONE | gpu_train_runtime_v1 |
| R013 | M1b | matched training | train=patch | fixed566/116/116,3seed | AC/MRR/time | MUST | DONE_SCOPED_WARN | existing M1 |
| R014 | M1c | structural control | matched node-only + operation deletion | test116 | paired AC/MRR | MUST | DONE_AUDITED_WARN | m1_matrix_v3 + operation_ablation_v1; integrity PASS/scope WARN |
| R015 | M1c | modality feature deletion | full/no-log/no-trace/no-metric/topology | Boutique test59 | paired AC/MRR | MUST | DONE_SCOPED | mixed-source call_edges retained except topology-only |
| R020 | M2a | Qwen bounded pilot | raw/catalog/proposal | 8 strata×2gen=48 | schema/usage/time | MUST | DONE_NEGATIVE | v2: GraphPatch+GNN below catalog-direct/rule |
| R021 | M2a | DeepSeek compatibility | raw/catalog/proposal | 1 test case=3 calls | schema/model/usage | MUST | DONE_INCOMPATIBLE | 3/3 HTTP 400; no retry; usage unknown |
| R022 | M2a | GPT Luna compatibility | raw/catalog/proposal | 1 test case=3 calls | schema/model/usage | MUST | DONE_COMPATIBLE | 3/3 transport and strict schema PASS |
| R023 | M2b | Qwen formal | three LLM routes | 8 pilot+remaining108; 2gen | unconditional metrics/cost | MUST | DONE_NEGATIVE_AUDITED_WARN | combined116: LLM patch AC1 .6422 < catalog .7931 < rule .8218 |
| R024 | M2b | DeepSeek full | three LLM routes | remaining108×2gen | unconditional metrics/cost | NICE | BLOCKED_EXPLICIT_BUDGET | only if cross-model claim retained |
| R025 | M2b | GPT Luna full | three LLM routes | remaining108×2gen | unconditional metrics/cost | NICE | BLOCKED_EXPLICIT_BUDGET | only if cross-model claim retained |
| R026 | M2c | frozen scorer | LLM patch ± fallback | all accepted/failed rows | AC/MRR/coverage/time | MUST | DONE_AUDITED_WARN | formal hash-pinned; pilot behavioral replay; 3,132 combined rows |
| R030 | M3 | LOFO | hold out T1/T2/T3/T4 | 4 folds×3seed | AC/MRR | MUST | DONE_AUDITED_WARN | AC1 .1389–.4848; controlled held-out base-test subsets |
| R031 | M3 | cross-app | Boutique↔TrainTicket | 2 directions×3seed | AC/MRR | MUST | DONE_AUDITED_WARN | AC1 .4152/.3051 |
| R032 | M3 | multi-root proxy | 57 disjoint pairs×2 orientations×3seed | synthetic pair | Recall@k/MAP | MUST | DONE_AUDITED_PASS_SIM | Recall2 .7895–.8246; GT-conditioned composition disclosed |
| R033 | M3 | scaling | 13/65/130/260/520 candidates × volume | synthetic expansion | latency/memory | MUST | DONE_AUDITED_WARN | CPU/GPU recorded environments; peak12.98MiB; pipeline CPU faster |
| R040 | M4a | safety binding | namespace/target/YAML/precondition/oracle | unit+mock | reject/accept | MUST | DONE_LOCAL_PASS | fresh provisional review PASS; 126 full tests pass |
| R041 | M4a | isolated deploy | Boutique USERS10 | isolated namespace | readiness/workload | MUST | DONE_BASELINE_V2_PASS | stabilized12 pods; v2 900 requests,0 error,p95 18.26ms; UID/restarts stable |
| R042 | M4b | no-action baseline | mapped faults | ≥3/fault | triple oracle | MUST | BLOCKED_BY_LIVE_SANITY | initial degradation required |
| R043 | M4b | typed vs direct | mapped faults | ≥3/fault/arm | verified recovery/failures | MUST | SANITY_V3_PASS_REVIEW_PENDING | v3 one-case oracle PASS; v1/v2 preserved; expansion not authorized |
| R044 | M4b | diagnostic factorial | service×category | meaningful service-targeted faults | recovery/rollback | MUST | BLOCKED_BY_R043 | exclude category-only Chaos cleanup from attribution claim |
| R045 | M4b | concurrent faults | selected pairs | isolated benchmark | set RCA/recovery | NICE | BLOCKED_BY_R043 | no production claim |
| R050 | M5 | artifact/audit | anonymous package | all new runs | reproducibility/integrity | MUST | PARTIAL | B2/B3 audit WARN/no BLOCK; M4 audit pending live results |
