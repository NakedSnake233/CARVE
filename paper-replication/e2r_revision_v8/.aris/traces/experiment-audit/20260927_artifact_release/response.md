# E2R revision v6 release audit

**Verdict: BLOCK for anonymous release.** The archive is internally intact, but identifying values leak through the generated validation report and tar ownership metadata. The final experiment reports correctly retain the interrupted comparison as invalid.

Date: 2026-09-27. Reviewer: fresh agent `/root/artifact_release_audit`; auxiliary offline replay reviewer: `/root/artifact_release_audit/offline_test_audit`. Review independence: **same-family**. Acceptance status: **provisional**. No network, cluster, API, SSH or GPU operations were performed. The package, archive, source and experimental records were not edited. Reports and traces were written outside the package.

Audited archive SHA-256: `96b708c0526163ac0151af4aa2adca009f6c9e1f3d03c7d714161d3fe150fa8d`.

## Findings and required fixes

### F001 — BLOCK: the redaction log reintroduces identifying values

`VALIDATION.json:9`, `:45`, `:61`, `:161` and many additional entries contain the original home paths, worker address and node name as `redactions[].marker`. Independent scanning of **all 7,207 files** found 50 home-path occurrences and 43 local-user occurrences in this report, as well as the original sensitive host and kubeconfig-path markers. These overlapping counts are not counts of distinct secrets. No other package file matched the tested identity markers.

The report nevertheless says `status: PASS` (`VALIDATION.json:2`) and `forbidden_matches: []` (`:288`). Tracker R050 repeats “0 forbidden markers” and `DONE_PACKAGE_PASS` (`refine-logs/EXPERIMENT_TRACKER.md:31`). These final release claims are false. The builder scans before writing its validation report; a scan of the complete finished payload catches the leak.

**Fix:** retain only safe marker classes/replacement IDs and counts in the distributed redaction log. Scan every completed metadata/report file before sealing; update R050 from the final independent result. Preserve the original v6 as rejected evidence and issue a new version.

### F002 — BLOCK: the tar header identifies the local owner/group

All **7,276 tar members** retain identifying owner and group names, with UID/GID 1000. File-content redaction does not remove this metadata. The independent tar inspection categorizes owner names without copying those identifying values into this report.

**Fix:** rebuild the archive with anonymous owner/group names and normalized numeric ownership, inspecting standard and extended tar headers. Recompute the external archive digest and repeat the full-content/metadata scan.

### F003 — WARN: manifest and count claims exclude generated metadata

Every one of the **7,205 manifest entries** has the correct byte size and SHA-256; there are no duplicate manifest paths, absent files or hash mismatches. Their total is **160,139,725 bytes**, exactly matching `VALIDATION.json:3–4`. The actual package contains **7,207 files / 161,592,480 bytes**: `MANIFEST.json` and `VALIDATION.json` are unlisted. Self-exclusion is expected for the manifest, but `README.md:19` incorrectly promises a digest for “every staged file,” and the final validation report is outside its per-file integrity coverage.

**Fix:** define the self-exclusion explicitly; include the finished validation report in the manifest and distinguish payload counts from full-package counts. The external archive hash covers every archived file but does not make the per-file coverage statement true.

### F004 — WARN: passing tests do not establish a standalone locked-environment reproduction

The exact claimed test selection independently passed **12/12** from the package directory. A second replay excluding parent pytest configuration (`-c /dev/null --confcutdir=<artifact>`) reported **12 passed in 2.49s**, exit 0 and no skips. The selection was: `tests/e2r/test_multi_root_proxy.py`, `test_m2_formal_pipeline.py`, and `test_observation.py`, with bytecode/cache disabled. `ASSURANCE.json:3–6` and `VALIDATION.json:295–299` are plausible for that narrow selection.

The replay used the already installed project environment: **Python 3.13.9, pytest 8.4.2, NumPy 2.3.5, Torch 2.14.0+cpu**, plus external DY-RCA source. It did not reproduce the README’s Python 3.10 target or install the lock (which records different package versions). The package contains neither dependency wheels nor the required DY-RCA graph/model source. `README.md:7` says `pytest -q`, which is broader than the sealed three-file check; the full suite was not established by this audit. No download or clean-environment installation was attempted.

**Fix:** document the exact verified command/environment and narrow scope, provide an immutable upstream DY-RCA source/version reference, and distinguish dependency installation from offline replay. A clean locked-environment claim requires a separate reproduction.

### F005 — WARN: final reports are present, but referenced forensic support is incomplete

The latest M1, B2/B3, M4 sanity and interrupted-block audit reports are included. Sixteen final audit/status/sidecar/report files match the current workspace sources after the declared anonymization substitutions; this includes the latest interrupted-block code-review addendum. Generalization runs include their individual split, predictions, results and checkpoints.

The following support is missing from the package:

- Corrected **formal-only** `m2_qwen38_formal_analysis_v2/analysis.json` and `REPORT.md`, explicitly called final in `b2_b3_audit_v1/EXPERIMENT_AUDIT.md:70` and hashed in its JSON at `:5972` and `:5980`. The combined corrected v2 report is included.
- The B2/B3, M4 and interrupted-block `.aris` forensic trace trees. For example, B2/B3 report `:196` and M4 report `:11` direct readers to absent verifier outputs/source snapshots.
- The historical M4 `workload_oracle.py` used for source-provenance replay (`m4_sanity_audit_v1/EXPERIMENT_AUDIT.md:69`).
- Generalization’s source split definitions and remote worker, listed in B2/B3 audit JSON `:5947–5952` and `:5971`; the individual run-local split copies remain available.

**Fix:** include sanitized final analysis/support files needed for the advertised checks, or explicitly label excluded trace/source materials and the resulting reproduction limits. Historical intermediate/superseded files need not all be distributed, but the corrected final formal analysis and claimed forensic dependencies should not silently disappear.

### F006 — WARN: original-byte audit receipts need an anonymization provenance layer

Anonymization changes recorded bytes while embedded historical receipts correctly retain the original hashes. All three `block.interruption.json.source_hashes` consequently differ from the staged original JSON files. The included interruption verifier expects exact original hashes (`audit_v1/recompute_typed_and_accounting.py:57–64`) and reads `Path(block['baseline_path'])` literally (`:30`), while the staged block contains `${PROJECT_ROOT}/...`. It is therefore not a relocatable replay of all 328 historical checks. This is a packaging limitation, not evidence that the original measurements were fabricated. The verifier was inspected and was not executed because it writes under its audit directory.

**Fix:** retain historical receipts, add a safe original-digest → staged-digest transformation map, and supply a release-specific read-only verifier that resolves placeholders and writes outside the sealed tree. Clearly distinguish verified original evidence from anonymized distribution bytes; do not silently rewrite historical attestations.

## Independently verified release properties

| Check | Result |
|---|---|
| Every manifest hash/size and unique path | PASS: 7,205/7,205 |
| Tar/directory regular-file equivalence | PASS: 7,207 identical files; no missing or extra files |
| Tar path traversal / special links / duplicate members | PASS: none; all members below one expected root |
| Archive SHA-256 versus external sidecar | PASS: exact match |
| Credential filenames | PASS within scan: no `llm_rca_config.json`, `llm_config.json`, private kubeconfig, SSH key/config directory or private-key filename |
| Credential-content patterns | PASS within scan: no tested OpenCode/OpenAI/Anthropic/GitHub/AWS key pattern, private-key PEM, JWT or embedded kubeconfig private key/certificate |
| Binary checkpoints | PASS within scan: 30 checkpoints, including all 480 internal ZIP entries, inspected as bytes without pickle execution; no forbidden marker matches |
| Full-data observation resolution | PASS: 798 distinct relative paths exist inside the package and have matching case IDs |
| Narrow offline test selection | PASS: 12/12 with documented external environment |
| Latest result/audit inclusion | PASS for latest audit narratives and final tracker; WARN for missing supporting materials |
| Interrupted-comparison status | PASS: no completed matched comparison claimed |

The complete no-action trajectory, partial typed deletion, sidecars and analysis remain consistent with **planned/attempted/injected = 2/2/2**, **repair invocation/mutation = 1/1**, **oracles = [FAIL, null]**, **complete matched pairs = 0**. The final analysis report opens with invalid comparison (`recovery_matched_frontend_cpu_v1/analysis_v1/REPORT.md:3`); tracker R042–R045, findings, results and latest code-review addendum preserve that limitation. Historical approval language is prospective and is superseded explicitly; no stale final report was found claiming a valid paired result. No rerun or replacement is authorized by this release audit.

The credential checks are bounded pattern/file inspections, not a guarantee that every possible secret encoding is absent. Observational and simulation-only scope limits in the included experiment audits remain applicable; this release audit does not promote their semantic acceptance beyond provisional.

## Evidence

Full traces: `.aris/traces/experiment-audit/20260927_artifact_release/`. `deterministic_checks.json` contains independent manifest/tar/scan/observation results; `audited_input_hashes.json` pins all inspected package bytes; `source_freshness.json` records source-to-package comparisons; `offline_test_replay.json` records the auxiliary replay. `verify_release.py` is the independent deterministic checker. A final hash comparison confirms every audited package/archive/sidecar byte stayed unchanged, with no added package files. No repair to v6 was made by this audit.
