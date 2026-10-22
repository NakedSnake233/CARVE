# CARVE supplementary artifact

This package provides an anonymous snapshot of the current implementation, selected offline engineering tests, and reproducibility material. The implementation retains its original `methods/E2R` module names. No Kubernetes credentials, API credentials, SSH files, personal Git history, virtual environment, or live deployment state is included.

## What is included

| Location | Purpose |
|---|---|
| `methods/`, `rca_bench/` | Current implementation and local support modules |
| `scripts/e2r/` | Experiment, analysis, and native orchestration source |
| `tests/intervention/` | Curated offline tests; in-memory clients and temporary files |
| `scripts/reproduce_context.py` | Recompute a frozen context predictor's reported metrics from exported feature tables |
| `data/context_repair/` | Frozen model, 72 feature/label rows, 32 online outcomes, expected metrics |
| `paper-replication/e2r_revision_v8/` | Uncompressed historical v8 source, observations, models, and analyses cited by the supplied manuscript |
| `ENVIRONMENT.json`, `ASSURANCE.json` | Tested environment and the exact validation actually performed |
| `MANIFEST.json` | File sizes and SHA-256 for this distribution |

**Evidence boundaries matter.** The manuscript's existing numerical tables cite the historical v8 package. The newer context-repair implementation and data are an additional development study and do not replace those tables or establish the original C1/C2 claims. See `EVIDENCE_SCOPE.md`.

## Quick start: offline only

Use a Linux Python environment. The exact locally tested version and package versions appear in `ENVIRONMENT.json`; a clean dependency installation has not been certified.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider
python scripts/reproduce_context.py
python scripts/verify_distribution.py
```

The first command suite validates contracts, configuration projection, missing evidence, bounded policy behavior, learning interfaces, and the public feature-table replay. It does not contact a cluster or call a language-model API. Test fixtures, including records labeled `source=live` inside mocked tests, are engineering fixtures and are never empirical experiment evidence.

`reproduce_context.py` loads the frozen parameters, evaluates the exported rows, and checks recovery/safety Brier and fixed-bin calibration errors against `EXPECTED_METRICS.json`. It does not train, select a checkpoint, or change thresholds. It also checks all 32 recorded online outcome denominators.

## Native and historical experiment drivers

The larger `scripts/e2r` source collection is included for inspection. Many collection/build/supervision drivers require omitted native deployments, registration files, source datasets, or local automation infrastructure; they are **not** part of the quick start. The exported context feature tables cannot reconstruct raw HTTP traffic or the Kubernetes environment.

Live experiments require an independently configured disposable Kubernetes environment, workload services, explicit authorization, and local credentials. Obtain and configure those separately. `KUBECONFIG` and external configuration paths must refer to the reviewer's own environment. No supplied command launches a live experiment by default.

Historical graph scoring additionally requires the separately obtained upstream `dyrca_rca` component through `DYRCA_ROOT`. Some historical adapters require MicroRemed. These integrations and third-party implementations are not bundled into the current source tree.

## Historical manuscript package

The historical v8 material is already expanded into regular files. No archive download or extraction is needed. All 7,321 member files retain their original bytes. Verify the historical manifest from this repository root:

```bash
python paper-replication/e2r_revision_v8/scripts/e2r/verify_anonymous_artifact.py \
  --artifact-root paper-replication/e2r_revision_v8
```

Read its own README and assurance before replaying historical analyses. Its target dependency lock and historical test assurance are separate from the current source snapshot. Its legacy paths use documented placeholders and may require configuration. No newer study inherits the historical audit status. `paper-replication/SOURCE_PROVENANCE.json` records the original archive digest for provenance; that archive is not distributed in the current tree. Native `.pt` model files keep their original serialization.

## Anonymous review and licensing

Files are published directly for browsing through an Anonymous GitHub mirror. Use the configured anonymous mirror URL as the review link and verify that it displays this README and the `paper-replication/e2r_revision_v8/` directory. The source hosting account and its Git history are not anonymous review material. `DATA_AVAILABILITY.txt` supplies a statement for the anonymous repository delivery.

Original third-party source notices and dependency attribution are retained where present. This export does not assign a new open-source license to unlicensed first-party source or change upstream licenses; see `COPYRIGHT_NOTICE.md`.
