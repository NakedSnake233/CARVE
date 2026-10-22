# Research findings — 2026-09-25

## Execution arrangement

Full migration was cancelled. The authoritative project, raw data, manuscript and analysis remain local. A 3.7MB frozen worker input bundle was sent to ${GPU_SSH}; no complete migration archive, private LLM configuration or raw dataset was uploaded. GPU work began only after the user's idle policy was satisfied.

## M1 completed

798 observed incidents; fixed 566/116/116 train/validation/test split; three trained checkpoints (42/43/44) each evaluated unchanged across reference/runtime/rule-patch graphs. Target: RTX4090D, PyTorch2.11.0+cu130. Early stopping finished at 43/47/59 epochs. Checkpoints, 1,044 prediction rows and logs are local under experiments/e2r/runs/gpu_full_4090d/.

- all: AC@1 reference=0.3649, runtime=0.4167, patch=0.8218.
- boutique: AC@1 reference=0.3842, runtime=0.4746, patch=0.8927.
- trainticket: AC@1 reference=0.3450, runtime=0.3567, patch=0.7485.

These are observations from a patch-trained scorer under graph substitution. They are not independently optimized baseline comparisons. Current TrainTicket snapshots have no diagnostic-period telemetry features. This does not establish fresh LLM planning quality, recovery efficacy, new-app/fault generalization or the validity of historical paper results.

Source report: experiments/e2r/runs/m1_analysis_v1/REPORT.md. Independent same-family/provisional integrity audit completed: artifact integrity PASS, overall WARN for scope and numerical replay limits. All AC@1/3/5 replay outcomes agree; near-tie CPU/GPU ordering changes each MRR by less than0.000102. Exact numerical inputs recur across some distinct incident IDs (2reference/2runtime/1patch test cases, all TrainTicket), so this is not an unseen-template split. See experiments/e2r/runs/m1_analysis_v1/EXPERIMENT_AUDIT.md; its scope limits must accompany any paper use. No manuscript numbers were replaced.

## Next planned work

M2: bounded fresh LLM-patch versus direct-ranking pilot, with explicit rule/no-fallback arms and request/cost records (initial budget at most40requests). Reuse the fixed M1 splits/checkpoints. No external-baseline implementation is included. M3/M4 require an isolated local live environment and independently checked restoration.

## M2 infrastructure preflight

The M2 code path passed fresh-agent review after enforcing strict label-free prompts, one common proposal acceptance gate, no redirects/retries, an exclusive request-budget lock, exact row coverage and explicit fallback accounting. Live sanity produced no model output: Gemini recorded two local `URLError` outcomes and two gateway HTTP404 outcomes; GPT and Claude each recorded two HTTP500 outcomes. All eight attempts have unknown usage, remain preserved and count against the original 40-attempt ceiling. The remaining 32 attempts and GPU scoring were not launched. This is an infrastructure block, not negative evidence about E2R or direct ranking. Evidence: `experiments/e2r/runs/m2_gateway_preflight_v1/` and `refine-logs/M2_CODE_REVIEW.md`.

## OpenCode Go reconfiguration — 2026-09-26

The user supplied a new OpenCode Go credential. Official OpenCode Go documentation identifies `qwen3.8-flash` as an Anthropic Messages model at `/zen/go/v1/messages` and `deepseek-v4.1-flash` as OpenAI-compatible at `/zen/go/v1/chat/completions`; the private configs default to Qwen. The shared transport sends a project-specific User-Agent and stable `x-opencode-session`, with protocol-specific authentication. Local simulated envelope/config tests passed; no OpenCode Go request or experiment was started. The eight earlier gateway failures and their unknown usage remain unchanged.

The same credential now also configures `gpt-5.6-luna` through the official OpenAI Responses `/zen/go/v1/responses` route. Qwen remains the default. A simulated Responses envelope/usage test passes; no GPT 5.6 Luna request was sent.

## Reviewer-aligned complete offline experiments — 2026-09-27

Matched training changed the M1 interpretation. Across three seeds, AC@1 is rule patch 0.8218, runtime 0.7730, reference 0.7184 and no-message-passing 0.7241. Patch−runtime is descriptive (+0.0489; Holm p=0.05339); patch−node-only is +0.0977 (Holm p=0.0004). The fresh audit found no integrity blocker but requires recurring-condition, missing-TrainTicket-telemetry and frozen-deletion qualifications.

The complete Qwen matched-information experiment covers all 116 fixed-test incidents through disjoint 8-case pilot and 108-case formal shards, two generations and 696 analyzed calls. Unconditional AC@1 is 0.6422 for LLM GraphPatch+GNN without fallback, 0.7931 for catalog-direct, and 0.8218 for rule GraphPatch+GNN. The paired LLM-patch minus catalog-direct delta is −0.1509 with 95% CI [−0.2356,−0.0689]. The proposed LLM GraphPatch necessity claim is therefore unsupported and contradicted in this setup; catalog exposure itself is strongly useful versus raw-direct (+0.4267 AC@1). GPT-5.6 Luna passed the bounded three-route compatibility test; DeepSeek V4.1 Flash returned HTTP400 for all three routes and was stopped without retry.

Corrected usage accounting includes cache fields. The combined Qwen evidence has 2,265,227 normalized input+output tokens plus 8,013,312 cache-read tokens (10,278,539 cache-inclusive reported volume). Across the five current OpenCode Go campaigns there are 705 attempts, 702 usage-bearing responses and 10,351,081 cache-inclusive reported tokens; failed DeepSeek usage is unknown and provider monetary cost was not returned. Eight earlier legacy-gateway failures remain separate.

Generalization is weak: LOFO AC@1 ranges 0.1389–0.4848; Boutique→TrainTicket is 0.4152 and TrainTicket→Boutique 0.3051. The GT-conditioned, simulation-only two-root proxy obtains Recall@2 0.7895–0.8246, but it combines different source times and cannot establish live concurrent-fault behavior. Synthetic scaling shows sub-millisecond recorded GPU inference at 520 candidates but a slower full GPU pipeline than CPU for these small graphs. Fresh B2/B3 audit is overall WARN with no integrity BLOCK; the two-root artifact passes only within its disclosed simulation scope.

## Isolated recovery preparation

The reviewed namespace `e2r-supplement-20260925` is deployed separately from the user `online-boutique` namespace. Twelve deployments are 2/2 Ready. The initial six-window 180-second calibration produced 900/900 successful HTTP requests at about 5 offered rps, aggregate p95 17.34ms and six passing state canaries. A mode0600, short-lived `e2r-executor` kubeconfig is Role-scoped to the isolated namespace and cannot read `online-boutique` pods or delete namespaces.

The first live attempt produced a valid degradation but no repair because the system Ansible launcher lacked its Python module; rollback/cleanup passed. The second attempt stopped before injection when its pre-window exceeded the old baseline tolerance. Both remain in the denominator. The synchronized drift was traced to all 12 BusyBox sidecars ending `sleep 3600`; an isolated-only rollout changed those commands to a long sleep. Baseline v2 then held the same 12 Pod UIDs and zero restarts across 900 requests, with zero errors, aggregate p95 18.26ms and all canaries true.

The hash-bound v3 known-target sanity completed successfully. Frontend CPU stress raised p95 from a 16.97ms fresh pre-window to 1399.84ms. A scoped, observed-evidence typed deletion completed at fault age63.92s. Three fresh post windows returned p95 16.94/17.15/16.89ms, about4.997rps, zero errors and passing canaries; all three technical predicates and the independent oracle passed, cleanup was NotFound, and the observed Pod restart total remained zero. Fresh audit is WARN/no integrity BLOCK and limits this to one contract sanity: across v1–v3 there are three attempts, two injections, one completed typed mutation and one oracle PASS. A matched no-action versus typed block is being prepared; no expanded live campaign is yet authorized.

The subsequently reviewed matched block started in fixed no-action→typed order but was interrupted by the parent session. The no-action episode is complete and shows persistent business degradation: validation p95 1359.61ms and three observation p95 values 1500.68/1501.26/1399.67ms while the same injected UID stayed active; technical `fault_absent=False`, joint oracle FAIL and cleanup NotFound were recorded. The typed episode confirmed comparable degradation (p95 1399.26ms) and a successful scoped delete at age63.82s, but zero complete evaluation windows were saved before interruption, so its technical/oracle/cleanup outcome is missing. Originals remain immutable; reviewed sidecars record two planned/attempted/injected episodes, one repair invocation/mutation, oracle statuses `[FAIL,null]`, and zero complete matched pairs. The comparison is invalid and no rerun/replacement is authorized.

Fresh audit of the interrupted block ran 328 offline checks with no mismatch or fabrication blocker. It recognizes the no-action sequence as a valid standalone persistent-fault trajectory and the typed delete as an executed process fact only. Late NotFound observations arrived roughly34,223seconds after repair and the scoped token was Unauthorized for the late Pod query, so runtime equality stays null and cannot fill missing contemporaneous evidence.

## Anonymous replication artifact

The first sealed release candidate passed internal hashes and 12 offline relocation/formal-pipeline/multi-root tests, but fresh release audit BLOCKED it: the generated validation report copied original redaction markers and the tar header retained local owner/group identity. A second candidate fixed payload and tar metadata and independently passed all manifest/checkpoint/observation/test checks, yet its distributed verifier still embedded the sensitive values as constant-foldable byte fragments, so it too remains rejected. The next candidate removes all source-specific verifier literals, retaining only generic structural secret/path patterns, and still requires independent release clearance.
