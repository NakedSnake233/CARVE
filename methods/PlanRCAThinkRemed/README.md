# PlanRCAThinkRemed

A unified **RCA + auto-remediation** SRE method for the
[MicroRemed](https://github.com/LLM4AIOps/MicroRemed) benchmark. It performs root
cause analysis and remediation as **one multi-agent workflow** — not an external
RCA module bolted in front of ThinkRemed.

Select it with:

```
--remediate-method PlanRCAThinkRemed
```

## Overview

Inspired by the plan2rca / DY-RCA pipeline (diagnostic planning → multi-agent
evidence collection → evidence-grounded hypothesis update → typed repair), the
method runs the full incident lifecycle:

```
incident / runtime context (label-free)
  → Plan2RCA-style diagnostic planning
  → multi-agent evidence collection (safe, read-only probes)
  → evidence-grounded RCA state update (hypotheses)
  → typed repair plan (repair DSL)
  → safe Ansible playbook generation
  → execution via MicroRemed's execution agent
  → verification via MicroRemed's status checker
  → reflection and retry
```

## Architecture

| Module | Responsibility |
|---|---|
| `coordinator.py` | Orchestrates the whole loop; entry point `remediate_failure(...)`. |
| `schemas.py` | Typed, JSON-serializable dataclasses (Evidence/Hypothesis/RCAPlan/RCAReport/RepairAction/RepairPlan). |
| `prompts.py` | Prompt templates + function-call tool schemas. Compact JSON, no private CoT. |
| `llm_json.py` | Robust `chat_api` → JSON helper. Never raises; falls back on bad output. |
| `safe_probe_agent.py` | Read-only probe runner with allowlist/denylist (wraps ThinkRemed's probe). |
| `rca_planner_agent.py` | Plan2RCA-style planner: multi-level hypotheses + modality-tagged steps. |
| `evidence_agents.py` | Resource / Log / Config / Dependency / Metric collectors (+ deterministic chaos discovery). |
| `hypothesis_agent.py` | Maintains ranked hypotheses; `should_stop_and_repair`; builds RCA report. |
| `cloudops_retriever.py` | Lexical Cloud-OpsBench retriever used as an RCA enhancer (RAG). |
| `repair_planner_agent.py` | Typed, low-risk repair plan (chaos-removal / config-restore / WaitAndVerify). |
| `playbook_generator_agent.py` | Renders the repair plan to executable Ansible YAML. |
| `safety_guard.py` | Rejects destructive plans/playbooks before execution. |
| `verification_bridge.py` | Wraps `chaos.check_status.check` (the only oracle consumer) + post-repair probes. |
| `logging_utils.py` | Structured debug trace + conversation log + token estimate. |

Every LLM-backed agent has a **deterministic fallback**, so the method runs
end-to-end even with no LLM configured (`models.llm.chat_api` unavailable). For
this benchmark's Chaos-Mesh fault model the deterministic path is itself correct:
it discovers the active `StressChaos/NetworkChaos/PodChaos/IOChaos` (or the
under-provisioned deployment) via read-only `kubectl get`, then removes that
specific injected fault / restores resources.

## How oracle hiding works (fairness)

MicroRemed passes `root_cause` and `failure_category` into every remediation
method. **PlanRCAThinkRemed treats these as hidden oracle labels:**

- They are **never** placed in any LLM prompt by default. Prompt builders only
  receive a label-free incident context; the optional `oracle_hint` is `None`
  unless `--use-oracle-rca` is set.
- They are used **only** inside `verification_bridge.verify_with_oracle(...)` to
  call MicroRemed's own status checker for verification/metrics — identical to
  what ThinkRemed/SoloGen use.
- `--use-oracle-rca` (ablation only) exposes them, clearly fenced as
  `[DEBUG ORACLE CONTEXT — ABLATION ONLY]`, and seeds a decisive hypothesis to
  measure an **upper bound**. Default behavior is oracle-hidden.

This is enforced by a unit test (`tests/test_cloudops_retriever.py::
test_oracle_labels_hidden_in_rca_prompts_by_default`).

## How Cloud-OpsBench RAG is used

With `--enable-cloudops-rag --cloudopsbench-root <path>`, the retriever returns
the top-`k` most lexically similar **past** cases, abstracted to: fault category,
symptom summary, useful diagnostic tools, evidence pattern, and (if inferable) a
repair hint. These are shown as clearly-separated **historical examples**, never
copied in as the current case's labels. Env mapping:
`online-boutique→boutique`, `train-ticket→trainticket`, `simple-micro→(none, returns [])`.
A pre-built index (`scripts/build_cloudops_rca_index.py` → `data/cloudops_rca_index.jsonl`)
is optional; the retriever scans the root in memory if the index is absent.

## How to run on MicroRemed

After installing into MicroRemed (see `../../integration/INSTALL.md`):

```bash
python3 inject_and_remediate.py \
  --experiments 10 \
  --namespace online-boutique \
  --wait-interval 10 \
  --injection-timeout 60 \
  --env online-boutique \
  --save-path conversations/planrca_online \
  --manifest-path envs/source-config/online-boutique-config.yaml \
  --remediate-method PlanRCAThinkRemed \
  --experiment-path experiments/easy.txt \
  --model qwen-plus \
  --enable-cloudops-rag \
  --cloudopsbench-root /path/to/Cloud-OpsBench \
  --rca-fewshot-k 3 \
  --max-rca-steps 6 \
  --max-remediation-trials 2
```

(`LLM_API_KEY` must be set for `models/llm.py`. The benchmark needs a live k3s
cluster with Chaos Mesh installed.)

## Ablation flags

```bash
--use-oracle-rca          # expose oracle labels (UPPER-BOUND debugging only)
# omit --enable-cloudops-rag   # no Cloud-OpsBench enhancement
--max-rca-steps 0         # RCA disabled -> direct-repair baseline
--max-remediation-trials 1   # single attempt, no reflection retry
```

`scripts/run_planrca_ablation.sh` runs: ThinkRemed baseline, PlanRCAThinkRemed
without RAG, with RAG, and with oracle exposed.

## Expected outputs

1. Normal MicroRemed conversation logs at `--save-path` (role/content messages,
   token + retry metadata) — same shape as ThinkRemed.
2. A structured debug trace per episode (under `--save-debug-dir`, else
   `--save-path`):

```json
{
  "method": "PlanRCAThinkRemed",
  "oracle_hidden": true,
  "namespace": "...", "env": "...",
  "rca_plan": {...}, "cloudops_examples": [...],
  "evidence_history": [...], "hypothesis_history": [...],
  "rca_report": {...}, "repair_plan": {...},
  "playbook": "...", "safety_check": {...},
  "execution_output": "...", "verification": {...},
  "attempts": 1, "success": true, "token_estimate": 1234
}
```
