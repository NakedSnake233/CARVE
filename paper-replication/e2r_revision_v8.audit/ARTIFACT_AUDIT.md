# E2R revision v8 release audit

**Verdict: PASS for the inspected anonymous release package. No remaining release blocker found.** V6 and v7 remain rejected; this verdict applies only to the v8 directory/archive bytes identified below.

Date: 2026-09-27. Fresh reviewer: `/root/artifact_release_audit`; auxiliary offline reviewer: `/root/artifact_release_audit/offline_test_audit`. Review independence: **same-family**. Acceptance status: **provisional**. No network, API, SSH, cluster, GPU or live-experiment operation was performed. The reviewers did not edit the package, archive, sidecar, source or experimental results. Tests and reports wrote outside the sealed package.

Archive SHA-256: **`76d5ce9f035a0b42a27fc8e6c82c58e0d663a65d06212b4c39016a4247c60c4b`**.

## Independent checks

| Check | Result and evidence |
|---|---|
| Every manifest hash and byte size | **PASS:** all 7,320 unique entries match; no absent file or mismatch |
| Manifest coverage | **PASS:** 7,321 actual files; only the explicitly self-excluded `MANIFEST.json` is unlisted; final `VALIDATION.json` and `ASSURANCE.json` are covered |
| Sizes | 165,670,654 manifested bytes; 167,137,372 bytes including the manifest |
| Archive/directory equivalence | **PASS:** all 7,321 regular files byte-identical; no extra or missing file |
| Tar path/link safety | **PASS:** 7,415 members, all under the expected root; no traversal/absolute path, link, special entry or duplicate |
| Tar anonymity metadata | **PASS:** every member has UID/GID 0, empty owner/group names and one fixed mtime; extended headers contain paths only |
| External SHA sidecar | **PASS:** exact match to the archive digest above |
| Finished payload identity/credential scan | **PASS within tested patterns:** all 7,321 files scanned, including generated metadata and previous audit evidence; no forbidden marker or credential-pattern match |
| Python constant expressions | **PASS:** 121 Python files parsed; no sensitive value recovered by literal concatenation/constant folding |
| Binary checkpoints | **PASS:** 30 checkpoints and all 480 internal ZIP entries inspected as bytes without unpickling; no tested forbidden marker |
| Private credential files | **PASS:** no private kubeconfig, LLM credential config, SSH key/config directory or private-key file |
| Full-data observations | **PASS:** 798 distinct relative references resolve inside the package with matching case IDs |
| Anonymization map | **PASS:** all 97 unique entries match both private source and staged digests; no changed copied source lacks a mapping |
| Source coverage/freshness | **PASS:** 7,219 copied files equal current source bytes and 97 are mapped transformations; no missing source; five generated metadata/readme files treated separately |
| Final metadata claims | **PASS:** redaction counts/categories, payload bytes/count, manifest/full count, commands and twelve-test counts agree; no original marker values retained in redaction records |
| Narrow offline tests | **PASS:** 12 passed in 2.72 seconds, no skips, parent pytest configuration excluded |
| Included read-only release verifier | **PASS:** 7,320 manifest entries, 798 observations, 16 required files, zero errors; output outside the package |
| Final result/audit support | **PASS:** required final analyses, original audit trace inventories, historical collector and generalization source support present |
| Final comparison claims | **PASS:** the interrupted comparison remains invalid with zero complete matched pairs |

The independent credential scan includes exact known source markers, absolute home/user markers, OpenCode/OpenAI/Anthropic/GitHub/AWS key formats, private-key PEM, JWT and embedded kubeconfig key/certificate patterns. Constant-expression inspection supplements literal-byte scanning, addressing v7’s specific blind spot. These are bounded checks, not a proof against every possible encoding of a secret.

## Previous blockers and qualifications resolved

**V6 redaction and tar leaks:** final redaction entries contain safe categories, not source values. Archive identities are normalized. The finished metadata is scanned and included in the manifest; counts and the manifest’s sole self-exclusion are accurately stated (`README.md:23`).

**V7 folded literals:** the distributed verifier now uses only generic structural regexes (`scripts/e2r/verify_anonymous_artifact.py:56–61`). Independent AST inspection found no remaining recoverable source-specific host, password or username. Literal-byte scans across the full payload also found none.

**Provenance wording:** the verifier now states that staged hashes are checked while source digests are recorded provenance because private bytes are not bundled (`verify_anonymous_artifact.py:71`). That matches its implementation. This auditor additionally had read-only access to the private source and independently matched all 97 source/staged pairs. All 7,316 copied source files were accounted for, excluding only generated README/manifest/map/validation/assurance files.

The anonymization map does not retroactively attest historical execution code. Historical receipts were preserved: the B2/B3, M4 and interrupted audit JSON files and their three forensic trace trees are byte-identical to v7. The interrupted sidecars retain their original source hashes; all three original-receipt → map-source → staged-file joins match. No new or fabricated historical attestation was found. Older mutable-source receipts still describe their historical versions, as expected.

**Supporting evidence:** the final formal-only and combined M2 corrected v2 analyses are included. B2/B3, M4 and interrupted audit trace inventories contain 33, 30 and 15 files. The historical M4 collector matches pinned SHA-256 `12f788298199b4e3c95d47d13bd53fb1562d1547522be3522b3ffd13ad74c0e3`. The generalization worker is present, and all six source split definitions equal their saved run-local copies.

## Accepted scope and retained limitations

The independent test replay used the included three-file selection with `-c /dev/null --confcutdir=<artifact>`, `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`, and a temporary test-output directory. Its environment was Python 3.13.9 / pytest 8.4.2 / NumPy 2.3.5 / Torch 2.14.0+cpu plus separately supplied DY-RCA source. `ASSURANCE.json` accurately records the actual environment and `target_lock_clean_install_tested: false`; `README.md:7` discloses the external source and distinguishes this from the declared Python 3.10 target.

This PASS verifies packaging, inspected anonymity, source-to-distribution mapping, support availability and the twelve selected tests. It does not establish a clean installation of the target lock, a self-contained dependency bundle, full-suite passage, retraining, live recovery reproduction or a rerun of every historical audit. Historical audit scripts retain some original path/hash assumptions; the release-specific verifier is the supported read-only package check.

The final tracker, findings, results, sidecars and analysis retain **planned/attempted/injected = 2/2/2**, **repair invocation/mutation = 1/1**, **oracles = [FAIL, null]**, and **complete matched pairs = 0**. Thirteen inspected comparison/accounting fields contain no true valid-comparison flag or positive complete-pair count. The typed post-repair outcome remains missing; a late absence observation cannot fill it. This release verdict grants no live-experiment, rerun or replacement authorization, and does not upgrade the original experiment audits beyond their stated scope.

## Evidence and immutability

Trace directory: `.aris/traces/experiment-audit/20260927_artifact_release/v8/`. It contains independent manifest/archive/byte results, complete input hashes, AST findings, metadata checks, mapping coverage, comparison inspection, scripts, request/response metadata and final immutability results. Auxiliary replay records are `v8_offline_test_replay.json` and `v8_included_release_verifier.json` in the parent trace directory.

The final verification confirms every audited package/archive/sidecar byte stayed unchanged and no package file was added during the audit. Any subsequent payload/archive change requires a new digest and corresponding verification; the PASS above remains tied to the stated v8 digest.
