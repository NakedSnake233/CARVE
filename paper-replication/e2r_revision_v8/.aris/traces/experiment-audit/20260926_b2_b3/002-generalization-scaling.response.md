**Generalization: WARN. Scaling: WARN.** No actual leakage or BLOCK-level contradiction found. Same-family/provisional review.

Artifacts saved in [generalization_scaling](${PROJECT_ROOT}/.aris/traces/experiment-audit/20260926_b2_b3/generalization_scaling):

- [REVIEW.md](${PROJECT_ROOT}/.aris/traces/experiment-audit/20260926_b2_b3/generalization_scaling/REVIEW.md): findings, exact source lines, narrow claims.
- `verdict.json`, `forensic_audit.py`, `forensic_output.json`, `forensic_stdout.json`, `sha256_inventory.json`, `input_fingerprints.json`.

Verified all six exact splits, 798 original GT labels, 2,784 prediction identities/metrics, 2,960 regenerated plans and 18 checkpoint validation losses. All arithmetic checks pass. No observed train/test ID, source-file, exact observation or exact tensor leakage.

CPU replay changes 499 full rankings and 70 true ranks, consistent with numerical near ties; only six patch true ranks change. Patch AC@1/3/5 remain unchanged. Patch MRR changes T3 **0.363055→0.362965**, TrainTicket→Boutique **0.444613→0.445178**. Maximum validation-loss discrepancy: **5.88e-8**.

Scaling’s 400 timing rows, all summaries/ratios, and 20 reconstructed fixture dimensions pass. Warnings: incomplete hardware/thread provenance; CPU/GPU PyTorch versions differ; p95 selects ninth of ten observations; pipeline sums include repeated catalog construction; additional telemetry keys leave model tensors unchanged.

Claims should remain restricted to controlled-dataset localization and synthetic implementation overhead. Historical run-specific content hashes are absent; audit-time hashes establish current consistency only.
