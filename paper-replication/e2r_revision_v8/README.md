# E2R revision artifact

This anonymous package contains the E2R implementation, observation snapshots, frozen model/results, analysis scripts, review-aligned experiment records and audits. External baseline implementations are intentionally excluded.

## Environment

The declared target is Linux x86_64/Python 3.10 with `migration/requirements.lock.txt`. Release smoke tests were run in the recorded host environment, not a clean install of that target lock; see `ASSURANCE.json`. Set `${DYRCA_ROOT}` to the separately obtained upstream pure graph/model component (not bundled) before model/scoring tests. Offline analyses under `scripts/e2r/` do not require API or cluster credentials. Live recovery records are evidence only; the scoped kubeconfig and all API/SSH credentials are excluded.

## Anonymization and audit hashes

Text files are path/host scrubbed, so historical pre-scrub hash receipts intentionally differ. `ANONYMIZATION_MAP.json` binds each transformed private source hash to its publishable staged hash. `MANIFEST.json` binds every staged file except its own self-referential bytes. Full B2/B3, M4 and interrupted-block audit traces (including the hash-verified historical collector source) are included under `.aris/traces/`.

## Final reporting artifacts

- `refine-logs/EXPERIMENT_RESULTS.md` and `EXPERIMENT_TRACKER.md`
- `experiments/e2r/runs/m1_matrix_v3/`
- `experiments/e2r/runs/m2_qwen38_combined_analysis_v2/` and `m2_qwen38_formal_analysis_v2/`
- `experiments/e2r/runs/generalization_analysis_v1/`
- `experiments/e2r/runs/scaling_analysis_v1/`
- `experiments/e2r/runs/b2_b3_audit_v1/` and `m4_sanity_audit_v1/`
- `experiments/e2r/runs/recovery_matched_frontend_cpu_v1/analysis_v1/` (invalid interrupted comparison, preserved honestly)

`MANIFEST.json` records SHA-256 and byte size for every staged regular file except itself.
