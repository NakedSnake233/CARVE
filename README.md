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
| `paper-replication/` | Separate, unchanged v8 archive cited by the supplied manuscript |
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

The original v8 archive is retained byte-for-byte:

```bash
cd paper-replication
sha256sum -c e2r_revision_v8.tar.gz.sha256
mkdir unpacked
python -m tarfile -e e2r_revision_v8.tar.gz unpacked
```

Read its own README and assurance before replaying historical analyses. Its target dependency lock and historical test assurance are separate from the current source snapshot. Its legacy paths use documented placeholders and may require configuration. No newer study inherits that archive's old audit status.

## Anonymous review and licensing

Submit the generated ZIP as an attachment, without `.git`. The hosting repository's owner/profile and repository URL are not anonymous and should not be used as the anonymous review link. `DATA_AVAILABILITY.txt` supplies an attachment-oriented statement.

Original third-party source notices and dependency attribution are retained where present. This export does not assign a new open-source license to unlicensed first-party source or change upstream licenses; see `COPYRIGHT_NOTICE.md`.
